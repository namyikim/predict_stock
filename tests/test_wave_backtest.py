"""연구용 일봉 재생: 미래 체결·유리한 봉내 순서·공백 소급 체결을 막는다."""
import copy
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
import unittest
import exchange_calendars as xcals

ROOT=Path(__file__).resolve().parents[1]


def sample(extra=8, strategy='trend_pullback'):
    cal=xcals.get_calendar('XKRX')
    sessions=cal.sessions_in_range('2026-01-05','2026-06-30')[:65+extra]
    prices=([80]*40+[100]*23+[99,102] if strategy=='trend_pullback' else [100]*63+[99,101])+[102]*extra
    out=[]
    for day,p in zip(sessions,prices):
        start,end=cal.session_open(day),cal.session_close(day)
        out.append(dict(target='samsung',timeframe='1d',session=str(day.date()),bar_start=start.isoformat(),
                        bar_end=end.isoformat(),observed_at=(end+timedelta(seconds=1)).isoformat(),
                        open=p,high=p+1,low=p-1,close=p,volume=100,source='test',adjustment='unadjusted',
                        requires_corporate_action_adjustment=False))
    return out


class WaveBacktestTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.wave_backtest'),'재생 엔진 필요')
        from tools.wave_backtest import simulate_wave
        self.simulate=simulate_wave
        cfg=json.loads((ROOT/'macro_inputs/wave_strategies.json').read_text())
        self.cfg=cfg
        self.config=dict(cfg['strategies']['trend_pullback'],execution=cfg['execution'])

    def run_sim(self, data, config=None):return self.simulate(data,config=config or self.config,capital=100000)

    def test_next_open_integer_shares_and_cash_costs(self):
        data=sample();original=copy.deepcopy(data);r=self.run_sim(data)
        buy,sell=r['fills'][:2]
        self.assertEqual(buy['session'],data[65]['session'])
        self.assertEqual(buy['timing'],'open')
        self.assertGreater(buy['price'],data[65]['open'])
        self.assertIsInstance(buy['qty'],int)
        self.assertLessEqual(buy['qty']*buy['price']+buy['fee'],100000)
        expected=100000-buy['qty']*buy['price']-buy['fee']+sell['qty']*sell['price']-sell['fee']-sell['tax']
        self.assertAlmostEqual(r['metrics']['final_equity'],expected)
        self.assertLess(r['metrics']['total'],0)
        self.assertEqual(data,original)

    def test_both_touched_uses_stop_first(self):
        data=sample(1);data[65].update(high=120,low=90)
        r=self.run_sim(data)
        self.assertEqual(r['fills'][-1]['reason'],'stop_before_target_assumption')
        self.assertAlmostEqual(r['fills'][-1]['reference_price'],98)

    def test_stop_gap_uses_open_not_stop(self):
        data=sample(2);data[66].update(open=90,high=92,low=89,close=91)
        r=self.run_sim(data)
        self.assertEqual(r['fills'][-1]['reason'],'stop_gap')
        self.assertEqual(r['fills'][-1]['reference_price'],90)

    def test_exit_on_fifth_session_close(self):
        data=sample();r=self.run_sim(data)
        self.assertEqual(r['fills'][1]['session'],data[69]['session'])
        self.assertEqual(r['fills'][1]['reason'],'max_holding')
        self.assertEqual(r['fills'][1]['timing'],'close')

    def test_missing_exit_session_delays_until_available_open(self):
        data=sample();missing=data[69]['session'];del data[69]
        r=self.run_sim(data)
        self.assertEqual(r['fills'][1]['reason'],'delayed_exit')
        self.assertEqual(r['fills'][1]['session'],data[69]['session'])
        self.assertTrue(any(x.get('session')==missing and x.get('reason')=='missing_bar' for x in r['orders']))

    def test_zero_volume_does_not_fill(self):
        data=sample(1);data[65]['volume']=0
        r=self.run_sim(data)
        self.assertEqual(r['fills'],[])
        self.assertTrue(any(x.get('reason')=='zero_volume' for x in r['orders']))

    def test_corporate_action_while_holding_invalidates_return(self):
        data=sample(2);data[66]['requires_corporate_action_adjustment']=True
        r=self.run_sim(data)
        self.assertIsNone(r['metrics']['total'])
        self.assertIn('corporate_action_unresolved',r['limitations'])
        self.assertEqual(len(r['fills']),1)

    def test_suspended_corporate_action_cannot_create_fake_loss(self):
        data=sample(3)
        data[66].update(volume=0,requires_corporate_action_adjustment=True)
        for key in ('open','high','low','close'):data[67][key]/=2
        r=self.run_sim(data)
        self.assertIsNone(r['metrics']['total'])
        self.assertIn('corporate_action_unresolved',r['limitations'])
        self.assertEqual(len(r['fills']),1)

    def test_range_gap_at_target_does_not_enter(self):
        data=sample(1,'range_rebound');data[65].update(open=100,high=101,low=99,close=100)
        config=dict(self.cfg['strategies']['range_rebound'],execution=self.cfg['execution'])
        r=self.run_sim(data,config)
        self.assertEqual(r['fills'],[])
        self.assertTrue(any(x.get('reason')=='entry_at_or_above_target' for x in r['orders']))

    def test_invalid_stop_and_insufficient_reward_do_not_enter(self):
        data=sample(1);data[65].update(open=97,high=98,low=96,close=97)
        self.assertEqual(self.run_sim(data)['fills'],[])
        config=copy.deepcopy(self.config);config['execution']['fee']=0.1
        self.assertEqual(self.run_sim(sample(1),config)['fills'],[])

    def test_late_observation_does_not_backdate_entry(self):
        data=sample(1);data[64]['observed_at']=data[65]['bar_end']
        r=self.run_sim(data)
        self.assertEqual(r['fills'],[])
        self.assertTrue(any(x.get('reason')=='decision_after_open' for x in r['orders']))

    def test_open_position_is_not_sold_using_future_last_day(self):
        r=self.run_sim(sample(1))
        self.assertEqual(len(r['fills']),1)
        self.assertEqual(r['metrics']['closed_trades'],0)
        self.assertGreater(r['metrics']['open_qty'],0)
        self.assertIn('ending_position_not_liquidated',r['limitations'])

    def test_evaluation_boundary_and_allocation_are_respected(self):
        data=sample(8);config=copy.deepcopy(self.config)
        config['execution'].update(position_weight=0.3,trade_start=data[65]['session'],entry_deadline=data[68]['session'])
        r=self.run_sim(data,config)
        self.assertEqual(r['equity_curve'][0]['session'],data[65]['session'])
        first=r['fills'][0]
        self.assertLessEqual(first['qty']*first['price']+first['fee'],30000)
        self.assertEqual(r['metrics']['open_qty'],0)
        config['execution']['entry_deadline']=data[64]['session']
        self.assertEqual(self.run_sim(data,config)['fills'],[])

    def test_unverified_price_basis_rejected(self):
        data=sample(1);data[0]['adjustment']='yahoo_auto_adjust_false'
        with self.assertRaises(ValueError):self.run_sim(data)

if __name__=='__main__':unittest.main()
