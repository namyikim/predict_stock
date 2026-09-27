"""현재 EPS/BPS와 명시적 배수 가정으로 계산하는 기업가치 참고 범위.

과거 백테스트 입력으로 쓰지 않는다. Yahoo 집계치이며 DART 확인 수치가 아니다.
"""
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CONFIG = Path(__file__).resolve().parents[1] / 'valuation_assumptions.json'


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def unavailable(reason):
    return {'status': 'unavailable', 'reason': reason,
            'scenarios': {'per': [], 'pbr': []}, 'per': None, 'pbr': None}


def calculate(info, price, price_date, observed_on, ticker, assumptions=None):
    assumptions = assumptions or json.loads(CONFIG.read_text(encoding='utf-8'))
    if info.get('currency') != 'KRW' or info.get('financialCurrency') != 'KRW':
        return unavailable('주가·재무자료의 원화 단위를 확인하지 못했습니다.')
    try:
        period = datetime.fromtimestamp(float(info['lastFiscalQuarter']), timezone.utc).date()
        observed = datetime.fromisoformat(observed_on).date()
        quoted = datetime.fromisoformat(price_date).date()
        if not 0 <= (observed - period).days <= 200:
            return unavailable('재무 기준일이 미래이거나 200일보다 오래되어 계산을 보류합니다.')
        if not 0 <= (observed - quoted).days <= 10:
            return unavailable('시세가 미래이거나 10일보다 오래되어 계산을 보류합니다.')
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return unavailable('재무 기준일 또는 시세 기준일을 확인하지 못했습니다.')
    price = number(price)
    if price is None or price <= 0:
        return unavailable('유효한 원종가가 없습니다.')
    eps, bps = number(info.get('trailingEps')), number(info.get('bookValue'))
    r = {'status': 'available', 'ticker': ticker, 'price': price, 'price_date': price_date,
         'observed_on': observed_on, 'financial_period': period.isoformat(),
         'eps_ttm': eps, 'bps': bps, 'per': None, 'pbr': None,
         'source': f'https://finance.yahoo.com/quote/{ticker}/key-statistics/',
         'assumptions': assumptions, 'scenarios': {'per': [], 'pbr': []},
         'evaluation_status': '예측 성능 미검증 — 현재 가치 시나리오이며 도달 시점을 예측하지 않음'}
    for key, basis in [('per', eps), ('pbr', bps)]:
        multiples = assumptions[key]
        if len(multiples) != 3 or any(number(x) is None or x <= 0 for x in multiples) or sorted(multiples) != multiples:
            raise ValueError('배수는 낮은 값부터 양수 세 개여야 합니다.')
        if basis is not None and basis > 0:
            r[key] = price / basis
            r['scenarios'][key] = [{'multiple': m, 'price': basis * m,
                                    'upside': basis * m / price - 1} for m in multiples]
    return r


def statement_basis(stock):
    """분기 재무제표로 최근 12개월 EPS·BPS·분기말을 만든다(2026-09-28).

    Yahoo 요약(get_info)이 한국 종목의 trailingEps·bookValue·lastFiscalQuarter 를 더 이상 주지 않아 '계산 보류'만
    나왔다. 분기 손익계산서·재무상태표는 있다. 주식 수는 '보통주 수'가 아니라 **Yahoo 가 EPS 에 쓴 주식 수**
    (분기 순이익 ÷ 기본 EPS 의 중앙값)를 쓴다 — 삼성전자는 우선주가 있어 보통주 수로 나누면 EPS 가 13% 부풀었다.
    최근 네 분기 순이익이 모두 있어야 한다. 없으면 None.
    """
    inc, bal = stock.quarterly_income_stmt, stock.quarterly_balance_sheet
    if inc is None or bal is None or inc.empty or bal.empty:
        return None
    ni_row = next((r for r in ('Net Income Common Stockholders', 'Net Income') if r in inc.index), None)
    eq_row = next((r for r in ('Common Stock Equity', 'Stockholders Equity') if r in bal.index), None)
    if ni_row is None or eq_row is None or 'Basic EPS' not in inc.index:
        return None
    income = inc.loc[ni_row].sort_index(ascending=False)
    last4 = income.iloc[:4]
    if len(last4) < 4 or last4.isna().any():
        return None
    eps = inc.loc['Basic EPS'].reindex(income.index)
    implied = (income / eps).replace([float('inf'), float('-inf')], float('nan')).dropna()
    implied = implied[implied > 0]
    if implied.empty:
        return None
    shares = float(implied.median())
    equity = bal.loc[eq_row].sort_index(ascending=False).dropna()
    if equity.empty:
        return None
    period = income.index[0]
    return {'trailingEps': float(last4.sum()) / shares, 'bookValue': float(equity.iloc[0]) / shares,
            'lastFiscalQuarter': float(period.timestamp()), 'shares_used': shares,
            'basis_note': f'분기 재무제표 최근 4분기 순이익({ni_row}) ÷ EPS 기준 주식 수 {shares:,.0f}주'}


