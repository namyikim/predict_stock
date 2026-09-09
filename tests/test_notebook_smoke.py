# -*- coding: utf-8 -*-
"""노트북을 처음부터 끝까지 실제로 실행해 본다.

다른 테스트는 셀을 조각으로 검사한다. 그래서 셀 순서가 어긋나거나 변수 이름이 바뀌는 오류를
못 잡는다(2026-09-08에 TARGET_TICKER가 정의되기 전에 쓰여 자동 실행이 죽었다). 여기서는
yfinance·외부 API를 모두 가짜로 바꾸고 합성 시세로 전 셀을 돌린다. 통과 기준은 '값이 맞는가'가
아니라 '끝까지 도는가, 산출물이 생기는가'다.

느리므로(1~2분) 기본 테스트에서 제외하고 싶다면 PREDICT_STOCK_SKIP_SMOKE=1 로 건너뛴다.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def synthetic_bars(ticker, rows=2400, last="2026-09-07"):
    """시세처럼 생긴 값. 값의 정확성이 아니라 파이프라인이 도는지를 본다."""
    import hashlib
    seed = int(hashlib.md5(ticker.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end=pd.Timestamp(last), periods=rows)
    returns = rng.normal(0, 0.018, rows)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = close * np.exp(rng.normal(0, 0.008, rows))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.004, rows)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.004, rows)))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Adj Close": close, "Volume": rng.integers(1e6, 2e7, rows).astype(float)},
                        index=index)


def write_offline_inputs(storage):
    """외부 API를 부르지 않도록 월별·일별 자료를 CSV로 미리 둔다."""
    inputs = Path(storage) / "macro_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    months = pd.date_range("2012-01-01", "2026-07-01", freq="MS")
    rng = np.random.default_rng(7)
    pd.DataFrame({"month": months.strftime("%Y-%m"),
                  "value": (100 + np.cumsum(rng.normal(0, .4, len(months)))).round(2)}
                 ).to_csv(inputs / "leading_cycle.csv", index=False)
    pd.DataFrame({"month": months.strftime("%Y-%m"),
                  "value": (4e10 * np.exp(np.cumsum(rng.normal(0, .03, len(months))))).round()}
                 ).to_csv(inputs / "semiconductor_exports.csv", index=False)
    days = pd.date_range("2014-07-01", "2026-09-06")
    pd.DataFrame({"date": days.strftime("%Y%m%d"),
                  "value": (100 + np.cumsum(rng.normal(0, .5, len(days)))).round(2)}
                 ).to_csv(inputs / "news_sentiment.csv", index=False)
    trading = pd.bdate_range("2015-01-01", "2026-09-07")
    pd.DataFrame({"date": trading.strftime("%Y-%m-%d"),
                  "foreign_net": rng.normal(0, 2e6, len(trading)).round(),
                  "inst_net": rng.normal(0, 1e6, len(trading)).round(),
                  "indiv_net": np.nan,
                  "volume": np.nan,
                  "foreign_ratio": (50 + np.cumsum(rng.normal(0, .01, len(trading)))).round(2)}
                 ).to_csv(inputs / "investor_flows_005930.csv", index=False)


@unittest.skipIf(os.environ.get("PREDICT_STOCK_SKIP_SMOKE"), "PREDICT_STOCK_SKIP_SMOKE")
class NotebookSmokeTests(unittest.TestCase):
    """전 셀을 돌려 산출물이 나오는지 본다."""

    @classmethod
    def setUpClass(cls):
        import matplotlib
        matplotlib.use("Agg")
        import yfinance

        cls.storage = Path(tempfile.mkdtemp())
        write_offline_inputs(cls.storage)
        cls.saved_download = yfinance.download
        yfinance.download = lambda ticker, **kwargs: synthetic_bars(str(ticker))

        os.environ.update(
            PREDICT_STOCK_STORAGE=str(cls.storage),
            PREDICT_STOCK_TARGETS="samsung",
            PREDICT_STOCK_PUBLISH="false",       # 저장소에 아무것도 올리지 않는다
            PREDICT_STOCK_SYNC_MACRO="false",    # 보관본도 건드리지 않는다
            MPLBACKEND="Agg",
        )
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("KOSIS_API_KEY", None)
        os.environ.pop("ECOS_API_KEY", None)

        notebook = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.namespace = {"__name__": "__main__", "display": lambda *a, **kw: None}
        import matplotlib.pyplot as plt
        plt.show = lambda *a, **kw: None
        cls.executed = 0
        for i, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            if source.lstrip().startswith("%%capture"):
                continue
            exec(compile(source, f"notebook-cell-{i}", "exec"), cls.namespace)
            cls.executed += 1
            if source.startswith("START_DATE ="):
                cls.namespace.update(QUICK_MODE=True, MAX_FOLDS=3, BOOTSTRAP_B=60,
                                     USE_DATA_CACHE=False)

    @classmethod
    def tearDownClass(cls):
        import yfinance
        yfinance.download = cls.saved_download

    def test_all_code_cells_ran(self):
        self.assertGreaterEqual(self.executed, 20)

    def test_walk_forward_and_live_prediction_exist(self):
        ns = self.namespace
        self.assertFalse(ns["predictions"].empty)
        self.assertIn("Mean ensemble", set(ns["predictions"]["model"]))
        self.assertIn("Mean ensemble", ns["live_table"].index)
        probabilities = ns["live_table"].loc["Mean ensemble", ["p_down", "p_flat", "p_up"]]
        self.assertAlmostEqual(float(probabilities.sum()), 1.0, places=6)

    def test_report_and_ledger_are_written(self):
        for name in ("report.html", "forecast_log.csv", "daily_forecast_comparison.csv"):
            path = self.storage / name
            self.assertTrue(path.exists(), f"{name} 이 생기지 않았습니다")
            self.assertGreater(path.stat().st_size, 200, name)

    def test_report_has_every_section_and_the_replaceable_marker(self):
        page = (self.storage / "report.html").read_text(encoding="utf-8")
        for token in ("예측 vs 실제", "외국인·기관 수급", "최근 공시", "시초가예측", "종가예측",
                      "장기 전망", "영업이익 추정", "자동 판정",
                      "<!--LEDGER_SECTION_START-->", "<!--LEDGER_SECTION_END-->", "코드 커밋"):
            # assertIn을 쓰면 실패할 때 25만 자짜리 보고서가 통째로 로그에 찍힌다.
            self.assertTrue(token in page, f"보고서에 '{token}' 이(가) 없습니다")

    def test_offline_run_publishes_nothing(self):
        # 발행을 껐으므로 저장소로 나가는 흔적이 없어야 한다.
        self.assertFalse(self.namespace["SYNC_LEDGER_TO_GITHUB"])


if __name__ == "__main__":
    unittest.main()
