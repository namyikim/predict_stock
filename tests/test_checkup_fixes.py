"""2026-11-10 점검에서 고친 것들: FRED 키 경로, 회고 재시도 실패 처리."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from data_sources import fred  # noqa: E402
import build_session_review as R  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FredKeyTests(unittest.TestCase):
    API = json.dumps({"observations": [{"date": "2026-11-05", "value": "2.81"}, {"date": "2026-11-06", "value": "."},
                                       {"date": "2026-11-09", "value": "2.84"}]}).encode()
    CSV = b"observation_date,BAMLH0A0HYM2\n2026-09-11,2.65\n2026-09-14,2.71\n"

    def test_key_uses_the_official_api_and_drops_missing_values(self):
        urls = []

        def fake(url, **kwargs):
            urls.append(url)
            return _Response(self.API)

        with mock.patch.dict(os.environ, {"FRED_API_KEY": "k"}), mock.patch.object(fred, "open_url", fake):
            series = fred.fetch_fred_daily("BAMLH0A0HYM2")
        self.assertEqual(len(urls), 1)
        self.assertIn("api.stlouisfed.org", urls[0])
        self.assertEqual([f"{d:%m-%d}" for d in series.index], ["11-05", "11-09"])
        self.assertAlmostEqual(float(series.iloc[-1]), 2.84)

    def test_no_key_keeps_the_csv_path(self):
        urls = []

        def fake(url, **kwargs):
            urls.append(url)
            return _Response(self.CSV)

        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(fred, "open_url", fake):
            series = fred.fetch_fred_daily("BAMLH0A0HYM2")
        self.assertTrue(all("fredgraph.csv" in url for url in urls))
        self.assertAlmostEqual(float(series.iloc[-1]), 2.71)

    def test_api_failure_falls_back_to_csv_without_leaking_the_key(self):
        def fake(url, **kwargs):
            if "api.stlouisfed.org" in url:
                raise OSError("boom " + url)
            return _Response(self.CSV)

        out = io.StringIO()
        with mock.patch.dict(os.environ, {"FRED_API_KEY": "secret-key"}), mock.patch.object(fred, "open_url", fake), \
                mock.patch.object(fred.time, "sleep", lambda *_: None), redirect_stdout(out):
            series = fred.fetch_fred_daily("BAMLH0A0HYM2")
        self.assertAlmostEqual(float(series.iloc[-1]), 2.71)
        self.assertNotIn("secret-key", out.getvalue())


class ReviewRetryTests(unittest.TestCase):
    def run_main(self, existing):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                if existing:
                    path = Path("forecast_history/samsung/reviews/2026-11-09.json")
                    path.parent.mkdir(parents=True)
                    path.write_text("{}", encoding="utf-8")
                argv = ["x", "--target", "samsung", "--date", "2026-11-09", "--out", "out", "--publish"]
                out = io.StringIO()
                with mock.patch.object(sys, "argv", argv), mock.patch.object(R.github_pages, "token", lambda: "t"), \
                        mock.patch.object(R, "build_review", side_effect=RuntimeError("가격 조회 실패")), \
                        redirect_stdout(out):
                    R.main()
                return out.getvalue()
            finally:
                os.chdir(cwd)

    def test_failed_retry_keeps_the_published_review(self):
        self.assertIn("::warning::회고 재시도 실패", self.run_main(existing=True))

    def test_first_attempt_still_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            self.run_main(existing=False)


if __name__ == "__main__":
    unittest.main()
