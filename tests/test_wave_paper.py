"""분리된 관측 장부의 반복 실행·충돌·한도와 운영 계좌 불변을 확인한다."""
import copy
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[1]
NOW='2026-10-06T05:00:00+00:00'


def bar(target='samsung',end='2026-10-06T04:59:00+00:00',price=100):
    return dict(target=target,timeframe='1m',bar_end=end,bar_start=end,observed_at=end,
                open=price,high=price,low=price,close=price,volume=100,adjustment='unadjusted',
                requires_corporate_action_adjustment=False)


class WavePaperTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.run_wave_paper'),'관측 장부 모듈 필요')
        import tools.run_wave_paper as module
        self.module=module
        self.config=json.loads((ROOT/'macro_inputs/wave_strategies.json').read_text())
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'paper_history').mkdir()
        (self.root/'paper_history/account.sqlite').write_bytes(b'original-account')
        self.quotes={t:bar(t) for t in ['samsung','sk_hynix']}
        self.state=dict(cash=10000000,positions={},reservations={},last_exits={})

    def run_observer(self,now=NOW,config=None):
        return self.module.run_wave_paper(config or self.config,now=now,root=self.root)

    def test_new_accounts_are_independent_and_observe_only(self):
        r=self.run_observer()
        self.assertEqual(len(r['strategies']),2)
        for x in r['strategies']:
            self.assertEqual(x['cash'],10000000)
            self.assertEqual(x['fill_count'],0)
            self.assertEqual(x['order_count'],0)
            self.assertIsNone(x['total_return'])
            self.assertEqual(x['mode'],'observe_only')
        self.assertEqual((self.root/'paper_history/account.sqlite').read_bytes(),b'original-account')

    def test_same_bar_and_restart_are_idempotent(self):
        daily=dict(bar(end='2026-10-02T06:30:00+00:00'),timeframe='1d')
        def fetch(root,*,target,timeframe):return [dict(daily,target=target)] if timeframe=='1d' else [self.quotes[target]]
        with patch.object(self.module,'read_archive',side_effect=fetch):
            first=self.run_observer();second=self.run_observer();third=self.run_observer('2026-10-06T05:15:00+00:00')
        for a,b,c in zip(first['strategies'],second['strategies'],third['strategies']):
            self.assertEqual(a['decision_count'],2)
            self.assertEqual(a['decision_count'],b['decision_count'])
            self.assertEqual(a['decision_count'],c['decision_count'])
            self.assertEqual(c['order_count'],0)

    def test_live_candidates_share_sector_budget_without_orders(self):
        import exchange_calendars as xcals
        from datetime import timedelta
        cal=xcals.get_calendar('XKRX')
        days=cal.sessions_in_range('2026-06-01','2026-10-02')[-65:]
        daily=[]
        for day,price in zip(days,[100]*63+[99,101]):
            end=cal.session_close(day).isoformat()
            daily.append(dict(bar(end=end,price=price),timeframe='1d',high=price+1,low=price-1))
        def fetch(root,*,target,timeframe):return [dict(r,target=target) for r in daily] if timeframe=='1d' else [self.quotes[target]]
        with patch.object(self.module,'read_archive',side_effect=fetch):result=self.run_observer()
        account=next(x for x in result['strategies'] if x['strategy']=='range_rebound')
        previews=[x['risk_preview'] for x in account['observations']]
        self.assertEqual([x['signal']['action'] for x in account['observations']],['enter','enter'])
        self.assertLessEqual(sum(x['reservation_preview'] for x in previews),5000000)
        self.assertLess(previews[1]['budget'],3000000)
        self.assertEqual(account['cash'],10000000)
        self.assertEqual(account['order_count'],0)

    def test_old_daily_data_is_not_a_new_candidate(self):
        daily=dict(bar(end='2026-09-30T06:30:00+00:00'),timeframe='1d')
        with patch.object(self.module,'read_archive',return_value=[daily]):result=self.run_observer()
        self.assertEqual(result['strategies'][0]['decision_count'],0)
        self.assertEqual(result['strategies'][0]['observations'][0]['status'],'stale_daily')

    def test_changed_config_is_rejected_without_reset(self):
        self.run_observer();config=copy.deepcopy(self.config);config['execution']['fee']*=2
        before=(self.root/'paper_history/strategies/range_rebound/account.sqlite').read_bytes()
        with self.assertRaises(ValueError):self.run_observer(config=config)
        self.assertEqual((self.root/'paper_history/strategies/range_rebound/account.sqlite').read_bytes(),before)

    def test_backward_time_and_live_mode_are_rejected(self):
        self.run_observer()
        with self.assertRaises(ValueError):self.run_observer('2026-10-06T04:00:00+00:00')
        with self.assertRaises(ValueError):self.module.run_wave_paper(self.config,now=NOW,dry_run=False,root=self.root)

    def test_locked_ledger_fails_without_overwrite(self):
        self.run_observer();db=sqlite3.connect(self.root/'paper_history/strategies/range_rebound/account.sqlite')
        try:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):self.run_observer('2026-10-06T05:15:00+00:00')
        finally:db.rollback();db.close()

    def preview(self,state=None,quotes=None,ends=None):
        return self.module.risk_preview(state or self.state,target='samsung',quotes=self.quotes if quotes is None else quotes,
                    now=NOW,completed_bar_ends=ends or [],paper=self.config['paper'],execution=self.config['execution'])

    def test_symbol_and_sector_caps_include_reservations(self):
        r=self.preview();self.assertGreater(r['suggested_qty'],0)
        self.assertLessEqual(r['budget'],3000000)
        state=copy.deepcopy(self.state);state['reservations']={'sk_hynix':4000000}
        self.assertLessEqual(self.preview(state)['budget'],1000000)
        state['reservations']={'sk_hynix':5000000}
        self.assertEqual(self.preview(state)['suggested_qty'],0)

    def test_insufficient_cash_and_missing_or_stale_quotes(self):
        state=copy.deepcopy(self.state);state['cash']=1
        self.assertEqual(self.preview(state)['suggested_qty'],0)
        self.assertIn('missing_quote',self.preview(quotes={})['reasons'])
        quotes=copy.deepcopy(self.quotes);quotes['samsung']['bar_end']='2026-10-06T04:00:00+00:00'
        self.assertIn('stale_quote',self.preview(quotes=quotes)['reasons'])

    def test_cooldown_requires_one_complete_bar_to_pass(self):
        state=copy.deepcopy(self.state);state['last_exits']={'samsung':'2026-10-01T06:30:00+00:00'}
        self.assertIn('reentry_cooldown',self.preview(state,ends=['2026-10-02T06:30:00+00:00'])['reasons'])
        self.assertNotIn('reentry_cooldown',self.preview(state,ends=['2026-10-02T06:30:00+00:00','2026-10-06T04:00:00+00:00'])['reasons'])

    def test_held_position_with_missing_quote_reports_exit_delay(self):
        state=copy.deepcopy(self.state);state['positions']={'samsung':100}
        self.assertIn('exit_deferred_missing_quote',self.preview(state,quotes={})['reasons'])

    def test_archive_error_is_reported_not_treated_as_signal(self):
        with patch.object(self.module,'read_archive',side_effect=ValueError('broken')):
            r=self.run_observer()
        self.assertEqual(len(r['collection_errors']),2)
        self.assertEqual(r['strategies'][0]['decision_count'],0)

    def test_strategy_path_cannot_escape_storage(self):
        config=copy.deepcopy(self.config);config['strategies']['../escape']=config['strategies'].pop('range_rebound')
        with self.assertRaises(ValueError):self.run_observer(config=config)

    def test_workflow_collects_saves_and_recovers_observer(self):
        workflow=(ROOT/'.github/workflows/paper-trading.yml').read_text()
        self.assertIn('python -m tools.run_wave_paper',workflow)
        self.assertIn('docs/lab/wave_paper_status.json',workflow)
        self.assertIn('cp -R paper_history/strategies',workflow)

if __name__=='__main__':unittest.main()
