"""SQLite 모의운용 원장. 실주문 API 없음. 최초 관측·결정·체결은 덮어쓰지 않는다(2026-10-04)."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
KST = timezone(timedelta(hours=9))


def instant(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("시간대가 있는 시각이 필요합니다")
    return dt.astimezone(UTC)


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("유한한 숫자가 필요합니다")
    return result


class PaperBook:
    def __init__(self, path, config):
        self.c = dict(config)
        c = self.c
        if finite(c['capital']) <= 0 or not c['version'] or not c['model']:
            raise ValueError('초기 자금·전략 버전을 확인하세요')
        for k in ['fee', 'tax', 'slip', 'max_price_move', 'max_daily_loss']:
            if not 0 < finite(c[k]) < 1:
                raise ValueError(k)
        if c['fee'] + c['tax'] >= 1 or not 0 < c['max_symbol_weight'] <= c['max_sector_weight'] <= 1:
            raise ValueError('비용·투자 한도를 확인하세요')
        if finite(c['max_quote_age_seconds']) <= 0 or finite(c['max_order_age_seconds']) <= 0:
            raise ValueError('유효 시간은 양수여야 합니다')
        if not c['targets'] or set(c['targets']) - {'samsung', 'sk_hynix'}:
            raise ValueError('지원하지 않는 종목')
        encoded = json.dumps(c, sort_keys=True, ensure_ascii=False)
        self.config_hash = hashlib.sha256(encoded.encode()).hexdigest()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS quotes (target TEXT, timestamp TEXT, source TEXT, price REAL,
            observed_at TEXT, PRIMARY KEY(target,timestamp,source));
          CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, target TEXT, side TEXT, qty INTEGER,
            decision_at TEXT, reference REAL, status TEXT, reason TEXT, signal TEXT);
          CREATE TABLE IF NOT EXISTS fills (order_id TEXT PRIMARY KEY, timestamp TEXT, price REAL,
            qty INTEGER, fee REAL, tax REAL, cash_after REAL);
          CREATE TABLE IF NOT EXISTS positions (target TEXT PRIMARY KEY, qty INTEGER, opened_at TEXT);
          CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, start_equity REAL);
          CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, timestamp TEXT, target TEXT, action TEXT, signal TEXT);
        ''')
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                stored = self._get('config')
                if stored and stored != encoded:
                    raise ValueError('기존 모의계좌 설정은 변경할 수 없습니다. 별도 원장을 사용하세요.')
                if not stored:
                    self._set('config', encoded)
                    self._set('cash', str(c['capital']))
                    self._set('halt', '')
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def _get(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))

    def halt(self, reason):
        with self.db:
            self._set('halt', reason)

    def snapshot(self):
        return {'schema_version': 1, 'mode': 'paper_only', 'config_hash': self.config_hash, 'config': self.c,
                'cash': float(self._get('cash')), 'halt_reason': self._get('halt'),
                'last_tick': self._get('last_tick'), 'last_equity': float(self._get('equity') or self._get('cash')),
                'positions': {r['target']: r['qty'] for r in self.db.execute('SELECT * FROM positions')},
                'decisions': [dict(r) for r in self.db.execute('SELECT * FROM decisions ORDER BY timestamp,rowid')],
                'fresh_targets': json.loads(self._get('fresh_targets') or '[]'),
                'orders': [dict(r) for r in self.db.execute('SELECT * FROM orders ORDER BY decision_at,rowid')],
                'fills': [dict(r) for r in self.db.execute('SELECT * FROM fills ORDER BY timestamp,rowid')],
                'quote_count': self.db.execute('SELECT count(*) FROM quotes').fetchone()[0]}

    def tick(self, signals, quotes, now, trading_day):
        now = instant(now)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self._get('last_tick') and now < instant(self._get('last_tick')):
                raise ValueError('이전 실행보다 과거 시각으로 원장을 변경할 수 없습니다')
            fresh = {}
            for q in quotes:
                t, price = instant(q['timestamp']), finite(q['price'])
                if q['target'] not in self.c['targets'] or t > now or price <= 0:
                    continue
                stamp = t.isoformat()
                old = self.db.execute('SELECT price FROM quotes WHERE target=? AND timestamp=? AND source=?',
                                      (q['target'], stamp, q['source'])).fetchone()
                if old and old[0] != price:
                    raise ValueError('동일 시세의 수정값으로 최초 관측을 덮어쓸 수 없습니다')
                self.db.execute('INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?)',
                                (q['target'], stamp, q['source'], price, now.isoformat()))
                local = t.astimezone(KST)
                minute = local.hour * 60 + local.minute
                if (now-t).total_seconds() <= self.c['max_quote_age_seconds'] and local.date() == now.astimezone(KST).date() and 540 <= minute <= 930:
                    if q['target'] not in fresh or t > fresh[q['target']]['time']:
                        fresh[q['target']] = {'price': price, 'time': t}
            cash = float(self._get('cash'))
            positions = {r['target']: dict(r) for r in self.db.execute('SELECT * FROM positions')}
            def equity():
                total = cash
                for target, pos in positions.items():
                    q = fresh.get(target)
                    last = self.db.execute('SELECT price FROM quotes WHERE target=? ORDER BY timestamp DESC LIMIT 1', (target,)).fetchone()
                    total += pos['qty'] * (q['price'] if q else last[0])
                return total
            local = now.astimezone(KST)
            day, minute = str(local.date()), local.hour*60+local.minute
            self.db.execute('INSERT OR IGNORE INTO days VALUES (?,?)', (day, float(self._get('equity') or equity())))
            start = self.db.execute('SELECT start_equity FROM days WHERE day=?', (day,)).fetchone()[0]
            if equity() <= start * (1-self.c['max_daily_loss']):
                self._set('halt', '일일 손실 한도 도달')
            pending = list(self.db.execute("SELECT * FROM orders WHERE status='pending' ORDER BY decision_at,rowid"))
            for order in pending:
                # 앞선 청산 비용이 한도를 넘겼다면 같은 실행의 뒤 매수도 막는다.
                if equity() <= start * (1-self.c['max_daily_loss']):
                    self._set('halt', '일일 손실 한도 도달')
                age = (now-instant(order['decision_at'])).total_seconds()
                reason = None
                if age > self.c['max_order_age_seconds']:
                    reason = 'expired'
                elif order['side'] == 'buy' and (self._get('halt') or minute >= 870):
                    reason = 'halted'
                if reason:
                    self.db.execute('UPDATE orders SET status=?,reason=? WHERE id=?', (reason, reason, order['id']))
                    continue
                q = fresh.get(order['target'])
                if not trading_day or not 585 <= minute <= 925 or not q or q['time'] <= instant(order['decision_at']):
                    continue
                if order['side'] == 'buy' and abs(q['price']/order['reference']-1) > self.c['max_price_move']:
                    self.db.execute("UPDATE orders SET status='rejected',reason='price_moved' WHERE id=?", (order['id'],))
                    continue
                qty = order['qty']
                buy = order['side'] == 'buy'
                price = q['price'] * (1+self.c['slip'] if buy else 1-self.c['slip'])
                amount = qty*price
                fee, tax = amount*self.c['fee'], 0 if buy else amount*self.c['tax']
                if buy:
                    if any(t not in fresh for t in positions):
                        continue
                    held = sum(p['qty']*fresh[t]['price'] for t,p in positions.items())
                    eq = equity() - qty*(price-q['price']) - fee
                    if (amount+fee > cash or qty*q['price'] > eq*self.c['max_symbol_weight'] or
                            held+qty*q['price'] > eq*self.c['max_sector_weight']):
                        self.db.execute("UPDATE orders SET status='rejected',reason='risk_changed' WHERE id=?", (order['id'],))
                        continue
                    cash -= amount+fee
                    positions[order['target']] = {'qty': qty, 'opened_at': now.isoformat()}
                    self.db.execute('INSERT INTO positions VALUES (?,?,?)', (order['target'],qty,now.isoformat()))
                else:
                    if order['target'] not in positions or positions[order['target']]['qty'] != qty:
                        raise ValueError('매도 수량과 모의계좌 잔고 불일치')
                    cash += amount-fee-tax
                    del positions[order['target']]
                    self.db.execute('DELETE FROM positions WHERE target=?', (order['target'],))
                self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?)', (order['id'],q['time'].isoformat(),price,qty,fee,tax,cash))
                self.db.execute("UPDATE orders SET status='filled',reason='' WHERE id=?", (order['id'],))
            if equity() <= start * (1-self.c['max_daily_loss']):
                self._set('halt', '일일 손실 한도 도달')
            def decide(target, side, qty, reference, sig=None):
                key = f"{self.c['version']}:{day}:{target}:{side}"
                if side == 'sell':
                    attempts=list(self.db.execute("SELECT id,status FROM orders WHERE target=? AND side='sell' AND substr(decision_at,1,10)=? ORDER BY rowid", (target,now.date().isoformat())))
                    if any(r['status']=='pending' for r in attempts):
                        return
                    # 만료 주문은 보존하고 재시도를 별도 기록한다. 전일 미청산도 같은 방식이다.
                    key += ':' + str(len(attempts)+1)
                    sig = dict(sig or {}, retry_of=attempts[-1]['id'] if attempts else None)
                self.db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?,?)',
                                (key,target,side,qty,now.isoformat(),reference,'pending','',json.dumps(sig or {},sort_keys=True)))
            if trading_day and 585 <= minute <= 920:
                for target,pos in positions.items():
                    if target in fresh and (minute >= 900 or instant(pos['opened_at']).astimezone(KST).date() < local.date()):
                        decide(target,'sell',pos['qty'],fresh[target]['price'])
                if not self._get('halt') and minute < 870 and all(t in fresh for t in positions):
                    eligible = []
                    for s in signals:
                        if s.get('target') not in fresh or s.get('model') != self.c['model'] or s.get('target_date') != day or s.get('kind') != 'direction' or str(s.get('horizon_days',1)) != '1':
                            continue
                        try:
                            created = instant(s['created_at_utc'])
                        except (ValueError, KeyError):
                            continue
                        if created > now or s.get('information_cutoff', 'pre_open') not in ('', 'pre_open', 'post_open'):
                            continue
                        # 당일 기록을 우선한다. 아침 모델은 개장 전 정보만 사용한다.
                        if s.get('information_cutoff') != 'post_open' and created.astimezone(KST).hour >= 9 and created.astimezone(KST).date() == local.date():
                            continue
                        rank = 0 if created.astimezone(KST).date() == local.date() else 1
                        eligible.append((rank,created,s))
                    seen = set()
                    for _,_,s in sorted(eligible,key=lambda x:(x[0],x[1])):
                        target=s['target']
                        if target in seen or target in positions:
                            continue
                        seen.add(target)
                        try:
                            up,down=finite(s['p_up']),finite(s['p_down'])
                        except (ValueError,KeyError,TypeError):
                            continue
                        if not 0 <= down <= 1 or not 0 <= up <= 1:
                            continue
                        decision_key=f"{self.c['version']}:{day}:{target}"
                        if self.db.execute('SELECT 1 FROM decisions WHERE id=?',(decision_key,)).fetchone():
                            continue
                        kept={k:s.get(k) for k in ['record_id','model','target_date','created_at_utc','p_up','p_down','information_cutoff']}
                        self.db.execute('INSERT INTO decisions VALUES (?,?,?,?,?)',
                                        (decision_key,now.isoformat(),target,'buy' if up>down else 'hold_cash',json.dumps(kept,sort_keys=True)))
                        if up <= down:
                            continue
                        eq=equity()
                        reserved=sum(r['qty']*r['reference']*(1+self.c['slip'])*(1+self.c['fee']) for r in self.db.execute("SELECT * FROM orders WHERE status='pending' AND side='buy'"))
                        held=sum(p['qty']*fresh[t]['price'] for t,p in positions.items())
                        budget=min(cash-reserved,eq*self.c['max_symbol_weight'],eq*self.c['max_sector_weight']-held-reserved)
                        ref=fresh[target]['price']
                        qty=math.floor(budget/(ref*(1+self.c['slip'])*(1+self.c['fee'])))
                        if qty>0:
                            # 실제 결과 열은 결정에 사용하거나 보관하지 않는다.
                            kept={k:s.get(k) for k in ['record_id','model','target_date','created_at_utc','p_up','p_down','information_cutoff']}
                            decide(target,'buy',qty,ref,kept)
                        else:
                            self.db.execute("UPDATE decisions SET action='blocked_by_limit' WHERE id=?",(decision_key,))
            self._set('cash', cash)
            self._set('equity', equity())
            self._set('last_tick', now.isoformat())
            self._set('fresh_targets', json.dumps(sorted(fresh)))
        return self.snapshot()
