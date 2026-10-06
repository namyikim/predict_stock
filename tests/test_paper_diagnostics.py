"""실제 장부 동작을 통해 무거래·지연 사유와 진단의 부작용을 확인한다."""
import json
import tempfile
import unittest
from pathlib import Path
from tools.paper_trading import PaperBook
from test_paper_trading import CONFIG, quote, signal


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = PaperBook(Path(self.tmp.name)/'book.sqlite', CONFIG)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.book.close)

    def tick(self, now='2026-10-06T00:45:00+00:00', signals=None, quotes=None, **kwargs):
        r = self.book.tick(signals or [], quotes or [], now, kwargs.pop('trading_day', True), **kwargs)
        self.assertIn('diagnostics', r)
        return r

    def reasons(self, result, target='samsung'):
        return result['diagnostics']['targets'][target]['reason_counts']

    def test_delayed_quote_explains_zero_decisions(self):
        r=self.tick('2026-10-06T03:57:57+00:00',[signal()],[quote(ts='2026-10-06T03:38:00+00:00')])
        d=r['diagnostics']['targets']['samsung']
        self.assertEqual(d['quote_age_seconds'],1197)
        self.assertEqual(d['observed_at'],'2026-10-06T03:57:57+00:00')
        self.assertEqual(d['signal_count'],1)
        self.assertEqual(d['eligible_signal_count'],1)
        self.assertEqual(self.reasons(r)['stale_quote'],1)
        self.assertEqual(r['decisions'],[])
        self.assertIsNone(r['diagnostics']['last_success_at'])

    def test_missing_quote_and_signal_are_distinguished(self):
        r=self.tick(quotes=[quote()])
        self.assertIn('no_signal',self.reasons(r))
        self.assertIn('no_quote',self.reasons(r,'sk_hynix'))

    def test_closed_day_and_outside_time(self):
        r=self.tick(quotes=[quote()],trading_day=False)
        self.assertIn('non_trading_day',self.reasons(r))
        r=self.tick('2026-10-06T07:00:00+00:00',quotes=[quote(ts='2026-10-06T07:00:00+00:00')])
        self.assertIn('outside_decision_window',self.reasons(r))

    def test_invalid_signal_is_not_eligible(self):
        r=self.tick(signals=[signal(created='2026-10-06T01:00:00+00:00')],quotes=[quote()])
        self.assertEqual(r['diagnostics']['targets']['samsung']['eligible_signal_count'],0)
        self.assertIn('no_eligible_signal',self.reasons(r))

    def test_repeated_decision_and_expiration_are_reported(self):
        self.tick(signals=[signal()],quotes=[quote()])
        r=self.tick('2026-10-06T01:10:00+00:00',[signal()],[quote(ts='2026-10-06T01:10:00+00:00')])
        self.assertIn('order_expired',self.reasons(r))
        self.assertIn('already_decided',self.reasons(r))
        self.assertEqual(len(r['orders']),1)
        self.assertEqual(r['fills'],[])

    def test_no_room_and_halt_reasons(self):
        r=self.tick(signals=[signal()],quotes=[quote(price=100000)])
        self.assertIn('blocked_by_limit',self.reasons(r))
        self.book.halt('테스트 중지')
        r=self.tick('2026-10-06T00:46:00+00:00',quotes=[quote(ts='2026-10-06T00:46:00+00:00')])
        self.assertIn('halted',self.reasons(r))

    def test_success_means_fresh_quotes_not_orders_and_is_preserved(self):
        r=self.tick(quotes=[quote(),quote('sk_hynix')])
        self.assertEqual(r['diagnostics']['last_success_at'],'2026-10-06T00:45:00+00:00')
        r=self.tick('2026-10-06T01:00:00+00:00')
        self.assertEqual(r['diagnostics']['last_success_at'],'2026-10-06T00:45:00+00:00')
        self.assertEqual(self.book.snapshot()['diagnostics'],r['diagnostics'])
        self.assertEqual(r['cash'],CONFIG['capital'])

    def test_collection_failures_and_eligible_orders(self):
        r=self.tick(signals=[signal()],quotes=[quote()],collection_status={'sk_hynix':{'quote_error':'OSError','signal_error':'FileNotFoundError'}})
        self.assertIn('quote_collection_failed',self.reasons(r,'sk_hynix'))
        self.assertIn('signal_collection_failed',self.reasons(r,'sk_hynix'))
        self.assertIn('order_created',self.reasons(r))
        self.assertEqual(len(r['orders']),1)
        self.assertIsNone(r['diagnostics']['last_success_at'])

    def test_invalid_first_signal_explains_skipped_later_valid_signal(self):
        for bad in ('', 1.5):
            with self.subTest(probability=bad):
                first=dict(signal(),p_up=bad)
                later=signal(created='2026-10-05T23:00:00+00:00')
                r=self.tick(signals=[first,later],quotes=[quote()])
                self.assertEqual(r['diagnostics']['targets']['samsung']['eligible_signal_count'],1)
                self.assertIn('selected_signal_invalid',self.reasons(r))
                self.assertEqual(r['orders'],[])
                self.assertEqual(r['decisions'],[])

    def test_current_no_quote_does_not_look_like_old_success(self):
        self.tick(quotes=[quote(),quote('sk_hynix')])
        r=self.tick('2026-10-06T01:00:00+00:00')
        d=r['diagnostics']['targets']['samsung']
        self.assertIsNone(d['quote_timestamp'])
        self.assertIn('no_quote',d['reason_counts'])

if __name__=='__main__':
    unittest.main()

class CollectorDiagnosticsTests(unittest.TestCase):
    def test_collector_passes_failures_to_persisted_diagnostics(self):
        from unittest.mock import patch
        from tools.run_paper_trading import run
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('tools.run_paper_trading.ROOT',root), patch('tools.run_paper_trading.is_session',return_value=True), patch('tools.run_paper_trading.fetch_quote',side_effect=OSError('offline')), patch('tools.run_paper_trading.collect_daily',side_effect=OSError('offline')):
                result=run(CONFIG,root/'book.sqlite',root/'status.json')
            counts=result['diagnostics']['targets']['samsung']['reason_counts']
            self.assertIn('quote_collection_failed',counts)
            self.assertIn('signal_collection_failed',counts)
            self.assertEqual(len(result['market_errors']),2)
            self.assertIsNone(result['diagnostics']['last_success_at'])
            self.assertEqual(json.loads((root/'status.json').read_text())['diagnostics'],result['diagnostics'])
