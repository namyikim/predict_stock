# -*- coding: utf-8 -*-
"""거시 경제 보고서 — 환율·금리·채권 등을 모아 보는 페이지(2026-09-15 시작).

지금은 뼈대만 있고 지표는 하나씩 붙인다. 이 테스트는 뼈대가 무너지지 않게 지킨다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_macro_report as macro  # noqa: E402


class PageTests(unittest.TestCase):
    def page(self):
        return macro.build_page(datetime(2026, 9, 15, 9, 0, tzinfo=timezone(timedelta(hours=9))))

    def test_has_the_standard_back_button(self):
        html = self.page()
        self.assertIn("← 보고서 목록", html)
        self.assertIn("border:1px solid #cedff0;border-radius:5px", html)
        self.assertLess(html.index("← 보고서 목록"), html.index("거시 경제</h2>"))

    def test_only_one_back_link(self):
        self.assertEqual(self.page().count('href="../"'), 1)

    def test_every_planned_section_is_rendered(self):
        html = self.page()
        for title, _, items in macro.SECTIONS:
            self.assertIn(f">{title}</h3>", html)
            for item in items:
                self.assertIn(item, html)

    def test_states_that_it_is_not_a_forecast(self):
        # 이 저장소의 다른 보고서와 같은 태도를 유지한다.
        html = self.page()
        self.assertIn("예측이 아니라 자료 정리입니다", html)
        self.assertIn("투자 자문이 아닙니다", html)

    def test_empty_sections_say_so_instead_of_guessing(self):
        # 뼈대 때의 '준비 중' 안내는 요약 절이 대신한다. 값이 없는 절이 비어 있는 그대로인지만 본다.
        html = self.page()
        self.assertIn('class="empty"', html)
        self.assertNotIn("준비 중입니다", html)

    def test_tag_balance(self):
        import re
        html = self.page()
        for tag in ("div", "ul", "h3", "body", "html"):
            self.assertEqual(len(re.findall(rf"<{tag}\b", html)),
                             len(re.findall(rf"</{tag}>", html)), tag)

    def test_committed_page_matches_the_generator(self):
        """발행본은 같은 보관본으로 만든 결과와 같아야 한다(시각 제외).

        페이지가 자료를 담으므로 자료 없이 만든 것과 비교하면 안 된다. 보관본이 없으면 비교하지
        않는다(첫 실행 전).
        """
        import re
        cache = ROOT / "macro_history" / "fx_inputs.csv"
        if not cache.exists():
            self.skipTest("fx_inputs 보관본이 아직 없습니다")
        frame, info = macro.load_fx(fetch=False)
        us_jp, us_jp_info = macro.load_us_jp(fetch=False)
        us_market, us_market_info = macro.load_us_market(fetch=False)
        saving, saving_info = macro.load_saving_investment(fetch=False)
        generated = macro.build_page(datetime(2026, 9, 15, 9, 0, tzinfo=timezone(timedelta(hours=9))),
                                     fx_frame=frame, fx_info=info,
                                     us_jp_frame=us_jp, us_jp_info=us_jp_info,
                                     us_market_frame=us_market, us_market_info=us_market_info,
                                     saving_frame=saving, saving_info=saving_info)
        committed = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        strip = lambda text: re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "", text)
        self.assertEqual(strip(committed), strip(generated),
                         "docs/macro/index.html 을 python tools/build_macro_report.py --write --no-fetch 로 다시 만드세요")

    def test_published_real_rate_chart_precedes_us_market_chart(self):
        """요청한 두 선이 빈 안내로 대체되지 않고 미국 금융시장 그림 바로 위에 있어야 한다."""
        html = (ROOT / "docs" / "macro" / "index.html").read_text(encoding="utf-8")
        real_rate = "원/달러와 한·미 실질금리차"
        us_market = "미국 신용위험·국채금리와 나스닥"
        self.assertIn(real_rate, html)
        self.assertIn(us_market, html)
        self.assertLess(html.index(real_rate), html.index(us_market))
        self.assertNotIn("한·미 실질금리차를 만들지 못했습니다", html)

    def test_committed_fx_cache_contains_real_rate_inputs(self):
        """발행본을 오프라인에서도 재현할 수 있게 계산 입력과 결과를 함께 보관한다."""
        import pandas as pd
        frame = pd.read_csv(ROOT / "macro_history" / "fx_inputs.csv")
        # 실질금리차는 보관된 명목 금리차와 양국 CPI 상승률로 직접 복원할 수 있다.
        # 개별 10년물 열은 조회에 성공했을 때 함께 남지만 오프라인 재현의 필수 열은 아니다.
        required = {"usdkrw", "rate_gap", "real_rate_gap"}
        self.assertTrue(required.issubset(frame.columns), required - set(frame.columns))
        self.assertGreaterEqual(frame["real_rate_gap"].notna().sum(), 24)

    def test_macro_workflow_rebuilds_after_collector_changes(self):
        """수집 코드를 고친 push 뒤 수동 실행을 잊어도 비밀키가 있는 Actions가 발행한다."""
        workflow = (ROOT / ".github" / "workflows" / "macro-report.yml").read_text(encoding="utf-8")
        self.assertIn("  push:\n", workflow)
        for path in ("tools/build_macro_report.py", "tools/macro_summary.py", "data_sources/fx_inputs.py",
                     "data_sources/ecos.py", "data_sources/fred.py"):
            self.assertIn(f'      - "{path}"', workflow)


class LandingTests(unittest.TestCase):
    def test_landing_links_to_the_macro_page_after_metals(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./macro/"', html)
        self.assertLess(html.index('href="./metals/"'), html.index('href="./macro/"'),
                        "금·은 아래에 두기로 했다")
        self.assertLess(html.index('href="./macro/"'), html.index('href="./news/"'))
