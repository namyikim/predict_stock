# -*- coding: utf-8 -*-
"""D램 현물가: DRAMeXchange 첫 페이지 스냅샷을 매일 누적한다."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import macro_utils as mu  # noqa: E402
from data_sources import dram_spot as ds  # noqa: E402

HTML = '''<tbody id="tb_NationalDramSpotPrice">
<tr><td class="tab_title">Item</td><td>Daily High</td><td>Daily Low</td><td>Session High</td>
<td>Session Low</td><td>Session Average</td><td>Session Change</td><td>History</td></tr>
<tr><td class="tab_tr_gray2"><a href="/Price/Dram_Spot"><img src="x"> DDR5 16Gb (2Gx8) 4800/5600 </a></td>
<td>66.00</td><td>38.50</td><td>66.00</td><td>39.00</td><td>54.333</td><td><img src="d"><br>-0.31 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR4 16Gb (2Gx8) 3200 </a></td><td>115</td><td>41</td><td>115</td>
<td>41</td><td>87.354</td><td>0.56 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR4 8Gb (1Gx8) 3200 </a></td><td>74.5</td><td>21</td><td>74.5</td>
<td>21</td><td>42.610</td><td>0.25 %</td><td></td></tr>
<tr><td class="tab_tr_gray2"><a href="#"> DDR3 4Gb 512Mx8 1600/1866 </a></td><td>20.5</td><td>9.6</td><td>20.5</td>
<td>9.6</td><td>13.982</td><td>-0.30 %</td><td></td></tr>
</tbody>'''


class ParserTests(unittest.TestCase):
    def test_reads_session_average_for_tracked_items(self):
        got = ds.parse_dramexchange_spot(HTML)
        self.assertEqual(got, {"ddr5_16gb": 54.333, "ddr4_16gb": 87.354, "ddr4_8gb": 42.61})

    def test_untracked_items_are_ignored(self):
        self.assertNotIn("ddr3", str(ds.parse_dramexchange_spot(HTML)))

    def test_missing_table_is_an_error(self):
        with self.assertRaises(ValueError):
            ds.parse_dramexchange_spot("<html>nothing</html>")

    def test_renamed_items_are_an_error_not_silent_zeros(self):
        with self.assertRaises(ValueError):
            ds.parse_dramexchange_spot(HTML.replace("DDR5 16Gb", "DDR9 99Gb")
                                        .replace("DDR4 16Gb", "X").replace("DDR4 8Gb", "Y"))


class AccumulationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.fb = self.root / "fb"
        self.fb.mkdir()
        pd.DataFrame({"date": pd.bdate_range("2026-09-01", periods=5).strftime("%Y-%m-%d"),
                      "ddr5_16gb": [50, 51, 52, 53, 54], "ddr4_16gb": [80, 82, 84, 86, 88],
                      "ddr4_8gb": [40, 41, 42, 43, 44]}).to_csv(self.fb / "dram_spot.csv", index=False)
        self.saved = ds.fetch_dram_spot
        self.addCleanup(lambda: setattr(ds, "fetch_dram_spot", self.saved))

    def test_today_is_appended_to_the_stored_history(self):
        ds.fetch_dram_spot = lambda *a, **k: pd.DataFrame(
            [{"date": pd.Timestamp("2026-09-11"), "ddr5_16gb": 54.333, "ddr4_16gb": 87.354, "ddr4_8gb": 42.61}])
        frame, info = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(info["source"], "DRAMEXCHANGE+cache")
        self.assertEqual((info["rows"], info["last"]), (6, "2026-09-11"))
        self.assertTrue(info["fresh"])

    def test_same_day_refetch_replaces_not_duplicates(self):
        ds.fetch_dram_spot = lambda *a, **k: pd.DataFrame(
            [{"date": pd.Timestamp("2026-09-07"), "ddr5_16gb": 99.0, "ddr4_16gb": 1.0, "ddr4_8gb": 1.0}])
        frame, _ = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(len(frame), 5)
        self.assertEqual(float(frame.set_index("date").loc["2026-09-07", "ddr5_16gb"]), 99.0)

    def test_fetch_failure_falls_back_to_history(self):
        def boom(*a, **k):
            raise RuntimeError("차단")
        ds.fetch_dram_spot = boom
        _, info = mu.load_dram_spot(self.root, fallback_dir=self.fb)
        self.assertEqual(info["source"], "last_successful_fetch")
        self.assertFalse(info["fresh"])

    def test_nothing_available_raises(self):
        def boom(*a, **k):
            raise RuntimeError("차단")
        ds.fetch_dram_spot = boom
        with self.assertRaises(RuntimeError):
            mu.load_dram_spot(Path(tempfile.mkdtemp()))


class SummaryTests(unittest.TestCase):
    def test_short_history_reports_none_for_unavailable_changes(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-11"]),
                              "ddr5_16gb": [50.0, 55.0]})
        got = mu.dram_spot_summary(frame)
        self.assertAlmostEqual(got["change_7d"], 0.10)   # 9/1 값 대비
        self.assertIsNone(got["change_30d"])              # 한 달 전 값이 없다
        self.assertEqual(got["days"], 2)

    def test_missing_item_returns_none(self):
        self.assertIsNone(mu.dram_spot_summary(pd.DataFrame({"date": [], "ddr5_16gb": []})))


class ReportGuardTests(unittest.TestCase):
    def test_dram_is_displayed_but_not_yet_a_feature(self):
        source = (ROOT / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("D램 현물 가격", source)
        self.assertIn("특징으로 넣지 않습니다", source)
        # FEATURES 목록에 들어가면 안 된다(이력이 검증에 충분해지기 전까지).
        self.assertNotIn("dram", source[source.index("FEATURES = ["):source.index("FEATURES = [") + 200].lower())


class ErrorDetailTests(unittest.TestCase):
    """URLError 는 종류만으로 원인을 알 수 없다. '해외 IP 차단'과 '키 문제'를 구분하려면
    reason 까지 남겨야 한다(2026-09-11: 관세청 실패가 URLError 로만 찍혀 원인을 알 수 없었다)."""

    def test_url_error_carries_the_underlying_reason(self):
        import socket
        from urllib.error import URLError
        text = mu.error_detail(URLError(socket.gaierror(-2, "Name or service not known")))
        self.assertIn("URLError", text)
        self.assertIn("gaierror", text)
        self.assertIn("Name or service not known", text)

    def test_http_error_keeps_the_status_code(self):
        from urllib.error import HTTPError
        text = mu.error_detail(HTTPError("http://example.com", 403, "Forbidden", {}, None))
        self.assertIn("403", text)

    def test_never_includes_a_url_because_keys_live_there(self):
        from urllib.error import HTTPError
        text = mu.error_detail(HTTPError("https://api.example.com?serviceKey=SECRET", 500, "err", {}, None))
        self.assertNotIn("serviceKey", text)
        self.assertNotIn("SECRET", text)

    def test_every_fetcher_uses_it(self):
        root = Path(mu.__file__).resolve().parent
        for name in ("dram_spot.py", "ecos.py", "exports.py", "kosis.py", "oecd.py"):
            source = (root / "data_sources" / name).read_text(encoding="utf-8")
            if "detail =" in source:
                self.assertIn("detail = error_detail(exc)", source, name)
                self.assertNotIn('getattr(exc, "code", "")}\'.strip()', source, name)

    def test_customs_message_explains_the_likely_cause(self):
        source = (Path(mu.__file__).resolve().parent / "data_sources" / "exports.py").read_text(encoding="utf-8")
        self.assertIn("해외 IP 에서 SERVICE_KEY_IS_NOT_REGISTERED", source)
        self.assertIn("이 실패는 정상이며", source)


class CustomsKeyVariantTests(unittest.TestCase):
    """data.go.kr 인증키는 API 마다 적용 방식이 다르다.

    포털 안내: "API 환경 또는 호출 조건에 따라 인증키가 적용되는 방식이 다를 수 있습니다."
    2026-09-11 실제 호출로 확인: 관세청 품목별 수출입실적은 포털의 인코딩 키를 그대로 붙여야
    resultCode 00 이 온다. 디코딩 후 재인코딩하면 SERVICE_KEY_IS_NOT_REGISTERED 가 난다.
    """

    ENCODED = "AbC%2Bd%2Fe%3D%3D"
    DECODED = "AbC+d/e=="

    def test_encoded_key_is_sent_as_is_first(self):
        from data_sources import exports as ex
        variants = ex.key_variants(self.ENCODED)
        self.assertEqual(variants[0], ("as_is", self.ENCODED))

    def test_decoded_key_tries_both_forms(self):
        from data_sources import exports as ex
        labels = [label for label, _ in ex.key_variants(self.DECODED)]
        self.assertEqual(labels, ["as_is", "once"])
        self.assertIn("%2B", dict(ex.key_variants(self.DECODED))["once"])

    def test_no_duplicate_requests(self):
        from data_sources import exports as ex
        texts = [text for _, text in ex.key_variants(self.ENCODED)]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(len(texts), 1)          # 인코딩 키는 한 형태뿐

    def test_empty_key_yields_nothing(self):
        from data_sources import exports as ex
        self.assertEqual(ex.key_variants(""), [])
        self.assertEqual(ex.key_variants(None), [])

    def test_key_is_not_mangled_by_the_loader(self):
        # 예전에는 data_go_kr_key() 가 %를 보고 디코딩해 버려 이 API 에서 실패했다.
        import os
        from data_sources import exports as ex
        saved = os.environ.get("DATA_GO_KR_KEY")
        os.environ["DATA_GO_KR_KEY"] = self.ENCODED
        try:
            self.assertEqual(ex.data_go_kr_key(), self.ENCODED)
        finally:
            if saved is None:
                os.environ.pop("DATA_GO_KR_KEY", None)
            else:
                os.environ["DATA_GO_KR_KEY"] = saved


class CustomsResponseTests(unittest.TestCase):
    """2026-09-11 실제 응답 구조로 파서를 고정한다."""

    XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>'
           '<header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header><body><items>'
           '<item><expDlr>5170747</expDlr><hsCode>8541101000</hsCode><impDlr>3959009</impDlr>'
           '<statKor>칩</statKor><year>2026.01</year></item>'
           '<item><expDlr>2000000</expDlr><hsCode>8541210000</hsCode><impDlr>3552433</impDlr>'
           '<statKor>트랜지스터</statKor><year>2026.01</year></item>'
           '<item><expDlr>9000000</expDlr><hsCode>8541</hsCode><impDlr>0</impDlr>'
           '<statKor>총계</statKor><year>2026</year></item>'
           '</items></body></response>')

    def test_sums_月_and_drops_the_yearly_total_row(self):
        from data_sources import exports as ex
        frame = ex.parse_customs_xml(self.XML)
        self.assertEqual(len(frame), 1)                       # 2026-01 한 달만
        self.assertEqual(frame["month"].iloc[0], pd.Timestamp("2026-01-01"))
        self.assertEqual(frame["value"].iloc[0], 5170747 + 2000000)   # expDlr 합산

    def test_uses_export_dollars_not_imports(self):
        from data_sources import exports as ex
        frame = ex.parse_customs_xml(self.XML)
        self.assertNotEqual(frame["value"].iloc[0], 3959009 + 3552433)


class KeyFingerprintTests(unittest.TestCase):
    """'Colab 에서는 되는데 Actions 에서는 안 된다'를 판정하려면 두 곳의 키가 같은지 알아야 한다.
    값은 로그에 남길 수 없으므로 지문만 남긴다."""

    ENCODED = "Zr1kTIs7wafCu" + "X" * 80 + "%3D%3D"
    DECODED = "Zr1kTIs7wafCu" + "X" * 80 + "=="

    def test_shape_distinguishes_encoded_from_decoded(self):
        self.assertIn("encoded", mu.key_fingerprint(self.ENCODED))
        self.assertIn("decoded", mu.key_fingerprint(self.DECODED))
        self.assertIn("plain", mu.key_fingerprint("abc123"))

    def test_length_is_reported(self):
        self.assertIn(f"len={len(self.ENCODED)}", mu.key_fingerprint(self.ENCODED))

    def test_does_not_reveal_the_key(self):
        text = mu.key_fingerprint(self.ENCODED)
        self.assertNotIn(self.ENCODED, text)
        self.assertLess(len(text), 40)                    # 앞뒤 4자만
        self.assertIn("…", text)

    def test_missing_key_is_stated(self):
        self.assertEqual(mu.key_fingerprint(""), "key=없음")
        self.assertEqual(mu.key_fingerprint(None), "key=없음")

    def test_customs_failure_includes_the_fingerprint(self):
        source = (Path(mu.__file__).resolve().parent / "data_sources" / "exports.py").read_text(encoding="utf-8")
        self.assertIn("key_fingerprint(key)", source)
        self.assertIn("지문이 포털의 키와 다르면", source)

    def test_report_keeps_enough_of_the_reason_to_diagnose(self):
        import report_html as rh
        long_reason = "관세청 조회 실패(HS 8541, key=encoded len=102 Zr1k…D%3D) — " + "x" * 200
        html = rh.fragment_sources_html({}, {"customs_info": {"enabled": False, "reason": long_reason}})
        self.assertIn("key=encoded len=102", html)        # 80자에서 잘리면 안 된다


class CustomsCacheRouteTests(unittest.TestCase):
    """관세청은 해외 IP 에서 막힌다(2026-09-11 확인: 같은 키가 한국에서는 resultCode 00,
    Actions 에서는 SERVICE_KEY_IS_NOT_REGISTERED). 그래서 한국에서 도는 Colab 실행이 보관본을
    갱신하고 Actions 는 그것을 쓴다 — KOSIS 와 같은 구조다."""

    @classmethod
    def setUpClass(cls):
        import json
        nb = json.loads((Path(mu.__file__).resolve().parent /
                         "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cls.source = "\n".join("".join(c["source"]) for c in nb["cells"])

    def test_colab_run_fetches_customs_for_the_cache(self):
        self.assertIn("_customs_key = data_go_kr_key()", self.source)
        self.assertIn("fetch_customs_exports(pd.Timestamp(START_DATE)", self.source)
        self.assertIn('_candidates.append(("customs_exports.csv"', self.source)

    def test_failure_leaves_the_cache_alone(self):
        # 받지 못하면 보관본을 덮어쓰지 않는다(보관본을 보관본으로 덮으면 이력이 굳는다).
        self.assertIn("관세청을 받지 못해 보관본을 그대로 둡니다", self.source)

    def test_missing_key_is_stated_not_silent(self):
        self.assertIn("DATA_GO_KR_KEY 가 없어 관세청 보관본을 갱신하지 않습니다", self.source)

    def test_error_message_names_the_real_cause(self):
        source = (Path(mu.__file__).resolve().parent / "data_sources" / "exports.py").read_text(encoding="utf-8")
        self.assertIn("해외 IP 에서 SERVICE_KEY_IS_NOT_REGISTERED", source)
        self.assertIn("macro_history/customs_exports.csv", source)
