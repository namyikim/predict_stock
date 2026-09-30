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
