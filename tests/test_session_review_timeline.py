"""세로 시간축 회고: 저장 데이터와 표시의 계약."""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_session_review as R


class TimelineDataTests(unittest.TestCase):
    def test_normalizes_and_filters_without_changing_input(self):
        bars = pd.DataFrame({'close': [100, 101, 102, -1, float('nan'), 103, 104],
                             'volume': [1] * 7}, index=pd.to_datetime([
            '2026-09-30 09:05', '2026-09-30 09:00', '2026-09-30 09:00',
            '2026-09-30 09:10', '2026-09-30 09:15', '2026-09-30 16:00',
            '2026-09-29 09:00']).tz_localize('Asia/Seoul'))
        original = bars.copy()
        self.assertTrue(hasattr(R, 'timeline_data'), '차트용 데이터 빌더 필요')
        path, items = R.timeline_data(bars, [], '2026-09-30')
        self.assertEqual([p['price'] for p in path], [102, 100])
        self.assertEqual(path[0]['time'], '2026-09-30T09:00:00+09:00')
        self.assertEqual(items, [])
        pd.testing.assert_frame_equal(bars, original)

    def test_events_use_exact_bar_price_and_time_order(self):
        self.assertTrue(hasattr(R, 'timeline_data'), '차트용 데이터 빌더 필요')
        bars = pd.DataFrame({'close': [100, 103]}, index=pd.date_range(
            '2026-09-30 00:00', periods=2, freq='5min', tz='UTC'))
        events = [{'time': bars.index[i], 'ret': .03, 'z': z, 'volume_ratio': 4,
                   'news': []} for i, z in [(1, 4), (0, 2)]]
        path, items = R.timeline_data(bars, events, '2026-09-30')
        self.assertEqual([i['price'] for i in items], [100, 103])
        self.assertEqual([i['kind'] for i in items], ['volume', 'turn_up'])
        self.assertEqual(path[0]['volume'], None)

    def test_empty_data(self):
        self.assertTrue(hasattr(R, 'timeline_data'), '차트용 데이터 빌더 필요')
        self.assertEqual(R.timeline_data(pd.DataFrame(), [], '2026-09-30'), ([], []))


# ---- 2·3단계(2026-10-02): 세로 시간축 차트 렌더러 ---------------------------------------------------
import re  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forecast_utils as fu  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def chart_review(**changes):
    """09:00~14:55 5분봉(72개), 급락 1곳·급등 1곳. 가격은 100 → 저점 90(10:00) → 110(14:55)."""
    times = pd.date_range('2026-09-30 09:00', periods=72, freq='5min', tz='Asia/Seoul')
    prices = [100 - i * 10 / 12 if i <= 12 else 90 + (i - 12) * 20 / 59 for i in range(72)]
    news = {'time': '2026-09-30T09:50:00+09:00', 'title': '삼성전자 <속보> & 관세', 'source': '한경',
            'link': 'https://example.com/a?x=1&y=2'}
    review = {
        'session_date': '2026-09-30',
        'summary': {'prev_close': 101.0, 'open': 100.0, 'close': 112.0, 'high': 112.0, 'low': 90.0, 'gap': -.0099},
        'intraday_path': [{'time': t.isoformat(), 'price': p, 'volume': 1.0} for t, p in zip(times, prices)],
        'timeline_items': [
            {'time': times[12].isoformat(), 'kind': 'turn_down', 'price': 90.0, 'ret': -.012, 'z': 3.1,
             'volume_ratio': 2.5, 'news': [news]},
            {'time': times[40].isoformat(), 'kind': 'turn_up', 'price': prices[40], 'ret': .008, 'z': 2.7,
             'volume_ratio': 1.9, 'news': [dict(news)]}],
        'overnight_news': [{'time': '2026-09-30T07:10:00+09:00', 'title': '밤사이 기사', 'source': '연합',
                            'link': 'javascript:alert(1)'}],
    }
    review.update(changes)
    return review


