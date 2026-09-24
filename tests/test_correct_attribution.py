import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'docs/lab/correct_attribution.js'


class CorrectAttributionTests(unittest.TestCase):
    def summary(self, attrs, ledger):
        script = 'const f=require(process.argv[1]); console.log(JSON.stringify(f(...JSON.parse(process.argv[2]))));'
        r = subprocess.run(['node', '-e', script, str(MODULE), json.dumps([attrs, ledger])],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def ledger(self, run='r1', hit='1', model='M', **kwargs):
        return dict(run_id=run, model=model, target_date='2026-09-14', kind='direction',
                    status='scored', is_prospective='True', direction_correct=hit,
                    created_at_utc='2026-09-13T00:00:00Z', **kwargs)

    def attr(self, feature='x', value='3', run='r1', model='M'):
        return dict(run_id=run, model=model, prediction_date='2026-09-14',
                    feature=feature, contribution=value)

    def test_only_exact_scored_prospective_match(self):
        a=[self.attr(), self.attr(run='unknown')]
        for changes in [{'is_prospective':'False'}, {'status':'pending'}, {'direction_correct':'0'},
                        {'model':'OTHER'}, {'direction_correct':''}]:
            with self.subTest(changes=changes):
                out=self.summary(a,[{**self.ledger(),**changes}])
                self.assertEqual(sum(g['correct'] for g in out),0)
        out=self.summary(a,[self.ledger()])[0]
        self.assertEqual(out['correct'],1)

    def test_deduplicated_features_and_normalized_absolute_share(self):
        a=[self.attr(),self.attr(),self.attr('y','-1'),self.attr('bad','NaN')]
        g=self.summary(a,[self.ledger(),self.ledger()])[0]
        self.assertEqual(g['correct'],1)
        self.assertEqual(g['rows'][0]['feature'],'x')
        self.assertEqual(g['rows'][0]['share'],.75)
        self.assertEqual(g['rows'][1]['share'],.25)
        self.assertEqual(g['rows'][0]['topCount'],1)

    def test_earliest_prediction_not_best_rerun(self):
        first={**self.ledger(hit='0'), 'created_at_utc':'2026-09-13T00:00:00Z'}
        later={**self.ledger(run='r2'), 'created_at_utc':'2026-09-13T01:00:00Z'}
        g=self.summary([self.attr(),self.attr(run='r2')],[later,first])[0]
        self.assertEqual(g['correct'],0)
        self.assertEqual(g['matched'],1)

    def test_models_are_separate_and_zero_rows_are_not_counted(self):
        a=[self.attr(model='A'),self.attr(model='B',value='0')]
        g=self.summary(a,[self.ledger(model='A'),self.ledger(model='B')])
        self.assertEqual(len(g),2)
        self.assertEqual(g[0]['correct'],1)
        self.assertEqual(g[1]['correct'],0)

    def test_mean_share_includes_correct_days_without_feature(self):
        l2={**self.ledger(run='r2'),'target_date':'2026-09-15'}
        a2={**self.attr('y','100',run='r2'),'prediction_date':'2026-09-15'}
        g=self.summary([self.attr(),a2],[self.ledger(),l2])[0]
        self.assertEqual(g['correct'],2)
        self.assertEqual([r['share'] for r in g['rows']],[.5,.5])
