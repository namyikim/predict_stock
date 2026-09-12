# -*- coding: utf-8 -*-
"""D램 현물가: DRAMeXchange 첫 페이지 스냅샷을 매일 누적한다."""
import contextlib
import io
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
        self.assertIn("한국 정부 API 는 해외 IP 에서 막히므로", source)
        self.assertIn("이 실패는 예상된", source)


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
    """관세청은 해외 IP 에서 막힐 수 있다(2026-09-11 확인: 같은 키가 한국에서는 resultCode 00,
    Actions 에서는 SERVICE_KEY_IS_NOT_REGISTERED). 그래서 한국에서 도는 Colab 실행이 보관본을
    갱신하고 Actions 는 그것을 쓴다 — KOSIS 와 같은 구조다.

    다만 항상 막히는 것은 아니다: 2026-09-12 Actions 는 키가 받아들여진 상태에서 resultCode 99
    ('조회기간은 1년이내')를 받았다. 서버가 XML 로 답했다는 것은 연결도 인증도 통과했다는 뜻이다.
    그러니 실패를 보면 IP 차단이라고 단정하지 말고 응답 내용을 먼저 읽어야 한다."""

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
        self.assertIn("한국 정부 API 는 해외 IP 에서 막히므로", source)
        self.assertIn("macro_history/customs_exports.csv", source)