def load(ticker, cache_dir, fetch=True, ticker_factory=None, now=None):
    """실패 시 오류 유형만 기록. 오래된 자료를 최신으로 다시 표시하지 않는다."""
    now = now or datetime.now(KST)
    today = now.astimezone(KST).date().isoformat()
    path = Path(cache_dir) / (ticker.replace('.', '_') + '_valuation.json')
    try:
        if fetch:
            if ticker_factory is None:
                import yfinance as yf
                ticker_factory = yf.Ticker
            stock = ticker_factory(ticker)
            info = stock.get_info()
            bars = stock.history(period='1mo', auto_adjust=False)
            # 당일 장중 가격을 종가로 표기하지 않는다.
            completed = bars[[d.date().isoformat() < today for d in bars.index]]
            completed = completed[completed['Close'].notna() & (completed['Close'] > 0)]
            last = completed.iloc[-1]
            fields = {k: info.get(k) for k in ('currency', 'financialCurrency',
                      'trailingEps', 'bookValue', 'lastFiscalQuarter')}
            # 요약값이 비면(2026-09 Yahoo 변경) 분기 재무제표로 직접 계산한다. 분기말 이름만 바뀐 경우도 받는다.
            if fields['lastFiscalQuarter'] is None and info.get('mostRecentQuarter'):
                fields['lastFiscalQuarter'] = info.get('mostRecentQuarter')
            if number(fields['trailingEps']) is None or number(fields['bookValue']) is None:
                try:
                    basis = statement_basis(stock)
                except Exception:
                    basis = None
                if basis:
                    fields.update({k: basis[k] for k in ('trailingEps', 'bookValue', 'lastFiscalQuarter')})
                    fields['basis_note'] = basis['basis_note']
            raw = {'info': fields,
                   'price': float(last['Close']), 'price_date': completed.index[-1].date().isoformat(),
                   'observed_on': today, 'ticker': ticker}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        else:
            raw = json.loads(path.read_text(encoding='utf-8'))
        if raw['ticker'] != ticker or not 0 <= (datetime.fromisoformat(today).date() -
                datetime.fromisoformat(raw['observed_on']).date()).days <= 10:
            return unavailable('재무자료 보관본이 없거나 오래되어 계산을 보류합니다.')
        result = calculate(raw['info'], raw['price'], raw['price_date'], today, ticker)
        if raw['info'].get('basis_note'):
            result['basis_note'] = raw['info']['basis_note']
        result['retrieved_on'] = raw['observed_on']
        result['cached'] = not fetch
        return result
    except Exception as exc:
        return unavailable(f'기업가치 자료 조회·검증 실패({type(exc).__name__}). 기존 예측은 계속 제공합니다.')


def render(r):
    if not r:
        return ''
    e = html.escape
    start = '<section class="valuation-reference"><h4>PER·PBR 기업가치 참고 범위</h4>'
    if r.get('status') != 'available':
        return start + '<p>계산 보류: ' + e(r.get('reason', '자료 없음')) + '</p></section>'
    def value(v, suffix=''):
        return '산출 불가' if v is None else f'{v:,.2f}{suffix}'
    out = [start, '<p><b>회사의 이익·순자산에 배수를 적용하면 얼마인가?</b>를 보는 참고표입니다. '
           '현재 가치의 가정별 범위이며, 특정 날짜에 도달할 목표주가가 아닙니다.</p>',
           f'<p>비교 종가: <b>{r["price"]:,.0f}원</b> ({e(r["price_date"])}) · '
           f'재무 기준 분기말: {e(r["financial_period"])} · 수집일: {e(r.get("retrieved_on", r["observed_on"]))}</p>',
           f'<p>최근 12개월 EPS(주당순이익): {value(r["eps_ttm"], "원")} · '
           f'BPS(주당순자산): {value(r["bps"], "원")}<br>'
           f'현재 PER: {value(r["per"], "배")} · 현재 PBR: {value(r["pbr"], "배")}</p>',
           '<div style="overflow-x:auto"><table><thead><tr><th>기준</th><th>낮은 배수 가정</th>'
           '<th>중간 배수 가정</th><th>높은 배수 가정</th></tr></thead><tbody>']
    for key in ('per', 'pbr'):
        out.append(f'<tr><th>{key.upper()}</th>')
        rows = r['scenarios'][key]
        if not rows:
            out.append('<td colspan="3">이익 또는 순자산이 0 이하이거나 누락되어 계산 보류</td>')
        for row in rows:
            out.append(f'<td>{row["multiple"]:g}배 → <b>{row["price"]:,.0f}원</b>'
                       f'<br>종가 대비 {row["upside"]:+.1%}</td>')
        out.append('</tr>')
    out += ['</tbody></table></div>', '<p>' + e(r['assumptions']['basis']) + '</p>',
            '<p>PER = 주가 ÷ 최근 12개월 EPS, PBR = 주가 ÷ BPS. 영업이익을 순이익으로 대체하지 않습니다. '
            '반도체 경기와 자사주·주식수 변동에 따라 해석이 달라집니다. 두 범위를 평균내거나 기존 모델 예측에 섞지 않습니다.</p>',
            '<p><b>예측 성능 미검증.</b> 기존 3·6·12개월 모델의 백테스트 성적은 이 표의 성적이 아닙니다. '
            'Yahoo 집계 EPS·BPS의 상세 산정 기간과 주식수 기준은 공시 원문으로 별도 확인해야 합니다.'
            + (f' EPS·BPS 계산: {e(r["basis_note"])}(Yahoo 요약값이 비어 있어 분기 재무제표로 직접 계산).'
               if r.get('basis_note') else '') + '</p>',
            f'<p><a href="{e(r["source"], quote=True)}">출처: Yahoo Finance (집계자료)</a></p></section>']
    return ''.join(out)
