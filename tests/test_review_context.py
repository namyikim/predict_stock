"""회고 관찰은 당시 자료만 사용하고 결측을 중립 신호로 바꾸지 않는다."""
import unittest
import pandas as pd
from tools import review_context as C


class PriceContextTests(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range('2026-01-01', periods=65)
        self.price = pd.DataFrame({'close': 100., 'high': 101.}, index=self.days)
        self.peer = self.price.copy()
        self.day = self.days[61]
        self.price.loc[self.day, ['close', 'high']] = [102., 105.]
        self.price.loc[self.days[62]:, ['close', 'high']] = 9999.

    def test_breakout_excludes_today_and_future(self):
        got = C.price_context(self.price, self.peer, self.day)
        self.assertEqual(got['breakouts']['20']['prior_high'], 101.)
        self.assertTrue(got['breakouts']['20']['close_breakout'])
        self.assertAlmostEqual(got['relative']['20']['excess'], .02)

    def test_empty_peer_preserves_own_breakout(self):
        got = C.price_context(self.price, pd.DataFrame(), self.day)
        self.assertEqual(got['relative'], {})
        self.assertTrue(got['breakouts']['20']['close_breakout'])

    def test_intraday_breakout_is_not_close_breakout(self):
        self.price.loc[self.day, 'close'] = 100.
        got = C.price_context(self.price, self.peer, self.day)['breakouts']['20']
        self.assertTrue(got['intraday_breakout'])
        self.assertFalse(got['close_breakout'])

    def test_missing_peer_date_does_not_extend_window(self):
        got = C.price_context(self.price, self.peer.drop(self.days[55]), self.day)
        self.assertNotIn('20', got['relative'])
        self.assertIn('20', got['breakouts'])

    def test_missing_high_or_short_history_is_unknown(self):
        self.price.loc[self.days[55], 'high'] = float('nan')
        self.assertNotIn('20', C.price_context(self.price, self.peer, self.day)['breakouts'])
        self.assertEqual(C.price_context(self.price.iloc[:4], self.peer, self.days[3])['breakouts'], {})


class BuybackContextTests(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range('2026-01-01', periods=45)
        self.end = self.days[22]
        self.periods = [{'start': '2025-12-01', 'end': str(self.end.date())}]
        self.flows = pd.DataFrame({'date': self.days, 'foreign_net': [-10.] * 23 + [20.] * 22,
                                   'inst_net': -5., 'other_net': 10.})

    def test_compare_equal_windows_and_ignore_future(self):
        got = C.buyback_transition(self.periods, self.flows, self.days, self.days[27])
        self.assertEqual(got['windows']['5']['foreign_net'], {'before': -50., 'after': 100.})
        self.assertNotIn('20', got['windows'])
        self.assertEqual(got['state'], 'scheduled_end_passed')

    def test_missing_day_not_filled_with_zero_or_older_day(self):
        got = C.buyback_transition(self.periods, self.flows.drop(25), self.days, self.days[27])
        self.assertEqual(got['windows'], {})

    def test_missing_actor_does_not_invent_residual(self):
        self.flows.loc[25, 'other_net'] = float('nan')
        got = C.buyback_transition(self.periods, self.flows, self.days, self.days[27])
        self.assertNotIn('other_net', got['windows']['5'])

    def test_overlapping_or_future_program_suppresses_end_claim(self):
        active = {'start': str(self.end.date()), 'end': str(self.days[-1].date())}
        self.assertIsNone(C.buyback_transition(self.periods + [active], self.flows, self.days, self.days[27]))
        self.assertIsNone(C.buyback_transition(self.periods, self.flows, self.days, self.days[20]))


class ObservationIntegrationTests(unittest.TestCase):
    def test_context_is_explained_without_buyback_completion_claim(self):
        from forecast_utils import market_observations
        summary = {
            'price_context': {'breakouts': {'20': {'close_breakout': False, 'intraday_breakout': True,
                                                  'distance': -.01}},
                              'relative': {'20': {'own': .1, 'peer': .05, 'excess': .05}}},
            'buyback_transition': {'scheduled_end': '2026-09-30', 'sessions_after': 5,
                                   'windows': {'5': {'foreign_net': {'before': -10., 'after': 20.}}}}}
        text = ' '.join(market_observations(summary, peer_name='상대 종목'))
        self.assertIn('장중 돌파', text)
        self.assertIn('5.00%p', text)
        self.assertIn('예정 종료', text)
        self.assertIn('-10주 → +20주', text)
        self.assertNotIn('매입이 완료', text)


class BuildReviewIntegrationTests(unittest.TestCase):
    def test_collector_persists_context_and_renderer_uses_it(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from tools import build_session_review as R
        days = pd.bdate_range('2026-01-01', periods=65)
        prices = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 1000.}, index=days)
        prices.loc[days[-1], ['high', 'close']] = [103., 102.]
        flows = pd.DataFrame({'date': days, 'foreign_net': 10., 'inst_net': -5., 'indiv_net': -5.})
        replacements = {'load_daily': prices, 'load_intraday': pd.DataFrame(), 'session_cross': {},
                        'after_hours': None, 'fetch_disclosures': ([], None), '_kr_events': [],
                        'buyback_on': [{'start': str(days[0].date()), 'end': str(days[-6].date())}],
                        'load_ledger': pd.DataFrame(), 'session_context': {}}
        with ExitStack() as stack:
            for name, result in replacements.items():
                stack.enter_context(patch.object(R, name, return_value=result))
            stack.enter_context(patch('data_sources.flows.load_investor_flows', return_value=(flows, {'source': 'test'})))
            review = R.build_review('samsung', days[-1], '/tmp', use_news=False)
        self.assertTrue(review['summary']['price_context']['breakouts']['20']['close_breakout'])
        self.assertEqual(review['summary']['buyback_transition']['windows']['5']['foreign_net']['after'], 50.)
        self.assertIn('예정 종료', ' '.join(review['flow_story']['observations']))
        page = R.review_section_html(review)
        self.assertIn('외국인 5일 수급 비교', page)
        self.assertIn('종가 돌파', page)
        self.assertIn('예정 종료', page)


class ForeignPressureTests(unittest.TestCase):
    def test_normalizes_by_each_windows_volume(self):
        days = pd.bdate_range('2026-01-01', periods=11)
        prices = pd.DataFrame({'volume': [100.] * 5 + [200.] * 6}, index=days)
        flows = pd.DataFrame({'date': days, 'foreign_net': [-10.] * 5 + [-10.] * 5 + [999.]})
        got = C.foreign_pressure(flows, prices, days[9])
        self.assertEqual(got['state'], 'selling_eased')
        self.assertAlmostEqual(got['previous_ratio'], -.1)
        self.assertAlmostEqual(got['recent_ratio'], -.05)
        self.assertEqual(got['recent_daily_net'], -10.)

    def test_missing_day_or_zero_volume_is_unknown(self):
        days = pd.bdate_range('2026-01-01', periods=10)
        prices = pd.DataFrame({'volume': 100.}, index=days)
        flows = pd.DataFrame({'date': days, 'foreign_net': -10.})
        self.assertIsNone(C.foreign_pressure(flows.drop(4), prices, days[-1]))
        prices.iloc[4, 0] = 0
        self.assertIsNone(C.foreign_pressure(flows, prices, days[-1]))

    def test_net_buying_is_not_called_weaker_selling(self):
        days = pd.bdate_range('2026-01-01', periods=10)
        prices = pd.DataFrame({'volume': 100.}, index=days)
        flows = pd.DataFrame({'date': days, 'foreign_net': [-10.] * 5 + [10.] * 5})
        self.assertEqual(C.foreign_pressure(flows, prices, days[-1])['state'], 'turned_buying')
