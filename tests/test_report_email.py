"""시스템이 게시한 요약만 하루 두 회차로 발송하고 수동 실행을 차단한다."""
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


class GmailTransportTests(unittest.TestCase):
    def test_tls_mime_auth_failure_and_uncertain_acceptance(self):
        import subprocess
        from pathlib import Path
        run=subprocess.run(['node','tests/gmail_smtp_cases.mjs'],cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr[-4000:])


class AutomaticMailTests(unittest.TestCase):
    def test_publisher_request_identifies_client_before_registering_event(self):
        """Python 기본 UA로 Worker 앞단에서 403/1010이 나던 회귀를 막는다."""
        import io
        import json
        import os
        from unittest.mock import patch
        from tools.notify_report_update import main

        env={'REPORT_MAIL_ENDPOINT':'https://counter.example','MAIL_PUBLISH_TOKEN':'secret',
             'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'schedule','GITHUB_RUN_ATTEMPT':'1'}
        review={'session_date':'2026-10-06','generated_at':'2026-10-06T08:00:00+00:00',
                'summary':{'close':100000,'c2c':.01,'gap':.02,'session':-.01}}
        output=io.StringIO()

        def network(request,timeout):
            url=request if isinstance(request,str) else request.full_url
            if url=='https://counter.example/mail/events':
                # 외부 호출만 대체하고 CLI의 원장·게시본 검증 및 요청 조립은 실제 실행한다.
                ua=request.get_header('User-agent','')
                self.assertTrue(ua and not ua.startswith('Python-urllib/'),
                                'Worker 요청에 애플리케이션 User-Agent가 필요합니다')
                self.assertEqual(request.get_method(),'POST')
                self.assertEqual(request.get_header('Authorization'),'Bearer secret')
                body=json.loads(request.data)
                self.assertEqual(body['automation'],{'event':'schedule','attempt':1,'caller':'','proof':''})
                self.assertEqual(body['session_date'],'2026-10-06')
                return io.BytesIO(b'{"status":"queued","event_id":"test-event","enabled":true,"configured":true}')
            if url.endswith('/commits/main'):
                return io.BytesIO(json.dumps({'sha':'a'*40}).encode())
            if url.endswith('/reviews/2026-10-06.json'):
                return io.BytesIO(json.dumps(review).encode())
            if url.endswith('/index.html'):
                return io.BytesIO(b'<p>2026-10-06 2026-10-06T08:00:00+00:00</p>')
            self.fail('예상하지 않은 외부 요청: '+url)

        with patch.dict(os.environ,env,clear=True), \
             patch('sys.argv',['notify','--target','samsung','--phase','post_close','--session','2026-10-06']), \
             patch('tools.notify_report_update.urlopen',side_effect=network), patch('sys.stdout',output):
            main()
        self.assertIn('queued test-event',output.getvalue())

    def test_only_schedule_or_signed_cloudflare_first_attempt_can_notify(self):
        from tools.notify_report_update import automatic_run
        import hashlib,hmac
        key='private-publish-key'
        now=1800000000
        raw=f'{now}:nonce'
        signature=hmac.new(key.encode(),('daily-report.yml|'+raw).encode(),hashlib.sha256).hexdigest()
        proof=raw+':'+signature
        def env(event,attempt='1',caller='',p=''):
            return {'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':event,'GITHUB_RUN_ATTEMPT':attempt,'MAIL_CALLER':caller,'MAIL_AUTOMATION_PROOF':p}
        self.assertTrue(automatic_run(env('schedule'),key,'pre_open',now))
        self.assertFalse(automatic_run(env('push'),key,'pre_open',now))
        self.assertFalse(automatic_run(env('workflow_dispatch',caller='cloudflare-cron'),key,'pre_open',now))
        self.assertFalse(automatic_run(env('schedule','2'),key,'pre_open',now))
        self.assertTrue(automatic_run(env('workflow_dispatch',caller='cloudflare-cron',p=proof),key,'pre_open',now))
        self.assertFalse(automatic_run(env('workflow_dispatch',caller='cloudflare-cron',p=proof),key,'post_close',now))
        self.assertFalse(automatic_run(env('workflow_dispatch',caller='cloudflare-cron',p=proof),key,'pre_open',now+14401))
        self.assertFalse(automatic_run({},key,'pre_open',now))

    def test_manual_cli_does_not_contact_publisher_or_email_service(self):
        import os
        from unittest.mock import patch
        from tools.notify_report_update import main
        env={'REPORT_MAIL_ENDPOINT':'https://counter.example','MAIL_PUBLISH_TOKEN':'secret',
             'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1'}
        with patch.dict(os.environ,env,clear=True), patch('sys.argv',['notify','--target','samsung','--phase','pre_open']), \
             patch('tools.notify_report_update.urlopen') as network, patch('builtins.print'):
            main()
            network.assert_not_called()
