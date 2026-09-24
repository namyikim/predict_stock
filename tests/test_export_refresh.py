"""속보 교체·월간 우선·검증 이력 보존을 검증한다."""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import export_refresh as er


class ExportRefreshTests(unittest.TestCase):
    def setUp(self):
        self.monthly = pd.Series([100., 120.], index=pd.to_datetime(['2025-09-01', '2026-08-01']))
        self.flash = pd.DataFrame({
            'month': pd.to_datetime(['2025-09-01'] * 3 + ['2026-09-01'] * 3),
            'days': [10, 20, 30, 10, 20, 30], 'value': [20., 60., 100., 30., 120., 180.]})

    def test_ten_then_twenty_then_full_month(self):
        for day, expected, period in [('2026-09-12', 150., 10),
                                      ('2026-09-22', 200., 20),
                                      ('2026-10-02', 180., 30)]:
            series, applied = er.resolve_exports(self.monthly, self.flash, now=day)
            self.assertEqual(series.loc['2026-09-01'], expected)
            self.assertEqual(applied[-1]['days'], period)

    def test_monthly_replaces_flash(self):
        monthly = self.monthly.copy()
        monthly.loc[pd.Timestamp('2026-09-01')] = 175.
        series, applied = er.resolve_exports(monthly, self.flash, now='2026-10-02')
        self.assertEqual(series.loc['2026-09-01'], 175.)
        self.assertEqual(applied, [])

    def test_partial_needs_matching_previous_period(self):
        flash = self.flash[self.flash.days != 10].copy()
        flash = flash[~((flash.month.dt.year == 2025) & (flash.days == 20))]
        series, applied = er.resolve_exports(self.monthly, flash, now='2026-09-22')
        self.assertNotIn(pd.Timestamp('2026-09-01'), series.index)
        self.assertEqual(applied, [])

    def test_month_end_does_not_require_previous_year(self):
        series, applied = er.resolve_exports(self.monthly.iloc[1:], self.flash.iloc[3:], now='2026-10-02')
        self.assertEqual(series.loc['2026-09-01'], 180.)
        self.assertEqual(applied[-1]['basis'], 'full_month_preliminary')

    def test_month_end_marker_31_in_thirty_day_month(self):
        flash = self.flash.copy()
        flash.loc[flash.days == 30, 'days'] = 31
        series, applied = er.resolve_exports(self.monthly, flash, now='2026-10-01')
        self.assertEqual(series.loc['2026-09-01'], 180.)
        self.assertEqual(applied[-1]['basis'], 'full_month_preliminary')

    def test_reject_bad_values(self):
        flash = self.flash.copy()
        flash.loc[flash.month.dt.year == 2026, 'value'] = float('inf')
        series, applied = er.resolve_exports(self.monthly, flash, now='2026-10-02')
        pd.testing.assert_series_equal(series, self.monthly)
        self.assertEqual(applied, [])

    def test_live_frame_never_changes_historical_rows_or_targets(self):
        import build_longterm_report as lt
        dates = pd.date_range('2000-01-31', periods=320, freq='ME')
        price = pd.Series(range(100, 420), index=dates, dtype=float)
        monthly = pd.DataFrame({'month': dates.to_period('M').to_timestamp(), 'value': range(1000, 1320)})
        macro = {'semiconductor_exports': monthly}
        frame = lt.build_frame(price, macro)
        before = frame.copy(deep=True)
        exports = monthly.set_index('month').value
        exports.loc[pd.Timestamp('2026-09-01')] = 2400.
        live = er.live_frame(frame, exports, now='2026-09-22')
        pd.testing.assert_frame_equal(frame, before)
        pd.testing.assert_frame_equal(live.iloc[:-1], before, check_freq=False)
        self.assertTrue(live.iloc[-1].filter(like='fwd_').isna().all())
        self.assertAlmostEqual(live.iloc[-1]['macro_semiconductor_yoy'], 2400. / exports.loc['2025-09-01'] - 1)

    def test_refresh_tab_preserves_other_panels_and_nested_sections(self):
        from refresh_longterm_tab import replace_panel
        page = '<section class="rtab-panel" id="rtab-0">PREDICTION</section><section class="rtab-panel" id="rtab-1"><section id="longterm-summary">OLD</section>OLD2</section><section class="rtab-panel" id="rtab-2">LEDGER</section>'
        result = replace_panel(page, 'NEW')
        self.assertEqual(result, page.replace('<section id="longterm-summary">OLD</section>OLD2', 'NEW'))

    def test_unknown_page_layout_fails_without_overwriting(self):
        from refresh_longterm_tab import replace_panel
        with self.assertRaises(ValueError):
            replace_panel('<html>no longterm panel</html>', 'NEW')

    def test_publish_conflict_reapplies_to_newest_daily_prediction(self):
        import base64
        from unittest.mock import patch
        import refresh_longterm_tab as rt
        page = '<section class="rtab-panel">{daily}</section><section class="rtab-panel"><section id="longterm-summary">OLD</section></section>'
        responses = iter([{'sha': 'first', 'content': base64.b64encode(page.format(daily='FIRST').encode()).decode()},
                          {'sha': 'second', 'content': base64.b64encode(page.format(daily='LATEST').encode()).decode()}])
        submitted = []
        def api(path, token, method='GET', body=None):
            if method == 'GET':
                return next(responses)
            submitted.append(body)
            if len(submitted) == 1:
                error = RuntimeError('conflict')
                error.code = 409
                raise error
            # 공용 publish(expected_sha, merge) 는 올린 파일의 blob sha 앞 7자를 돌려준다(2026-09-23).
            return {'content': {'sha': 'success99'}, 'commit': {'sha': 'c'}}
        with patch.object(rt.github_pages, '_api', side_effect=api), patch('time.sleep'):
            self.assertEqual(rt.publish_tab('samsung', 'NEW', '', 'test'), 'success')
        published = base64.b64decode(submitted[-1]['content']).decode()
        self.assertIn('LATEST', published)
        self.assertNotIn('FIRST', published)
        self.assertEqual(submitted[-1]['sha'], 'second')

    def test_daily_schedule_has_one_export_refresh(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / '.github/workflows/monthly-longterm.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['schedule'], [{'cron': '17 1 * * *'}])
        daily = yaml.load((root / '.github/workflows/daily-report.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertNotIn('earnings', daily['jobs'])


if __name__ == '__main__':
    unittest.main()
