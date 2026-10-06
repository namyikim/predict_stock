"""검증되지 않은 가격을 체결 자료로 승격하지 않는다."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

NOW = '2026-10-06T20:00:00+09:00'


def row(day='2026-10-06', **extra):
    return dict(dict(timestamp=day+'T00:00:00+09:00', open=100, high=103, low=99,
                     close=102, volume=1000), **extra)


class CashPriceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.collect_cash_prices'),
                             '별도 현금 가격 검증 수집기가 필요합니다')
        from tools import collect_cash_prices
        self.m = collect_cash_prices
        self.raw = [row()]
        self.yahoo = [row(split=0, dividend=0)]

    def verify(self, raw=None, yahoo=None, at=NOW):
        return self.m.verify_cash_bars(self.raw if raw is None else raw,
                                      self.yahoo if yahoo is None else yahoo,
                                      target='samsung', observed_at=at)

    def test_matching_prices_keep_proof_and_real_observation_time(self):
        rows, issues = self.verify()
        self.assertEqual(issues, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['adjustment'], 'unadjusted')
        self.assertEqual(rows[0]['observed_at'], '2026-10-06T11:00:00+00:00')
        self.assertEqual(rows[0]['cash_verification']['method'], 'krx_yahoo_ohlc_v1')
        self.assertEqual(rows[0]['cash_verification']['krx_ohlc'], [100,103,99,102])
        self.assertEqual(rows[0]['cash_verification']['yahoo_ohlc'], [100,103,99,102])

    def test_each_price_mismatch_missing_source_and_events_are_rejected(self):
        for key in ('open','high','low','close'):
            yahoo = copy.deepcopy(self.yahoo); yahoo[0][key] += 1
            rows, issues = self.verify(yahoo=yahoo)
            self.assertEqual(rows, [], key)
            self.assertTrue(issues)
        for yahoo in ([], [row(split=2,dividend=0)], [row(split=0,dividend=1)], [row()]):
            rows, issues = self.verify(yahoo=yahoo)
            self.assertEqual(rows, [])
            self.assertTrue(issues)

    def test_invalid_duplicate_zero_volume_and_unfinished_are_not_prices(self):
        for raw in ([row(volume_bad=0,close=float('nan'))], self.raw*2,
                    [dict(self.raw[0],volume=0)]):
            with self.subTest(raw=raw):
                rows, issues=self.verify(raw=raw)
                self.assertEqual(rows, [])
                self.assertTrue(issues)
        self.assertEqual(self.verify(at='2026-10-06T12:00:00+09:00')[0], [])

    def test_backfill_is_not_backdated(self):
        rows, _ = self.verify(raw=[row('2026-10-02')],yahoo=[row('2026-10-02',split=0,dividend=0)])
        self.assertEqual(rows[0]['provenance'], 'historical_backfill')
        from tools.paper_market_data import available_bars
        self.assertEqual(available_bars(rows,decision_at='2026-10-02T16:00:00+09:00'), [])

    def test_collection_is_separate_once_per_completed_session_and_failure_visible(self):
        from tools.paper_market_data import read_archive
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(self.m,'fetch_sources',return_value=(self.raw,self.yahoo)) as fetch:
                first=self.m.collect_target('samsung',root=root,now=NOW)
                again=self.m.collect_target('samsung',root=root,now=NOW)
                self.assertEqual(first['accepted_bars'],1)
                self.assertEqual(fetch.call_count,1)
                self.assertTrue(again['skipped'])
            self.assertEqual(len(read_archive(root/'paper_history/market_cash',target='samsung',timeframe='1d')),1)
            self.assertFalse((root/'paper_history/market').exists())
            with patch.object(self.m,'fetch_sources',side_effect=TimeoutError):
                failed=self.m.collect_target('sk_hynix',root=root,now=NOW)
                self.assertEqual(failed['status'],'unavailable')
                self.assertIn('TimeoutError',failed['error'])
                self.assertTrue(self.m.collect_target('sk_hynix',root=root,now=NOW)['skipped'])

    def test_evaluator_never_falls_back_to_unverified_archive(self):
        from tools import evaluate_wave_trading as evaluator
        self.assertTrue(hasattr(evaluator,'validation_bars'))
        from tools.paper_market_data import archive_bars
        with tempfile.TemporaryDirectory() as tmp:
            rows,_=self.verify()
            archive_bars(rows,root=Path(tmp)/'paper_history/market',target='samsung',timeframe='1d',observed_at=rows[0]['observed_at'])
            self.assertEqual(evaluator.validation_bars(Path(tmp),'samsung'),[])
            archive_bars(rows,root=Path(tmp)/'paper_history/market_cash',target='samsung',timeframe='1d',observed_at=rows[0]['observed_at'])
            self.assertEqual(evaluator.validation_bars(Path(tmp),'samsung'),rows)

    def test_workflow_orders_collection_and_preserves_recovery(self):
        import yaml
        data=yaml.safe_load(Path('.github/workflows/paper-trading.yml').read_text())
        steps=data['jobs']['observe']['steps']; names=[s.get('id') for s in steps]
        self.assertLess(names.index('observe'),names.index('cash_prices'))
        self.assertLess(names.index('cash_prices'),names.index('wave_validation'))
        step=steps[names.index('cash_prices')]
        self.assertTrue(step['continue-on-error'])
        self.assertIn('market_cash',str(steps))

    def test_provider_requests_unadjusted_prices_and_keeps_missing_actions_error(self):
        import pandas as pd
        import types
        import sys
        from unittest.mock import Mock
        krx=Mock(return_value=pd.DataFrame({'시가':[100],'고가':[103],'저가':[99],'종가':[102],'거래량':[1000]},index=pd.to_datetime(['2026-10-06'])))
        history=Mock(return_value=pd.DataFrame({'Open':[100],'High':[103],'Low':[99],'Close':[102],'Volume':[1000],'Stock Splits':[0],'Dividends':[0]},index=pd.to_datetime(['2026-10-06T00:00:00+09:00'])))
        yf=types.SimpleNamespace(Ticker=lambda _:types.SimpleNamespace(history=history))
        with patch.dict(sys.modules,{'pykrx':types.SimpleNamespace(stock=types.SimpleNamespace(get_market_ohlcv=krx)),'yfinance':yf}):
            a,b=self.m._provider_rows('samsung','2026-10-01','2026-10-06')
        self.assertFalse(krx.call_args.kwargs['adjusted'])
        self.assertFalse(history.call_args.kwargs['auto_adjust'])
        self.assertEqual(len(self.verify(a,b)[0]),1)
        self.assertEqual(history.call_args.kwargs['end'],'2026-10-07')

    def test_next_completed_session_retries_but_same_day_revisions_keep_history(self):
        from tools.paper_market_data import read_archive
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(self.m,'fetch_sources',return_value=(self.raw,self.yahoo)):
                self.m.collect_target('samsung',root=root,now=NOW)
            with patch.object(self.m,'fetch_sources',return_value=([dict(self.raw[0],close=103)],[dict(self.yahoo[0],close=103)])):
                self.m.collect_target('samsung',root=root,now='2026-10-07T10:00:00+09:00')
            rows=read_archive(root/'paper_history/market_cash',target='samsung',timeframe='1d')
            self.assertEqual([r['close'] for r in rows],[102,103])
            self.assertLess(rows[0]['observed_at'],rows[1]['observed_at'])

    def test_source_process_timeout_is_bounded_and_does_not_touch_legacy_book(self):
        import subprocess
        import os
        with patch.dict(os.environ,{'KRX_ID':'test','KRX_PW':'test'}), patch.object(self.m.subprocess,'run',side_effect=subprocess.TimeoutExpired('fetch',40)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):self.m.fetch_sources('samsung','2026-01-01','2026-10-06')
            self.assertEqual(run.call_args.kwargs['timeout'],40)
        with tempfile.TemporaryDirectory() as tmp:
            book=Path(tmp)/'paper_history/account.sqlite';book.parent.mkdir();book.write_bytes(b'original')
            with patch.object(self.m,'fetch_sources',side_effect=RuntimeError):
                self.m.collect_target('samsung',root=Path(tmp),now=NOW)
            self.assertEqual(book.read_bytes(),b'original')

    def test_missing_krx_credentials_is_explicit_without_network(self):
        import os
        with patch.dict(os.environ,{},clear=True), patch.object(self.m.subprocess,'run') as run:
            with tempfile.TemporaryDirectory() as tmp:
                result=self.m.collect_target('samsung',root=Path(tmp),now=NOW)
        self.assertEqual(result['error'],'KRXCredentialsMissing')
        run.assert_not_called()
