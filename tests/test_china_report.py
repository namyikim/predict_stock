"""중국 보고서 절 순서: 진행 중인 15차가 맨 앞, 과거 계획은 최신순."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_china_report as cr  # noqa: E402

SOURCE = (ROOT / "tools" / "build_china_report.py").read_text(encoding="utf-8")


class SectionOrderTests(unittest.TestCase):
    def test_section_titles_are_numbered_with_the_current_plan_first(self):
        for title in ('h3("1. 한눈에")', 'h3("2. 15차 계획(2026~2030)과 후보 종목")',
                      'h3("3. 계획별: 정책 업종 대표 종목의 계획 기간 수익률")',
                      'h3("4. CSI 300(沪深300) 분석")', 'h3("5. 데이터와 방법")'):
            self.assertIn(title, SOURCE)

    def test_parts_are_reordered_before_returning(self):
        self.assertIn("parts[_i_15:_i_data]", SOURCE)
        # 재배치가 5절(데이터와 방법) 뒤가 아니라 그 앞의 세 절만 옮기는지
        self.assertIn("parts[:_i_plans] + parts[_i_15:_i_data]", SOURCE)

    def test_past_plans_stay_newest_first(self):
        self.assertIn("for pr in reversed(plan_results):", SOURCE)
        self.assertIn('(2026, 2030, "15차(진행 중)"), (2021, 2025, "14차")', SOURCE)

    def test_cross_references_match_the_new_numbers(self):
        self.assertIn("지수 자체가 약했다(4절)", SOURCE)
        self.assertIn("3절의 교훈을 그대로 적용하면", SOURCE)
        self.assertNotIn("2절의 교훈", SOURCE)
        self.assertNotIn("약했다(3절)", SOURCE)


if __name__ == "__main__":
    unittest.main()
