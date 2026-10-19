"""기준금리·물가 그림: 달 정렬, 결측, 조회 실패와 보고서 연결."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))
import policy_inflation as pi


class PolicyInflationTests(unittest.TestCase):
    def inputs(self):
        months = pd.date_range('2024-01-01', periods=28, freq='MS')
        return pd.DataFrame({'policy_rate': 3., 'cpi_index': range(100, 128)}, index=months)

    def test_yoy_uses_calendar_month_and_never_fills_cpi(self):
        raw = self.inputs().drop(pd.Timestamp('2024-02-01'))
        out = pi.comparison(raw, now='2026-04-19')
        self.assertAlmostEqual(out.loc['2025-01-01', 'inflation'], 12.)
        self.assertTrue(pd.isna(out.loc['2025-02-01', 'inflation']))
        self.assertNotIn(pd.Timestamp('2026-04-01'), out.index)

    def test_policy_rate_is_daily_base_rate_monthly_average(self):
        daily = pd.DataFrame({'date': pd.to_datetime(['2026-01-01', '2026-01-02']), 'value': [3., 2.5]})
        with patch('data_sources.ecos.ecos_key', return_value='test'), \
             patch('data_sources.ecos.fetch_ecos_daily', return_value=daily) as request:
            series = pi.fetch_series('policy_rate', '2026-01-01', '2026-02-01')
        self.assertEqual(request.call_args.args[:2], ('722Y001', '0101000'))
        self.assertEqual(series.iloc[0], 2.75)

    def test_svg_breaks_lines_at_missing_months(self):
        import xml.etree.ElementTree as ET
        rows = [{'month': f'2026-0{i+1}', 'policy_rate': v, 'inflation': 2.}
                for i, v in enumerate([3., None, 2.5])]
        root = ET.fromstring(pi.chart(rows))
        paths = root.findall('{http://www.w3.org/2000/svg}path')
        self.assertEqual(paths[0].attrib['d'].count('M'), 2)
        self.assertNotIn('L', paths[0].attrib['d'])

    def test_real_rate_line_has_positive_negative_zero_and_gaps(self):
        import xml.etree.ElementTree as ET
        rows = [{'month': f'2026-0{i+1}', 'policy_rate': rate, 'inflation': inflation}
                for i, (rate, inflation) in enumerate([(3., 2.), (2., 3.), (2., 2.), (None, 2.), (3., 2.)])]
        svg = pi.chart(rows)
        root = ET.fromstring(svg)
        ns = {'s': 'http://www.w3.org/2000/svg'}
        line = root.find('s:path[@data-series="real_rate"]', ns)
        self.assertIsNotNone(line)
        self.assertEqual(line.attrib['d'].count('M'), 2)
        self.assertIsNotNone(root.find('s:line[@data-role="zero-line"]', ns))
        self.assertIn('실질금리 참고값: +1.00%', svg)
        self.assertIn('실질금리 참고값: -1.00%', svg)
        self.assertIn('실질금리 참고값: +0.00%', svg)

    def test_latest_real_rate_status_and_rounded_zero(self):
        for gap, label in [(1., '양수(+)'), (-1., '음수(−)'), (0., '0 수준'), (-.001, '0 수준')]:
            result = {'rows': [{'month': '2026-09', 'policy_rate': 2.+gap, 'inflation': 2.}],
                      'latest': {'month': '2026-09', 'policy_rate': 2.+gap, 'inflation': 2., 'gap': gap}}
            text = pi.render(result)
            self.assertIn(label, text)
            self.assertIn('기대물가', text)
            self.assertIn('근사', text)

    def test_partial_failure_preserves_cache_and_marks_failed_series(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'policy_inflation.csv'
            self.inputs().to_csv(path, index_label='month')
            fresh = pd.Series([2.5], index=pd.to_datetime(['2026-03-01']))
            def get(name, start, end):
                if name == 'cpi_index':
                    raise RuntimeError('private credential must not appear')
                return fresh
            result = pi.load(path, now='2026-04-19', fetch_fn=get)
            self.assertEqual(result['latest']['policy_rate'], 2.5)
            self.assertIn('cpi_index', result['failed'])
            self.assertNotIn('private credential', str(result))
            saved = pd.read_csv(path)
            self.assertEqual(saved.iloc[0]['month'], '2024-01-01')
            self.assertIn('보관본', pi.render(result))

    def test_no_fetch_no_cache_is_explicit_and_does_not_request(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(pi, 'fetch_series', side_effect=AssertionError('network')):
                result = pi.load(Path(d) / 'missing.csv', fetch=False)
            self.assertIn('자료가 없어', pi.render(result))
            self.assertNotIn('<svg', pi.render(result))

    def test_chart_gaps_dates_units_and_same_month_gap(self):
        with tempfile.TemporaryDirectory() as d:
            raw = self.inputs()
            raw.loc['2026-03-01', 'cpi_index'] = float('nan')
            path = Path(d) / 'input.csv'
            raw.to_csv(path, index_label='month')
            result = pi.load(path, fetch=False, now='2026-04-19')
            self.assertEqual(result['latest']['month'], '2026-02')
            text = pi.render(result)
            self.assertIn('viewBox=', text)
            self.assertIn('전년 동월 대비', text)
            self.assertIn('%p', text)
            self.assertIn('2026-03', text)  # 금리 최신월은 물가와 별도 표기
            self.assertIn('관측월', text)
            self.assertNotIn('nan', text.lower())
            self.assertIn('점선', text)

    def test_longterm_fragment_contains_comparison_for_both_companies(self):
        import json
        import build_longterm_report as lt
        for target in ('samsung', 'sk_hynix'):
            result = json.loads((ROOT / f'docs/{target}/longterm.json').read_text())
            result['policy_inflation'] = {'rows': [], 'failed': {}, 'latest': None}
            # 저장 JSON의 가치평가에는 렌더용 DataFrame이 없다.
            result['stock_value'] = None
            text = lt.render_fragment(result)
            self.assertIn('기준금리와 소비자물가 상승률', text)
            self.assertLess(text.index('기준금리와 소비자물가 상승률'), text.index('실제 통계치와 주가'))


if __name__ == '__main__':
    unittest.main()
