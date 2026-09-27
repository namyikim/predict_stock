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


class WriterAlignmentTests(unittest.TestCase):
    """기여도는 원장이 그 실행을 기록한 이름·실행 ID 로 남아야 요약이 짝을 짓는다(2026-09-28: 연결 0건).

    원인 둘: 저녁 실행은 원장에 'Candidate evening forecast' 로 기록되는데 기여도는 대표 이름으로 남았고,
    기여도 중복 제거가 예측일 기준(첫 기록만)이라 전날 저녁 기록이 아침 대표 실행의 기여도를 밀어냈다.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def source(self):
        import json
        nb = json.loads((self.ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
        return "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")

    def test_writer_uses_the_ledger_label_and_keeps_every_run(self):
        s = self.source()
        self.assertIn("_attr_model = EVENING_MODEL if RECORD_EVENING_ONLY else _attr_source", s)
        self.assertIn('drop_duplicates(["run_id", "model", "kind", "horizon_days", "rank"]', s)
        self.assertNotIn('drop_duplicates(["prediction_date", "model", "rank"]', s)
        self.assertIn('"source_model": _attr_source', s)

    def test_stored_attribution_matches_the_ledger_labels(self):
        """저장소의 기여도 기록: 원장이 저녁 후보로 기록한 실행은 기여도도 저녁 후보 이름이어야 한다."""
        import pandas as pd
        for target in ("samsung", "sk_hynix"):
            folder = self.ROOT / "forecast_history" / target
            if not (folder / "attribution.csv").exists():
                continue
            attr = pd.read_csv(folder / "attribution.csv", low_memory=False)
            ledger = pd.read_csv(folder / "forecast_log.csv", low_memory=False)
            evening = set(ledger.loc[(ledger["kind"] == "direction")
                                     & (ledger["model"] == "Candidate evening forecast"), "run_id"])
            wrong = attr[attr["run_id"].isin(evening) & (attr["model"] != "Candidate evening forecast")]
            self.assertEqual(len(wrong), 0, f"{target}: 저녁 실행 기여도가 대표 이름으로 남았습니다 {sorted(set(wrong['run_id']))[:3]}")


class PriceScopeTests(unittest.TestCase):
    """가격 예측(1·5·20거래일)의 기여도 요약(2026-09-28). 방향과 다른 모델이라 따로 집계한다."""

    def summary(self, attrs, ledger, scope):
        script = ('const f=require(process.argv[1]); const a=JSON.parse(process.argv[2]);'
                  'console.log(JSON.stringify(f(a[0], a[1], a[2])));')
        r = subprocess.run(['node', '-e', script, str(MODULE), json.dumps([attrs, ledger, scope])],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def ledger(self, raw, actual, run='r1', h=5):
        return dict(run_id=run, model='Ridge', kind='price', horizon_days=str(h), prediction_date='2026-09-14',
                    target_date='2026-09-18', status='scored', is_prospective='True',
                    raw_predicted_return=str(raw), actual_return=str(actual), created_at_utc='2026-09-13T22:00:00Z')

    def attr(self, h=5, feature='micron_ret_5', value='0.4', run='r1'):
        return dict(run_id=run, model='Ridge', kind='price', horizon_days=str(h), prediction_date='2026-09-14',
                    feature=feature, contribution=value)

    def test_price_is_correct_when_raw_sign_matches_actual(self):
        g = self.summary([self.attr()], [self.ledger(0.01, 0.03)], {'kind': 'price', 'horizon': 5})[0]
        self.assertEqual((g['matched'], g['correct']), (1, 1))
        g = self.summary([self.attr()], [self.ledger(0.01, -0.03)], {'kind': 'price', 'horizon': 5})[0]
        self.assertEqual((g['matched'], g['correct']), (1, 0))

    def test_horizons_do_not_mix(self):
        attrs = [self.attr(h=5), self.attr(h=20, feature='macro_leading_cycle')]
        ledger = [self.ledger(0.01, 0.03, h=5), self.ledger(0.01, 0.03, h=20)]
        five = self.summary(attrs, ledger, {'kind': 'price', 'horizon': 5})[0]
        twenty = self.summary(attrs, ledger, {'kind': 'price', 'horizon': 20})[0]
        self.assertEqual([r['feature'] for r in five['rows']], ['micron_ret_5'])
        self.assertEqual([r['feature'] for r in twenty['rows']], ['macro_leading_cycle'])

    def test_price_rows_join_on_prediction_date_not_maturity(self):
        # 가격 원장의 target_date(만기 09-18)가 아니라 예측일(09-14)로 짝을 짓는다.
        g = self.summary([self.attr()], [self.ledger(0.01, 0.03)], {'kind': 'price', 'horizon': 5})[0]
        self.assertEqual(g['dates'], ['2026-09-14'])

    def test_direction_scope_ignores_price_rows(self):
        out = self.summary([self.attr()], [self.ledger(0.01, 0.03)], {'kind': 'direction', 'horizon': 1})
        self.assertEqual(out, [])

    def test_writer_records_price_horizons_in_the_morning_only(self):
        s = WriterAlignmentTests().source()
        self.assertIn('if not RECORD_EVENING_ONLY:', s)
        self.assertIn('"kind": "price", "horizon_days": int(_h)', s)
        self.assertIn('feature_contributions({"estimator": _fitted}, live_X, feature_cols)', s)
