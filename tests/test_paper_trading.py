"""모의계좌는 주문을 보내지 않고 결정 이후 새 관측만으로 체결한다."""
import tempfile
import unittest
from pathlib import Path
from tools.paper_trading import PaperBook

CONFIG = {'version':'test-v1','capital':10000,'fee':.001,'tax':.002,'slip':.001,
          'max_symbol_weight':.3,'max_sector_weight':.5,'max_daily_loss':.03,
          'max_quote_age_seconds':120,'max_order_age_seconds':1200,
          'max_price_move':.02,'targets':['samsung','sk_hynix'],'model':'No macro ensemble'}


def quote(target='samsung',ts='2026-10-06T00:45:00+00:00',price=100):
    return {'target':target,'timestamp':ts,'price':price,'source':'test'}


def signal(target='samsung',created='2026-10-05T22:00:00+00:00'):
    return {'target':target,'target_date':'2026-10-06','created_at_utc':created,
            'model':'No macro ensemble','kind':'direction','horizon_days':'1',
            'information_cutoff':'pre_open','p_up':.7,'p_down':.2,'record_id':target+'-first'}


class PaperBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'paper.sqlite'
        self.book=PaperBook(self.path,CONFIG)

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def tick(self,now,signals=None,quotes=None):
        return self.book.tick(signals or [], quotes or [], now, trading_day=True)

    def test_decision_does_not_fill_on_same_observation(self):
        out=self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.assertEqual(len(out['orders']),1)
        self.assertEqual(out['positions'],{})
        self.assertEqual(out['orders'][0]['status'],'pending')

    def test_new_quote_fills_once_and_restarts_preserve_cash(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        q=quote(ts='2026-10-06T01:00:00+00:00')
        a=self.tick('2026-10-06T01:00:00+00:00',[signal()],[q])
        b=self.tick('2026-10-06T01:00:00+00:00',[signal()],[q])
        self.assertEqual(a['cash'],b['cash'])
        self.assertEqual(len(b['fills']),1)
        self.assertLessEqual(b['positions']['samsung']*100,3000)
        self.book.close(); self.book=PaperBook(self.path,CONFIG)
        self.assertEqual(self.book.snapshot()['cash'],b['cash'])

    def test_stale_future_or_late_prediction_cannot_buy(self):
        for now,q,s in [('2026-10-06T00:45:00+00:00',quote(ts='2026-10-06T00:40:00+00:00'),signal()),
                        ('2026-10-06T00:45:00+00:00',quote(ts='2026-10-06T01:00:00+00:00'),signal()),
                        ('2026-10-06T00:45:00+00:00',quote(),signal(created='2026-10-06T01:00:00+00:00'))]:
            self.assertEqual(self.tick(now,[s],[q])['orders'],[])

    def test_config_cannot_change_existing_account(self):
        with self.assertRaises(ValueError):
            PaperBook(self.path,dict(CONFIG,fee=.01))

    def test_halt_blocks_buys_but_allows_exit(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00')])
        self.book.halt('수동 중지')
        out=self.tick('2026-10-06T06:00:00+00:00',quotes=[quote(ts='2026-10-06T06:00:00+00:00')])
        self.assertEqual(out['orders'][-1]['side'],'sell')
        out=self.tick('2026-10-06T06:15:00+00:00',quotes=[quote(ts='2026-10-06T06:15:00+00:00')])
        self.assertEqual(out['positions'],{})
        self.assertEqual(len(out['fills']),2)

    def test_closed_day_and_expired_orders_do_not_fill(self):
        self.assertEqual(self.book.tick([signal()],[quote()],'2026-10-06T00:45:00+00:00',False)['orders'],[])
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        out=self.tick('2026-10-06T01:10:00+00:00',quotes=[quote(ts='2026-10-06T01:10:00+00:00')])
        self.assertEqual(out['orders'][0]['status'],'expired')
        self.assertEqual(out['fills'],[])

    def test_two_symbols_share_one_cap(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal(),signal('sk_hynix')],[quote(),quote('sk_hynix')])
        out=self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00'),quote('sk_hynix',ts='2026-10-06T01:00:00+00:00')])
        self.assertLessEqual(sum(out['positions'].values())*100,5000)

    def test_revised_quote_cannot_rewrite_original(self):
        self.tick('2026-10-06T00:45:00+00:00',quotes=[quote()])
        with self.assertRaises(ValueError):
            self.tick('2026-10-06T00:46:00+00:00',quotes=[quote(price=200)])

    def test_cash_decision_is_frozen_against_later_signal(self):
        first=dict(signal(),p_up=.1,p_down=.8)
        self.tick('2026-10-06T00:45:00+00:00',[first],[quote()])
        out=self.tick('2026-10-06T01:00:00+00:00',[signal()],[quote(ts='2026-10-06T01:00:00+00:00')])
        self.assertEqual(out['orders'],[])
        self.assertEqual(out['decisions'][0]['action'],'hold_cash')

    def test_stale_position_quote_blocks_new_exposure(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00')])
        out=self.tick('2026-10-06T01:15:00+00:00',[signal('sk_hynix')],[quote('sk_hynix',ts='2026-10-06T01:15:00+00:00')])
        self.assertEqual(len(out['orders']),1)

    def test_loss_limit_stops_pending_buys(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00')])
        out=self.tick('2026-10-06T01:15:00+00:00',[signal('sk_hynix')],[quote(ts='2026-10-06T01:15:00+00:00',price=50),quote('sk_hynix',ts='2026-10-06T01:15:00+00:00')])
        self.assertTrue(out['halt_reason'])
        self.assertEqual(len(out['orders']),1)

    def test_expired_exit_can_retry_without_duplicate_pending_sells(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00')])
        self.tick('2026-10-07T00:45:00+00:00',quotes=[quote(ts='2026-10-07T00:45:00+00:00')])
        out=self.tick('2026-10-07T01:10:00+00:00',quotes=[quote(ts='2026-10-07T01:10:00+00:00')])
        self.assertEqual(sum(o['side']=='sell' and o['status']=='pending' for o in out['orders']),1)
        self.tick('2026-10-07T01:11:00+00:00')
        out=self.tick('2026-10-07T01:15:00+00:00',quotes=[quote(ts='2026-10-07T01:15:00+00:00')])
        self.assertEqual(out['positions'],{})
        self.assertEqual(len(out['fills']),2)

    def test_exit_cost_crossing_loss_limit_stops_next_buy_in_same_tick(self):
        self.tick('2026-10-06T00:45:00+00:00',[signal()],[quote()])
        self.tick('2026-10-06T01:00:00+00:00',quotes=[quote(ts='2026-10-06T01:00:00+00:00')])
        later=dict(signal('sk_hynix',created='2026-10-06T22:00:00+00:00'),target_date='2026-10-07')
        self.tick('2026-10-07T00:45:00+00:00',[later],[quote(ts='2026-10-07T00:45:00+00:00',price=90),quote('sk_hynix',ts='2026-10-07T00:45:00+00:00')])
        out=self.tick('2026-10-07T01:00:00+00:00',quotes=[quote(ts='2026-10-07T01:00:00+00:00',price=90),quote('sk_hynix',ts='2026-10-07T01:00:00+00:00')])
        self.assertTrue(out['halt_reason'])
        self.assertEqual(out['positions'],{})
        self.assertEqual(len(out['fills']),2)


class QuoteCollectionTests(unittest.TestCase):
    def test_uses_end_of_completed_bar_not_its_open(self):
        from tools.run_paper_trading import completed_quote
        bars=[{'timestamp':'2026-10-06T00:44:00+00:00','close':100},
              {'timestamp':'2026-10-06T00:45:00+00:00','close':200}]
        q=completed_quote(bars,'samsung','2026-10-06T00:45:30+00:00')
        self.assertEqual(q['price'],100)
        self.assertEqual(q['timestamp'],'2026-10-06T00:45:00+00:00')

    def test_initialization_does_not_read_market_or_place_orders(self):
        from tools.run_paper_trading import run
        with tempfile.TemporaryDirectory() as d:
            result=run(CONFIG,Path(d)/'book.sqlite',Path(d)/'status.json',initialize=True)
            self.assertEqual(result['cash'],10000)
            self.assertEqual(result['orders'],[])
            self.assertEqual(result['fills'],[])

    def test_manual_halt_works_without_calendar_or_network(self):
        from unittest.mock import patch
        from tools.run_paper_trading import run
        with tempfile.TemporaryDirectory() as d, patch('tools.run_paper_trading.is_session', side_effect=RuntimeError('offline')):
            result=run(CONFIG,Path(d)/'book.sqlite',Path(d)/'status.json',halt=True)
            self.assertTrue(result['halt_reason'])
            self.assertEqual(result['orders'],[])
