# -*- coding: utf-8 -*-
"""P02 — 정보 공개 시각의 경계 사례.

기존 누수 테스트(test_pipeline_behavior, test_macro_release)가 다루지 않은 경계만 넣는다.
핵심 질문은 하나다: "한국 아침 예측 시각에 그 봉이 정말 마감돼 있었는가?"

`drop_unclosed_last_bar`는 시각 판단이 전부인 함수인데 `pd.Timestamp.now()`를 직접 불러
결정적으로 검증할 수 없었다. 주입 가능한 `now`를 받도록 바꾸고 여기서 경계를 고정한다.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline_behavior import _cell_containing, load_notebook_functions  # noqa: E402


def notebook_value(marker, name):
    """셀에서 최상위 이름 하나의 값을 얻는다(SESSION_CLOSE 같은 표는 함수가 아니라서)."""
    import ast
    tree = ast.parse(_cell_containing(marker))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} 을 찾지 못했습니다.")


def _bars(dates):
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    return pd.DataFrame({"close": range(1, len(index) + 1)}, index=index)


class SessionCloseTests(unittest.TestCase):
    """미국·한국 마감 시각과 서머타임 경계."""

    @classmethod
    def setUpClass(cls):
        # 이 셀의 다른 함수들이 기본 인자에서 노트북 전역값을 참조한다. 시각 판단과
        # 무관하므로 자리만 채운다.
        cls.ns = load_notebook_functions(
            ["def drop_unclosed_last_bar"],
            extra_globals={"START_DATE": "2015-01-01", "END_DATE": None,
                           "DATA_CACHE_DIR": ".", "USE_DATA_CACHE": False,
                           "MAX_STALE_DAYS": 7, "SESSION_CLOSE": notebook_value(
                               "SESSION_CLOSE = {", "SESSION_CLOSE")})
        cls.drop = staticmethod(cls.ns["drop_unclosed_last_bar"])
        cls.close = notebook_value("SESSION_CLOSE = {", "SESSION_CLOSE")

    def test_session_close_table_covers_every_session(self):
        for session in ("korea", "us", "london", "cont"):
            self.assertIn(session, self.close)

    # ---- 당일 봉 ----------------------------------------------------------
    def test_bar_before_close_is_dropped(self):
        """미국 장이 아직 열려 있으면 그날 봉은 미완성이다."""
        frame = _bars(["2026-09-08", "2026-09-09"])
        now = pd.Timestamp("2026-09-09 15:30", tz="America/New_York")   # 16:05 전
        kept = self.drop(frame, "us", now=now)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept.index[-1], pd.Timestamp("2026-09-08"))

    def test_bar_exactly_at_close_is_kept(self):
        """마감 시각 자체는 '마감했다'로 본다. 경계에서 하루가 사라지면 안 된다."""
        frame = _bars(["2026-09-08", "2026-09-09"])
        now = pd.Timestamp("2026-09-09 16:05", tz="America/New_York")
        self.assertEqual(len(self.drop(frame, "us", now=now)), 2)

    def test_bar_one_minute_before_close_is_dropped(self):
        frame = _bars(["2026-09-08", "2026-09-09"])
        now = pd.Timestamp("2026-09-09 16:04", tz="America/New_York")
        self.assertEqual(len(self.drop(frame, "us", now=now)), 1)

    def test_bar_after_close_is_kept(self):
        frame = _bars(["2026-09-08", "2026-09-09"])
        now = pd.Timestamp("2026-09-09 18:00", tz="America/New_York")
        self.assertEqual(len(self.drop(frame, "us", now=now)), 2)

    # ---- 미래 날짜 봉 ------------------------------------------------------
    def test_future_dated_bar_is_dropped(self):
        """그 시장에서 아직 시작하지도 않은 날짜의 봉은 남기면 안 된다.

        한국 07:00 예측은 뉴욕 기준 전날 18:00이다. 이때 Yahoo가 '오늘(한국 날짜)' 봉을
        내주면 옛 판정은 그것을 마감된 봉으로 보고 남겼다 — 당일 시각이 마감 이후라
        (18:00 >= 16:05) 시간 조건이 통과했기 때문이다.
        """
        frame = _bars(["2026-09-08", "2026-09-09", "2026-09-10"])
        now = pd.Timestamp("2026-09-09 18:00", tz="America/New_York")   # 한국은 09-10 07:00
        kept = self.drop(frame, "us", now=now)
        self.assertEqual(kept.index[-1], pd.Timestamp("2026-09-09"),
                         "미국 시장에서 아직 열리지 않은 날짜의 봉이 남았다")

    def test_multiple_future_bars_are_all_dropped(self):
        frame = _bars(["2026-09-09", "2026-09-10", "2026-09-11"])
        now = pd.Timestamp("2026-09-09 18:00", tz="America/New_York")
        kept = self.drop(frame, "us", now=now)
        self.assertEqual(list(kept.index), [pd.Timestamp("2026-09-09")])

    def test_dropping_everything_leaves_an_empty_frame_not_an_error(self):
        frame = _bars(["2026-09-11"])
        now = pd.Timestamp("2026-09-09 18:00", tz="America/New_York")
        self.assertTrue(self.drop(frame, "us", now=now).empty)

    # ---- 서머타임 ----------------------------------------------------------
    def test_dst_transition_uses_local_close_not_a_fixed_utc_offset(self):
        """미국 서머타임이 끝나면 16:05 ET가 UTC로는 한 시간 뒤로 밀린다.

        같은 UTC 시각이라도 여름과 겨울에서 '마감했는가'의 답이 달라야 한다.
        고정 오프셋으로 계산했다면 둘 중 하나가 틀린다.
        """
        summer_frame = _bars(["2026-07-01"])
        winter_frame = _bars(["2026-12-01"])
        # 20:30 UTC = 여름 16:30 EDT(마감 후), 겨울 15:30 EST(마감 전)
        summer_now = pd.Timestamp("2026-07-01 20:30", tz="UTC")
        winter_now = pd.Timestamp("2026-12-01 20:30", tz="UTC")
        self.assertEqual(len(self.drop(summer_frame, "us", now=summer_now)), 1,
                         "여름에는 20:30 UTC가 16:30 EDT라 마감 후다")
        self.assertEqual(len(self.drop(winter_frame, "us", now=winter_now)), 0,
                         "겨울에는 20:30 UTC가 15:30 EST라 아직 장중이다")

    def test_korea_close_is_evaluated_in_seoul_time(self):
        frame = _bars(["2026-09-09"])
        before = pd.Timestamp("2026-09-09 15:00", tz="Asia/Seoul")
        after = pd.Timestamp("2026-09-09 15:40", tz="Asia/Seoul")
        self.assertEqual(len(self.drop(frame, "korea", now=before)), 0)
        self.assertEqual(len(self.drop(frame, "korea", now=after)), 1)

    def test_naive_now_is_read_in_the_session_timezone(self):
        """tz 없는 시각을 넘겨도 그 시장의 현지 시각으로 읽는다."""
        frame = _bars(["2026-09-09"])
        self.assertEqual(len(self.drop(frame, "us", now=pd.Timestamp("2026-09-09 15:30"))), 0)
        self.assertEqual(len(self.drop(frame, "us", now=pd.Timestamp("2026-09-09 16:30"))), 1)

    def test_default_now_still_works(self):
        """now를 넘기지 않으면 예전처럼 현재 시각을 쓴다."""
        old = _bars(["2020-01-02"])
        self.assertEqual(len(self.drop(old, "us")), 1)


class AvailabilityToleranceTests(unittest.TestCase):
    """월별 지표를 며칠까지 이어 쓰는가."""

    @classmethod
    def setUpClass(cls):
        cls.ns = load_notebook_functions(["def merge_latest_available"])
        cls.merge = staticmethod(cls.ns["merge_latest_available"])

    def test_value_becomes_available_the_next_day(self):
        base = pd.DatetimeIndex(pd.date_range("2026-03-02", periods=4, freq="D"))
        series = pd.Series([7.0], index=pd.DatetimeIndex(["2026-03-03"]), name="x")
        merged = self.merge(base, series, availability_days=1)
        self.assertTrue(pd.isna(merged.loc["2026-03-03"]), "발표 당일에 이미 쓰이면 누수다")
        self.assertEqual(merged.loc["2026-03-04"], 7.0)

    def test_stale_value_is_not_carried_past_the_tolerance(self):
        """오래된 값을 무한정 끌고 가면 '최신 지표'라는 표시가 거짓이 된다."""
        base = pd.DatetimeIndex(["2026-03-02", "2026-03-20"])
        series = pd.Series([7.0], index=pd.DatetimeIndex(["2026-03-01"]), name="x")
        merged = self.merge(base, series, availability_days=1)
        self.assertEqual(merged.loc["2026-03-02"], 7.0)
        self.assertTrue(pd.isna(merged.loc["2026-03-20"]), "7일 허용치를 넘겨 값이 남았다")


class LaggedVintageLabelTests(unittest.TestCase):
    """released_at이 없는 월별 지표는 '지연 가정 자료'로 표시돼야 한다."""

    def test_notebook_marks_the_history_mode(self):
        text = (ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8")
        self.assertIn("lagged_latest_vintage", text,
                      "최신 수정치를 지연 정렬한 자료라는 표시가 없다")
        self.assertIn("macro_history_mode", text)


if __name__ == "__main__":
    unittest.main()
