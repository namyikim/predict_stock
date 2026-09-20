# -*- coding: utf-8 -*-
"""R01 — 예측 구간 재보정 (guides/research-candidates-plan.md R01, experiments/medium_horizon/R01/decision.md).

계약:
  1. 어떤 후보도 아직 정답이 나오지 않은 라벨을 쓰지 않는다 — 날 t 이후에 확정되는 라벨을 바꿔도 t 까지의 q 는 그대로다.
  2. conformal 순위는 ⌈(n+1)(1−α)⌉ 이다(유한표본 보정). 순위가 n 을 넘으면 창의 최댓값.
  3. ACI 갱신은 α_{t+1} = α_t + γ(α − err_t) 이고, 지연 피드백에서는 예측마다 정답이 나온 뒤 정확히 한 번 갱신한다.
  4. Winkler(구간) 점수는 손으로 계산한 값과 같다.
  5. 러너는 합성 입력에서 계약 파일을 남기고, 현행 후보의 평가 구간 포함률은 운영 함수의 값과 같다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import run_medium_horizon as mh  # noqa: E402
from test_medium_horizon import synthetic_namespace  # noqa: E402


def _series(n=700, horizon=5, seed=5):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n + horizon)
    y = rng.normal(0, .03, n) * (1 + (np.arange(n) > n // 2))        # 뒤 절반은 변동성이 두 배
    center = rng.normal(0, .005, n)
    sigma = np.full(n, .03)
    return y, center, sigma, dates[:n], dates[horizon - 1:horizon - 1 + n]


class MaturityTests(unittest.TestCase):
    def test_counts_only_labels_realized_before_the_prediction_morning(self):
        _, _, _, pred, targ = _series(n=50, horizon=5)
        avail = mh.matured_counts(pred, targ)
        self.assertEqual(list(avail[:6]), [0, 0, 0, 0, 0, 1])          # 5일 라벨은 닷새 뒤 아침에야 하나 확정된다
        for t, a in enumerate(avail):
            self.assertTrue(all(targ[:a] < pred[t]))
            if a < len(targ):
                self.assertGreaterEqual(targ[a], pred[t])

    def test_one_day_labels_are_available_the_next_morning(self):
        _, _, _, pred, _ = _series(n=30, horizon=1)
        self.assertEqual(list(mh.matured_counts(pred, pred)), list(range(30)))

    def test_unsorted_target_dates_are_rejected(self):
        _, _, _, pred, targ = _series(n=30, horizon=1)
        with self.assertRaises(ValueError):
            mh.matured_counts(pred, targ[::-1])


class NoLookaheadTests(unittest.TestCase):
    def test_mutating_a_label_realized_after_t_does_not_change_any_q_up_to_t(self):
        for horizon in (1, 5, 20):
            y, center, sigma, pred, targ = _series(horizon=horizon)
            before = mh.interval_q_paths(y, center, sigma, pred, targ, current_q=1.3)
            t = 450
            changed = y.copy()
            late = np.asarray(targ >= pred[t])                       # t 아침에 아직 정답이 없는 라벨 전부
            self.assertTrue(late[t - horizon + 1:].all())
            changed[late] += 5.
            after = mh.interval_q_paths(changed, center, sigma, pred, targ, current_q=1.3)
            self.assertEqual(set(before), {"current", *sum(mh.R01_FAMILIES.values(), [])})
            for name in before:
                np.testing.assert_array_equal(before[name][:t + 1], after[name][:t + 1], err_msg=f"{name} h={horizon}")
            # 검사가 공허하지 않도록: 뒤쪽 q 는 실제로 달라진다
            self.assertFalse(np.array_equal(before["trailing_q_w250"][t + horizon + 5:], after["trailing_q_w250"][t + horizon + 5:]))
            self.assertFalse(np.array_equal(before["aci_g0.02_w250"][t + horizon + 5:], after["aci_g0.02_w250"][t + horizon + 5:]))

    def test_no_q_before_the_minimum_number_of_matured_scores(self):
        y, center, sigma, pred, targ = _series(horizon=5)
        paths = mh.interval_q_paths(y, center, sigma, pred, targ, current_q=1.3)
        first = mh.R01_MIN_SCORES + 5 - 1                                # 확정 점수 100개가 모이는 첫 행
        for name in sum(mh.R01_FAMILIES.values(), []):
            self.assertTrue(np.isnan(paths[name][:first]).all(), name)
            self.assertTrue(np.isfinite(paths[name][first:]).all(), name)
        self.assertTrue((paths["current"] == 1.3).all())

    def test_trailing_window_uses_exactly_the_last_w_matured_scores(self):
        y, center, sigma, pred, targ = _series(horizon=5)
        scores = mh.scaled_scores(y, center, sigma)
        avail = mh.matured_counts(pred, targ)
        q = mh.trailing_q_path(scores, avail, 250)
        t = 600
        self.assertAlmostEqual(q[t], float(np.quantile(scores[avail[t] - 250:avail[t]], .8)), places=14)
        self.assertEqual(avail[t], t - 4)


class ConformalRankTests(unittest.TestCase):
    def test_rank_is_ceil_n_plus_one_times_coverage(self):
        self.assertEqual(mh.conformal_rank(250, .8), 201)               # ⌈251 × 0.8⌉ = ⌈200.8⌉
        self.assertEqual(mh.conformal_rank(500, .8), 401)
        self.assertEqual(mh.conformal_rank(9, .8), 8)                   # ⌈10 × 0.8⌉ = 8 (정수일 때 올리지 않는다)
        self.assertEqual(mh.conformal_rank(4, .8), 4)
        self.assertEqual(mh.conformal_rank(3, .8), 4)                   # n 을 넘는다 → 무한 구간에 해당

    def test_quantile_picks_that_order_statistic_and_is_not_below_the_plain_quantile(self):
        scores = np.random.default_rng(0).permutation(np.arange(1., 251.))
        self.assertEqual(mh.conformal_quantile(scores, .8), 201.)
        self.assertGreaterEqual(mh.conformal_quantile(scores, .8), float(np.quantile(scores, .8)))
        self.assertEqual(mh.conformal_quantile([3., 1., 2.], .8), 3.)   # 순위가 n 을 넘으면 최댓값
        self.assertTrue(np.isnan(mh.conformal_quantile([], .8)))


class AciTests(unittest.TestCase):
    def test_update_rule(self):
        self.assertAlmostEqual(mh.aci_update(.2, True, .02), .2 + .02 * (.2 - 1), places=15)    # 빗나감 → α 감소(넓어짐)
        self.assertAlmostEqual(mh.aci_update(.2, False, .02), .2 + .02 * .2, places=15)         # 포함 → α 증가(좁아짐)

    def test_path_replays_the_update_by_hand_with_delayed_feedback(self):
        horizon, gamma, window = 5, .02, 250
        y, center, sigma, pred, targ = _series(horizon=horizon)
        scores = mh.scaled_scores(y, center, sigma)
        avail = mh.matured_counts(pred, targ)
        q, alphas = mh.aci_q_path(scores, avail, gamma, window, return_alpha=True)
        alpha_t, done = .2, 0
        for t in range(len(scores)):
            for s in range(done, avail[t]):                              # 어젯밤까지 확정된 예측만, 한 번씩
                if np.isfinite(q[s]):
                    alpha_t = alpha_t + gamma * (.2 - float(scores[s] > q[s]))
            done = avail[t]
            self.assertAlmostEqual(alphas[t], alpha_t, places=12)
            if avail[t] >= mh.R01_MIN_SCORES:
                self.assertAlmostEqual(q[t], mh.level_quantile(scores[max(0, avail[t] - window):avail[t]], 1 - alpha_t), places=12)

    def test_aci_widens_after_the_volatility_doubles_and_restores_coverage(self):
        y, center, sigma, pred, targ = _series(n=1400, horizon=1)
        paths = mh.interval_q_paths(y, center, sigma, pred, targ, current_q=float(np.quantile(mh.scaled_scores(y, center, sigma)[:350], .8)))
        late = np.arange(1000, 1400)
        hit = lambda q: float((np.abs(y - center)[late] <= (q * sigma)[late]).mean())       # noqa: E731
        self.assertLess(hit(paths["current"]), .6)                      # 조용한 구간의 q 는 격변 구간에서 무너진다
        self.assertGreater(hit(paths["aci_g0.02_w250"]), .74)

    def test_level_quantile_stays_finite_outside_the_unit_interval(self):
        self.assertEqual(mh.level_quantile([1., 2., 3.], 1.2), 3.)
        self.assertEqual(mh.level_quantile([1., 2., 3.], -.1), 0.)


class WinklerTests(unittest.TestCase):
    def test_hand_computed_example(self):
        # 80% 구간 [−1, 1] (폭 2), 벌점 계수 2/α = 10
        y = np.array([0., 1.5, -1.2, 1.])
        score = mh.interval_score(y, np.full(4, -1.), np.full(4, 1.), .8)
        np.testing.assert_allclose(score, [2., 2 + 10 * .5, 2 + 10 * .2, 2.], atol=1e-12)
        hit, s2, width = mh.coverage_and_score(y, np.zeros(4), np.ones(4), .8)
        np.testing.assert_allclose(s2, score, atol=1e-12)
        self.assertEqual(list(hit), [True, False, False, True])
        np.testing.assert_allclose(width, 2.)

    def test_verdict_rule(self):
        good, tie, bad = (-.01, -.02, -.001), (-.01, -.02, .001), (.01, .001, .02)
        self.assertEqual(mh.r01_verdict(good, good, 1, .82, .86), "채택 조건 충족")
        self.assertEqual(mh.r01_verdict(good, tie, 1, .82, .86), "채택 조건 충족")              # 1일은 월 블록만
        self.assertEqual(mh.r01_verdict(good, tie, 20, .82, .86), "동률")                       # h>1 은 연속 블록도
        self.assertEqual(mh.r01_verdict(good, good, 20, .70, .80), "점수 우위·포함률 탈락")
        self.assertEqual(mh.r01_verdict(good, good, 20, .74, .66), "채택 조건 충족")            # 현행이 밖이고 후보가 더 가깝다
        self.assertEqual(mh.r01_verdict(good, good, 20, .64, .66), "점수 우위·포함률 탈락")
        self.assertEqual(mh.r01_verdict(bad, bad, 5, .80, .80), "점수 열위")
        self.assertEqual(mh.r01_verdict(tie, tie, 5, .70, .80), "동률·포함률 탈락")


class OpenGapDesignTests(unittest.TestCase):
    def test_gap_label_and_sigma_follow_the_notebook_definition(self):
        inputs = mh.extract_inputs(synthetic_namespace(n=600))
        reg, cols = mh.open_gap_design(inputs)
        sam = inputs["sam"]
        d = reg.index[200]
        p = sam.index.get_loc(d)
        gap = sam["open"] / sam["close"].shift(1) - 1
        self.assertAlmostEqual(reg.loc[d, "future_return"], sam["open"].iloc[p] / sam["close"].iloc[p - 1] - 1, places=14)
        self.assertAlmostEqual(reg.loc[d, "sigma_simple"], float(gap.iloc[p - 20:p].std()), places=14)   # d−1 까지의 20개
        self.assertEqual(cols, inputs["feature_cols"])


class RunnerR01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        storage, results = tmp / "runs", tmp / "results"
        cache = storage / "samsung" / "data_cache"; cache.mkdir(parents=True)
        (cache / "target.parquet").write_bytes(b"snapshot-v1")
        ns = synthetic_namespace(n=2000)
        cls.state = mh.execute("R01", "samsung", "quick", storage, results, False,
                               run_notebook_fn=lambda *a, **k: {"samsung": ns})
        cls.summaries = {tag: json.loads((cls.state.run_dir / f"summary_samsung_{tag}.json").read_text(encoding="utf-8"))
                         for tag in ("open", "h1", "h5", "h20")}

    def test_contract_files_and_cells(self):
        metrics = pd.read_csv(self.state.run_dir / "metrics.csv")
        comparisons = pd.read_csv(self.state.run_dir / "comparisons.csv")
        self.assertEqual(set(metrics["cell"].astype(str)), {"open", "1", "5", "20"})
        self.assertEqual(set(metrics["evaluation_stage"]), {"selection", "evaluation", "sequential_all", "halfyear"})
        schemes = {"current", *sum(mh.R01_FAMILIES.values(), [])}
        self.assertTrue(schemes <= set(metrics["candidate"]))
        self.assertIn("trailing_q_w250 - current", set(comparisons["comparison"]))
        self.assertTrue({"month", "contiguous_40"} <= set(comparisons["block"]))
        self.assertEqual(sorted(self.state.completed()), sorted(f"samsung:{t}" for t in ("open", "h1", "h5", "h20")))

    def test_current_reproduces_the_production_interval(self):
        for tag, s in self.summaries.items():
            self.assertTrue(s["current_matches_production_coverage"], tag)
            self.assertAlmostEqual(s["evaluation"]["current"]["interval_coverage"], s["production"]["band_coverage_realized"], places=12)
            self.assertAlmostEqual(s["evaluation"]["current"]["mean_halfwidth"], s["production"]["band_halfwidth_mean"], places=12)
            self.assertEqual(s["leak_violations"], 0)

    def test_parameters_are_chosen_on_the_selection_window_only(self):
        for tag, s in self.summaries.items():
            for family, names in mh.R01_FAMILIES.items():
                best = min(names, key=lambda n: s["selection"][n]["interval_score"])
                self.assertEqual(s["chosen"][family], best, f"{tag} {family}")
            self.assertEqual(set(s["not_run"]), {"pid", "enbpi"})

    def test_oof_file_holds_every_q_path_and_the_stage_labels(self):
        oof = pd.read_csv(self.summaries["h5"]["oof_file"], parse_dates=["prediction_date", "target_date"])
        self.assertTrue({"q_current", "q_trailing_q_w250", "q_conformal_w500", "q_aci_g0.005_w250", "n_matured"} <= set(oof.columns))
        self.assertEqual(set(oof["stage"].dropna()) - {""}, {"calibration", "selection", "evaluation"})
        used = oof[oof["n_matured"] > 0]
        last_used_target = oof["target_date"].to_numpy()[used["n_matured"].to_numpy() - 1]
        self.assertTrue((last_used_target < used["prediction_date"].to_numpy()).all())


if __name__ == "__main__":
    unittest.main()
