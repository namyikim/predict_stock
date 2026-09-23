import importlib.util
import sys
from pathlib import Path
from datetime import datetime, timezone

import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


def module():
    assert importlib.util.find_spec('valuation_reference'), '기업가치 참고 모듈 필요'
    import valuation_reference
    return valuation_reference


def sample():
    return {'currency': 'KRW', 'financialCurrency': 'KRW', 'trailingEps': 5000,
            'bookValue': 40000, 'lastFiscalQuarter': 1782777600}


def calculate(info=None):
    return module().calculate(info or sample(), 60000, '2026-09-22',
                              '2026-09-23', '005930.KS')


class ValuationTests(unittest.TestCase):
    def test_ranges_and_no_operating_profit_substitution(self):
        r = calculate()
        self.assertEqual((r['per'], r['pbr']), (12, 1.5))
        for key in ('per', 'pbr'):
            self.assertEqual([x['price'] for x in r['scenarios'][key]], [40000, 60000, 80000])
        self.assertAlmostEqual(r['scenarios']['per'][0]['upside'], -1/3)
        self.assertIsNone(calculate({**sample(), 'trailingEps': None, 'operatingIncome': 1e12})['per'])

    def test_invalid_eps_keeps_pbr_only(self):
        for eps in [0, -100, float('nan'), float('inf'), None]:
            with self.subTest(eps=eps):
                r = calculate({**sample(), 'trailingEps': eps})
                self.assertEqual(r['scenarios']['per'], [])
                self.assertTrue(r['scenarios']['pbr'])

    def test_currency_and_stale_or_future_period_block_ranges(self):
        for changes in [{'financialCurrency': 'USD'}, {'currency': None}, {'lastFiscalQuarter': None},
                        {'lastFiscalQuarter': 1577836800}, {'lastFiscalQuarter': 1893456000}]:
            with self.subTest(changes=changes):
                r = calculate({**sample(), **changes})
                self.assertEqual(r['status'], 'unavailable')
                self.assertFalse(any(r['scenarios'].values()))

    def test_assumptions_and_limitations_visible_and_escaped(self):
        m = module()
        rendered = m.render(calculate())
        for phrase in ['PER', 'PBR', '가정', '최근 12개월', '예측 성능', '2026-09-22']:
            self.assertIn(phrase, rendered)
        self.assertNotIn('<script>', m.render({'status': 'unavailable', 'reason': '<script>'}))

    def test_no_fetch_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(module().load('005930.KS', Path(tmp), fetch=False)['status'], 'unavailable')

    def test_load_excludes_intraday_and_rejects_stale_cache(self):
        import pandas as pd
        class Ticker:
            def get_info(self):
                return sample()
            def history(self, **kwargs):
                return pd.DataFrame({'Close': [60000, 99999]},
                                    index=pd.to_datetime(['2026-09-22', '2026-09-23']))
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            now = datetime(2026, 9, 23, tzinfo=timezone.utc)
            r = module().load('005930.KS', cache, ticker_factory=lambda _: Ticker(), now=now)
            self.assertEqual(r['price'], 60000)
            self.assertEqual(r['price_date'], '2026-09-22')
            self.assertEqual(module().load('005930.KS', cache, fetch=False, now=now)['price'], 60000)
            stale = module().load('005930.KS', cache, fetch=False,
                                  now=datetime(2026, 10, 23, tzinfo=timezone.utc))
            self.assertEqual(stale['status'], 'unavailable')

    def test_fetch_failure_is_isolated(self):
        def fail(_):
            raise RuntimeError('secret should not be rendered')
        with tempfile.TemporaryDirectory() as tmp:
            r = module().load('005930.KS', Path(tmp), ticker_factory=fail)
        self.assertEqual(r['status'], 'unavailable')
        self.assertNotIn('secret', r['reason'])

    def test_invalid_assumptions_rejected(self):
        with self.assertRaises(ValueError):
            module().calculate(sample(), 60000, '2026-09-22', '2026-09-23', '005930.KS',
                               {'per': [16, 8, 12], 'pbr': [1, 1.5, 2]})
