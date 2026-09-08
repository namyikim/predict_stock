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
