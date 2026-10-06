"""선택 이후 평가값을 보지 않고 동일 조건·비용으로 비교한다."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from test_wave_backtest import sample

ROOT=Path(__file__).resolve().parents[1]


class WaveValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.evaluate_wave_trading'),'기간 분리 평가 모듈 필요')
        from tools.evaluate_wave_trading import evaluate_wave
        self.evaluate=evaluate_wave
        self.rows=sample(60)
        self.config=json.loads((ROOT/'macro_inputs/wave_strategies.json').read_text())
        self.config['validation']={'selection':{'start':self.rows[65]['session'],'end':self.rows[79]['session']},
          'evaluation':{'start':self.rows[85]['session'],'end':self.rows[109]['session']},
          'legacy_model':'No macro ensemble','min_days':60,'min_trades':20,'purge_sessions':5,
          'bootstrap':{'block_sessions':5,'samples':200,'seed':17}}
        self.data={'target':'samsung','bars':self.rows,'forecasts':[]}

    def run_eval(self,data=None,config=None):return self.evaluate(data or self.data,config=config or self.config)

    def test_evaluation_prices_cannot_change_selection(self):
        before=self.run_eval();changed=copy.deepcopy(self.data)
        for r in changed['bars'][85:]:
            for k in ('open','high','low','close'):r[k]*=1.5
        after=self.run_eval(changed)
        self.assertEqual(before['selected'],after['selected'])
        self.assertEqual(before['selection'],after['selection'])
        self.assertEqual(before['candidate_count'],2)
        self.assertFalse(before['automatic_promotion'])

    def test_future_price_basis_cannot_invalidate_selection(self):
        before=self.run_eval();data=copy.deepcopy(self.data)
        data['bars'][90]['adjustment']='yahoo_auto_adjust_false'
        after=self.run_eval(data)
        self.assertEqual(before['selection'],after['selection'])
        self.assertEqual(before['selected'],after['selected'])
        self.assertEqual(after['evaluation']['status'],'unavailable')

    def test_common_curve_dates_limits_and_boundary_liquidation(self):
        r=self.run_eval();period=r['evaluation']
        for name in ['range_rebound','trend_pullback','buy_hold','cash']:
            strategy=period['standard'][name]
            self.assertEqual([p['session'] for p in strategy['equity_curve']],period['dates'])
            self.assertEqual(strategy['metrics']['open_qty'],0)
            self.assertEqual(strategy['metrics']['start'],period['dates'][0])
            self.assertEqual(strategy['metrics']['observed_sessions'],len(period['dates']))
            for f in strategy['fills']:
                if f['side']=='buy':self.assertLessEqual(f['qty']*f['price']+f['fee'],3000000)
        self.assertEqual(len(period['dates']),25)
        self.assertEqual(r['evidence'],'insufficient')

    def test_missing_and_corporate_action_block_all_price_comparisons(self):
        for action in ('missing','split'):
            data=copy.deepcopy(self.data)
            if action=='missing':del data['bars'][90]
            else:data['bars'][90]['requires_corporate_action_adjustment']=True
            r=self.run_eval(data)
            self.assertEqual(r['evaluation']['status'],'unavailable')
            self.assertIsNone(r['evaluation']['standard']['buy_hold']['metrics']['total'])

    def test_unverified_cash_prices_are_not_used(self):
        data=copy.deepcopy(self.data);data['bars'][0]['adjustment']='yahoo_auto_adjust_false'
        r=self.run_eval(data)
        self.assertEqual(r['evaluation']['status'],'unavailable')
        self.assertIn('unverified_cash_price_basis',r['evaluation']['reasons'])

    def test_double_costs_and_uncertainty_are_reproducible(self):
        a=self.run_eval();b=self.run_eval()
        standard=a['evaluation']['standard']['buy_hold']['metrics']
        doubled=a['evaluation']['double_cost']['buy_hold']['metrics']
        self.assertGreater(doubled['cost_won'],standard['cost_won'])
        self.assertLess(doubled['total'],standard['total'])
        self.assertEqual(a['evaluation']['standard']['buy_hold']['uncertainty'],b['evaluation']['standard']['buy_hold']['uncertainty'])
        self.assertIn('interval',a['evaluation']['standard']['buy_hold']['uncertainty'])

    def test_missing_original_forecasts_do_not_invent_legacy_performance(self):
        r=self.run_eval();legacy=r['evaluation']['standard']['legacy']
        self.assertIsNone(legacy['metrics']['total'])
        self.assertIn('missing_original_forecasts',legacy['limitations'])

    def test_existing_forecasts_use_same_cash_price_and_allocation(self):
        data=copy.deepcopy(self.data)
        for row in data['bars']:
            day=row['session'];data['forecasts'].append(dict(kind='direction',model='No macro ensemble',target_date=day,
                created_at_utc=day+'T08:00:00+09:00',is_prospective='true',status='scored',p_up='0.8',p_down='0.1',
                actual_open=str(row['open']),actual_close=str(row['close']),prediction='상승',information_cutoff='pre_open'))
        r=self.run_eval(data);legacy=r['evaluation']['standard']['legacy']
        self.assertIsNotNone(legacy['metrics']['total'])
        self.assertEqual(legacy['metrics']['closed_trades'],25)
        for f in legacy['fills']:
            if f['side']=='buy':self.assertLessEqual(f['qty']*f['price']+f['fee'],3000000)

    def test_zero_trades_and_hashes_preserve_input(self):
        data=copy.deepcopy(self.data)
        for row in data['bars']:row.update(open=100,high=100,low=100,close=100)
        original=copy.deepcopy(data);r=self.run_eval(data)
        self.assertIsNone(r['selected'])
        self.assertEqual(r['evidence'],'insufficient')
        self.assertEqual(data,original)
        for key in ['input_sha256','config_sha256','engine_sha256']:self.assertEqual(len(r[key]),64)

    def test_workflow_persists_evaluation_and_saved_missing_data_is_null(self):
        workflow=(ROOT/'.github/workflows/paper-trading.yml').read_text()
        self.assertIn('python -m tools.evaluate_wave_trading',workflow)
        self.assertIn('for file in docs/lab/wave_validation_*.json',workflow)
        for target in ('samsung','sk_hynix'):
            result=json.loads((ROOT/'docs/lab'/('wave_validation_'+target+'.json')).read_text())
            self.assertFalse(result['automatic_promotion'])
            if result['evaluation']['status']=='unavailable':
                self.assertTrue(all(r['metrics']['total'] is None for r in result['evaluation']['standard'].values()))

    def test_overlap_or_short_purge_rejected(self):
        config=copy.deepcopy(self.config);config['validation']['evaluation']['start']=self.rows[81]['session']
        with self.assertRaises(ValueError):self.run_eval(config=config)
        config['validation']['evaluation']['start']=self.rows[79]['session']
        with self.assertRaises(ValueError):self.run_eval(config=config)

if __name__=='__main__':unittest.main()
