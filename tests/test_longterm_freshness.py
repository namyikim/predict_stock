"""최신 월간 자료 대체 수집과 잠정 수출의 구분을 검증한다."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
import longterm_freshness as lf


class FreshnessTests(unittest.TestCase):
    def test_api_failure_uses_same_series_public_csv_and_saves(self):
        old = pd.DataFrame({'month': ['2026-06-01'], 'value': [100.]})
        fresh = pd.Series([101., 105.], index=pd.to_datetime(['2026-06-01', '2026-08-01']))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'cache.csv'
            old.to_csv(path, index=False)
            with patch('data_sources.oecd.fred_key', return_value=' key\n'), \
                 patch('data_sources.oecd.fetch_fred_monthly', side_effect=RuntimeError('private')) as api, \
                 patch('data_sources.fred.fetch_fred', return_value=fresh) as csv:
                series, info = lf.load_exports(path, [], now='2026-10-19')
            self.assertEqual(api.call_args.args[1], 'key')
            self.assertEqual(csv.call_args.args[0], lf.EXPORT_SERIES)
            self.assertEqual(info['source'], 'FRED 공개 CSV')
            self.assertEqual(info['last'], '2026-08')
            self.assertTrue(info['fresh'])
            self.assertEqual(series.loc['2026-06-01'], 101.)
            self.assertNotIn('private', str(info))
            self.assertEqual(pd.read_csv(path).iloc[-1]['month'], '2026-08-01')

    def test_failure_keeps_cache_and_does_not_shift_yoy_over_gap(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'cache.csv'
            pd.DataFrame({'month': ['2025-07-01', '2025-08-01', '2026-08-01'],
                          'value': [100., 110., 121.]}).to_csv(p,index=False)
            with patch('data_sources.oecd.fred_key', return_value=None), \
                 patch('data_sources.fred.fetch_fred', side_effect=RuntimeError()):
                series, info = lf.load_exports(p, [], now='2026-10-19')
            self.assertFalse(info['fresh'])
            self.assertIn('보관본', info['source'])
            growth = series / series.shift(12) - 1
            self.assertAlmostEqual(growth.loc['2026-08-01'], .1)

    def test_monthly_export_extension_keeps_kosis_and_excludes_partial_months(self):
        base = pd.DataFrame({'month': pd.to_datetime(['2026-07-01','2026-08-01']), 'value': [100., 110.]})
        snapshot = {'series': [{'month':'2026-08','value':999.}, {'month':'2026-09','value':130.},
                               {'month':'2026-10','value':150.}],
                    'applied':[{'month':'2026-10','basis':'partial_month_scenario'}],
                    'monthly_info': {'customs': {'enabled':True,'months_added':['2026-09'],'scale':1.2}}}
        out, info = lf.monthly_exports(base, snapshot, now='2026-11-15')
        self.assertEqual(out.iloc[-1].value,130.)
        self.assertEqual(out.iloc[1].value,110.)
        self.assertEqual(info['months_added'],['2026-09'])
        self.assertEqual(len(base),2)
        snapshot['monthly_info']['customs']['enabled']=False
        out, info = lf.monthly_exports(base, snapshot, now='2026-11-15')
        self.assertEqual(len(out),2)

    def test_freshness_note_distinguishes_observation_and_query(self):
        text = lf.note('한국 수출', {'last':'2026-06','source':'보관본','fresh':False,
                                   'checked_at':'2026-10-19 16:30 KST'}, now='2026-10-19')
        self.assertIn('2026-06',text)
        self.assertIn('2026-10-19',text)
        self.assertIn('지연',text)
        self.assertIn('보관본',text)

    def test_snapshot_records_monthly_provenance_and_changes_hash(self):
        import export_refresh as er
        exports = pd.Series([100.],index=pd.to_datetime(['2026-09-01']))
        old = er.snapshot(exports, [], {}, '2026-10-19')
        new = er.snapshot(exports, [], {}, '2026-10-19', monthly_info={
            'customs':{'enabled':True,'scale':1.2,'months_added':['2026-09']}})
        self.assertIn('monthly_info',new)
        self.assertNotEqual(old['snapshot_hash'],new['snapshot_hash'])

    def test_stale_api_does_not_delete_newer_cached_month(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'cache.csv'
            pd.DataFrame({'month':['2026-08-01'], 'value':[110.]}).to_csv(path,index=False)
            with patch('data_sources.oecd.fred_key', return_value='key'), \
                 patch('data_sources.oecd.fetch_fred_monthly',return_value=pd.DataFrame({
                     'month':pd.to_datetime(['2026-06-01']), 'value':[100.]})), \
                 patch('data_sources.fred.fetch_fred',side_effect=RuntimeError()):
                out,info=lf.load_exports(path,[],now='2026-10-19')
            self.assertEqual(out.loc['2026-08-01'],110.)
            self.assertEqual(info['last'],'2026-08')
            self.assertFalse(info['fresh'])

    def test_no_fetch_does_not_call_network(self):
        with tempfile.TemporaryDirectory() as d, \
             patch('data_sources.oecd.fred_key',side_effect=AssertionError('network')), \
             patch('data_sources.fred.fetch_fred',side_effect=AssertionError('network')):
            out, info = lf.load_exports(Path(d)/'none.csv',fetch=False)
            self.assertTrue(out.empty)
            self.assertFalse(info['fresh'])


if __name__ == '__main__':
    unittest.main()
