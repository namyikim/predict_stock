"""수출 자료 분기와 한국 달력의 현재 분기를 분리한다."""
import unittest
import pandas as pd
from tests.test_earnings_forecast import ef

class CalendarQuarterTests(unittest.TestCase):
    def result(self, quarter='2026Q3'):
        q = pd.Period(quarter);n=q+1
        return {'target':'samsung','quarter_code':str(q),'quarter':f'{q.year}년 {q.quarter}분기',
                'months_used':3,'point':110e12,'raw_point':110e12,'announced_actual':107.4e12,
                'last_actual':89.5e12,'last_actual_quarter':str(q-1),'evaluation':{'n':0},
                'next_quarter':{'quarter_code':str(n),'quarter':f'{n.year}년 {n.quarter}분기',
                  'point':135e12,'raw_point':135e12,'low':100e12,'high':170e12,
                  'evaluation':{'n':0},'chosen':'released_residual','anchor_actual':107.4e12}}

    def test_october_promotes_q4_and_waits_for_q1_without_using_q3_actual_as_q4(self):
        self.assertTrue(hasattr(ef,'align_calendar_quarters'))
        r=ef.align_calendar_quarters(self.result(), '2026-10-08')
        self.assertEqual(r['quarter_code'],'2026Q4');self.assertEqual(r['months_used'],0)
        self.assertEqual(r['point'],135e12);self.assertIsNone(ef.known_quarter_actual(r))
        self.assertEqual(r['last_actual_quarter'],'2026Q3');self.assertEqual(r['last_actual'],107.4e12)
        self.assertEqual(r['next_quarter']['quarter_code'],'2027Q1')
        self.assertIsNone(r['next_quarter']['point'])
        ledger,_=ef.append_estimate(ef.read_ledger('/missing.csv'),r,'rollover')
        self.assertEqual(ledger['record_id'].tolist(),['samsung:2026Q4:k0'])

    def test_render_shows_current_forecast_and_next_waiting_without_old_diagnostics(self):
        original=self.result()
        original.update(name='삼성전자', profit_source='test', profit_first='2016Q1',
                        profit_last='2026Q2', profit_n=42, exports_last_month='2026-09-01',
                        generated_at='2026-10-08', chart_svg='OLD_CHART',
                        segment_split={'reason':'old segment'})
        r=ef.align_calendar_quarters(original,'2026-10-08')
        text=ef.render_fragment(r)
        self.assertIn('135.00조원',text)
        self.assertIn('다음 분기(2027년 1분기)',text)
        self.assertIn('107.40조원',text)
        self.assertNotIn('앞 0개월',text)
        self.assertNotIn('발표 후 재계산',text)
        self.assertNotIn('OLD_CHART',text)

    def test_year_boundary_and_stale_data_do_not_relabel_old_forecast(self):
        self.assertTrue(hasattr(ef,'align_calendar_quarters'))
        r=ef.align_calendar_quarters(self.result('2026Q4'),'2027-01-01')
        self.assertEqual(r['quarter_code'],'2027Q1');self.assertEqual(r['next_quarter']['quarter_code'],'2027Q2')
        stale=ef.align_calendar_quarters(self.result(),'2027-01-01')
        self.assertIsNone(stale['point'])
        ledger,added=ef.append_estimate(ef.read_ledger('/missing.csv'),stale,'stale')
        self.assertFalse(added);self.assertTrue(ledger.empty)

    def test_same_quarter_keeps_nowcast_and_kst_midnight_rolls_over(self):
        self.assertTrue(hasattr(ef,'align_calendar_quarters'))
        original=self.result()
        self.assertEqual(ef.align_calendar_quarters(original,'2026-09-30T14:59:00+00:00'),original)
        r=ef.align_calendar_quarters(original,'2026-09-30T15:00:00+00:00')
        self.assertEqual(r['quarter_code'],'2026Q4')
        self.assertEqual(original['quarter_code'],'2026Q3')

if __name__=='__main__':unittest.main()
