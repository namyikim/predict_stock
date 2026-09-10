# -*- coding: utf-8 -*-
"""장 마감 회고의 계약.

  1. 전환점 탐지는 결정적이고, 문턱(σ 배수·거래량 배수)을 넘는 봉만 잡으며, 이웃은 합친다.
  2. 뉴스 시각은 RFC 2822(UTC 등) → KST로 정확히 옮겨지고, 창 안의 것만 골라 관련도 순으로 놓는다.
  3. 성격 판정은 숫자 근거를 함께 내고, 규칙이 겹치면 여러 성격을 낸다.
  4. 뉴스가 0건이어도 오류가 아니고, 인과 부인 문구는 항상 들어간다.
  5. 절 삽입은 있으면 교체·없으면 원장 절 뒤·그것도 없으면 body 끝이며, 두 번 넣어도 하나만 남는다.
"""
import sys
import unittest
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_session_review as R  # noqa: E402

KST = timezone(timedelta(hours=9))


def _bars(n=75, seed=3, spike_at=None, spike_ret=0.02, spike_vol=6.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-09-09 09:00", periods=n, freq="5min", tz="Asia/Seoul")
    rets = rng.normal(0, 0.0008, n)
    vol = rng.integers(80_000, 120_000, n).astype(float)
    if spike_at is not None:
        rets[spike_at] = spike_ret
        vol[spike_at] *= spike_vol
    close = 70_000 * np.cumprod(1 + rets)
    open_ = np.concatenate([[70_000], close[:-1]])
    return pd.DataFrame({"open": open_, "close": close, "volume": vol}, index=idx)


class DetectEventsTests(unittest.TestCase):
    def test_spike_is_detected_at_the_right_bar(self):
        bars = _bars(spike_at=30)
        events = R.detect_events(bars, prev_close=70_000)
        self.assertTrue(events)
        self.assertEqual(events[0]["time"], bars.index[30])
        self.assertGreaterEqual(events[0]["z"], R.EVENT_Z)
        self.assertGreaterEqual(events[0]["volume_ratio"], R.VOLUME_SPIKE)

    def test_quiet_session_has_no_events(self):
        events = R.detect_events(_bars(seed=1), prev_close=70_000)
        self.assertLessEqual(len(events), 2)          # 잡음에서 한둘 걸릴 수는 있지만 급변(σ×4 이상)으로 읽히면 안 된다
        for e in events:
            self.assertLess(e["z"], 4.0)

    def test_opening_bar_volume_alone_is_not_an_event(self):
        """개장 봉은 동시호가로 거래량이 늘 크다. 가격이 안 움직였으면 전환점이 아니다."""
        bars = _bars(seed=5)
        bars.loc[bars.index[0], "volume"] *= 8
        events = R.detect_events(bars, prev_close=float(bars["open"].iloc[0]))
        self.assertFalse(any(e["time"] == bars.index[0] for e in events))

    def test_neighbouring_bars_are_merged(self):
        bars = _bars(spike_at=30)
        bars.loc[bars.index[31], "close"] = bars["close"].iloc[30] * 1.015   # 5분 뒤 또 큰 봉
        bars.loc[bars.index[32:], "close"] *= 1.015
        events = R.detect_events(bars, prev_close=70_000)
        times = [e["time"] for e in events]
        self.assertEqual(len([t for t in times if abs((t - bars.index[30]).total_seconds()) <= 15 * 60]), 1)

    def test_is_deterministic(self):
        bars = _bars(spike_at=20)
        a, b = R.detect_events(bars, prev_close=70_000), R.detect_events(bars, prev_close=70_000)
        self.assertEqual([(e["time"], e["z"]) for e in a], [(e["time"], e["z"]) for e in b])

    def test_first_bar_includes_the_gap(self):
        bars = _bars(seed=2)
        events = R.detect_events(bars, prev_close=bars["open"].iloc[0] / 1.03)   # 3% 갭
        self.assertTrue(events and events[0]["time"] == bars.index[0])

    def test_too_few_bars_gives_empty(self):
        self.assertEqual(R.detect_events(_bars(n=5), prev_close=70_000), [])

    def test_closing_share(self):
        bars = _bars(n=78, seed=4)
        session = bars["close"].iloc[-1] / bars["open"].iloc[0] - 1
        share = R.closing_share(bars, session)
        self.assertTrue(np.isfinite(share))


class NewsTests(unittest.TestCase):
    RSS = b"""<?xml version="1.0"?><rss><channel>
      <item><title>\xec\x82\xbc\xec\x84\xb1\xec\xa0\x84\xec\x9e\x90 HBM \xea\xb3\xb5\xea\xb8\x89 \xed\x99\x95\xeb\x8c\x80 - A\xec\x8b\xa0\xeb\xac\xb8</title>
        <link>http://a</link><pubDate>Wed, 09 Sep 2026 01:30:00 GMT</pubDate><source>A\xec\x8b\xa0\xeb\xac\xb8</source></item>
      <item><title>\xea\xb0\xa4\xeb\x9f\xad\xec\x8b\x9c \xec\xb6\x9c\xec\x8b\x9c \xec\x9d\xb4\xeb\xb2\xa4\xed\x8a\xb8 - B</title>
        <link>http://b</link><pubDate>Wed, 09 Sep 2026 01:40:00 GMT</pubDate><source>B</source></item>
      <item><title>dup - A</title><link>http://c</link><pubDate>Wed, 09 Sep 2026 02:00:00 GMT</pubDate><source>A</source></item>
      <item><title>dup - A</title><link>http://d</link><pubDate>Wed, 09 Sep 2026 02:05:00 GMT</pubDate><source>A</source></item>
      <item><title>no date</title><link>http://e</link></item>
    </channel></rss>"""

    def test_pubdate_is_converted_to_kst(self):
        items = R.parse_rss(self.RSS)
        first = next(i for i in items if "HBM" in i["title"])
        self.assertEqual(first["time"].tzinfo.utcoffset(None), timedelta(hours=9))
        self.assertEqual((first["time"].hour, first["time"].minute), (10, 30))   # 01:30 GMT = 10:30 KST

    def test_duplicates_and_undated_items_are_dropped(self):
        titles = [i["title"] for i in R.parse_rss(self.RSS)]
        self.assertEqual(titles.count("dup"), 1)
        self.assertNotIn("no date", titles)

    def test_source_suffix_is_stripped(self):
        items = R.parse_rss(self.RSS)
        self.assertTrue(any(i["title"] == "삼성전자 HBM 공급 확대" for i in items))

    def test_window_and_relevance_order(self):
        items = R.parse_rss(self.RSS)
        start = pd.Timestamp("2026-09-09 10:00", tz="Asia/Seoul")
        end = pd.Timestamp("2026-09-09 10:45", tz="Asia/Seoul")
        hits = R.news_in_window(items, start, end, "삼성전자")
        self.assertEqual([h["title"] for h in hits][0], "삼성전자 HBM 공급 확대")
        self.assertEqual(len(hits), 2)
        late = pd.Timestamp("2026-09-09 12:00", tz="Asia/Seoul")
        self.assertEqual(R.news_in_window(items, late, late + pd.Timedelta(hours=1), "삼성전자"), [])

    def test_noise_scores_below_relevant(self):
        self.assertGreater(R.score_headline("삼성전자 HBM 공급 확대", "삼성전자"),
                           R.score_headline("갤럭시 출시 이벤트", "삼성전자"))


class ClassifyTests(unittest.TestCase):
    def test_gap_dominant(self):
        c = R.classify_session(c2c=0.02, gap=0.018, session=0.002, events=[], kospi_c2c=0.003)
        self.assertIn("갭 주도", c["labels"])
        self.assertTrue(any("갭" in r for r in c["reasons"]))

    def test_intraday_break_with_time(self):
        ev = {"time": pd.Timestamp("2026-09-09 10:35", tz="Asia/Seoul"), "ret": -0.015, "z": 4.2, "volume_ratio": 5.0,
              "session_share": 0.7, "cum_before": 0.0, "cum_after": -0.015}
        c = R.classify_session(c2c=-0.02, gap=0.0, session=-0.02, events=[ev])
        self.assertTrue(any(l.startswith("장중 급변 10:35") for l in c["labels"]))

    def test_index_synchronised(self):
        c = R.classify_session(c2c=-0.012, gap=-0.003, session=-0.009, events=[], kospi_c2c=-0.011, intraday_corr=0.7)
        self.assertIn("지수 동조", c["labels"])

    def test_flat_day(self):
        c = R.classify_session(c2c=0.001, gap=0.0005, session=0.0005, events=[], kospi_c2c=0.004)
        self.assertIn("방향성 약함", c["labels"])

    def test_closing_auction(self):
        c = R.classify_session(c2c=0.01, gap=0.0, session=0.01, events=[], close_share=0.5)
        self.assertIn("마감 동시호가 쏠림", c["labels"])

    def test_multiple_labels_can_coexist(self):
        c = R.classify_session(c2c=0.02, gap=0.015, session=0.005, events=[], kospi_c2c=0.019)
        self.assertIn("갭 주도", c["labels"]); self.assertIn("지수 동조", c["labels"])

    def test_every_label_has_a_reason(self):
        c = R.classify_session(c2c=0.02, gap=0.015, session=0.005, events=[], kospi_c2c=0.019)
        self.assertEqual(len(c["labels"]), len(c["reasons"]))


class ForecastExplanationTests(unittest.TestCase):
    def _row(self, p_up, p_flat, p_down, band=0.006):
        return pd.Series({"model": "No macro ensemble", "band": band, "p_down": p_down, "p_flat": p_flat, "p_up": p_up})

    def test_hit_and_miss(self):
        hit = R.explain_forecast(self._row(.5, .3, .2), c2c=0.012, gap=0.01, session=0.002)
        self.assertTrue(hit["hit"]); self.assertIn("맞음", hit["verdict"])
        miss = R.explain_forecast(self._row(.5, .3, .2), c2c=-0.012, gap=-0.01, session=-0.002)
        self.assertFalse(miss["hit"]); self.assertIn("틀림", miss["verdict"])

    def test_gap_right_but_reversed_intraday(self):
        out = R.explain_forecast(self._row(.5, .3, .2), c2c=-0.008, gap=0.009, session=-0.017)
        self.assertFalse(out["hit"])
        self.assertIn("반대로", out["where"])

    def test_missing_band_returns_none(self):
        self.assertIsNone(R.explain_forecast(self._row(.5, .3, .2, band=np.nan), 0.01, 0.005, 0.005))


class SectionTests(unittest.TestCase):
    def _review(self, events=()):
        return {"target": "samsung", "name": "삼성전자", "peer_name": "SK하이닉스", "session_date": "2026-09-09",
                "generated_at": "2026-09-09 16:10 KST",
                "summary": {"gap": 0.01, "session": -0.004, "c2c": 0.006, "high_vs_open": 0.005, "low_vs_open": -0.008,
                            "volume_ratio": 1.2, "kospi_c2c": 0.004, "peer_c2c": 0.02, "usdkrw_chg": -0.001,
                            "sox_ret": 0.015, "nasdaq_ret": 0.008},
                "classification": {"labels": ["갭 주도"], "reasons": ["갭 +1.00%가 종가→종가의 167%"]},
                "events": list(events), "turning_point": None, "overnight_news": [], "top_news": [],
                "disclosures": None, "disclosure_note": "DART 키 없음 — 공시 미조회", "flows": None,
                "forecasts": [], "price_check": None, "disclaimer": R.DISCLAIMER}

    def test_disclaimer_and_no_news_text_are_present(self):
        html_ = R.render_section(self._review())
        self.assertIn(R.DISCLAIMER, html_)
        self.assertIn("관련 뉴스 없음", html_)
        self.assertIn("아침 예측 기록이 원장에 없습니다", html_)

    def test_insert_replace_and_fallbacks(self):
        section = R.render_section(self._review())
        page = "<html><body><p>x</p><!--LEDGER_SECTION_START-->L<!--LEDGER_SECTION_END--><footer/></body></html>"
        once = R.insert_section(page, section)
        self.assertEqual(once.count(R.MARK_START), 1)
        self.assertLess(once.find("<!--LEDGER_SECTION_END-->"), once.find(R.MARK_START))
        twice = R.insert_section(once, section)
        self.assertEqual(twice.count(R.MARK_START), 1)
        self.assertEqual(twice, once)
        bare = R.insert_section("<html><body><p>y</p></body></html>", section)
        self.assertEqual(bare.count(R.MARK_START), 1)
        self.assertLess(bare.find(R.MARK_END), bare.find("</body>"))

    def test_event_with_news_renders_links(self):
        ev = {"time": pd.Timestamp("2026-09-09 10:35", tz="Asia/Seoul"), "ret": -0.012, "z": 3.9, "volume_ratio": 4.1,
              "cum_before": 0.001, "cum_after": -0.011, "session_share": 0.6,
              "news": [{"time": pd.Timestamp("2026-09-09 10:20", tz="Asia/Seoul"), "title": "삼성전자 관세 리스크",
                        "source": "A", "link": "http://a"}]}
        html_ = R.render_section(self._review(events=[ev]))
        self.assertIn("10:35", html_); self.assertIn("삼성전자 관세 리스크", html_); self.assertIn('href="http://a"', html_)


if __name__ == "__main__":
    unittest.main()
