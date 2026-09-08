import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import macro_utils as mu
from data_sources import _common, kosis


class MacroTests(unittest.TestCase):
    def monthly(self):
        dates = pd.date_range('2023-01-01', periods=30, freq='MS')
        return pd.DataFrame({'month': dates, 'value': np.arange(30) + 100.})

    def test_month_is_available_only_after_following_month_end(self):
        monthly = self.monthly()
        dates = pd.to_datetime(['2025-01-31', '2025-02-01', '2025-02-28', '2025-03-01'])
        got = mu.macro_features({'leading_cycle': monthly}, dates)
        self.assertEqual(got.macro_leading_cycle.tolist(), [22., 23., 23., 24.])

    def test_future_month_changes_do_not_change_earlier_features(self):
        a = self.monthly(); b = a.copy(); b.loc[b.month >= '2025-01-01', 'value'] = 999
        dates = pd.date_range('2024-08-01', '2025-02-28')
        pd.testing.assert_frame_equal(mu.macro_features({'leading_cycle': a}, dates),
                                      mu.macro_features({'leading_cycle': b}, dates))

    def test_missing_month_is_not_treated_as_previous_calendar_month(self):
        monthly = self.monthly().drop(index=23)
        got = mu.macro_features({'semiconductor_exports': monthly}, pd.to_datetime(['2025-03-01']))
        self.assertTrue(pd.isna(got.macro_semiconductor_mom.iloc[0]))
        self.assertAlmostEqual(got.macro_semiconductor_yoy.iloc[0], 124 / 112 - 1)

    def test_stale_months_expire(self):
        got = mu.macro_features({'leading_cycle': self.monthly()}, pd.to_datetime(['2026-01-01']))
        self.assertTrue(got.isna().all().all())

    def test_api_filters_exact_semiconductor_total_and_converts_units(self):
        rows = [dict(PRD_DE='202507', DT='1,250', C1='x', C1_NM='반도체', UNIT_NM='백만달러'),
                dict(PRD_DE='202507', DT='900', C1='y', C1_NM='메모리반도체', UNIT_NM='백만달러')]
        got = mu.parse_kosis_rows(rows, 'semiconductor_exports')
        self.assertEqual(got.value.tolist(), [1250000000.])
        with self.assertRaises(ValueError):
            mu.parse_kosis_rows(rows[1:], 'semiconductor_exports')

    def test_api_unknown_units_and_error_response_fail(self):
        with self.assertRaises(ValueError):
            mu.parse_kosis_rows({'err': '20', 'errMsg': 'bad key'}, 'leading_cycle')
        with self.assertRaises(ValueError):
            mu.parse_kosis_rows([dict(PRD_DE='202507', DT='1', C1_NM='반도체', UNIT_NM='원')],
                                'semiconductor_exports')

    def test_csv_standard_and_kosis_wide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.csv'
            path.write_text('산업별,2025.06,2025.07\n반도체,120,125\n메모리반도체,99,100\n', encoding='utf-8-sig')
            got = mu.read_macro_csv(path, 'semiconductor_exports')
            self.assertEqual(got.value.tolist(), [120., 125.])
            path.write_text('month,value\n2025-06,100\n2025-07,101\n', encoding='utf-8')
            self.assertEqual(mu.read_macro_csv(path, 'leading_cycle').value.tolist(), [100., 101.])

    def test_snapshots_preserve_revisions_and_cache_never_silently_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / 'macro_inputs'; inputs.mkdir()
            for key in mu.MACRO_SERIES:
                self.monthly().to_csv(inputs / f'{key}.csv', index=False)
            _, info1 = mu.load_macro_data(tmp, '2023-01-01', '2025-09-01')
            b = self.monthly(); b.loc[0, 'value'] = 99
            b.to_csv(inputs / 'leading_cycle.csv', index=False)
            _, info2 = mu.load_macro_data(tmp, '2023-01-01', '2025-09-01')
            self.assertNotEqual(info1['snapshot_hash'], info2['snapshot_hash'])
            self.assertEqual(len(list((Path(tmp) / 'macro_snapshots').glob('*.csv'))), 2)
            for p in inputs.glob('*.csv'): p.unlink()
            with patch.dict('os.environ', {}, clear=True), patch.object(mu, 'kosis_key', return_value=None):
                with self.assertRaises(RuntimeError): mu.load_macro_data(tmp, '2023-01-01', '2025-09-01')
                cached, _ = mu.load_macro_data(tmp, '2023-01-01', '2025-09-01', use_cache=True)
                self.assertEqual(cached['leading_cycle'].value.iloc[0], 99)

    def test_notebook_embeds_tested_macro_source(self):
        root = Path(__file__).resolve().parents[1]
        nb = json.loads((root / 'samsung_direction_model_colab.ipynb').read_text(encoding='utf-8'))
        cells = [c for c in nb['cells'] if 'macro_utils' in c.get('metadata', {}).get('tags', [])]
        self.assertEqual(len(cells), 1)
        # macro_utils 는 facade 라서 노트북에는 data_sources/ 모듈을 이어 붙인 본문이 들어간다.
        import sys
        sys.path.insert(0, str(root / 'tools'))
        from sync_notebook_helpers import helper_source
        self.assertEqual(''.join(cells[0]['source']), helper_source('macro_utils'))

    def test_api_resolves_total_code_then_requests_monthly_history(self):
        row = dict(PRD_DE='202507', DT='125', C1='total-code', C1_NM='반도체', UNIT_NM='달러')
        with patch.object(kosis, '_kosis_request', side_effect=[[row], [row]]) as api:
            got = mu.fetch_kosis_monthly('semiconductor_exports', '2023-01-01', '2025-09-01', 'private')
        self.assertEqual(got.value.iloc[0], 125)
        self.assertEqual(api.call_args_list[0].args[1]['objL1'], 'ALL')
        self.assertEqual(api.call_args_list[1].args[1]['objL1'], 'total-code')
        self.assertEqual(api.call_args_list[1].args[1]['startPrdDe'], '202301')

    def test_http_failure_does_not_expose_key(self):
        with patch.object(_common, 'urlopen', side_effect=OSError('URL with private-api-key')):
            with self.assertRaises(RuntimeError) as error:
                mu._kosis_request('private-api-key', {})
        self.assertNotIn('private-api-key', str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_notebook_uses_core_macro_features_including_acceleration(self):
        from test_pipeline_behavior import make_synthetic_raw, run_feature_cell
        months = pd.date_range('2019-01-01', '2025-05-01', freq='MS')
        monthly = pd.DataFrame({'month': months, 'value': 100 + np.sin(np.arange(len(months)))})
        raw = make_synthetic_raw()[0]
        ns = run_feature_cell(raw, USE_MACRO_FEATURES=True,
                              macro_features=mu.macro_features,
                              macro_data={k: monthly for k in mu.MACRO_SERIES})
        self.assertIn('macro_semiconductor_yoy_change_1m', ns['feature_cols'])
        self.assertIn('macro_semiconductor_yoy_change_3m', ns['feature_cols'])
        self.assertIn('macro_leading_cycle', ns['feature_cols'])
        self.assertEqual(ns['live_row'].filter(like='macro_').isna().sum().sum(), 0)


if __name__ == '__main__': unittest.main()


class DataSourcesPackageTests(unittest.TestCase):
    """macro_utils 는 facade 이고 구현은 data_sources/ 에 있다. 두 경로가 같은 것을 가리켜야 한다."""

    def test_facade_reexports_every_public_and_private_helper(self):
        import data_sources as ds
        for name in ("load_macro_data", "macro_features", "load_nsi", "load_cli", "load_term_spread",
                     "load_investor_flows", "fetch_customs_exports", "event_flags", "open_url",
                     "_kosis_request", "_flows_from_naver"):
            self.assertIs(getattr(mu, name), getattr(ds, name), name)

    def test_modules_only_depend_on_common(self):
        import ast
        root = Path(mu.__file__).resolve().parent / "data_sources"
        defined = {}
        for path in root.glob("*.py"):
            if path.name == "__init__.py":
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    defined[node.name] = path.stem
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined[target.id] = path.stem
        for path in root.glob("*.py"):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            foreign = {u for u in used if u in defined and defined[u] not in (path.stem, "_common")}
            self.assertEqual(foreign, set(), f"{path.stem} 이 다른 자료원 모듈의 이름을 씁니다: {foreign}")

    def test_notebook_copy_runs_standalone(self):
        # Colab 은 패키지 없이 셀 하나로 돈다. 이어 붙인 본문이 그 자체로 실행돼야 한다.
        root = Path(__file__).resolve().parents[1]
        import sys
        sys.path.insert(0, str(root / 'tools'))
        from sync_notebook_helpers import helper_source
        namespace = {}
        exec(compile(helper_source('macro_utils'), 'macro_cell', 'exec'), namespace)
        for name in ("load_macro_data", "load_nsi", "load_cli", "load_investor_flows", "event_flags"):
            self.assertIn(name, namespace)
