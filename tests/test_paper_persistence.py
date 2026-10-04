"""원장 저장 실패 후에는 이전 상태에서 조용히 재시작하지 않는다."""
import unittest
from subprocess import CompletedProcess
from tools.persist_paper_trading import check_previous, push_with_retries


class PersistenceTests(unittest.TestCase):
    def test_previous_failure_requires_explicit_recovery(self):
        runs=[{'id':5,'status':'completed','conclusion':'failure'},
              {'id':6,'status':'in_progress','conclusion':None}]
        with self.assertRaises(RuntimeError):
            check_previous(runs,6,False)
        check_previous(runs,6,True)
        check_previous([dict(runs[0],conclusion='success')],6,False)

    def test_rejected_push_rebases_again_without_recalculating_book(self):
        calls=[]
        codes=iter([0,1,0,0])
        def run(cmd,**kwargs):
            calls.append(cmd)
            return CompletedProcess(cmd,next(codes))
        push_with_retries('.',run=run)
        self.assertEqual([c[1] for c in calls],['pull','push','pull','push'])

    def test_rebase_conflict_stops_without_push_or_overwrite(self):
        calls=[]
        def run(cmd,**kwargs):
            calls.append(cmd)
            return CompletedProcess(cmd,1)
        with self.assertRaises(RuntimeError):
            push_with_retries('.',run=run)
        self.assertEqual(len(calls),1)

    def test_rerun_cannot_bypass_failed_run_guard(self):
        with self.assertRaises(RuntimeError):
            check_previous([],6,False,attempt=2)

    def test_exhausted_push_attempts_fail(self):
        def run(cmd,**kwargs):
            return CompletedProcess(cmd,0 if cmd[1]=='pull' else 1)
        with self.assertRaises(RuntimeError):
            push_with_retries('.',run=run)
