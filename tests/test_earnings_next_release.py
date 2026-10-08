"""다음 분기 전망에서 발표 실적 반영과 미래 타깃 차단을 검증한다."""
import unittest
import numpy as np
import pandas as pd
from tests.test_earnings_forecast import ef, synthetic


class NextReleaseTests(unittest.TestCase):
    def frame(self):
        p, x, fx = synthetic(last_month='2026-09-01')
        return ef.build_frame(p, x, fx, 3)

    def test_release_required_and_partial_inputs_do_not_use_new_regime(self):
        self.assertTrue(hasattr(ef, 'fit_released_next'), '발표 후 다음 분기 추정이 필요합니다')
        for actual, months, full in [(None, 3, True), (float('nan'), 3, True),
                                      (100e12, 2, True), (100e12, 3, False)]:
            self.assertIsNone(ef.fit_released_next(self.frame(), pd.Period('2026Q3'), actual, months, full))

    def test_actual_changes_prediction_without_reading_next_answer(self):
        self.assertTrue(hasattr(ef, 'fit_released_next'))
        f = self.frame(); q = pd.Period('2026Q3')
        first = ef.fit_released_next(f, q, 20e12, 3)
        second = ef.fit_released_next(f, q, 40e12, 3)
        self.assertNotAlmostEqual(first['raw_point']/1e12, second['raw_point']/1e12)
        f.loc[q, 'profit_next'] = 9999e12
        again = ef.fit_released_next(f, q, 20e12, 3)
        self.assertEqual(first['raw_point'], again['raw_point'])
        self.assertEqual(first['evaluation'], again['evaluation'])
        self.assertEqual(first['anchor_actual'], 20e12)
        self.assertLess(pd.Period(first['evaluation']['last'])+1, q)

    def test_future_rows_cannot_change_prediction_or_selection(self):
        self.assertTrue(hasattr(ef, 'fit_released_next'))
        f = self.frame(); q = pd.Period('2025Q3')
        first = ef.fit_released_next(f, q, 15e12, 3)
        f.loc[f.index > q, :] = 999e12
        again = ef.fit_released_next(f, q, 15e12, 3)
        self.assertEqual(first['raw_point'], again['raw_point'])
        self.assertEqual(first['chosen'], again['chosen'])
        self.assertEqual(first['selection'], again['selection'])

    def test_updated_next_estimate_does_not_replace_first_ledger_entry(self):
        result = {'target': 'samsung', 'quarter_code': '2026Q3', 'months_used': 3,
                  'announced_actual': 107.4e12, 'next_quarter': {
                      'quarter_code': '2026Q4', 'point': 160e12}}
        ledger, _ = ef.append_estimate(ef.read_ledger('/missing.csv'), result, 'first')
        result['next_quarter']['point'] = 135e12
        updated, added = ef.append_estimate(ledger, result, 'second')
        self.assertFalse(added)
        self.assertEqual(updated.iloc[0]['point'], 160e12)

if __name__ == '__main__':
    unittest.main()
