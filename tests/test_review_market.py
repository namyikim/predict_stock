"""장 회고의 '시장 전체' 칸(2026-10-02). 장 마감 회고 영상과 비교해 더했다.

영상은 종목보다 먼저 시장 전체를 본다 — 오른·내린 종목 수, 시장 전체 투자자별 순매수, 지수가 어디서 끝났는지.
회고에는 이 종목의 수급만 있었다. 숫자는 그대로 옮기고, 이 종목이 움직인 이유라고 말하지 않는다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import forecast_utils as fu  # noqa: E402
import build_session_review as R  # noqa: E402

INTEGRATION = {
    "totalInfos": [{"code": "lastClosePrice", "key": "전일", "value": "6,971.35"},
                   {"code": "openPrice", "key": "시가", "value": "6,938.27"},
                   {"code": "highPrice", "key": "고가", "value": "7,011.04"},
                   {"code": "lowPrice", "key": "저가", "value": "6,927.88"}],
    "dealTrendInfo": {"bizdate": "20261002", "personalValue": "-17,725", "foreignValue": "-1,083",
                      "institutionalValue": "+3,987"},
    "upDownStockInfo": {"upperCount": "1", "riseCount": "500", "lowerCount": "0", "fallCount": "362",
                        "steadyCount": "56"},
}
BASIC = {"closePrice": "7,003.74"}


def fetcher(integration=INTEGRATION, basic=BASIC, fail=()):
    def fetch(kind):
        if kind in fail:
            raise RuntimeError("HTTP 500")
        return integration if kind == "integration" else basic
    return fetch


class ParseTests(unittest.TestCase):
    def test_counts_flows_and_index_levels(self):
        m = R.parse_market_overview(INTEGRATION, BASIC)
        self.assertEqual((m["date"], m["rise"], m["fall"], m["steady"], m["upper"], m["lower"]),
                         ("2026-10-02", 500, 362, 56, 1, 0))
        self.assertEqual((m["individual"], m["foreign"], m["institution"]), (-17725.0, -1083.0, 3987.0))
        self.assertEqual((m["prev_close"], m["open"], m["high"], m["low"], m["close"]),
                         (6971.35, 6938.27, 7011.04, 6927.88, 7003.74))

    def test_unreadable_payloads_give_nothing(self):
        for payload in (None, {}, {"upDownStockInfo": {}, "dealTrendInfo": {"bizdate": "20261002"}},
                        dict(INTEGRATION, dealTrendInfo={"bizdate": "오늘"})):
            self.assertIsNone(R.parse_market_overview(payload, BASIC))


class FetchTests(unittest.TestCase):
    def test_only_the_session_day_is_used(self):
        """API 는 최신 거래일만 준다. 지난 날짜 회고를 다시 만들 때 오늘 숫자를 끼워 넣지 않는다."""
        self.assertEqual(R.fetch_market_overview("2026-10-02", fetch=fetcher())["rise"], 500)
        self.assertIsNone(R.fetch_market_overview("2026-10-01", fetch=fetcher()))

    def test_failures_do_not_stop_the_review(self):
        self.assertIsNone(R.fetch_market_overview("2026-10-02", fetch=fetcher(fail=("integration",))))
        partial = R.fetch_market_overview("2026-10-02", fetch=fetcher(fail=("basic",)))
        self.assertEqual(partial["fall"], 362)            # 종가를 못 받아도 종목 수·수급은 보인다
        self.assertIsNone(partial["close"])

    def test_review_stores_it(self):
        source = (ROOT / "tools" / "build_session_review.py").read_text(encoding="utf-8")
        self.assertIn('"market": fetch_market_overview(session_date) if use_news else None,', source)


class RenderTests(unittest.TestCase):
    def market(self, **changes):
        return dict(R.parse_market_overview(INTEGRATION, BASIC), **changes)

    def test_block_shows_breadth_flows_and_where_the_index_closed(self):
        html = fu.review_market_html(self.market())
        for text in ("시장 전체 (코스피)", "오른 종목이 더 많았습니다", "상승 500", "보합 56", "하락 362", "상한가 1",
                     "−1.77조원", "−1,083억원", "+3,987억원", "7,003.74", "+32.39p(+0.46%)",
                     "장중 저점 대비 +75.86p", "고가 부근 마감", "저가 6,927.88", "고가 7,011.04"):
            self.assertIn(text, html)
        self.assertNotIn("하한가", html)                                  # 0 이면 적지 않는다
        self.assertIn("이 종목이 움직인 이유를 말하지 않습니다", html)
        self.assertIn("flex:500 1 0", html)                              # 막대 폭은 종목 수 비율
        self.assertIn("flex:362 1 0", html)
        self.assertNotIn("<h3", html)
        self.assertNotIn("<script", html)

    def test_more_decliners_and_low_close_are_worded_the_other_way(self):
        html = fu.review_market_html(self.market(rise=200, fall=700, close=6930.0))
        self.assertIn("내린 종목이 더 많았습니다", html)
        self.assertIn("저가 부근 마감", html)

    def test_missing_pieces_are_left_out(self):
        html = fu.review_market_html(self.market(close=None, individual=None, foreign=None, institution=None))
        self.assertIn("상승 500", html)
        self.assertNotIn("순매수</div>", html)
        self.assertNotIn("지수 종가", html)
        self.assertNotIn("nan", html.lower())
        for empty in (None, {}, {"rise": None, "fall": 3}, {"rise": 0, "fall": 0, "steady": 0}):
            self.assertEqual(fu.review_market_html(empty), "")

    def test_section_puts_the_market_first_and_old_reviews_are_unchanged(self):
        import json
        path = ROOT / "forecast_history" / "samsung" / "reviews" / "2026-10-01.json"
        if not path.exists():
            self.skipTest("회고 기록 없음")
        review = json.loads(path.read_text(encoding="utf-8"))
        without = fu.review_section_html(review)
        self.assertNotIn("시장 전체 (코스피)", without)                  # market 이 없는 옛 회고는 그대로
        with_market = fu.review_section_html(dict(review, market=self.market()))
        self.assertIn("시장 전체 (코스피)", with_market)
        self.assertLess(with_market.index("시장 전체 (코스피)"), with_market.index("흐름이 바뀐 시각과 그 전후의 뉴스"))
        self.assertEqual(with_market.count("<h3"), 1)


class CloseLocationTests(unittest.TestCase):
    """이 종목의 종가가 당일 범위의 어디였나 — 영상의 '종가가 고가 부근에서 마무리'."""

    def seen(self, **summary):
        base = {"c2c": .01, "high": 277000.0, "low": 271500.0, "close": 276000.0}
        base.update(summary)
        return [x for x in fu.market_observations(base) if "부근에서 마감" in x]

    def test_close_near_the_high_or_low_is_stated(self):
        self.assertEqual(len(self.seen()), 1)
        self.assertIn("위쪽 82% 지점 — 고가 부근에서 마감", self.seen()[0])
        self.assertIn("아래쪽 9% 지점 — 저가 부근에서 마감", self.seen(close=272000.0)[0])

    def test_middle_closes_and_tiny_ranges_say_nothing(self):
        self.assertEqual(self.seen(close=274000.0), [])
        self.assertEqual(self.seen(high=276100.0, low=275900.0), [])      # 범위가 거의 없던 날
        self.assertEqual(self.seen(high=None), [])


if __name__ == "__main__":
    unittest.main()
