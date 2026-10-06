"""미래 가격이나 나중 수정값이 연구 신호를 바꾸지 않는지 확인한다."""
import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def bars(prices):
    out=[]
    for i,p in enumerate(prices):
        end=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=i)).isoformat()
        out.append(dict(target='samsung',timeframe='1d',bar_start=end,bar_end=end,observed_at=end,
                        open=p,high=p+1,low=p-1,close=p,volume=100,source='test',adjustment='unadjusted',
                        requires_corporate_action_adjustment=False))
    return out


class WaveSignalsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.wave_signals'), '파동 신호 모듈 필요')
        from tools.wave_signals import wave_signal
        self.signal=wave_signal
        self.config=json.loads((ROOT/'macro_inputs/wave_strategies.json').read_text())['strategies']
        self.cutoff='2026-04-01T00:00:00+00:00'

    def run_signal(self, data, strategy='range_rebound', cutoff=None):
        return self.signal(data,decision_at=cutoff or self.cutoff,config=self.config[strategy])

    def test_range_rebound_uses_range_before_previous_bar(self):
        data=bars([100]*63+[99,101])
        r=self.run_signal(data)
        self.assertEqual(r['action'],'enter')
        self.assertEqual(r['range_position'],0)
        self.assertEqual(r['reference_stop'],98)
        self.assertEqual(r['regime'],'range')
        self.assertEqual(r['information_cutoff'],data[-1]['observed_at'])

    def test_pullback_in_rising_trend(self):
        r=self.run_signal(bars([80]*40+[100]*23+[99,102]),'trend_pullback')
        self.assertEqual(r['action'],'enter')
        self.assertEqual(r['regime'],'uptrend')

    def test_decline_does_not_become_rebound(self):
        data=bars([150-i for i in range(63)]+[86,88])
        for strategy in self.config:
            self.assertEqual(self.run_signal(data,strategy)['action'],'wait')

    def test_insufficient_and_zero_range(self):
        self.assertIn('insufficient_bars',self.run_signal(bars([100]*64))['reason_codes'])
        data=bars([100]*65)
        for r in data:r.update(open=100,high=100,low=100,close=100)
        self.assertIn('zero_range',self.run_signal(data)['reason_codes'])

    def test_rebound_requires_break_above_previous_high(self):
        self.assertEqual(self.run_signal(bars([100]*63+[99,100]))['action'],'wait')

    def test_future_and_later_revisions_do_not_change_past(self):
        data=bars([100]*63+[99,101]); cutoff=data[-1]['bar_end']
        before=copy.deepcopy(data)
        expected=self.run_signal(data,cutoff=cutoff)
        future=bars([100]*66)[-1];future.update(close=900,high=901)
        revised=dict(data[-1],close=5,observed_at=self.cutoff)
        self.assertEqual(self.run_signal(data+[future,revised],cutoff=cutoff),expected)
        self.assertEqual(data,before)

    def test_timezone_and_observation_boundary(self):
        data=bars([100]*63+[99,101])
        t=datetime.fromisoformat(data[-1]['observed_at'])
        self.assertEqual(self.run_signal(data,cutoff=(t-timedelta(seconds=1)).isoformat())['action'],'wait')
        self.assertEqual(self.run_signal(data,cutoff=t.astimezone(timezone(timedelta(hours=9))).isoformat())['action'],'enter')

    def test_corporate_action_requires_new_window(self):
        data=bars([100]*63+[99,101]);data[-3]['requires_corporate_action_adjustment']=True
        self.assertIn('insufficient_bars',self.run_signal(data)['reason_codes'])

    def test_mixed_symbols_or_timeframes_are_rejected(self):
        for field,value in [('target','sk_hynix'),('timeframe','1m')]:
            data=bars([100]*65);data[-1][field]=value
            with self.assertRaises(ValueError):self.run_signal(data)

    def test_invalid_price_and_config_rejected(self):
        data=bars([100]*65);data[-1]['close']=float('nan')
        with self.assertRaises(ValueError):self.run_signal(data)
        with self.assertRaises(ValueError):self.signal([],decision_at=self.cutoff,config={'strategy':'unknown'})

    def test_forecast_combination_not_silently_enabled(self):
        config=dict(self.config['range_rebound'],requires_forecast=True)
        r=self.signal(bars([100]*63+[99,101]),decision_at=self.cutoff,config=config)
        self.assertEqual(r['action'],'wait')
        self.assertIn('aligned_forecast_unavailable',r['reason_codes'])

if __name__=='__main__':unittest.main()
