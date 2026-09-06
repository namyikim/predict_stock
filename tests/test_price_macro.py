import unittest
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import forecast_utils as fu


class PriceMacroTests(unittest.TestCase):
    def run_comparison(self, y=None):
        rng = np.random.default_rng(17)
        x = rng.normal(size=(600, 2))
        target = .03 * x[:, 1] + .002 * rng.normal(size=600) if y is None else y
        def ci(dates, score):
            value = score(np.arange(len(dates)))
            return value, value
        result, rows = fu.price_macro_ablation(
            x, target, np.full(600, .04), pd.bdate_range('2020-01-01', periods=600),
            ['market', 'macro_example'], 20, make_pipeline(StandardScaler(), Ridge(alpha=1)), ci,
            n_splits=3)
        return target, result, rows

    def test_macro_and_market_predictions_share_dates_and_error_is_paired(self):
        _, stats, rows = self.run_comparison()
        self.assertTrue(rows.index.is_unique)
        self.assertTrue(rows[['macro_raw', 'market_raw']].notna().all().all())
        self.assertLess(stats['raw_mae_delta'], 0)
        evaluation = rows.loc[rows['is_evaluation']]
        actual = ((evaluation.actual_return - evaluation.macro_raw).abs()
                  - (evaluation.actual_return - evaluation.market_raw).abs()).mean()
        self.assertAlmostEqual(stats['raw_mae_delta'], actual)
        self.assertEqual(stats['n_evaluation'], len(evaluation))
        self.assertTrue(stats['macro_signal'])

    def test_late_labels_do_not_change_earlier_oof_or_calibration(self):
        y, stats, rows = self.run_comparison()
        changed = y.copy(); changed[-100:] += .7
        _, other_stats, other = self.run_comparison(changed)
        pd.testing.assert_frame_equal(rows[['macro_raw', 'market_raw']], other[['macro_raw', 'market_raw']])
        self.assertEqual(stats['macro_slope'], other_stats['macro_slope'])
        self.assertEqual(stats['market_slope'], other_stats['market_slope'])


if __name__ == '__main__':
    unittest.main()