class TimelineChartTests(unittest.TestCase):
    def test_time_runs_down_and_price_runs_right(self):
        html = fu.review_timeline_html(chart_review())
        points = [tuple(map(float, p.split(','))) for p in
                  re.search(r'<polyline points="([^"]+)"', html).group(1).split()]
        self.assertEqual(len(points), 72)
        ys = [y for _, y in points]
        self.assertEqual(ys, sorted(ys))                              # 시간은 위에서 아래로
        self.assertAlmostEqual(ys[0], fu._TL_PRE, places=1)           # 09:00 은 개장 전 띠 바로 아래
        self.assertAlmostEqual(ys[12] - ys[0], 60 * fu._TL_PX_PER_MIN, places=1)   # 10:00 은 60분 아래
        xs = [x for x, _ in points]
        self.assertEqual(xs.index(min(xs)), 12)                       # 가장 낮은 가격이 가장 왼쪽
        self.assertEqual(xs.index(max(xs)), 71)
        self.assertTrue(all(0 <= x <= 100 for x in xs))

    def test_event_markers_sit_on_their_bar(self):
        html = fu.review_timeline_html(chart_review())
        points = re.search(r'<polyline points="([^"]+)"', html).group(1).split()
        x, y = map(float, points[12].split(','))
        self.assertIn(f'left:calc({x:.2f}% - 9px);top:{y - 9:.0f}px', html)
        self.assertIn('10:00 ▼ 급락', html)
        self.assertIn('-1.20%', html)
        self.assertIn('12:20 ▲ 급등', html)

    def test_same_article_is_shown_once_and_text_is_escaped(self):
        html = fu.review_timeline_html(chart_review())
        self.assertEqual(html.count('삼성전자 &lt;속보&gt; &amp; 관세'), 1)   # 두 시점에 걸렸지만 처음 한 번만
        self.assertNotIn('<속보>', html)
        self.assertIn('href="https://example.com/a?x=1&amp;y=2"', html)
        self.assertIn('관련 뉴스 없음', html)                         # 두 번째 시점은 남은 기사가 없다

    def test_only_http_links_are_linked(self):
        html = fu.review_timeline_html(chart_review())
        self.assertNotIn('javascript:', html)
        self.assertIn('밤사이 기사', html)

    def test_uncollected_tail_is_not_drawn_as_price(self):
        html = fu.review_timeline_html(chart_review())
        self.assertIn('5분봉 미수집 구간(14:55~15:30)', html)
        self.assertIn('stroke-dasharray="3 3"', html)                 # 일봉 종가까지는 점선
        self.assertEqual(len(re.search(r'<polyline points="([^"]+)"', html).group(1).split()), 72)
        full = chart_review()
        times = pd.date_range('2026-09-30 09:00', periods=79, freq='5min', tz='Asia/Seoul')
        full['intraday_path'] = [{'time': t.isoformat(), 'price': 100.0 + i} for i, t in enumerate(times)]
        self.assertNotIn('미수집 구간', fu.review_timeline_html(full))

    def test_flat_price_and_missing_news_do_not_break(self):
        flat = chart_review(timeline_items=[], overnight_news=None)
        flat['intraday_path'] = [dict(p, price=100.0) for p in flat['intraday_path']]
        flat['summary'] = {'prev_close': 100.0, 'open': 100.0, 'close': 100.0}
        html = fu.review_timeline_html(flat)
        self.assertIn('<polyline', html)
        self.assertNotIn('nan', html.lower())
        self.assertNotIn('tl-card', html.split('</style>', 1)[1])

    def test_old_reviews_without_a_path_render_no_chart(self):
        for review in ({}, {'intraday_path': []}, {'intraday_path': [{'time': 'x', 'price': 1}]}, None):
            self.assertEqual(fu.review_timeline_html(review), '')

    def test_no_script_and_phone_layout_stacks(self):
        html = fu.review_timeline_html(chart_review())
        self.assertNotIn('<script', html)
        self.assertIn('@media (max-width:640px)', html)
        self.assertIn('.tl-card{position:static!important', html)
        self.assertIn('role="img"', html)


class SectionWiringTests(unittest.TestCase):
    """회고 절: 경로가 있으면 차트 + 접은 목록, 없으면 예전 글 목록 그대로."""

    def stored(self, name):
        import json
        path = ROOT / 'forecast_history' / 'samsung' / 'reviews' / name
        if not path.exists():
            self.skipTest(f'{name} 회고 기록 없음')
        return json.loads(path.read_text(encoding='utf-8'))

    def test_review_with_a_path_shows_the_chart_above_the_folded_list(self):
        html = fu.review_section_html(self.stored('2026-10-01.json'))
        self.assertIn('class="tl"', html)
        self.assertIn('시점별 뉴스 전체 목록', html)
        self.assertLess(html.index('class="tl"'), html.index('시점별 뉴스 전체 목록'))
        self.assertLess(html.index('흐름이 바뀐 시각과 그 전후의 뉴스'), html.index('class="tl"'))
        self.assertEqual(html.count('<h3'), 1)

    def test_old_review_keeps_the_plain_list(self):
        html = fu.review_section_html(self.stored('2026-09-23.json'))
        self.assertNotIn('class="tl"', html)
        self.assertNotIn('시점별 뉴스 전체 목록', html)
        self.assertIn('밤사이 (전일 15:30 ~ 09:00)', html)


if __name__ == '__main__':
    unittest.main()
