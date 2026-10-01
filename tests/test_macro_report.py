# -*- coding: utf-8 -*-
"""거시 경제 보고서 — 환율·금리·채권 등을 모아 보는 페이지(2026-09-15 시작).

지금은 뼈대만 있고 지표는 하나씩 붙인다. 이 테스트는 뼈대가 무너지지 않게 지킨다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

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

    def test_placeholder_sections_are_gone(self):
        """뼈대 때 두었던 '환율·금리·채권·경기 지표' 빈 칸은 그림이 채워진 뒤 지웠다(2026-09-16 요청)."""
        html = self.page()
        for title in ("환율", "금리", "채권", "경기 지표"):
            self.assertNotIn(f">{title}</h3>", html)
        self.assertNotIn("앞으로 더할 것입니다", html)
        self.assertNotIn("준비 중입니다", html)
        self.assertFalse(hasattr(macro, "SECTIONS"))
        self.assertFalse(hasattr(macro, "planned_section"))

    def test_states_that_it_is_not_a_forecast(self):
        # 이 저장소의 다른 보고서와 같은 태도를 유지한다.
        html = self.page()
        self.assertIn("예측이 아니라 자료 정리입니다", html)
        self.assertIn("투자 자문이 아닙니다", html)

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
        made_from = re.search(r'<meta name="macro-inputs" content="([0-9a-f]+)">', committed)
        if not made_from or made_from.group(1) != macro.inputs_fingerprint():
            # 페이지를 만든 뒤 다른 작업이 보관본만 갱신했다 — 다음 거시 실행이 다시 만든다. 코드 변경 누락이 아니다.
            self.skipTest("보관본이 발행본을 만든 뒤 바뀌었습니다(다음 거시 보고서 실행에서 다시 만듭니다)")
        # '자료 상태' 줄은 실행 순간의 기록이라 시각처럼 뺀다(2026-10-01): 그 실행에서 Yahoo로 대체했는지는 보관본에
        # 남지 않고, 'n영업일 전'은 실행한 날짜에 따라 바뀐다. 이 줄 때문에 테스트가 실패해 push 실행의 보고서가 건너뛰어졌다.
        strip = lambda text: re.sub(r"자료 상태: .*?</div>", "",
                                    re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "", text), flags=re.S)
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



class ReadOnlyRunDoesNotRewriteCacheTests(unittest.TestCase):
    """fetch=False(오프라인 읽기)는 보관본을 덮어쓰지 않는다(2026-09-20 검토).

    전에는 읽기만 해도 macro_history/*.csv 를 다시 저장해, 테스트를 돌릴 때마다 버전 관리 파일이 바뀌었다.
    """

    def run_loader(self, loader, cache_attr, builder_module, builder_name, fetch):
        import importlib, tempfile
        frame = pd.DataFrame({"a": [1.0, 2.0]}, index=pd.date_range("2026-01-31", periods=2, freq="ME"))
        module = importlib.import_module(builder_module)
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache.csv"
            with patch.object(macro, cache_attr, cache), patch.object(module, builder_name, return_value=(frame, {})):
                loader(fetch=fetch)
            return cache.exists()

    def test_fx_cache_is_written_only_when_fetching(self):
        self.assertFalse(self.run_loader(macro.load_fx, "FX_CACHE", "data_sources.fx_inputs", "build_fx_inputs", False))
        self.assertTrue(self.run_loader(macro.load_fx, "FX_CACHE", "data_sources.fx_inputs", "build_fx_inputs", True))

    def test_us_jp_cache_is_written_only_when_fetching(self):
        self.assertFalse(self.run_loader(macro.load_us_jp, "US_JP_CACHE", "data_sources.fred", "build_us_jp_inputs", False))
        self.assertTrue(self.run_loader(macro.load_us_jp, "US_JP_CACHE", "data_sources.fred", "build_us_jp_inputs", True))


class InputsFingerprintTests(unittest.TestCase):
    """발행본에 남기는 보관본 지문(2026-10-01). 줄바꿈 차이는 무시하고, 페이지가 읽는 파일이 바뀌면 달라진다."""

    def test_fingerprint_ignores_line_endings_and_tracks_inputs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "term_spread.csv").write_bytes(b"date,value\n2026-09-01,1.0\n")
            lf = macro.inputs_fingerprint(folder)
            (folder / "term_spread.csv").write_bytes(b"date,value\r\n2026-09-01,1.0\r\n")
            self.assertEqual(macro.inputs_fingerprint(folder), lf)
            (folder / "term_spread.csv").write_bytes(b"date,value\n2026-09-01,1.0\n2026-10-01,1.1\n")
            self.assertNotEqual(macro.inputs_fingerprint(folder), lf)
            (folder / "investor_flows_005930.csv").write_bytes(b"date\n2026-10-01\n")   # 페이지가 읽지 않는 파일
            before = macro.inputs_fingerprint(folder)
            (folder / "investor_flows_005930.csv").write_bytes(b"date\n2026-10-02\n")
            self.assertEqual(macro.inputs_fingerprint(folder), before)

    def test_page_carries_the_fingerprint(self):
        page = macro.build_page(datetime(2026, 9, 15, 9, 0, tzinfo=timezone(timedelta(hours=9))), cycle_fetch=False)
        self.assertIn(f'<meta name="macro-inputs" content="{macro.inputs_fingerprint()}">', page)
