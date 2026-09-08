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


class ApiRetryTests(unittest.TestCase):
    """같은 키로 잡이 동시에 조회하면 거절되는 일이 있다. 재시도하고, 실패 사유를 남긴다."""

    def _patch(self, fake):
        # 함수는 자기 모듈의 전역을 본다. _kosis_request 는 data_sources.kosis 에 있고 open_url 은
        # data_sources._common 에 있으므로 그 둘을 바꿔친다.
        import macro_utils as mu
        from data_sources import _common
        self._saved = (_common.urlopen, _common.time.sleep)
        _common.urlopen, _common.time.sleep = fake, lambda s: None
        self.addCleanup(lambda: setattr(_common, "urlopen", self._saved[0]))
        self.addCleanup(lambda: setattr(_common.time, "sleep", self._saved[1]))
        return mu

    def test_transient_failure_is_retried(self):
        import io
        import json as js
        calls = {"n": 0}

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def flaky(url, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("temporary")
            return Resp(js.dumps([{"PRD_DE": "202601", "DT": "1", "C1_NM": "반도체", "UNIT_NM": "달러"}]).encode())

        mu = self._patch(flaky)
        rows = mu._kosis_request("key", {})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(rows[0]["PRD_DE"], "202601")

    def test_final_failure_reports_the_reason_without_the_key(self):
        def always(url, timeout=None):
            exc = OSError("nope")
            exc.code = 429
            raise exc

        mu = self._patch(always)
        with self.assertRaises(RuntimeError) as ctx:
            mu._kosis_request("SECRETKEY", {})
        message = str(ctx.exception)
        self.assertIn("429", message)          # 원인을 알 수 있어야 한다
        self.assertNotIn("SECRETKEY", message)  # 키는 절대 새지 않는다


class MacroFallbackTests(unittest.TestCase):
    """KOSIS 조회가 막히면 저장소에 보관된 마지막 성공분으로 계속 돌아야 한다."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "fallback").mkdir()
        for series in ("leading_cycle", "semiconductor_exports"):
            pd.DataFrame({"month": ["2026-06", "2026-07"], "value": [101.2, 5.5e9]}).to_csv(
                self.dir / "fallback" / f"{series}.csv", index=False)

    def test_fallback_is_used_and_marked(self):
        import macro_utils as mu
        from data_sources import kosis
        saved_key, saved_fetch = kosis.kosis_key, kosis.fetch_kosis_monthly
        kosis.kosis_key = lambda: "key"

        def boom(*a, **k):
            raise RuntimeError("KOSIS API 조회 실패(URLError).")
        kosis.fetch_kosis_monthly = boom
        try:
            data, info = mu.load_macro_data(self.dir, "2020-01-01", "2026-09-01",
                                            fallback_dir=self.dir / "fallback")
        finally:
            kosis.kosis_key, kosis.fetch_kosis_monthly = saved_key, saved_fetch
        self.assertEqual(set(info["sources"].values()), {"last_successful_fetch"})
        self.assertFalse(info["fresh"])
        self.assertIn("URLError", str(info["fetch_errors"]))
        self.assertEqual(len(data["leading_cycle"]), 2)

    def test_without_fallback_the_error_still_propagates(self):
        import macro_utils as mu
        from data_sources import kosis
        saved_key, saved_fetch = kosis.kosis_key, kosis.fetch_kosis_monthly
        kosis.kosis_key = lambda: "key"

        def boom(*a, **k):
            raise RuntimeError("KOSIS API 조회 실패(URLError).")
        kosis.fetch_kosis_monthly = boom
        try:
            with self.assertRaises(RuntimeError):
                mu.load_macro_data(self.dir, "2020-01-01", "2026-09-01")
        finally:
            kosis.kosis_key, kosis.fetch_kosis_monthly = saved_key, saved_fetch