class CustomsYearLimitTests(unittest.TestCase):
    """관세청은 한 번에 1년 이내만 조회할 수 있다(초과하면 resultCode 99).

    2026-09-12: Actions 가 30개월을 한 번에 요청해 관세청 계열이 통째로 꺼져 있었다. 노트북은
    2015년부터 요청하고 있었으니 Colab 에서 돌려도 같은 오류였다. 그래서 창으로 나눠 부른다.
    """

    def setUp(self):
        from data_sources import exports as ex
        self.ex = ex

    # ---- 창 나누기 ----------------------------------------------------------
    def test_windows_never_exceed_one_year_and_cover_everything(self):
        for start, end in (("2015-01-01", "2026-09-12"), ("2024-03-01", "2026-09-12"),
                           ("2026-01-01", "2026-08-01"), ("2026-09-01", "2026-09-30")):
            windows = self.ex.month_windows(start, end)
            first = pd.Timestamp(start).to_period("M")
            last = pd.Timestamp(end).to_period("M")
            self.assertEqual(windows[0][0], first.strftime("%Y%m"))
            self.assertEqual(windows[-1][1], last.strftime("%Y%m"))
            previous_end = None
            for strt, stop in windows:
                a, b = pd.Period(strt, "M"), pd.Period(stop, "M")
                self.assertLessEqual((b - a).n, 11, f"{strt}~{stop} 이 1년을 넘는다")
                self.assertLessEqual(a, b)
                if previous_end is not None:
                    self.assertEqual((a - previous_end).n, 1, "창 사이에 구멍이나 겹침이 있다")
                previous_end = b

    def test_reversed_range_is_refused(self):
        with self.assertRaises(ValueError):
            self.ex.month_windows("2026-09-01", "2026-01-01")

    # ---- 여러 창을 이어 붙인다 ----------------------------------------------
    def test_fetch_splits_the_request_and_concatenates(self):
        calls = []

        def fake_request(hs, start_text, end_text, key, retries=3):
            calls.append((hs, start_text, end_text))
            months = pd.period_range(start_text, end_text, freq="M")
            return pd.DataFrame({"month": [m.to_timestamp() for m in months],
                                 "value": [1.0] * len(months)})
        saved = self.ex._customs_request
        self.ex._customs_request = fake_request
        try:
            frame = self.ex.fetch_customs_exports("2024-03-01", "2026-09-12", "k", hs_codes=("8541", "8542"))
        finally:
            self.ex._customs_request = saved
        self.assertEqual(len(calls), 6, "HS 2개 × 창 3개여야 한다")
        for _, strt, stop in calls:
            self.assertLessEqual((pd.Period(stop, "M") - pd.Period(strt, "M")).n, 11)
        self.assertEqual(len(frame), 31, "2024-03~2026-09 는 31개월")
        # HS 두 개를 합쳤으니 달마다 2.0, 창이 겹쳐 두 번 더해지지 않았다는 뜻이기도 하다.
        self.assertTrue((frame["value"] == 2.0).all(), "창이 겹쳐 중복 합산됐다")

    def test_empty_windows_are_skipped_but_an_all_empty_code_fails(self):
        def only_recent(hs, start_text, end_text, key, retries=3):
            if start_text < "202501":
                raise self.ex.CustomsEmpty("관세청 응답에 월별 수출액이 없습니다.")
            months = pd.period_range(start_text, end_text, freq="M")
            return pd.DataFrame({"month": [m.to_timestamp() for m in months], "value": [1.0] * len(months)})

        saved = self.ex._customs_request
        self.ex._customs_request = only_recent
        try:
            frame = self.ex.fetch_customs_exports("2023-01-01", "2026-09-12", "k", hs_codes=("8541",))
            self.assertGreater(len(frame), 0)
            self.assertGreaterEqual(frame["month"].min(), pd.Timestamp("2025-01-01"))

            def always_empty(hs, start_text, end_text, key, retries=3):
                raise self.ex.CustomsEmpty("관세청 응답에 월별 수출액이 없습니다.")
            self.ex._customs_request = always_empty
            with self.assertRaises(RuntimeError) as caught:
                self.ex.fetch_customs_exports("2023-01-01", "2026-09-12", "k", hs_codes=("8541",))
            self.assertIn("자료가 없습니다", str(caught.exception))
        finally:
            self.ex._customs_request = saved

    # ---- 서버가 거부한 요청 --------------------------------------------------
    def test_rejected_request_is_not_retried_and_does_not_blame_the_ip(self):
        """resultCode 99 는 서버가 정상 응답한 것이다. 재시도해도 같고, IP 차단도 아니다."""
        attempts = []

        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner):
                attempts.append(1)
                return ('<response><header><resultCode>99</resultCode>'
                        '<resultMsg>시작과 종료의 조회기간은 1년이내 기간만 가능합니다.</resultMsg>'
                        '</header></response>').encode("utf-8")

        saved = self.ex.open_url
        self.ex.open_url = lambda *a, **k: Response()
        try:
            with self.assertRaises(RuntimeError) as caught:
                self.ex.fetch_customs_exports("2026-01-01", "2026-08-01", "AbC%2Bd%3D%3D", retries=3)
        finally:
            self.ex.open_url = saved
        text = str(caught.exception)
        self.assertEqual(len(attempts), 1, "서버가 거부한 요청을 다시 보냈다(기다리기만 하고 결과는 같다)")
        self.assertIn("1년이내", text, "서버가 말한 진짜 이유가 사라졌다")
        self.assertIn("연결과 인증키에는 문제가 없다", text)
        self.assertNotIn("해외 IP 에서 막히므로", text, "IP 차단이 아닌데 그렇게 적었다")
        self.assertIn("2026-01~2026-08".replace("-", ""), text.replace("-", ""))

    def test_call_sites_no_longer_ask_for_more_than_a_year_at_once(self):
        """호출부가 긴 기간을 줘도 fetch 가 나눈다 — 나누는 책임이 fetch 에 있어야 한다."""
        import inspect
        source = inspect.getsource(self.ex.fetch_customs_exports)
        self.assertIn("month_windows(start, end, span)", source)
        earnings = (Path(mu.__file__).resolve().parent / "tools" / "build_earnings_forecast.py").read_text(encoding="utf-8")
        self.assertIn("fetch_customs_exports(", earnings)


