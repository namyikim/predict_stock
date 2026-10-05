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

    def test_rebuilt_page_uses_official_forecast_identity_not_render_identity(self):
        from tools.notify_report_update import publication_matches
        payload={'phase':'pre_open','session_date':'2026-10-06','report_run_id':'morning-run'}
        note=('보고서는 다시 만들었지만 예측은 원장에 기록된 공식 사전 예측'
              '(10-06 06:41 KST 실행 morning-run)을 그대로 보여 줍니다.')
        summary='<section id="easy-summary"><div>다음 거래일 2026-10-06 (화) 예측</div><p>'+note+'</p></section>'
        footer='run_id <code>render-run</code>'
        self.assertTrue(publication_matches(payload,summary+footer))
        # 다른 예측일·실행, 뉴스에 인용된 문장, 문자열 일부 일치는 증거가 아니다.
        self.assertFalse(publication_matches(payload,summary.replace('2026-10-06','2026-10-07')+footer))
        self.assertFalse(publication_matches(payload,summary.replace('morning-run','morning-run-extra')+footer))
        self.assertFalse(publication_matches(payload,summary.replace('easy-summary','news')+footer))
        # 재생성 실행이 우연히 같아도 공식 예측의 불일치를 무시하지 않는다.
        self.assertFalse(publication_matches(payload,summary.replace('morning-run','other-run')+
                                            'run_id <code>morning-run</code>'))

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
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return cls(2026,10,6,8,tzinfo=timezone.utc)

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
             patch('tools.notify_report_update.urlopen',side_effect=network), patch('sys.stdout',output), \
             patch('tools.notify_report_update.datetime',Clock):
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

class MailRecoveryTests(unittest.TestCase):
    def test_scoped_admin_and_recovery_signatures_expire_and_do_not_authorize_other_editions(self):
        import hashlib, hmac
        from tools.notify_report_update import authorized_run
        now=int(datetime(2026,10,5,23,tzinfo=timezone.utc).timestamp())
        for caller in ('admin-mail','cloudflare-mail'):
            raw=f'{now}:nonce'
            sig=hmac.new(b'key',f'report-email.yml|{caller}|pre_open|2026-10-06|{raw}'.encode(),hashlib.sha256).hexdigest()
            env={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1',
                 'MAIL_CALLER':caller,'MAIL_AUTOMATION_PROOF':raw+':'+sig,'MAIL_SESSION':'2026-10-06'}
            self.assertTrue(authorized_run(env,'key','pre_open',now))
            self.assertFalse(authorized_run(env,'key','post_close',now))
            self.assertFalse(authorized_run(dict(env,MAIL_SESSION='2026-10-07'),'key','pre_open',now))
            self.assertFalse(authorized_run(dict(env,GITHUB_RUN_ATTEMPT='2'),'key','pre_open',now))
            self.assertFalse(authorized_run(env,'key','pre_open',now+901))
            self.assertFalse(authorized_run(dict(env,MAIL_CALLER='cloudflare-cron'),'key','pre_open',now))

    def test_recovery_cli_skips_expired_session_before_reading_network(self):
        import os
        from unittest.mock import patch
        from tools.notify_report_update import main
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return cls(2026,10,6,0,0,tzinfo=timezone.utc)
        env={'REPORT_MAIL_ENDPOINT':'https://counter.example','MAIL_PUBLISH_TOKEN':'secret',
             'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'schedule','GITHUB_RUN_ATTEMPT':'1'}
        with patch.dict(os.environ,env,clear=True), patch('sys.argv',['notify','--target','samsung','--phase','pre_open']), \
             patch('tools.notify_report_update.datetime',Clock), patch('tools.notify_report_update.urlopen') as net, \
             patch('builtins.print'):
            main()
            net.assert_not_called()

    def test_todays_prediction_is_not_hidden_by_another_future_target(self):
        row=dict(kind='direction',model='No macro ensemble',horizon_days='1',target_date='2026-10-06',
                 prediction_date='2026-10-06',created_at_utc='2026-10-05T22:00:00Z',is_prospective='True',
                 run_id='today',p_up='.6',p_flat='.3',p_down='.1')
        rows=[row,dict(row,target_date='2026-10-07',prediction_date='2026-10-07',run_id='tomorrow')]
        result=build_pre_open('samsung',rows,datetime(2026,10,5,23,tzinfo=timezone.utc))
        self.assertEqual(result['session_date'],'2026-10-06')
        self.assertEqual(result['report_run_id'],'today')

    def test_invalid_recovery_signature_is_failure_not_a_silent_success(self):
        import os
        from unittest.mock import patch
        from tools.notify_report_update import main
        env={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1',
             'MAIL_CALLER':'admin-mail','MAIL_PUBLISH_TOKEN':'key','MAIL_AUTOMATION_PROOF':'invalid'}
        with patch.dict(os.environ,env,clear=True), patch('sys.argv',['notify','--target','samsung','--phase','pre_open']), \
             patch('tools.notify_report_update.urlopen') as network:
            with self.assertRaisesRegex(RuntimeError,'서명'):
                main()
            network.assert_not_called()
