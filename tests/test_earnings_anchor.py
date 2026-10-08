"""수출 기준 보정 모델의 미래 정보 차단·후보 선택·기존 모델 복귀 검증."""
import unittest

import numpy as np
import pandas as pd

from tests.test_earnings_forecast import ef, synthetic


class EarningsAnchorTests(unittest.TestCase):
    def test_anchor_uses_previous_profit_and_same_calendar_months(self):
        profit, exports, fx = synthetic()
        f = ef.build_frame(profit, exports, fx, 2)
        quarter = pd.Period('2026Q2')
        self.assertIn('profit_export_anchor', f)
        expected = profit.loc[quarter - 1] * f.loc[quarter, 'exports_krw_k'] / f.loc[quarter - 1, 'exports_krw_k']
        self.assertAlmostEqual(f.loc[quarter, 'profit_export_anchor'], expected, delta=.05)
        changed = profit.copy()
        changed.loc[quarter:] *= 100
        again = ef.build_frame(changed, exports, fx, 2)
        self.assertEqual(f.loc[quarter, 'profit_export_anchor'], again.loc[quarter, 'profit_export_anchor'])

    def test_offset_prediction_keeps_known_scale_and_does_not_use_current_answer(self):
        index = pd.period_range('2010Q1', periods=25, freq='Q')
        f = pd.DataFrame({'x': 1., 'anchor': np.arange(25)*1e12,
                          'profit': np.arange(25)*1e12 + 2e12}, index=index)
        f.loc[index[-1], 'profit'] = 999e12
        point, n = ef.fit_live(f, index[-1], features=['x'], offset='anchor')
        self.assertEqual(n, 24)
        self.assertAlmostEqual(point / 1e12, 26.)
        f.loc[index[-1], 'anchor'] = np.nan
        self.assertIsNone(ef.fit_live(f, index[-1], features=['x'], offset='anchor')[0])

    def test_choice_uses_only_errors_before_each_quarter(self):
        index = pd.period_range('2020Q1', periods=12, freq='Q')
        base = pd.DataFrame({'actual': 10., 'model': 12., 'random_walk': 8., 'seasonal_naive': 7.}, index=index)
        candidate = base.copy()
        candidate['model'] = 11.
        # 9번째 분기의 큰 오차는 그 분기 선택에는 아직 쓸 수 없다.
        candidate.loc[index[8], 'model'] = 100.
        chosen = ef.prequential_anchor_choice(base, candidate)
        self.assertEqual(chosen['model'].iloc[:8].tolist(), [12.]*8)
        self.assertEqual(chosen['model'].iloc[8], 100.)
        self.assertEqual(chosen['model'].iloc[9], 12.)
        modified = candidate.copy()
        modified.loc[index[10]:, 'model'] = -999.
        pd.testing.assert_frame_equal(chosen.iloc[:10], ef.prequential_anchor_choice(base, modified).iloc[:10])

    def test_missing_candidate_keeps_base_quarter(self):
        index = pd.period_range('2020Q1', periods=12, freq='Q')
        base = pd.DataFrame({'actual': 10., 'model': 12., 'random_walk': 8., 'seasonal_naive': 7.}, index=index)
        candidate = base.copy()
        candidate['model'] = 10.
        candidate = candidate.drop(index[9])
        chosen = ef.prequential_anchor_choice(base, candidate)
        self.assertEqual(list(chosen.index), list(base.index))
        self.assertEqual(chosen.loc[index[9], 'model'], 12.)

    def test_partial_month_or_one_two_month_inputs_keep_existing_model(self):
        profit, exports, fx = synthetic()
        for k, full in [(1, True), (2, True), (3, False)]:
            f = ef.build_frame(profit, exports, fx, k)
            quarter = f.dropna(subset=ef.FEATURES).index[-1]
            expected, _ = ef.fit_live(f, quarter)
            got = ef.fit_nowcast(f, quarter, k, full_months=full)
            self.assertEqual(got['chosen'], 'level_ridge')
            self.assertAlmostEqual(got['point'], expected)

    def test_walk_forward_offset_matches_live_fit_and_preserves_time_gap(self):
        profit, exports, fx = synthetic()
        f = ef.build_frame(profit, exports, fx, 2)
        oof = ef.walk_forward(f, offset='profit_export_anchor')
        quarter = oof.index[-1]
        point, _ = ef.fit_live(f, quarter, offset='profit_export_anchor')
        self.assertAlmostEqual(oof.loc[quarter, 'model'], point, delta=.05)
        f.loc[quarter, 'profit'] *= 100
        again = ef.walk_forward(f, offset='profit_export_anchor')
        self.assertAlmostEqual(again.loc[quarter, 'model'], point, delta=.05)

    def test_live_selection_ignores_current_actual_and_records_model(self):
        index = pd.period_range('2010Q1', periods=40, freq='Q')
        f = pd.DataFrame({key: np.arange(40)+1. for key in ef.FEATURES}, index=index)
        f['profit_export_anchor'] = (np.arange(40)+1.)*1e12
        f['profit'] = f['profit_export_anchor']+2e12
        got = ef.fit_nowcast(f, index[-1], 3)
        self.assertEqual(got['chosen'], 'export_anchor_residual')
        self.assertAlmostEqual(got['point']/1e12, 42.)
        f.loc[index[-1], 'profit'] = -999e12
        again = ef.fit_nowcast(f, index[-1], 3)
        self.assertEqual(got['selection'], again['selection'])
        self.assertEqual(got['point'], again['point'])
        result = {'target': 'samsung', 'quarter_code': '2026Q3', 'months_used': 3,
                  'point': got['point'], 'nowcast_model': got['chosen']}
        ledger, _ = ef.append_estimate(ef.read_ledger('/no/such/ledger.csv'), result, 'test')
        self.assertEqual(ledger.iloc[0]['nowcast_model'], 'export_anchor_residual')

    def test_report_labels_recalculation_after_actual_is_known(self):
        result = {'name': '삼성전자', 'quarter': '2026년 3분기', 'quarter_code': '2026Q3',
                  'months_used': 3, 'months_included': '7월, 8월, 9월',
                  'point': 110e12, 'low': 90e12, 'high': 130e12, 'change_vs_last': .2,
                  'last_actual': 89e12, 'last_actual_quarter': '2026Q2',
                  'evaluation': {'n': 0}, 'profit_source': 'DART_API', 'profit_n': 42,
                  'profit_first': '2016Q1', 'profit_last': '2026Q2', 'exports_last_month': '2026-09-01',
                  'generated_at': '2026-10-08', 'provisional': {'2026Q3': 107.4e12},
                  'nowcast_model': 'export_anchor_residual', 'nowcast_selection': {'eligible': True}}
        text = ef.render_fragment(result)
        self.assertIn('발표 후 재계산', text)
        self.assertIn('107.40조원', text)
        self.assertIn('수출 기준 보정', text)
        result['provisional'] = {}
        result.update(last_actual_quarter='2026Q3', last_actual=107.5e12)
        confirmed = ef.render_fragment(result)
        self.assertIn('발표 후 재계산', confirmed)
        self.assertIn('107.50조원', confirmed)

    def test_known_ledger_actual_survives_fetch_failure_and_blocks_new_current_record(self):
        result = {'target': 'samsung', 'quarter_code': '2026Q3', 'months_used': 1, 'point': 122e12}
        ledger, _ = ef.append_estimate(ef.read_ledger('/no/such/ledger.csv'), result, 'first')
        ledger, _ = ef.score_ledger(ledger, pd.Series(dtype=float), {'2026Q3': 107.4e12})
        result.update(months_used=3, point=110e12, provisional={},
                      next_quarter={'quarter_code': '2026Q4', 'point': 150e12})
        self.assertEqual(ef.known_quarter_actual(result, ledger), 107.4e12)
        updated, added = ef.append_estimate(ledger, result, 'after-release')
        self.assertTrue(added)  # 미발표 다음 분기만 기록한다.
        self.assertEqual(updated['record_id'].tolist(), ['samsung:2026Q3:k1', 'samsung:2026Q4:k0'])
        self.assertEqual(updated.iloc[0]['point'], 122e12)


if __name__ == '__main__':
    unittest.main()
