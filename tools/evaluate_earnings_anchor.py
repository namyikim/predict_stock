"""고정된 저장소 자료로 수출 기준 보정 정책을 재현한다. 사전 예측 원장은 쓰지 않는다.

python tools/evaluate_earnings_anchor.py --source-ref 67ec5688592e9657d193187aab0cee901d97eda2 --out experiments/earnings_anchor_20261008
"""
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
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', args.source_ref + '^{commit}'],
                                     cwd=ef.ROOT, text=True).strip()
    hashes = {}

    def read(path):
        data = subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=ef.ROOT)
        hashes[path] = hashlib.sha256(data).hexdigest()
        return data.decode('utf-8')

    fx = pd.read_csv(io.StringIO(read('macro_history/fx_inputs.csv')), index_col=0, parse_dates=True).usdkrw.dropna()
    summary, rows, screening = [], [], []
    for target in ('samsung', 'sk_hynix'):
        snapshot = json.loads(read(f'docs/{target}/exports_snapshot.json'))
        exports = pd.Series({pd.Timestamp(r['month']): r['value'] for r in snapshot['series']})
        raw = pd.read_csv(io.StringIO(read(f'macro_history/operating_profit_{target}.csv')))
        profit = pd.Series(raw.value.to_numpy(), index=pd.PeriodIndex(raw.quarter, freq='Q'))
        # 이번 실패 사례를 학습·선택·역사 검증에 섞지 않는다.
        cutoff = pd.Period('2026Q3', freq='Q')
        profit = profit.loc[profit.index < cutoff]
        for months in (1, 2, 3):
            frame = ef.build_frame(profit, exports, fx, months)
            result = ef.fit_nowcast(frame, cutoff, months)
            base, policy = result['base_oof'], result['oof']
            # 비교한 후보를 모두 남겨 선택한 방식만 잘 보이게 보고하지 않는다.
            for name, offset, extra in [('level', None, []), ('delta', 'profit_lag1', []),
                                        ('anchor', 'profit_export_anchor', []), ('leverage', None, ef.LEVERAGE_FEATURES)]:
                candidate = ef.walk_forward(frame.loc[frame.index < cutoff],
                                            features=ef.FEATURES+extra, offset=offset)
                metrics = ef.evaluate(candidate)
                screening.append({'target': target, 'months_used': months, 'method': name,
                                  'n': metrics['n'], 'mae_krw_tn': metrics['mae_model']/1e12})
            assert list(base.index) == list(policy.index)
            for quarter in base.index:
                actual = float(base.loc[quarter, 'actual']) / 1e12
                rows.append({'target': target, 'months_used': months, 'quarter': str(quarter),
                             'actual_krw_tn': actual, 'base_krw_tn': float(base.loc[quarter, 'model']) / 1e12,
                             'policy_krw_tn': float(policy.loc[quarter, 'model']) / 1e12})
            old, new = ef.evaluate(base), ef.evaluate(policy)
            old_late, new_late = ef.evaluate(base.iloc[len(base)//2:]), ef.evaluate(policy.iloc[len(base)//2:])
            summary.append({'target': target, 'months_used': months, 'n': old['n'],
                            'first': old['first'], 'last': old['last'],
                            'base_mae_krw_tn': old['mae_model']/1e12, 'policy_mae_krw_tn': new['mae_model']/1e12,
                            'improvement_pct': (1-new['mae_model']/old['mae_model'])*100,
                            'late_n': old_late['n'], 'late_base_mae_krw_tn': old_late['mae_model']/1e12,
                            'late_policy_mae_krw_tn': new_late['mae_model']/1e12,
                            'live_chosen': result['chosen'], 'q3_recalculated_krw_tn': result['point']/1e12})
    output = {'source_commit': commit, 'input_sha256': hashes, 'evaluation_cutoff_exclusive': '2026Q3',
              'policy': '3개월 월 전체 자료에서 이전 쌍체 오차 8개 이상, 후보 MAE가 작을 때만 선택',
              'limitations': ['현재 저장된 수정 이력 자료를 이용한 시간순 재검증이며 당시 원본 자료 빈티지가 아님',
                              '후보 방식과 3개월 적용 범위는 이번 사후 연구에서 정했으므로 독립된 최종 검증이 아님',
                              '2026Q3 재계산은 진단용이며 원장의 사전 성적에 합산하지 않음',
                              '원/달러는 발행 때 쓰던 yfinance 월평균을 저장한 fx_inputs 보관본 사용'],
              'summary': summary, 'candidate_screening': screening}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'evaluation.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    pd.DataFrame(rows).to_csv(args.out/'paired_predictions.csv', index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
