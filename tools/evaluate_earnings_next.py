"""고정 입력으로 실적 발표 후 다음 분기 모델을 검증한다. 원장은 변경하지 않는다."""
import argparse
import hashlib
import io
import json
import subprocess
from pathlib import Path

import pandas as pd
import build_earnings_forecast as ef


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-ref', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', args.source_ref+'^{commit}'], cwd=ef.ROOT, text=True).strip()
    hashes = {}

    def read(path):
        data = subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=ef.ROOT)
        hashes[path] = hashlib.sha256(data).hexdigest()
        return data.decode()

    fx = pd.read_csv(io.StringIO(read('macro_history/fx_inputs.csv')), index_col=0, parse_dates=True).usdkrw.dropna()
    output = {'source_commit': commit, 'input_sha256': hashes, 'targets': {}}
    for target in ('samsung', 'sk_hynix'):
        snap = json.loads(read(f'docs/{target}/exports_snapshot.json'))
        exports = pd.Series({pd.Timestamp(r['month']): r['value'] for r in snap['series']})
        raw = pd.read_csv(io.StringIO(read(f'macro_history/operating_profit_{target}.csv')))
        profit = pd.Series(raw.value.to_numpy(), index=pd.PeriodIndex(raw.quarter, freq='Q'))
        ledger = pd.read_csv(io.StringIO(read(f'forecast_history/{target}/earnings_log.csv')))
        q = pd.Period('2026Q3')
        actual = ef.known_quarter_actual({'target': target, 'quarter_code': str(q),
            'last_actual_quarter': str(profit.index[-1]), 'last_actual': float(profit.iloc[-1])}, ledger)
        f = ef.build_frame(profit, exports, fx, 3)
        live = ef.fit_released_next(f, q, actual, 3)
        # 하이닉스처럼 당기 실적이 없으면 현재 전망을 새 방식으로 바꾸지 않는다.
        # 과거 발표 후 조건의 검증은 마지막 확정 분기까지만 계산한다.
        historic_q = q if actual is not None else profit.index[-1]
        historic_actual = actual if actual is not None else float(profit.iloc[-1])
        checked = live or ef.fit_released_next(f, historic_q, historic_actual, 3)
        old, _ = ef.fit_live(f, q, target='profit_next', features=ef.FEATURES_NEXT, gap=1)
        output['targets'][target] = {'live_release_available': actual is not None,
            'legacy_without_cli_raw_krw_tn': None if old is None else old/1e12,
            'live_result': live, 'historical_reference_quarter': str(historic_q),
            'historical_comparison': checked['release_comparison'] if checked else None}
    output['limitations'] = [
        '과거 확정·수정 자료 기준이며 당시 잠정치 빈티지 검증은 아님',
        '후보 설계는 사후 연구. 시간 분할 평가도 독립적인 미래 성적을 보장하지 않음',
        '원장 최초 예측을 대체하지 않으며 새 4분기 값은 보고서의 최신 전망임']
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'evaluation.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    for target, data in output['targets'].items():
        live = data['live_result']
        print(target, '4분기=', None if live is None or live['point'] is None else live['point']/1e12,
              '선택=', None if live is None else live['chosen'])
        for name, values in (data['historical_comparison'] or {}).items():
            ev = values['evaluation']
            print(name, ev['n'], ev['first'], ev['last'], 'MAE=', ev['mae_model']/1e12)


if __name__ == '__main__':
    main()
