"""가상 매매 연구용 OHLCV 보관. 과거 자료의 수집 시각과 수정 이력을 보존한다."""
import hashlib
import json
import math
import sqlite3
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

try:
    from .paper_trading import instant, KST
except ImportError:
    from paper_trading import instant, KST


@lru_cache(maxsize=4096)
def _session_bounds(day):
    import exchange_calendars as xcals
    calendar = xcals.get_calendar('XKRX')
    if not calendar.is_session(day):
        return None
    return instant(str(calendar.session_open(day))), instant(str(calendar.session_close(day)))


def _number(value):
    if isinstance(value, bool):
        raise ValueError('숫자 대신 참/거짓이 들어왔습니다')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('유한한 가격·거래량이 필요합니다')
    return result


def normalize_bars(rows, *, target, timeframe, observed_at):
    """완료된 비조정 봉만 정규화한다. 잘못된 OHLCV·상충 중복은 수집 전체를 거부한다."""
    if target not in ('samsung', 'sk_hynix') or timeframe not in ('1m', '1d'):
        raise ValueError('지원하지 않는 종목·주기')
    observed = instant(observed_at)
    unique = {}
    for row in rows:
        start = instant(row['timestamp'])
        day = str(start.astimezone(KST).date())
        if start > observed:
            continue
        bounds = _session_bounds(day)
        if bounds is None:
            continue
        opened, closed = bounds
        if timeframe == '1d':
            start, end = opened, closed
        else:
            if start.second or start.microsecond:
                raise ValueError('분봉 시작 시각은 분 경계여야 합니다')
            end = start + timedelta(minutes=1)
            if start < opened or end > closed:
                continue
        if end > observed:
            continue
        if row.get('adjustment') not in ('unadjusted', 'yahoo_auto_adjust_false') or not row.get('source'):
            raise ValueError('비조정 시세와 출처를 명시해야 합니다')
        values = {k: _number(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
        if (min(values[k] for k in ('open', 'high', 'low', 'close')) <= 0 or values['volume'] < 0 or
                values['high'] < max(values['open'], values['close'], values['low']) or
                values['low'] > min(values['open'], values['close'], values['high'])):
            raise ValueError('OHLCV 범위 오류')
        split, dividend = _number(row.get('split', 0)), _number(row.get('dividend', 0))
        if split < 0 or dividend < 0:
            raise ValueError('기업행사 값 오류')
        normalized = dict(values, target=target, timeframe=timeframe, session=day,
                          bar_start=start.isoformat(), bar_end=end.isoformat(), observed_at=observed.isoformat(),
                          source=str(row['source']), adjustment=row['adjustment'], split=split, dividend=dividend,
                          requires_corporate_action_adjustment=(split not in (0, 1) or dividend != 0),
                          provenance='historical_backfill' if end.astimezone(KST).date() < observed.astimezone(KST).date() else 'observed_snapshot')
        key = normalized['bar_end']
        if key in unique and unique[key] != normalized:
            raise ValueError('동일 봉의 상충 중복')
        unique[key] = normalized
    return [unique[k] for k in sorted(unique)]


def available_bars(rows, *, decision_at):
    """당시 수집된 최신 버전만 선택. 조정하지 못한 기업행사 이전 구간은 사용하지 않는다."""
    now = instant(decision_at)
    selected = {}
    for row in rows:
        if instant(row['observed_at']) > now or instant(row['bar_end']) > now:
            continue
        key = (row['target'], row['timeframe'], row['bar_end'])
        old = selected.get(key)
        if old is None or instant(old['observed_at']) < instant(row['observed_at']):
            selected[key] = row
        elif old['observed_at'] == row['observed_at'] and old != row:
            raise ValueError('동일 수집 시각의 수정 충돌')
    boundaries = {}
    bases = {}
    for row in selected.values():
        series = row['target'], row['timeframe']
        basis = bases.setdefault(series, row['adjustment'])
        if row['adjustment'] not in ('unadjusted', 'yahoo_auto_adjust_false') or basis != row['adjustment']:
            raise ValueError('조정 계열 혼합 금지')
        if row['requires_corporate_action_adjustment']:
            key = row['target'], row['timeframe']
            boundaries[key] = max(boundaries.get(key, ''), row['bar_end'])
    return sorted((r for r in selected.values() if r['bar_end'] > boundaries.get((r['target'], r['timeframe']), '')),
                  key=lambda r: (r['target'], r['timeframe'], r['bar_end']))


def _connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'market.sqlite', timeout=30)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS bars (
            target TEXT, timeframe TEXT, bar_end TEXT, observed_at TEXT, value_hash TEXT, payload TEXT,
            PRIMARY KEY(target,timeframe,bar_end,observed_at));
        CREATE TABLE IF NOT EXISTS observations (
            target TEXT, timeframe TEXT, observed_at TEXT, response_hash TEXT, quality TEXT,
            PRIMARY KEY(target,timeframe,observed_at));
    ''')
    return db


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _expected_ends(timeframe, observed, rows):
    # 분봉 누락률은 당일 개장부터 관측 시각까지, 일봉은 응답의 첫 거래일부터 마지막 완료 거래일까지.
    day = observed.astimezone(KST).date()
    if timeframe == '1m':
        bounds = _session_bounds(str(day))
        if not bounds:
            return set()
        opened, closed = bounds
        count = max(0, int((min(observed, closed)-opened).total_seconds()//60))
        return {(opened+timedelta(minutes=i)).isoformat() for i in range(1, count+1)}
    if not rows:
        return set()
    first = min(instant(r['bar_start']).astimezone(KST).date() for r in rows)
    result = set()
    while first <= day:
        bounds = _session_bounds(str(first))
        if bounds and bounds[1] <= observed:
            result.add(bounds[1].isoformat())
        first += timedelta(days=1)
    return result


def archive_bars(rows, *, root, target, timeframe, observed_at):
    """별도 SQLite 트랜잭션으로 저장. 같은 응답 재시도는 무변경, 수정은 새 버전으로 보존한다."""
    # 입력의 종목·주기·시각을 검사하고 서로 다른 관측을 섞지 않는다.
    observed = instant(observed_at)
    stamp = observed.isoformat()
    if target not in ('samsung', 'sk_hynix') or timeframe not in ('1m', '1d'):
        raise ValueError('지원하지 않는 종목·주기')
    if any(r['target'] != target or r['timeframe'] != timeframe or r['observed_at'] != stamp or
           instant(r['bar_end']) > observed for r in rows):
        raise ValueError('보관 자료의 종목·주기·수집 시각 불일치')
    rows = sorted(rows, key=lambda r: r['bar_end'])
    response_hash = _digest(rows)
    db = _connect(root)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT observed_at FROM observations WHERE target=? AND timeframe=? ORDER BY observed_at DESC LIMIT 1', (target, timeframe)).fetchone()
            if previous and stamp < previous[0]:
                raise ValueError('과거 수집 시각으로 보관본을 바꿀 수 없습니다')
            existing = db.execute('SELECT response_hash,quality FROM observations WHERE target=? AND timeframe=? AND observed_at=?', (target, timeframe, stamp)).fetchone()
            if existing:
                if response_hash != existing[0]:
                    raise ValueError('같은 수집 시각의 응답을 덮어쓸 수 없습니다')
                return json.loads(existing[1])
            added = 0
            for row in rows:
                # 수집 시각만 다른 같은 값은 새 가격 버전이 아니다. 관측 이력은 아래 따로 남긴다.
                values = {k:v for k,v in row.items() if k not in ('observed_at', 'provenance')}
                digest = _digest(values)
                last = db.execute('SELECT value_hash FROM bars WHERE target=? AND timeframe=? AND bar_end=? ORDER BY observed_at DESC LIMIT 1', (target, timeframe, row['bar_end'])).fetchone()
                if not last or last[0] != digest:
                    db.execute('INSERT INTO bars VALUES (?,?,?,?,?,?)', (target, timeframe, row['bar_end'], stamp, digest, json.dumps(row, ensure_ascii=False)))
                    added += 1
            expected = _expected_ends(timeframe, observed, rows)
            covered = {r['bar_end'] for r in rows} & expected
            missing = len(expected-covered)
            quality = {'observed_at': stamp, 'timeframe': timeframe, 'received_bars': len(rows),
                       'new_versions': added, 'expected_bars': len(expected), 'missing_bars': missing,
                       'missing_ratio': missing/len(expected) if expected else None,
                       'coverage_basis': '이번 수집 응답의 완료 봉',
                       'collection_interval_seconds': (observed-instant(previous[0])).total_seconds() if previous else None,
                       'latest_bar_end': max((r['bar_end'] for r in rows), default=None),
                       'latest_bar_age_seconds': (observed-max(instant(r['bar_end']) for r in rows)).total_seconds() if rows else None,
                       'corporate_action_bars': sum(r['requires_corporate_action_adjustment'] for r in rows),
                       'daily_range_note': '응답 시작 이전 자료의 누락은 판정하지 않음' if timeframe=='1d' else None}
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?)', (target, timeframe, stamp, response_hash, json.dumps(quality, ensure_ascii=False)))
            return quality
    finally:
        db.close()


def read_archive(root, *, target, timeframe):
    path = Path(root)/'market.sqlite'
    if not path.exists():
        return []
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM bars WHERE target=? AND timeframe=? ORDER BY bar_end,observed_at', (target, timeframe))]
    finally:
        db.close()


def daily_collected(root, *, target, observed_at):
    path = Path(root)/'market.sqlite'
    if not path.exists():
        return False
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        row = db.execute("SELECT observed_at,quality FROM observations WHERE target=? AND timeframe='1d' ORDER BY observed_at DESC LIMIT 1", (target,)).fetchone()
        if not row or instant(row[0]).astimezone(KST).date() != instant(observed_at).astimezone(KST).date():
            return False
        quality = json.loads(row[1])
        now = instant(observed_at)
        day = now.astimezone(KST).date()
        # 아침에 전일 일봉을 받았어도 장 마감 뒤에는 당일 완료 봉을 한 번 더 받아야 한다.
        while True:
            bounds = _session_bounds(str(day))
            if bounds and bounds[1] <= now:
                return bool(quality['received_bars'] and quality['latest_bar_end'] == bounds[1].isoformat())
            day -= timedelta(days=1)
    finally:
        db.close()
