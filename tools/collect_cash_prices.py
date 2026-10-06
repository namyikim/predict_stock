"""KRX 비수정 일봉과 Yahoo OHLC를 대조해 별도 연구 가격 보관본을 만든다.

가격 일치는 두 공급처의 교차 확인이며 거래소 체결 보증이 아니다.
과거 자료를 지금 받으면 observed_at은 지금이다. 기존 원장은 변경하지 않는다.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from tools.paper_market_data import normalize_bars, archive_bars, _session_bounds
from tools.paper_trading import instant, KST

ROOT = Path(__file__).resolve().parents[1]
TICKERS = {'samsung': '005930', 'sk_hynix': '000660'}
METHOD = 'krx_yahoo_ohlc_v1'
OHLC = ('open', 'high', 'low', 'close')


def verify_cash_bars(krx_rows, yahoo_rows, *, target, observed_at):
    """당시 받은 두 응답의 일치·완료·기업행사 조건을 통과한 봉만 반환한다."""
    if target not in TICKERS:
        raise ValueError('지원하지 않는 종목')
    sources = []
    issues = []
    for label, rows in (('krx', krx_rows), ('yahoo', yahoo_rows)):
        indexed = {}
        duplicate = set()
        for row in rows:
            try:
                day = str(instant(row['timestamp']).astimezone(KST).date())
            except (KeyError, ValueError, TypeError):
                issues.append({'session': None, 'reason': label+'_invalid_timestamp'})
                continue
            if day in indexed:
                duplicate.add(day)
            indexed[day] = row
        sources.append((indexed, duplicate))
    krx, krx_dupes = sources[0]; yahoo, yahoo_dupes = sources[1]
    accepted = []
    for day in sorted(set(krx) | set(yahoo)):
        reason = None
        if day in krx_dupes or day in yahoo_dupes:
            reason = 'duplicate_session'
        elif day not in krx or day not in yahoo:
            reason = 'missing_cross_source'
        else:
            a, b = krx[day], yahoo[day]
            try:
                # 기업행사 필드가 없으면 0으로 추정하지 않는다.
                if 'split' not in b or 'dividend' not in b:
                    raise ValueError('missing_actions')
                pairs = []
                for raw, source in ((a, 'KRX adjusted=False'), (b, 'Yahoo auto_adjust=False')):
                    rows = normalize_bars([dict(raw, source=source, adjustment='unadjusted',
                                               split=b['split'], dividend=b['dividend'])],
                                          target=target, timeframe='1d', observed_at=observed_at)
                    if not rows:
                        raise ValueError('not_completed_session')
                    pairs.append(rows[0])
                left, right = pairs
                if left['volume'] <= 0 or right['volume'] <= 0:
                    reason = 'untradeable_session'
                elif left['requires_corporate_action_adjustment']:
                    reason = 'corporate_action_unresolved'
                elif any(left[k] != right[k] for k in OHLC):
                    reason = 'ohlc_mismatch'
                else:
                    left['source'] = 'KRX adjusted=False / Yahoo OHLC cross-check'
                    left['cash_verification'] = {'method': METHOD,
                        'krx_ohlc': [left[k] for k in OHLC],
                        'yahoo_ohlc': [right[k] for k in OHLC]}
                    accepted.append(left)
            except (KeyError, ValueError, TypeError, OverflowError):
                reason = 'invalid_or_incomplete_bar'
        if reason:
            issues.append({'session': day, 'reason': reason})
    return accepted, issues


def _provider_rows(target, start, end):
    """네트워크 조회는 자식 프로세스에서 실행하고 부모가 시간을 제한한다."""
    from pykrx import stock
    import yfinance as yf
    # pykrx README의 명시적 비수정주가 인터페이스. 기본 adjusted=True를 사용하지 않는다.
    frame = stock.get_market_ohlcv(start.replace('-', ''), end.replace('-', ''),
                                  TICKERS[target], adjusted=False)
    krx = []
    for idx, row in frame.iterrows():
        krx.append(dict(timestamp=str(idx.date())+'T00:00:00+09:00',
                        **{key: float(row[col]) for key,col in zip(OHLC+('volume',), ('시가','고가','저가','종가','거래량'))}))
    frame = yf.Ticker(TICKERS[target]+'.KS').history(start=start,
        end=str(datetime.fromisoformat(end).date()+timedelta(days=1)),
        interval='1d', auto_adjust=False, actions=True, prepost=False)
    yahoo = []
    for idx, row in frame.iterrows():
        yahoo.append(dict(timestamp=idx.isoformat(), split=float(row['Stock Splits']),
                          dividend=float(row['Dividends']),
                          **{key: float(row[col]) for key,col in zip(OHLC+('volume',), ('Open','High','Low','Close','Volume'))}))
    return krx, yahoo


class KRXCredentialsMissing(RuntimeError):
    """KRX 비수정 가격 조회 인증이 설정되지 않았다."""


def fetch_sources(target, start, end):
    if not os.environ.get('KRX_ID') or not os.environ.get('KRX_PW'):
        raise KRXCredentialsMissing('GitHub Secrets의 KRX_ID·KRX_PW 설정 필요')
    # 네트워크 정체가 기존 원장 저장을 막지 않도록 종목당 40초로 제한한다.
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp)/'sources.json'
        subprocess.run([sys.executable, '-m', 'tools.collect_cash_prices', '--fetch', target,
                        '--start', start, '--end', end, '--out', str(output)],
                       cwd=ROOT, capture_output=True, timeout=40, check=True)
        return json.loads(output.read_text())


def completed_session(now):
    day = instant(now).astimezone(KST).date()
    for _ in range(20):
        bounds = _session_bounds(str(day))
        if bounds and bounds[1] <= instant(now):
            return str(day)
        day -= timedelta(days=1)
    raise ValueError('최근 완료 거래일 확인 실패')


def collect_target(target, *, root=ROOT, now=None):
    if target not in TICKERS:
        raise ValueError('지원하지 않는 종목')
    stamp = instant(now or datetime.now(timezone.utc).isoformat())
    session = completed_session(stamp)
    folder = Path(root)/'paper_history/market_cash'
    folder.mkdir(parents=True, exist_ok=True)
    status_path = folder/(target+'_status.json')
    previous = json.loads(status_path.read_text()) if status_path.exists() else {}
    # 성공/실패와 관계없이 같은 한국 날짜·완료 거래일에는 한 번만 조회한다.
    attempt = str(stamp.astimezone(KST).date())+'/'+session+'/'+METHOD
    if previous.get('attempt') == attempt:
        return dict(previous, skipped=True)
    result = dict(target=target, method=METHOD, attempt=attempt, attempted_at=stamp.isoformat(),
                  completed_session=session, status='in_progress', accepted_bars=0)

    def save():
        temp = status_path.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        temp.replace(status_path)
    save()
    try:
        start = str(datetime.fromisoformat(session).date()-timedelta(days=365))
        krx, yahoo = fetch_sources(target, start, session)
        observed = now or datetime.now(timezone.utc).isoformat()
        bars, issues = verify_cash_bars(krx, yahoo, target=target, observed_at=observed)
        quality = archive_bars(bars, root=folder, target=target, timeframe='1d', observed_at=observed)
        result.update(status='available' if bars and not issues else 'partial' if bars else 'unavailable',
                      accepted_bars=len(bars), observed_at=instant(observed).isoformat(),
                      issues=issues, reason_counts=dict(Counter(x['reason'] for x in issues)), quality=quality)
        if not krx or not yahoo:
            result['error'] = 'empty_source_response'
    except Exception as exc:
        # 응답 본문·쿠키 등을 상태 파일로 노출하지 않고 실패 종류만 기록한다.
        result.update(status='unavailable', error=type(exc).__name__)
    save()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', choices=list(TICKERS))
    parser.add_argument('--start'); parser.add_argument('--end'); parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.fetch:
        args.out.write_text(json.dumps(_provider_rows(args.fetch, args.start, args.end)))
        return
    for target in TICKERS:
        result = collect_target(target)
        print(target, result['status'], '검증 완료 봉', result['accepted_bars'], result.get('error', ''))
        if result['status'] != 'available':
            print('::warning::'+target+' 현금 가격 검증 자료 불충분: '+result['status'])


if __name__ == '__main__':
    main()
