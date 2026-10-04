"""예측 이후 시세 관측과 모의원장 갱신. 증권사 주문 기능은 없다."""
import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .paper_trading import PaperBook, instant, finite
except ImportError:
    from paper_trading import PaperBook, instant, finite

ROOT = Path(__file__).resolve().parents[1]
TICKERS = {'samsung': '005930.KS', 'sk_hynix': '000660.KS'}


def completed_quote(bars, target, observed_at):
    """1분봉의 끝 시각과 종가를 관측가로 기록한다. 미완성 봉·조정주가를 체결가로 쓰지 않는다."""
    now = instant(observed_at)
    valid = []
    for bar in bars:
        end = instant(bar['timestamp']) + timedelta(minutes=1)
        price = finite(bar['close'])
        if end <= now and price > 0:
            valid.append({'target': target, 'timestamp': end.isoformat(), 'price': price,
                          'source': 'Yahoo 1m close (unadjusted)'})
    return max(valid, key=lambda q: q['timestamp']) if valid else None


def fetch_quote(target):
    import yfinance as yf
    frame = yf.Ticker(TICKERS[target]).history(period='1d', interval='1m', auto_adjust=False, prepost=False)
    bars = [{'timestamp': idx.isoformat(), 'close': row['Close']} for idx, row in frame.iterrows()]
    return completed_quote(bars, target, datetime.now(timezone.utc).isoformat())


def is_session(now):
    import exchange_calendars as xcals
    from zoneinfo import ZoneInfo
    return bool(xcals.get_calendar('XKRX').is_session(now.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()))


def run(config, database, status, initialize=False, halt=False):
    now = datetime.now(timezone.utc)
    # 달력이 실패하면 휴장일을 추정하지 않고 중단한다.
    trading_day = False if initialize or halt else is_session(now)
    errors, quotes, signals = [], [], []
    if trading_day and not initialize:
        for target in config['targets']:
            try:
                q = fetch_quote(target)
                if q:
                    quotes.append(q)
                else:
                    errors.append(target + ': 완료된 1분봉 없음')
            except Exception as exc:
                errors.append(target + ': 시세 수집 실패 ' + type(exc).__name__)
            path = ROOT / 'forecast_history' / target / 'forecast_log.csv'
            try:
                with path.open(encoding='utf-8-sig', newline='') as f:
                    signals.extend(dict(r, target=target) for r in csv.DictReader(f))
            except Exception as exc:
                errors.append(target + ': 예측 수집 실패 ' + type(exc).__name__)
    book = PaperBook(database, config)
    try:
        if halt:
            book.halt('운영자 신규 매수 중지')
        result = book.snapshot() if initialize or halt else book.tick(signals, quotes, datetime.now(timezone.utc).isoformat(), trading_day)
    finally:
        book.close()
    result.update({'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'collection_errors': errors,
                   'trading_day': trading_day, 'initialized_only': initialize,
                   'notice': '수익성 미검증 관찰 전략. 다음 실행에서 결정 이후의 신선한 1분봉 종가로 가상 체결합니다. 실제 체결·호가를 보장하지 않습니다.'})
    status = Path(status)
    status.parent.mkdir(parents=True, exist_ok=True)
    # 브라우저용 요약과 별개로 SQLite에 전체 원장이 남는다.
    result['order_count'] = len(result['orders'])
    result['fill_count'] = len(result['fills'])
    result['decision_count'] = len(result['decisions'])
    result['orders'] = result['orders'][-100:]
    result['decisions'] = result['decisions'][-100:]
    result['fills'] = result['fills'][-100:]
    status.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'macro_inputs/paper_trading.json')
    parser.add_argument('--db', type=Path, default=ROOT/'paper_history/account.sqlite')
    parser.add_argument('--status', type=Path, default=ROOT/'docs/lab/paper_status.json')
    parser.add_argument('--initialize', action='store_true', help='관측·결정 없이 가상 계좌만 시작')
    parser.add_argument('--halt', action='store_true', help='신규 매수 중지. 보유 주식의 모의 청산은 유지')
    args = parser.parse_args()
    result = run(json.loads(args.config.read_text()), args.db, args.status, args.initialize, args.halt)
    print(f"모의계좌: 현금 {result['cash']:,.0f}원 / 보유 {result['positions']} / 수집 오류 {len(result['collection_errors'])}개")


if __name__ == '__main__':
    main()
