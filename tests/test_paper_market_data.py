"""XKRX 시각과 최초 수집 시각을 지켜 재생에 미래 자료가 섞이지 않는다."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.find_spec('tools.paper_market_data')
if SPEC:
    from tools import paper_market_data as market


def bar(ts='2026-10-06T09:00:00+09:00', **changes):
    row=dict(timestamp=ts,open=100,high=102,low=99,close=101,volume=10,
             source='test',adjustment='unadjusted',split=0,dividend=0)
    return dict(row,**changes)


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SPEC,'시세 기록 모듈이 필요합니다')
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def normalize(self, rows, timeframe='1m', observed='2026-10-06T09:03:00+09:00'):
        return market.normalize_bars(rows,target='samsung',timeframe=timeframe,observed_at=observed)

    def test_completed_sorted_deduplicated_and_delayed_bars(self):
        rows=self.normalize([bar('2026-10-06T09:01:00+09:00'),bar(),bar(),bar('2026-10-06T09:03:00+09:00')])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['bar_end'],'2026-10-06T00:01:00+00:00')
        self.assertEqual(rows[0]['observed_at'],'2026-10-06T00:03:00+00:00')
        self.assertEqual(rows[0]['session'],'2026-10-06')

    def test_bad_prices_volume_adjustment_and_conflicting_duplicates(self):
        for changes in [dict(open=0),dict(high=98),dict(volume=-1),dict(close=float('nan')),dict(adjustment='adjusted')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.normalize([bar(**changes)])
        with self.assertRaises(ValueError):self.normalize([bar(),bar(close=100)])
        with self.assertRaises(ValueError):self.normalize([bar('2026-10-06T09:00:00')])

    def test_holiday_future_and_unfinished_daily_are_excluded(self):
        self.assertEqual(self.normalize([bar('2026-10-03T09:00:00+09:00')]),[])
        self.assertEqual(self.normalize([bar('2026-10-07T09:00:00+09:00')]),[])
        self.assertEqual(self.normalize([bar('2026-10-06T00:00:00+09:00')],'1d'),[])
        rows=self.normalize([bar('2026-10-06T00:00:00+09:00')],'1d','2026-10-06T16:00:00+09:00')
        self.assertEqual(rows[0]['bar_end'],'2026-10-06T06:30:00+00:00')

    def test_available_respects_observation_and_revision_times(self):
        rows=self.normalize([bar()])
        revised=self.normalize([bar(close=102)],observed='2026-10-06T09:05:00+09:00')
        self.assertEqual(market.available_bars(rows,decision_at='2026-10-06T09:02:00+09:00'),[])
        first=market.available_bars(rows+revised,decision_at='2026-10-06T09:04:00+09:00')
        self.assertEqual(first[0]['close'],101)
        self.assertEqual(market.available_bars(rows+revised,decision_at='2026-10-06T09:05:00+09:00')[0]['close'],102)

    def test_archive_keeps_versions_and_retries_do_not_duplicate(self):
        rows=self.normalize([bar()]);q=market.archive_bars(rows,root=self.root,target='samsung',timeframe='1m',observed_at=rows[0]['observed_at'])
        market.archive_bars(rows,root=self.root,target='samsung',timeframe='1m',observed_at=rows[0]['observed_at'])
        self.assertEqual(len(market.read_archive(self.root,target='samsung',timeframe='1m')),1)
        revised=self.normalize([bar(close=102)],observed='2026-10-06T09:05:00+09:00')
        q=market.archive_bars(revised,root=self.root,target='samsung',timeframe='1m',observed_at=revised[0]['observed_at'])
        self.assertEqual(len(market.read_archive(self.root,target='samsung',timeframe='1m')),2)
        self.assertEqual(q['collection_interval_seconds'],120)
        self.assertEqual(q['missing_bars'],4)
        self.assertEqual(q['expected_bars'],5)
        self.assertEqual(q['coverage_basis'],'이번 수집 응답의 완료 봉')

    def test_backfill_and_corporate_action_are_explicit(self):
        rows=self.normalize([bar('2026-10-02T00:00:00+09:00',split=2)],'1d','2026-10-06T16:00:00+09:00')
        self.assertEqual(rows[0]['provenance'],'historical_backfill')
        self.assertTrue(rows[0]['requires_corporate_action_adjustment'])
        self.assertEqual(market.available_bars(rows,decision_at='2026-10-06T16:00:00+09:00'),[])

    def test_split_resets_unadjusted_window_not_just_event_row(self):
        rows=self.normalize([bar('2026-10-01T00:00:00+09:00'),bar('2026-10-02T00:00:00+09:00',split=2),bar('2026-10-06T00:00:00+09:00')],'1d','2026-10-06T16:00:00+09:00')
        usable=market.available_bars(rows,decision_at='2026-10-06T16:00:00+09:00')
        self.assertEqual([x['session'] for x in usable],['2026-10-06'])

    def test_archive_rejects_time_travel_and_same_timestamp_rewrite(self):
        rows=self.normalize([bar()]);market.archive_bars(rows,root=self.root,target='samsung',timeframe='1m',observed_at=rows[0]['observed_at'])
        for observed in ['2026-10-06T09:02:00+09:00','2026-10-06T09:03:00+09:00']:
            with self.subTest(observed=observed),self.assertRaises(ValueError):
                revision=self.normalize([bar(close=102)],observed=observed)
                market.archive_bars(revision,root=self.root,target='samsung',timeframe='1m',observed_at=observed)

class MarketCollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SPEC)

    def test_quote_archives_full_minute_data_and_daily_keeps_separate_errors(self):
        import pandas as pd
        from unittest.mock import patch
        from tools.run_paper_trading import fetch_quote
        frame=pd.DataFrame({'Open':[100,101], 'High':[102,103], 'Low':[99,100],
                            'Close':[101,102], 'Volume':[10,12]},
                           index=pd.to_datetime(['2026-10-06T09:00:00+09:00','2026-10-06T09:01:00+09:00']))
        import tools.run_paper_trading as runner
        self.assertTrue(hasattr(runner,'fetch_bars'))
        with tempfile.TemporaryDirectory() as folder, patch.object(runner,'fetch_bars',return_value=frame):
            q=fetch_quote('samsung',archive_root=folder,observed_at='2026-10-06T09:03:00+09:00')
            rows=market.read_archive(folder,target='samsung',timeframe='1m')
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['open'],100)
            self.assertEqual(q['price'],102)
            self.assertEqual(q['market_quality']['missing_bars'],1)

    def test_daily_collects_again_after_close_then_skips_repeat(self):
        import tools.run_paper_trading as runner
        import pandas as pd
        from unittest.mock import patch
        self.assertTrue(hasattr(runner,'collect_daily'))
        frame=pd.DataFrame({'Open':[100,101], 'High':[102,103], 'Low':[99,100],
                            'Close':[101,102], 'Volume':[10,12]},
                           index=pd.to_datetime(['2026-10-02T00:00:00+09:00','2026-10-06T00:00:00+09:00']))
        with tempfile.TemporaryDirectory() as folder, patch.object(runner,'fetch_bars',return_value=frame) as fetch:
            runner.collect_daily('samsung',folder,observed_at='2026-10-06T09:45:00+09:00')
            runner.collect_daily('samsung',folder,observed_at='2026-10-06T10:00:00+09:00')
            self.assertEqual(fetch.call_count,1)
            runner.collect_daily('samsung',folder,observed_at='2026-10-06T15:45:00+09:00')
            runner.collect_daily('samsung',folder,observed_at='2026-10-06T15:50:00+09:00')
            self.assertEqual(fetch.call_count,2)
            rows=market.read_archive(folder,target='samsung',timeframe='1d')
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[-1]['adjustment'],'yahoo_auto_adjust_false')
            self.assertEqual(rows[-1]['bar_end'],'2026-10-06T06:30:00+00:00')

    def test_workflow_saves_market_archive_and_recovery_copy(self):
        content=(Path(__file__).resolve().parents[1]/'.github/workflows/paper-trading.yml').read_text()
        self.assertIn('git add paper_history docs/lab/paper_status.json',content)
        self.assertIn('cp -R paper_history/market',content)

if __name__=='__main__':unittest.main()
