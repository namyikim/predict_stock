"""장기 전망 모듈: 겹치는 타깃의 purge, 국면 분류, 유사 시기, 잡음에서의 판정."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_longterm_report as lt  # noqa: E402


def synthetic(seed=0, with_cycle=True, n_years=26):
    rng = np.random.default_rng(seed)
    months = pd.date_range("2000-01-31", periods=n_years * 12, freq="ME")
    n = len(months)
    cycle = np.sin(np.arange(n) / 9) if with_cycle else np.zeros(n)
    exports = pd.DataFrame({"month": months.to_period("M").to_timestamp(),
                            "value": (5e9 * np.exp(0.006 * np.arange(n) + 0.4 * cycle + rng.normal(0, .05, n))).round()})
    leading = pd.DataFrame({"month": months.to_period("M").to_timestamp(),
                            "value": 100 + 3 * np.roll(cycle, -2) + rng.normal(0, .3, n)})
    drift = 0.03 * np.roll(cycle, -4) if with_cycle else 0.
    price = pd.Series(1000 * np.exp(np.cumsum(0.005 + drift + rng.normal(0, .07, n))), index=months)
    return price, {"semiconductor_exports": exports, "leading_cycle": leading}


class LongTermTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lt.BOOTSTRAP_B = 100
        price, macro = synthetic()
        cls.frame = lt.build_frame(price, macro)
        cls.cols = [c for c, _ in lt.FEATURES if c in cls.frame.columns]

    def test_features_do_not_use_the_future(self):
        f = self.frame
        # 월+2 지연: t월 말에 보이는 수출 YoY는 늦어도 (t-2)월 값이다 → 마지막 두 달의 지표를 바꿔도 앞선 행은 그대로.
        price, macro = synthetic()
        macro["semiconductor_exports"].loc[macro["semiconductor_exports"].index[-2:], "value"] *= 3
        g = lt.build_frame(price, macro)
        cut = f.index[-3]
        pd.testing.assert_series_equal(f.loc[:cut, "macro_semiconductor_yoy"], g.loc[:cut, "macro_semiconductor_yoy"])

    def test_walk_forward_purges_overlapping_targets(self):
        # 시험 시점 t의 학습 행은 타깃이 t 이전에 실현된 행(t-h 이하)뿐이어야 한다.
        seen = {}
        original = lt.walk_forward

        from sklearn.linear_model import Ridge
        captured = []
        real_fit = Ridge.fit

        def spy(self_, X, y, *a, **k):
            captured.append(len(y))
            return real_fit(self_, X, y, *a, **k)
        Ridge.fit = spy
        try:
            oof = lt.walk_forward(self.frame, self.cols, 12)
        finally:
            Ridge.fit = real_fit
        first_t = oof.dropna().index[0]
        f = self.frame
        allowed = f.loc[f.index <= first_t - pd.DateOffset(months=12)]
        allowed = allowed[allowed[self.cols].notna().all(axis=1) & allowed["fwd_12m"].notna()]
        self.assertEqual(captured[0], len(allowed))

    def test_phase_classification(self):
        self.assertEqual(lt.phase_of(-.1, .02), lt.PHASES[0])
        self.assertEqual(lt.phase_of(.1, .02), lt.PHASES[1])
        self.assertEqual(lt.phase_of(.1, -.02), lt.PHASES[2])
        self.assertEqual(lt.phase_of(-.1, -.02), lt.PHASES[3])
        self.assertIsNone(lt.phase_of(np.nan, .1))

    def test_similar_episodes_are_realized_and_spaced(self):
        sims = lt.similar_episodes(self.frame, self.cols, 12, k=3)
        self.assertEqual(len(sims), 3)
        last = self.frame.index[-1]
        for s in sims:
            self.assertLessEqual(pd.Timestamp(s["date"]), last - pd.DateOffset(months=12))
            self.assertTrue(np.isfinite(s["fwd"]))
        dates = [pd.Timestamp(s["date"]) for s in sims]
        for a in dates:
            for b in dates:
                if a != b:
                    self.assertGreaterEqual(abs((a - b).days), 360)

    def test_no_signal_on_pure_noise(self):
        price, macro = synthetic(seed=1, with_cycle=False)
        f = lt.build_frame(price, macro)
        cols = [c for c, _ in lt.FEATURES if c in f.columns]
        ev, _ = lt.evaluate(f, cols, 12)
        self.assertFalse(ev["beats_zero"])

    def test_signal_on_embedded_cycle(self):
        ev, _ = lt.evaluate(self.frame, self.cols, 12)
        self.assertGreater(ev["corr_spearman"], 0.3)

    def test_nonpositive_prices_are_dropped_not_logged(self):
        price, macro = synthetic()
        bad = price.copy()
        bad.iloc[:6] = 0.0              # Yahoo 수정종가 오류 흉내
        f = lt.build_frame(bad, macro)
        self.assertEqual(f.index[0], price.index[6])
        self.assertTrue((f["price"] > 0).all())
        self.assertIn("<svg", lt.render_chart(f, "x"))

    def test_log_ticks_cover_any_price_level(self):
        self.assertIn(1000000, lt.log_ticks(15000, 1650000))     # SK하이닉스는 100만원대다
        self.assertIn(500, lt.log_ticks(300, 260000))
        self.assertEqual(lt.log_ticks(0, 10), [])                # 0 이하는 로그축에 못 그린다
        self.assertEqual(lt.log_ticks(float("nan"), 10), [])

    def test_chart_shows_no_data_instead_of_nan_labels(self):
        idx = pd.date_range("2000-01-31", periods=120, freq="ME")
        f = pd.DataFrame({"price": np.linspace(1000, 50000, len(idx))}, index=idx)   # 지표 없음
        svg = lt.render_chart(f, "x")
        self.assertIn("자료 없음", svg)
        self.assertNotIn("nan", svg)

    def test_late_starting_exports_series_does_not_crash(self):
        price, macro = synthetic()
        ex = macro["semiconductor_exports"]
        macro["semiconductor_exports"] = ex[ex["month"] >= "2009-01-01"].reset_index(drop=True)
        f = lt.build_frame(price, macro)
        cols = [c for c, _ in lt.FEATURES if c in f.columns]
        ev, _ = lt.evaluate(f, cols, 12)
        self.assertGreater(ev["n_oof"], 0)
        self.assertIn("<svg", lt.render_chart(f, "x"))

    def test_fragment_renders(self):
        from pathlib import Path
        lt.monthly_prices = lambda t, c, fetch=True: synthetic()[0]
        lt.load_macro_data = lambda *a, **k: (synthetic()[1], {"latest_month": {}, "snapshot_hash": "x"})
        res, _ = lt.analyse("samsung", Path("/tmp/lt_test"), fetch=False)
        html_ = lt.render_fragment(res)
        # 장기 전망은 결론이므로 예측 절(3번)로 앞당겼다. 저장소에 남은 옛 조각(7번)은
        # report_html.renumber_fragment 가 끼울 때 바꾼다.
        self.assertIn("3. 장기 전망", html_)
        self.assertIn("검증", html_)
        self.assertNotIn("HP 필터를 적용", html_)


if __name__ == "__main__":
    unittest.main()


class DetrendedChartTests(unittest.TestCase):
    """수준이 아니라 추세를 뺀 사이클끼리 겹쳐야 오르내림이 맞물려 보인다."""

    def test_chart_uses_price_returns_not_levels_for_the_overlay(self):
        price, macro = synthetic()
        f = lt.build_frame(price, macro)
        svg = lt.render_chart(f, "삼성전자")
        self.assertIn("12개월 수익률", svg)          # 위 칸은 수익률
        self.assertIn("실제 주가 수준", svg)          # 수준은 아래 참고 칸에만
        self.assertEqual(svg.count("<polyline"), 4)  # 수익률·수출·선행 + 수준

    def test_lead_lag_finds_a_known_shift(self):
        # 수출이 주가보다 6개월 뒤에 오도록 만들면, 주가가 6개월 선행으로 잡혀야 한다.
        idx = pd.date_range("2000-01-31", periods=300, freq="ME")
        cycle = np.sin(np.arange(len(idx)) / 9)
        f = pd.DataFrame({"mom_12m": cycle,
                          "macro_semiconductor_yoy": np.roll(cycle, 6)}, index=idx)
        got = lt.lead_lag(f)
        self.assertEqual(got["lead_months"], 6)
        self.assertGreater(got["corr"], 0.9)

    def test_lead_lag_is_none_without_the_columns(self):
        self.assertIsNone(lt.lead_lag(pd.DataFrame({"mom_12m": [1, 2, 3]})))


class PhaseDurationTests(unittest.TestCase):
    """지금 국면이 얼마나 더 갈까 — 조건부 잔여 기간."""

    def frame(self, phases):
        index = pd.date_range("2000-01-31", periods=len(phases), freq="ME")
        return pd.DataFrame({"phase": phases}, index=index)

    def test_short_flickers_are_not_episodes(self):
        # 확장 5개월 → 둔화 1개월(깜빡임) → 확장 5개월 은 확장 11개월 하나로 봐야 한다.
        phases = ["확장"] * 5 + ["둔화"] * 1 + ["확장"] * 5
        episodes = lt.phase_episodes(self.frame(phases))
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["months"], 11)

    def test_episode_boundaries_and_ongoing_flag(self):
        phases = ["확장"] * 6 + ["둔화"] * 4
        episodes = lt.phase_episodes(self.frame(phases))
        self.assertEqual([(e["phase"], e["months"], e["ongoing"]) for e in episodes],
                         [("확장", 6, False), ("둔화", 4, True)])
        self.assertEqual(episodes[0]["start"], "2000-01")
        self.assertEqual(episodes[0]["end"], "2000-06")

    def test_remaining_uses_only_episodes_that_lasted_longer(self):
        # 과거 확장: 12·10·8개월. 지금 9개월째면 9개월을 넘긴 12·10만 세어 잔여는 3·1 → 중앙값 2.
        phases = (["확장"] * 12 + ["둔화"] * 4 + ["확장"] * 10 + ["둔화"] * 4
                  + ["확장"] * 8 + ["둔화"] * 4 + ["확장"] * 9)
        got = lt.phase_duration_outlook(self.frame(phases), min_sample=2)
        self.assertEqual(got["months_so_far"], 9)
        self.assertEqual(got["n_past"], 3)
        self.assertEqual(got["n_conditional"], 2)
        self.assertEqual(got["remaining_median"], 2.0)

    def test_says_so_when_the_current_run_is_already_the_longest(self):
        phases = ["확장"] * 5 + ["둔화"] * 4 + ["확장"] * 20
        got = lt.phase_duration_outlook(self.frame(phases))
        self.assertIsNone(got.get("remaining_median"))
        self.assertIn("표본이 없습니다", got["reason"])

    def test_outlook_is_included_in_the_fragment(self):
        price, macro = synthetic()
        f = lt.build_frame(price, macro)
        outlook = lt.phase_duration_outlook(f)
        self.assertIsNotNone(outlook)
        chart = lt.render_duration_chart(outlook, outlook["months_so_far"])
        self.assertIn("<svg", chart)
        self.assertIn("진행 중", chart)


class CycleScoreTests(unittest.TestCase):
    """합성 사이클 점수: 확장 창 표준화, 등가중, 구성 요소 부족 시 미생성."""

    def inputs(self):
        price, macro = synthetic()
        days = pd.date_range("1998-01-01", "2026-09-06")
        nsi = pd.DataFrame({"date": days, "value": 100 + 4 * np.sin(np.arange(len(days)) / 270)})
        spread = pd.DataFrame({"date": days, "value": 0.6 + 0.8 * np.sin(np.arange(len(days)) / 300)})
        return price, macro, nsi, spread

    def test_expanding_z_uses_only_the_past(self):
        s = pd.Series(np.arange(100, dtype=float))
        z = lt.expanding_z(s, min_periods=10)
        self.assertTrue(z.iloc[:9].isna().all())
        # 뒤쪽 값을 바꿔도 앞쪽 z는 그대로
        s2 = s.copy(); s2.iloc[50:] += 1000
        pd.testing.assert_series_equal(z.iloc[:50], lt.expanding_z(s2, min_periods=10).iloc[:50])

    def test_score_is_the_equal_weight_mean_of_components(self):
        price, macro, nsi, spread = self.inputs()
        f = lt.build_frame(price, macro, None, nsi, spread)
        have = [c for c in lt.CYCLE_COMPONENTS if c in f.columns]
        self.assertEqual(len(have), 4)
        z = pd.concat([lt.expanding_z(f[c]) for c in have], axis=1).mean(axis=1, skipna=False)
        pd.testing.assert_series_equal(f["cycle_score"], z, check_names=False)

    def test_score_absent_with_too_few_components(self):
        price, macro, _, _ = self.inputs()
        f = lt.build_frame(price, macro)      # 수출·선행지수만 → 2개
        self.assertNotIn("cycle_score", f.columns)

    def test_term_spread_has_no_publication_lag(self):
        price, macro, nsi, spread = self.inputs()
        f = lt.build_frame(price, macro, None, nsi, spread)
        last = f.index[-1]                                   # 합성 가격의 마지막 월말
        month = spread.set_index("date")["value"].loc[last.strftime("%Y-%m")].mean()
        self.assertAlmostEqual(f.loc[last, "term_spread"], month, places=9)

    def test_spread_conditional_table(self):
        price, macro, nsi, spread = self.inputs()
        f = lt.build_frame(price, macro, None, nsi, spread)
        got = lt.phase_duration_by_spread(f)
        self.assertIsNotNone(got)
        self.assertEqual(got["n_inverted"] + got["n_normal"], len(got["rows"]))
        for row in got["rows"]:
            self.assertEqual(row["inverted"], row["spread_at_k"] <= 0)


class FallbackFetchTests(unittest.TestCase):
    """외부 API가 막힌 날을 대비해, 쓰는 자료는 모두 저장소 보관본을 받아 둔다."""

    def test_every_optional_source_has_a_cache_pull(self):
        source = (ROOT / "tools" / "build_longterm_report.py").read_text(encoding="utf-8")
        for name in ("leading_cycle.csv", "semiconductor_exports.csv", "cli_g20.csv",
                     "cli_kor.csv", "kospi_monthly.csv",
                     "news_sentiment.csv", "term_spread.csv"):
            self.assertIn(f'"{name}"', source, name)
        # 파일마다 따로 받아야 하나가 실패해도 나머지가 들어온다.
        self.assertIn("except Exception:\n            continue", source)


class RejectedSectionTests(unittest.TestCase):
    """낫지 않다고 판정한 것은 표 대신 한 줄로 줄인다.

    지우지는 않는다 — 지우면 '해 보지도 않았다'와 구별되지 않고 같은 것을 다시 제안하게 된다.
    """

    def result(self, cli_better, cycle_better):
        def block(better):
            return {str(h): {"mae_with": 0.10 - (0.01 if better else -0.01), "mae_without": 0.10}
                    for h in lt.HORIZONS.values()}
        return {"cli_active": True, "cycle_active": True,
                "cli_ablation": block(cli_better), "cycle_ablation": block(cycle_better),
                "cycle_components": ["exports_daily_yoy"]}

    def test_a_losing_ablation_is_one_line_not_a_table(self):
        html = "".join(lt.render_rejected(self.result(False, False)))
        self.assertIn("재 보고 쓰지 않기로 한 것", html)
        self.assertIn("낫지 않았습니다", html)
        self.assertNotIn("<table", html, "판정이 '낫지 않다'인데 표를 그리면 안 된다")

    def test_the_numbers_are_still_reachable(self):
        html = "".join(lt.render_rejected(self.result(False, False)))
        self.assertIn("cli_ablation", html)
        self.assertIn("cycle_ablation", html)

    def test_the_verdict_counts_how_many_horizons_improved(self):
        verdict = lt.ablation_verdict(self.result(True, True)["cli_ablation"], lt.HORIZONS)
        self.assertEqual(verdict["better"], verdict["n"])
        self.assertIn("나았습니다", verdict["summary"])
        verdict = lt.ablation_verdict(self.result(False, False)["cli_ablation"], lt.HORIZONS)
        self.assertEqual(verdict["better"], 0)

    def test_nothing_is_drawn_when_there_is_nothing_to_say(self):
        self.assertEqual(lt.render_rejected({"cli_active": False, "cycle_active": False}), [])

    def test_a_missing_source_is_named_rather_than_hidden(self):
        html = "".join(lt.render_rejected(
            {"cli_active": False, "cycle_active": False,
             "cli_info": {"reason": "OECD 조회 실패"}}))
        self.assertIn("OECD 조회 실패", html)


class CliOutlookSectionTests(unittest.TestCase):
    """선행지수 전망 절(R09). 보고서를 멈추지 않는 것과, 숫자를 한계와 함께 내는 것이 요점이다."""

    def outlook(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        fallback = tmp / "macro_fallback"
        fallback.mkdir(parents=True)
        for name in ("cli_kor.csv", "kospi_monthly.csv"):
            shutil.copy(ROOT / "macro_history" / name, fallback / name)
        return lt.korea_cli_outlook(tmp, fallback, fetch=False)

    def test_a_missing_archive_returns_nothing_instead_of_raising(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.assertIsNone(lt.korea_cli_outlook(tmp, tmp, fetch=False))
        self.assertEqual(lt.render_cli_outlook(None), [])

    def test_the_outlook_covers_six_months_from_the_last_confirmed_month(self):
        outlook = self.outlook()
        self.assertEqual(len(outlook["rows"]), 6)
        self.assertEqual([r["horizon"] for r in outlook["rows"]], [1, 2, 3, 4, 5, 6])

    def test_the_section_shows_the_chart_and_every_forecast_month(self):
        outlook = self.outlook()
        html = "".join(lt.render_cli_outlook(outlook))
        self.assertIn("<svg", html)
        for row in outlook["rows"]:
            self.assertIn(str(row["month"]), html)

    def test_the_section_states_the_three_limits(self):
        """구간의 뜻, 개정, '지수의 전망이지 주가의 전망이 아니다' — 셋 다 적어야 한다."""
        html = "".join(lt.render_cli_outlook(self.outlook()))
        self.assertIn("빗나간 폭", html)
        self.assertIn("나중에 값이 바뀝니다", html)
        self.assertIn("주가의 전망이 아닙니다", html)

    def test_a_failure_does_not_stop_the_report(self):
        source = (ROOT / "tools" / "build_longterm_report.py").read_text(encoding="utf-8")
        call = "korea_cli_outlook(out_dir, fallback_dir, fetch=fetch)"
        self.assertIn(call, source, "analyse 가 전망을 부르지 않는다")
        block = source[source.index(call):]
        self.assertIn("except Exception", block[:600])
        self.assertIn("cli_outlook = None", block[:600])


class LegendLayoutTests(unittest.TestCase):
    """한글은 라틴 문자보다 두 배 가까이 넓다. 글자 수에 고정 폭을 곱하면 범례가 겹친다."""

    def test_text_width_counts_hangul_as_wide(self):
        self.assertGreater(lt.text_width("반도체"), lt.text_width("abc") * 1.5)
        self.assertAlmostEqual(lt.text_width("abcd", size=10), 4 * 5.5)

    def test_legend_items_do_not_overlap(self):
        import re
        price, macro = synthetic()
        svg = lt.render_chart(lt.build_frame(price, macro), "삼성전자")
        items = re.findall(
            r'<line x1="(\d+)" x2="\d+" y1="30" y2="30".*?<text x="(\d+)" y="34"[^>]*>([^<]+)</text>', svg)
        self.assertEqual(len(items), 3)
        end = 0
        for x1, tx, label in items:
            self.assertGreaterEqual(int(x1), end, f"'{label}' 범례가 앞 항목과 겹칩니다")
            end = int(tx) + lt.text_width(label)


class OptionalSourcesActuallyLoadTests(unittest.TestCase):
    """선택 자료원이 '조용히 빠지는' 것을 잡는다.

    2026-09-08까지 장기 전망은 load_nsi()에 fallback_dir 를 넘겼는데 그 함수에는 인자가 없었다.
    호출부가 예외를 잡고 계속 진행했으므로 테스트도 Actions 도 초록불이었고, NSI 는 매번 통째로
    빠져 있었다. '실행이 됐는가'가 아니라 '실제로 켜졌는가'를 봐야 한다.
    """

    def test_every_optional_loader_accepts_the_shared_arguments(self):
        import inspect
        import macro_utils as mu
        for name in ("load_macro_data", "load_nsi", "load_cli", "load_term_spread",
                     "load_investor_flows"):
            parameters = inspect.signature(getattr(mu, name)).parameters
            self.assertIn("use_cache", parameters, name)
            self.assertIn("fallback_dir", parameters, f"{name} 이 보관본을 받지 못합니다")

    def test_analyse_reports_each_source_as_enabled(self):
        import macro_utils as mu
        from pathlib import Path as _Path
        price, macro = synthetic()
        months = pd.date_range("1998-01-01", "2026-07-01", freq="MS")
        days = pd.date_range("1998-01-01", "2026-09-06")
        rng = np.random.default_rng(3)
        cli = pd.DataFrame({"month": months, "value": 100 + np.sin(np.arange(len(months)) / 9)})
        nsi = pd.DataFrame({"date": days, "value": 100 + np.cumsum(rng.normal(0, .3, len(days)))})
        spread = pd.DataFrame({"date": days, "value": .5 + np.sin(np.arange(len(days)) / 300)})
        saved = (lt.monthly_prices, lt.load_macro_data, lt.load_cli, lt.load_nsi,
                 lt.load_term_spread, lt.github_pages.token, lt.BOOTSTRAP_B)
        lt.BOOTSTRAP_B = 50
        lt.monthly_prices = lambda *a, **k: price
        lt.load_macro_data = lambda *a, **k: (macro, {"latest_month": {}, "snapshot_hash": "x"})
        # 실제 호출과 같은 방식으로 부른다 — 인자가 맞지 않으면 여기서 드러난다.
        lt.load_cli = lambda storage, start, end, use_cache=False, fallback_dir=None: (
            cli, {"source": "test", "first": "1998-01", "last": "2026-07"})
        lt.load_nsi = lambda storage, start, end, use_cache=False, fallback_dir=None: (
            nsi, {"source": "test", "last": "2026-09-06"})
        lt.load_term_spread = lambda storage, start, end, use_cache=False, fallback_dir=None: (
            spread, {"source": "test", "last": "2026-09-06"})
        lt.github_pages.token = lambda: None
        try:
            result, _ = lt.analyse("samsung", _Path(tempfile.mkdtemp()), fetch=False)
        finally:
            (lt.monthly_prices, lt.load_macro_data, lt.load_cli, lt.load_nsi,
             lt.load_term_spread, lt.github_pages.token, lt.BOOTSTRAP_B) = saved
        self.assertTrue(result["cli_active"], "G20 CLI 가 켜지지 않았습니다")
        for name in ("nsi", "term_spread"):
            self.assertTrue(result["extra_info"][name].get("enabled"),
                            f"{name} 가 켜지지 않았습니다: {result['extra_info'][name].get('reason')}")
        self.assertTrue(result["cycle_active"], "합성 사이클 점수가 만들어지지 않았습니다")
        self.assertEqual(len(result["cycle_components"]), 4)


class SampleSizeGateTests(unittest.TestCase):
    """표본이 적으면 우위를 선언하지 않는다.

    하한이 없을 때 SK하이닉스 12개월이 독립 표본 6개로 'MAE 72.3% vs 73.0%'를 우위로 선언했다.
    최종 평가 구간을 따로 떼기에는 표본이 애초에 부족하므로, 떼는 대신 판정을 보류한다.
    """

    def frame(self, quarters):
        index = pd.date_range("2000-01-31", periods=quarters, freq="ME")
        rng = np.random.default_rng(0)
        f = pd.DataFrame(index=index)
        for column in ("mom_12m", "drawdown_36m"):
            f[column] = rng.normal(0, 1, len(index))
        f["fwd_12m"] = rng.normal(0, .1, len(index))
        return f

    def test_short_history_is_undecided_not_a_win(self):
        f = self.frame(140)                       # 12개월 지평 독립 표본 10개 남짓
        ev, _ = lt.evaluate(f, ["mom_12m", "drawdown_36m"], 12)
        if "mae_model" in ev:
            self.assertLess(ev["n_evaluation"] // 12, lt.MIN_INDEPENDENT)
            self.assertFalse(ev["beats_zero"], "표본이 부족한데 우위로 판정했습니다")
            self.assertFalse(ev["enough_samples"])
            self.assertIn("독립 표본", ev["note"])

    def test_gate_is_reported_in_the_fragment(self):
        source = (ROOT / "tools" / "build_longterm_report.py").read_text(encoding="utf-8")
        self.assertIn("판정 불가(표본 부족)", source)
        self.assertIn("MIN_INDEPENDENT = 20", source)

    def test_gate_counts_only_the_final_evaluation_half(self):
        index = pd.date_range("2000-01-31", periods=240, freq="ME")
        frame = pd.DataFrame({"fwd_12m": np.full(len(index), .10)}, index=index)
        predictions = pd.Series(np.full(len(index), .10), index=index)
        with patch.object(lt, "walk_forward", return_value=predictions):
            ev, _ = lt.evaluate(frame, [], 12)
        self.assertEqual(ev["n_oof"], 240)
        self.assertEqual(ev["n_evaluation"], 120)
        self.assertEqual(ev["n_independent"], 10)
        self.assertFalse(ev["enough_samples"])
        self.assertFalse(ev["beats_zero"])
