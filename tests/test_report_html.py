# -*- coding: utf-8 -*-
"""report_html: 노트북에서 떼어낸 보고서 조각 함수들.

노트북 셀 안에 있을 때는 전역에 얽혀 있어 테스트할 수 없었다. 명시적 인자만 받도록 옮겼으므로
이제 직접 검사한다.
"""
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import report_html as rh  # noqa: E402


class FormatTests(unittest.TestCase):
    def test_fmt_handles_units_and_missing(self):
        self.assertEqual(rh.fmt(None), "—")
        self.assertEqual(rh.fmt(float("nan")), "—")
        self.assertEqual(rh.fmt(70100, "won"), "70,100원")
        self.assertEqual(rh.fmt(0.0123, "pct"), "+1.23%")
        self.assertEqual(rh.fmt(-7.2, "bp"), "-7.2bp")

    def test_prob_bar_widths_match_probabilities(self):
        html = rh.prob_bar(0.15, 0.25, 0.60)
        # 첫 width:100% 는 표 자체의 폭이다. 칸 세 개만 본다.
        widths = [float(w) for w in re.findall(r"width:([\d.]+)%", html)][1:]
        self.assertEqual(widths, [15.0, 25.0, 60.0])
        self.assertIn("상승", html)
        # 좁은 칸은 글자가 넘치므로 이름(14% 이하)과 숫자(7% 이하)를 뺀다.
        # CSS 의 width:5.0% 와 섞이지 않도록 칸 안의 글자만 꺼내 본다.
        texts = [t.strip() for t in re.findall(r"nowrap\">(.*?)</td>", rh.prob_bar(0.05, 0.10, 0.85))]
        self.assertEqual(texts, ["", "10%", "상승 85%"])

    def test_range_bar_places_the_center_and_current_marks(self):
        html = rh.range_bar(100, 110, 120, 105)
        positions = [float(x) for x in re.findall(r"left:([\d.]+)%", html)]
        self.assertIn(50.0, positions)       # 중앙값 110 → 50%
        self.assertIn(25.0, positions)       # 현재가 105 → 25%


class SectionTests(unittest.TestCase):
    def test_event_notice_only_when_flagged(self):
        self.assertEqual(rh.event_notice_html([]), "")
        self.assertEqual(rh.event_notice_html(None), "")
        html = rh.event_notice_html(["실적시즌", "공시:잠정실적"])
        self.assertIn("이벤트일", html)
        self.assertIn("분기 실적 발표 시즌", html)
        self.assertIn("잠정실적 공시 직후", html)

    def test_disclosure_section_escapes_and_links(self):
        rows = [{"report_nm": "연결재무제표기준영업(잠정)실적<script>", "rcept_dt": "20261007",
                 "rcept_no": "1", "url": "https://dart.example/1"}]
        html = rh.disclosure_section_html(rows, {"enabled": True}, lambda n: "잠정실적")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("https://dart.example/1", html)
        self.assertIn("2026-10-07", html)
        self.assertIn("잠정실적", html)
        self.assertIn("참고 정보: 최근 공시와 예정 발표", html)

    def test_disclosure_section_explains_absence(self):
        html = rh.disclosure_section_html([], {"enabled": False, "reason": "DART_API_KEY 없음"},
                                          lambda n: "")
        self.assertIn("DART_API_KEY 없음", html)

    def flows(self):
        days = pd.bdate_range("2026-06-01", "2026-09-07")
        rng = np.random.default_rng(0)
        return pd.DataFrame({"date": days, "foreign_net": rng.normal(0, 2e6, len(days)),
                             "inst_net": rng.normal(0, 1e6, len(days)), "indiv_net": np.nan,
                             "volume": 2e7, "foreign_ratio": 50 + np.cumsum(rng.normal(0, .01, len(days)))})

    def test_flow_section_reports_windows_and_ratio(self):
        flows = self.flows()
        close = pd.Series(70000.0, index=pd.DatetimeIndex(flows["date"]))
        html = rh.flow_section_html(flows, {"source": "naver"}, True, close,
                                    pd.Timestamp("2026-09-07"), pd.DataFrame())
        for label in ("최근 1일", "최근 5일", "최근 20일", "최근 60일", "외국인 지분율"):
            self.assertIn(label, html)
        self.assertIn("naver", html)
        self.assertIn("1-1. 외국인·기관 수급", html)

    def test_flow_section_says_when_absent(self):
        html = rh.flow_section_html(None, {"reason": "자료 없음"}, False, pd.Series(dtype=float),
                                    pd.Timestamp("2026-09-07"), pd.DataFrame())
        self.assertIn("자료 없음", html)