class CustomsCheckCellTests(unittest.TestCase):
    """노트북 0.5절 점검 셀 — 전체 실행 없이 관세청만 받아 본다.

    실제 API 는 부르지 않는다. 셀이 준비된 이름만으로 돌아가는지, 키가 없을 때 무엇을 알려 주는지,
    허락 없이 보관본을 덮어쓰지 않는지를 본다. Colab 에서 처음 눌렀을 때 NameError 로 죽지 않게
    하는 것이 이 테스트의 목적이다.
    """

    long_key = "Zr1k" + "aB3%2Bx9" * 12 + "D%3D"      # 실제 키와 비슷한 길이. 값은 출력되면 안 된다.

    @classmethod
    def setUpClass(cls):
        import json as _json
        nb = _json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        cells = [c for c in nb["cells"] if "customs_check" in (c.get("metadata", {}).get("tags") or [])]
        assert len(cells) == 1, f"customs_check 셀이 {len(cells)}개"
        cls.code = "".join(cells[0]["source"])
        cls.position = nb["cells"].index(cells[0])
        cls.tail = [c for c in nb["cells"][cls.position + 1:]
                    if "== transformer-experiment ==" in "".join(c["source"])]

    def namespace(self, key=None, fetch=None, token=None, target="samsung"):
        """셀이 기대하는 이름만 담은 네임스페이스. 실제 노트북이 이 시점에 갖는 것과 같다."""
        import json as _json
        from data_sources import exports as ex

        def fake_fetch(start, end, k, **kw):
            months = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")
            return pd.DataFrame({"month": [m.to_timestamp() for m in months],
                                 "value": [1.1e10] * len(months)})
        self.published = []

        def github_get(path, tok):
            return None, None
        return {
            "TARGET": target, "RUN_TARGETS": ["samsung", "sk_hynix"], "pd": pd, "json": _json,
            "data_go_kr_key": (lambda: key), "fetch_customs_exports": fetch or fake_fetch,
            "month_windows": ex.month_windows, "CUSTOMS_HS": ex.CUSTOMS_HS,
            "key_fingerprint": ex.key_fingerprint, "reconcile_customs": ex.reconcile_customs,
            "github_token": (lambda: token), "github_get": github_get,
            "GITHUB_REPO": "namyikim/predict_stock", "GITHUB_BRANCH": "main",
            "GITHUB_MACRO_DIR": "macro_history",
        }

    def run_cell(self, **kw):
        ns = self.namespace(**kw)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            exec(compile(self.code, "<customs_check>", "exec"), ns)
        return out.getvalue(), ns

    def test_cell_runs_with_only_the_names_available_at_that_point(self):
        """셀 위치(헬퍼 직후)에서 정의돼 있는 이름만으로 끝까지 돈다 — NameError 가 나면 안 된다."""
        text, ns = self.run_cell(key=self.long_key)
        self.assertIn("수신 성공", text)
        self.assertIn("억 달러", text)
        self.assertIn("12개월 창", text, "1년 한도 분할을 보여 주지 않는다")

    def test_missing_key_tells_the_user_where_to_put_it(self):
        text, _ = self.run_cell(key=None)
        self.assertIn("DATA_GO_KR_KEY 가 없습니다", text)
        self.assertIn("노트북 액세스", text)

    def test_key_is_never_printed_in_full(self):
        text, _ = self.run_cell(key=self.long_key)
        self.assertNotIn(self.long_key, text, "인증키가 통째로 찍혔다")
        self.assertIn("len=", text, "어느 키를 썼는지 지문이 없다")

    def test_nothing_is_published_without_the_switch(self):
        text, _ = self.run_cell(key=self.long_key, token="tok")
        self.assertIn("보관본은 그대로", text)
        self.assertNotIn("보관본 갱신 macro_history", text)

    def test_second_target_skips_so_the_replay_does_not_refetch(self):
        """셀 45 의 재실행은 TARGET 만 바꿔 같은 셀들을 다시 돌린다. 두 번 받을 이유가 없다."""
        text, _ = self.run_cell(key=self.long_key, target="sk_hynix")
        self.assertIn("첫 종목에서만", text)
        self.assertNotIn("수신 성공", text)

    def test_failure_separates_a_server_answer_from_a_blocked_ip(self):
        def rejected(*a, **k):
            raise RuntimeError("관세청 조회 실패(HS 8541, 202509~202609, key=encoded len=102 ab…cd) — "
                               "as_is 키: CustomsRejected 관세청 API 오류 99: 조회기간은 1년이내")
        text, _ = self.run_cell(key=self.long_key, fetch=rejected)
        self.assertIn("실패", text)
        self.assertIn("IP 차단이 아니라", text)
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED", text, "차단일 때의 판별법도 함께 적어야 한다")

    def test_cell_sits_before_the_pipeline_and_after_the_helpers(self):
        import json as _json
        nb = _json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        helper = max(i for i, c in enumerate(nb["cells"])
                     if set(c.get("metadata", {}).get("tags") or []) & {"macro_utils", "forecast_utils", "report_html"})
        self.assertGreater(self.position, helper, "헬퍼보다 앞에 있으면 함수가 없어 죽는다")
        self.assertTrue(self.tail, "셀 45 재실행 구간(transformer 표식)보다 뒤에 있으면 안 된다")


