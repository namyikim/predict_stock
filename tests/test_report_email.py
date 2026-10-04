"""메일에는 발행된 보고서 내용만 담고 같은 내용은 같은 알림으로 식별한다."""
import unittest
from datetime import datetime, timezone
from tools.notify_report_update import build_pre_open, build_post_close


class ReportEmailTests(unittest.TestCase):
    def test_pre_open_uses_representative_prediction_without_realized_prices(self):
        rows=[dict(kind='direction',model='No macro ensemble',horizon_days='1',target_date='2026-10-06',
                   created_at_utc='2026-10-05T22:00:00+00:00',prediction_date='2026-10-06',is_prospective='True',run_id='r',p_up='.6',p_flat='.3',p_down='.1',prediction='상승',actual_close='999999')]
        result=build_pre_open('samsung',rows,datetime(2026,10,5,23,tzinfo=timezone.utc))
        self.assertIn('60.0%', ' '.join(result['lines']))
        self.assertNotIn('999999',str(result))
        self.assertEqual(result['session_date'],'2026-10-06')

    def test_old_or_post_open_only_ledger_is_not_sent_as_new_morning_report(self):
        rows=[dict(kind='direction',model='Post-open',target_date='2026-10-06',created_at_utc='2026-10-06T01:00:00Z')]
        self.assertIsNone(build_pre_open('samsung',rows,datetime(2026,10,6,2,tzinfo=timezone.utc)))

    def test_close_summary_contains_flow_and_result_not_raw_html(self):
        review={'session_date':'2026-10-06','generated_at':'2026-10-06T08:00:00+00:00',
                'summary':{'close':100000,'c2c':.01,'gap':.02,'session':-.01},
                'classification':{'labels':['장중 되밀림'],'reasons':[]},'flows':'외국인 -1,000 / 기관 +500',
                'forecasts':[{'model':'No macro ensemble','verdict':'적중'}]}
        result=build_post_close('samsung',review)
        self.assertIn('100,000원',' '.join(result['lines']))
        self.assertIn('외국인 -1,000',' '.join(result['lines']))
        self.assertNotIn('html',result)

    def test_non_prospective_or_late_record_is_not_mailed(self):
        row=dict(kind='direction',model='No macro ensemble',horizon_days='1',target_date='2026-10-07',prediction_date='2026-10-06',
                 created_at_utc='2026-10-06T01:00:00Z',is_prospective='False',p_up='.6',p_flat='.3',p_down='.1')
        now=datetime(2026,10,6,2,tzinfo=timezone.utc)
        self.assertIsNone(build_pre_open('samsung',[row],now))
        self.assertIsNone(build_pre_open('samsung',[dict(row,is_prospective='True')],now))

    def test_unpublished_run_is_rejected(self):
        from tools.notify_report_update import publication_matches
        payload={'phase':'pre_open','session_date':'2026-10-06','report_run_id':'new-run'}
        self.assertFalse(publication_matches(payload,'<title>2026-10-06</title>run_id <code>old-run</code>'))
        self.assertTrue(publication_matches(payload,'<title>2026-10-06</title>run_id <code>new-run</code>'))

    def test_missing_review_is_normal_skip(self):
        from tools.notify_report_update import read_review
        from urllib.error import HTTPError
        def missing(path):
            raise HTTPError(path,404,'missing',None,None)
        self.assertIsNone(read_review(missing,'samsung','2026-10-09'))
        def denied(path):
            raise HTTPError(path,403,'denied',None,None)
        with self.assertRaises(HTTPError):
            read_review(denied,'samsung','2026-10-09')


class MailWorkerTests(unittest.TestCase):
    def test_real_sqlite_worker_delivery_contracts(self):
        import subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        run=subprocess.run(['node','tests/report_mail_cases.mjs'],cwd=root,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr[-4000:])