class LoaderTests(unittest.TestCase):
    def test_summary_data_tolerates_broken_json(self):
        saved = rh.load_fragment
        try:
            for fragment in (None, "not json", "[]"):
                rh.load_fragment = lambda *a, **k: fragment
                self.assertEqual(rh.load_summary_data("x.json", "samsung", "r", "main"), {})
            rh.load_fragment = lambda *a, **k: '{"quarter": "2026년 3분기"}'
            self.assertEqual(rh.load_summary_data("x.json", "samsung", "r", "main"),
                             {"quarter": "2026년 3분기"})
        finally:
            rh.load_fragment = saved

    def test_code_version_survives_without_network(self):
        import os
        saved = os.environ.get("GITHUB_SHA")
        os.environ["GITHUB_SHA"] = "0123456789abcdef0123456789abcdef01234567"
        try:
            info = rh.code_version("owner/repo", "main", token=None)
        finally:
            if saved is None:
                os.environ.pop("GITHUB_SHA", None)
            else:
                os.environ["GITHUB_SHA"] = saved
        self.assertEqual(info["short"], "0123456")
        self.assertIn("owner/repo", info["url"])


class NotebookWiringTests(unittest.TestCase):
    def test_notebook_only_keeps_thin_adapters(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        for name in ("_fmt", "_prob_bar", "_range_bar", "_code_version", "_load_fragment",
                     "_load_summary_data", "_event_notice_html", "_disclosure_section_html",
                     "_flow_section_html"):
            self.assertNotIn(f"def {name}(", report, f"{name} 은 report_html 로 옮겨졌습니다")
            self.assertIn(f"{name} = ", report)

    def test_helper_cell_is_synced_verbatim(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cells = [c for c in nb["cells"] if "report_html" in c.get("metadata", {}).get("tags", [])]
        self.assertEqual(len(cells), 1)
        self.assertEqual("".join(cells[0]["source"]),
                         (ROOT / "report_html.py").read_text(encoding="utf-8"))


class FragmentSourcesTests(unittest.TestCase):
    """7·8절이 쓴 자료원도 6절에 적는다.

    6절 표는 노트북이 직접 받은 자료만 적어서 G20 CLI·TSMC·D램 현물가가 '이 보고서의 데이터'에서
    빠져 보였다(2026-09-11 지적). 조각 JSON 이 남긴 출처를 읽어 표로 붙인다.
    """

    LONGTERM = {"cli_info": {"source": "OECD_API(G20)", "fresh": True, "first": "1998-01", "last": "2026-08"},
                "extra_info": {"nsi": {"source": "ECOS_API", "enabled": True, "first": "2005-01-01",
                                       "last": "2026-09-06"},
                               "term_spread": {"source": "ECOS_API", "enabled": True, "last": "2026-09-10"}}}
    EARNINGS = {"tsmc_info": {"source": "last_successful_fetch", "enabled": True, "fresh": False,
                              "fetch_error": "TWSE 조회 실패", "first": "2025-07", "last": "2026-07"},
                "dram_info": {"source": "DRAMEXCHANGE+cache", "enabled": True, "fresh": True,
                              "first": "2026-09-11", "last": "2026-09-11"},
                "customs_info": {"enabled": False, "reason": "관세청 조회 실패(URLError)"},
                "profit_source": "DART_API", "profit_first": "2016Q1", "profit_last": "2026Q2",
                "profit_n": 42}

    def test_every_fragment_source_is_listed(self):
        html = rh.fragment_sources_html(self.LONGTERM, self.EARNINGS)
        for label in ("G20 경기선행지수", "뉴스심리지수(장기)", "장단기 금리차", "TSMC 월매출",
                      "D램 현물가", "관세청 수출입실적", "분기 영업이익(DART)"):
            self.assertIn(label, html, label)

    def test_cache_use_and_failure_are_visible(self):
        html = rh.fragment_sources_html(self.LONGTERM, self.EARNINGS)
        self.assertIn("저장소 보관본 사용", html)
        self.assertIn("TWSE 조회 실패", html)
        self.assertIn("미포함", html)
        self.assertIn("관세청 조회 실패(URLError)", html)
        self.assertIn("color:#a8322a", html)          # 보관본·실패는 빨간 글씨

    def test_empty_inputs_render_nothing(self):
        self.assertEqual(rh.fragment_sources_html(None, None), "")
        self.assertEqual(rh.fragment_sources_html({}, {}), "")

    def test_escapes_source_text(self):
        html = rh.fragment_sources_html({"cli_info": {"source": "<script>", "last": "x"}}, {})
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_notebook_attaches_it_to_section_six(self):
        import json
        nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        report = next("".join(c["source"]) for c in nb["cells"]
                      if "def build_summary():" in "".join(c.get("source", [])))
        self.assertIn('fragment_sources_html(_load_summary_data("longterm.json")', report)
        # 6절 안에 있어야 한다(월별 지표 표 바로 뒤).
        self.assertLess(report.index("_macro_rows}{_nsi_row}"), report.index("fragment_sources_html"))