class CustomsFailureMessageTests(unittest.TestCase):
    """같은 차단이 타임아웃으로도, 인증 오류로도 나타난다. 설명을 하나로 고정하면 엉뚱해진다.

    2026-09-11: 실제 실패가 TimeoutError 인데 'SERVICE_KEY_IS_NOT_REGISTERED 로 거부한다'는
    설명이 붙어 혼란스러웠다.
    """

    def message_for(self, exc):
        from data_sources import exports as ex
        saved = ex.open_url

        def boom(*a, **k):
            raise exc
        ex.open_url = boom
        try:
            ex.fetch_customs_exports("2026-01-01", "2026-08-01", "AbC%2Bd%3D%3D", retries=1)
        except RuntimeError as error:
            return str(error)
        finally:
            ex.open_url = saved
        self.fail("실패해야 한다")

    def test_timeout_says_connection_failed(self):
        text = self.message_for(TimeoutError("timed out"))
        self.assertIn("연결이 되지 않았다", text)
        self.assertNotIn("지문이 포털의 키와 다르면", text)

    def test_auth_rejection_says_key_rejected(self):
        text = self.message_for(RuntimeError("관세청 API 오류 30: SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED 로 거부한 것으로 보인다", text)
        self.assertIn("Secrets 를 확인하라", text)

    def test_both_point_to_the_cache(self):
        for exc in (TimeoutError("timed out"),
                    RuntimeError("관세청 API 오류 30: SERVICE_KEY_IS_NOT_REGISTERED_ERROR")):
            text = self.message_for(exc)
            self.assertIn("macro_history/customs_exports.csv", text)
            self.assertIn("Colab 전체 실행", text)

    def test_error_detail_keeps_the_message_when_there_is_no_reason(self):
        # RuntimeError 는 reason 이 없다. 메시지를 버리면 결정적 단서가 사라진다.
        text = mu.error_detail(RuntimeError("SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED", text)


class SecretScrubTests(unittest.TestCase):
    """예외 메시지에 URL 이 담기면 인증키가 로그에 남는다. 기존 테스트가 이 누출을 잡았다."""

    def test_url_in_message_is_masked(self):
        text = mu.error_detail(OSError("failed to open https://apis.data.go.kr/x?serviceKey=SECRET123"))
        self.assertNotIn("SECRET123", text)
        self.assertNotIn("serviceKey", text)

    def test_suspicious_message_is_dropped_entirely(self):
        # 'URL with private-api-key' 처럼 형태가 URL 이 아니어도 낱말이 의심스러우면 버린다.
        text = mu.error_detail(OSError("URL with private-api-key"))
        self.assertNotIn("private-api-key", text)
        self.assertIn("OSError", text)                 # 예외 종류는 남는다
        self.assertIn("가림", text)

    def test_key_parameters_are_masked(self):
        self.assertNotIn("ABCDEF", mu.error_detail(OSError("crtfc_key=ABCDEF 실패")))

    def test_our_own_api_error_survives_because_it_is_the_diagnosis(self):
        text = mu.error_detail(RuntimeError("관세청 API 오류 30: SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED", text)

    def test_harmless_messages_pass_through(self):
        self.assertIn("timed out", mu.error_detail(TimeoutError("timed out")))
        self.assertIn("Connection reset", mu.error_detail(ConnectionResetError("Connection reset by peer")))
