"""장 회고의 '누가 팔고 샀나'(2026-09-29 요청).

하락한 날은 가장 많이 판 주체, 상승한 날은 가장 많이 산 주체를 고르고, 이유는 단정하지 않고 그날 확인되는 사실
(시장·업종·환율·전날 밤 SOX·직전 5거래일·규모·거래량)이 같은 방향이었는지만 적는다.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast_utils as fu  # noqa: E402


def history(n=20, foreign=1_000_000, inst=500_000):
    return pd.DataFrame({"foreign_net": [foreign] * n, "inst_net": [inst] * n, "indiv_net": [-foreign - inst] * n})


class FlowStoryTests(unittest.TestCase):
    def test_down_day_names_the_biggest_seller_and_the_buyer_opposite(self):
        story = fu.flow_story({"foreign_net": -5_000_000, "inst_net": -2_400_000, "indiv_net": 5_300_000},
                              history(), {"c2c": -.054}, 270_000)
        self.assertEqual(story["lead"]["name"], "외국인")
        self.assertEqual(story["counter"]["name"], "개인")
        self.assertAlmostEqual(story["lead"]["vs_usual"], 5.0)

    def test_up_day_names_the_biggest_buyer(self):
        story = fu.flow_story({"foreign_net": 300_000, "inst_net": 2_000_000, "indiv_net": -2_300_000},
                              history(), {"c2c": .03}, 270_000)
        self.assertEqual(story["lead"]["name"], "기관")

    def test_individual_is_estimated_when_missing(self):
        story = fu.flow_story({"foreign_net": -100, "inst_net": -50, "indiv_net": None}, None, {"c2c": -.01}, 1000)
        self.assertTrue(story["estimated_indiv"])
        names = [a["name"] for a in story["actors"]]
        self.assertIn("개인(추정)", names)
        self.assertIn("추정했습니다", fu.flow_story_html(story))

    def test_observations_are_facts_in_the_same_or_opposite_direction(self):
        summary = {"c2c": -.054, "kospi_c2c": -.02, "peer_c2c": -.05, "usdkrw_chg": -.0064, "sox_ret": .014,
                   "volume_ratio": 1.8}
        story = fu.flow_story({"foreign_net": -5_000_000, "inst_net": -2_400_000, "indiv_net": 5_300_000},
                              history(), summary, 270_000, prior_5d=.126, peer_name="SK하이닉스")
        text = " ".join(story["observations"])
        self.assertIn("이 종목(-5.40%)이 훨씬 크게", text)
        self.assertIn("반도체 업종이 함께", text)
        self.assertIn("환율로는 설명되지 않습니다", text)
        self.assertIn("미국 반도체 흐름으로는 설명되지 않습니다", text)
        self.assertIn("차익 실현과 맞는 모양", text)
        self.assertIn("20일 평균의 1.8배", text)

    def test_weak_won_matches_foreign_selling(self):
        story = fu.flow_story({"foreign_net": -1_000, "inst_net": 500, "indiv_net": 500}, None,
                              {"c2c": -.02, "usdkrw_chg": .008}, 1000)
        self.assertTrue(any("원화 약세" in o for o in story["observations"]))

    def test_no_flow_data_returns_none(self):
        self.assertIsNone(fu.flow_story({"foreign_net": None, "inst_net": None}, None, {"c2c": -.01}, 1000))
        self.assertEqual(fu.flow_story_html(None), "")

    def test_review_section_shows_the_block_instead_of_the_old_line(self):
        story = fu.flow_story({"foreign_net": -5_000_000, "inst_net": -2_400_000, "indiv_net": 5_300_000},
                              history(), {"c2c": -.054}, 270_000)
        self.assertIn("누가 팔고 샀나", fu.flow_story_html(story))
        self.assertIn("매매 이유를 단정하지 않습니다", fu.flow_story_html(story))


class ReviewJobDependencyTests(unittest.TestCase):
    def test_review_job_installs_lxml_for_the_naver_table(self):
        # 이것이 빠져 9/17~9/28 회고의 수급이 모두 비어 있었다.
        text = (ROOT / ".github" / "workflows" / "afternoon-report.yml").read_text(encoding="utf-8")
        self.assertIn("yfinance==1.7.0 lxml", text)

    def test_review_uses_the_repository_archive_for_history(self):
        text = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('fallback_dir=Path("macro_history")', text)
        self.assertIn('"flow_story": flow_story_data', text)


if __name__ == "__main__":
    unittest.main()


class MicronAndRangeTests(unittest.TestCase):
    """장 마감 회고 영상 검토(2026-09-30)에서 더한 것: 전날 밤 마이크론, 동종 종목과의 장중 변동폭 비교."""

    def test_micron_same_or_opposite_direction(self):
        same = fu.flow_story({"foreign_net": -1000, "inst_net": 500, "indiv_net": 500}, None,
                             {"c2c": -.02, "micron_ret": -.03}, 1000)
        opposite = fu.flow_story({"foreign_net": -1000, "inst_net": 500, "indiv_net": 500}, None,
                                 {"c2c": -.02, "micron_ret": .03}, 1000)
        self.assertTrue(any("마이크론 -3.00% — 미국 메모리 회사와 같은 방향" in o for o in same["observations"]))
        self.assertTrue(any("마이크론은 +3.00%로 반대 방향" in o for o in opposite["observations"]))

    def test_review_table_shows_range_vs_peer_and_micron(self):
        import json
        review = json.loads((ROOT / "forecast_history" / "samsung" / "reviews" / "2026-09-29.json").read_text(encoding="utf-8"))
        review["summary"].update(range=.0376, peer_range=.028, micron_ret=-.021)
        html = fu.review_section_html(review)
        self.assertIn("장중 변동폭 저점→고점 · 이 종목 / SK하이닉스", html)
        self.assertIn("+3.76% / +2.80%", html)
        self.assertIn("전날 밤 SOX / 나스닥 / 마이크론", html)

    def test_review_run_collects_both(self):
        text = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('summary["micron_ret"] = overnight_of("MU")', text)
        self.assertIn('summary["peer_range"] = range_of(spec["peer"])', text)
