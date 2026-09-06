import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import macro_utils as mu


class MacroReleaseTests(unittest.TestCase):
    def test_release_time_survives_csv_and_blocks_morning_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'leading_cycle.csv'
            path.write_text('month,value,released_at\n2025-08,102,2025-09-01T08:00:00+09:00\n')
            frame = mu.read_macro_csv(path, 'leading_cycle')
            self.assertIn('released_at', frame)
            dates = pd.to_datetime(['2025-09-01', '2025-09-02'])
            got = mu.macro_features({'leading_cycle': frame}, dates)
            self.assertTrue(pd.isna(got.macro_leading_cycle.iloc[0]))
            self.assertEqual(got.macro_leading_cycle.iloc[1], 2.)
            at_open = mu.macro_features({'leading_cycle': frame}, dates, prediction_hour=9)
            self.assertEqual(at_open.macro_leading_cycle.iloc[0], 2.)

    def test_revisions_affect_only_predictions_after_the_revision(self):
        frame = pd.DataFrame({
            'month': ['2025-07', '2025-08', '2025-07'], 'value': [100., 102., 101.],
            'released_at': ['2025-08-01T00:00:00Z', '2025-09-01T00:00:00Z',
                            '2025-09-10T00:00:00Z']})
        dates = pd.to_datetime(['2025-09-02', '2025-09-11'])
        got = mu.macro_features({'leading_cycle': frame}, dates)
        self.assertEqual(got.macro_leading_cycle.tolist(), [2., 2.])
        self.assertEqual(got.macro_leading_change_1m.tolist(), [2., 1.])
        original = mu.macro_features({'leading_cycle': frame.iloc[:2]}, dates[:1])
        pd.testing.assert_frame_equal(got.iloc[:1], original)

    def test_invalid_or_ambiguous_release_times_are_rejected(self):
        for release in ['2025-09-01', '', 'not-a-date', '2025-08-10T10:00:00+09:00']:
            with self.subTest(release=release), self.assertRaises(ValueError):
                mu.normalize_monthly(pd.DataFrame({'month': ['2025-08'], 'value': [100],
                                                   'released_at': [release]}))

    def test_acceleration_distinguishes_positive_but_slowing_exports(self):
        months = pd.date_range('2023-01-01', periods=16, freq='MS')
        values = [100.] * 12 + [150., 140., 130., 120.]
        got = mu.macro_features({'semiconductor_exports': pd.DataFrame({'month': months, 'value': values})},
                                pd.to_datetime(['2024-06-01']))
        self.assertIn('macro_semiconductor_yoy_change_1m', got)
        self.assertAlmostEqual(got.macro_semiconductor_yoy.iloc[0], .2)
        self.assertAlmostEqual(got.macro_semiconductor_yoy_change_1m.iloc[0], -.1)
        self.assertAlmostEqual(got.macro_semiconductor_yoy_change_3m.iloc[0], -.3)

    def test_snapshot_and_cache_retain_release_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mu, 'kosis_key', return_value=None):
            inputs = Path(tmp) / 'macro_inputs'; inputs.mkdir()
            for series in mu.MACRO_SERIES:
                pd.DataFrame({'month': ['2025-08'], 'value': [102],
                              'released_at': ['2025-09-01T08:00:00+09:00']}).to_csv(inputs / f'{series}.csv', index=False)
            data, info = mu.load_macro_data(tmp, '2024-01-01', '2025-09-30')
            snapshot = pd.read_csv(Path(tmp) / 'macro_snapshots' / (info['snapshot_hash'] + '.csv'))
            self.assertIn('released_at', snapshot)
            self.assertEqual(info['availability_modes']['leading_cycle'], 'supplied_release_history')
            cached, _ = mu.load_macro_data(tmp, '2024-01-01', '2025-09-30', use_cache=True)
            pd.testing.assert_frame_equal(data['leading_cycle'], cached['leading_cycle'])

    def test_optional_indicators_have_separate_features(self):
        frame = pd.DataFrame({'month': pd.date_range('2023-01-01', periods=24, freq='MS'),
                              'value': np.arange(24) + 100.})
        got = mu.macro_features({'daily_exports': frame, 'oecd_g20_cli': frame},
                                pd.to_datetime(['2024-03-01']))
        self.assertIn('macro_daily_exports_yoy', got)
        self.assertAlmostEqual(got.macro_daily_exports_yoy.iloc[0], .12)
        self.assertEqual(got.macro_oecd_g20_cli_change_3m.iloc[0], 3.)

    def test_optional_csv_is_loaded_and_bad_optional_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mu, 'kosis_key', return_value=None):
            inputs = Path(tmp) / 'macro_inputs'; inputs.mkdir()
            for series in [*mu.MACRO_SERIES, 'daily_exports']:
                (inputs / f'{series}.csv').write_text('month,value\n2025-08,102\n')
            (inputs / 'oecd_g20_cli.csv').write_text('wrong,format\n1,2\n')
            data, info = mu.load_macro_data(tmp, '2024-01-01', '2025-09-30')
            self.assertIn('daily_exports', data)
            self.assertNotIn('oecd_g20_cli', data)
            self.assertEqual(info['optional_status']['oecd_g20_cli'], 'invalid:ValueError')
            self.assertEqual(info['availability_modes']['daily_exports'], 'lagged_latest_vintage')

    def test_short_optional_history_does_not_remove_valid_core_indicators(self):
        from test_pipeline_behavior import make_synthetic_raw, run_feature_cell
        months = pd.date_range('2019-01-01', '2025-05-01', freq='MS')
        full = pd.DataFrame({'month': months, 'value': 100 + np.sin(np.arange(len(months)))})
        data = {s: full for s in mu.MACRO_SERIES}
        data['daily_exports'] = full.iloc[-3:]
        ns = run_feature_cell(make_synthetic_raw()[0], USE_MACRO_FEATURES=True,
                              macro_data=data, macro_features=mu.macro_features,
                              OPTIONAL_MACRO_SERIES=mu.OPTIONAL_MACRO_SERIES,
                              macro_info={'enabled': True})
        self.assertTrue(ns['MACRO_ACTIVE'])
        self.assertIn('macro_semiconductor_yoy_change_1m', ns['feature_cols'])
        self.assertFalse(any(c.startswith('macro_daily_exports') for c in ns['feature_cols']))


if __name__ == '__main__':
    unittest.main()
