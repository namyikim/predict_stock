"""Exercise the local CLI with tiny, offline notebooks and real output files."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_notebook as runner


class RunNotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = self.root / "results"
        self.notebook = self.root / "samsung_direction_model_colab.ipynb"
        self.write_notebook()

    def write_notebook(self, fail_target=None):
        sources = [
            "%%capture\n!pip install should-never-run\n",
            "import os, json\nfrom pathlib import Path\n"
            "assert 'previous_target' not in globals(), 'namespace leaked'\n"
            "RUN_TARGETS = [t.strip() for t in "
            "(os.environ.get('PREDICT_STOCK_TARGETS') or 'samsung,sk_hynix').split(',') if t.strip()]\n"
            "TARGET = RUN_TARGETS[0]\n"
            "STORAGE_ROOT = Path(os.environ['PREDICT_STOCK_STORAGE'])\n"
            "STORAGE_ROOT.mkdir(parents=True, exist_ok=True)\n",
            "START_DATE = '2020-01-01'\nQUICK_MODE = False\nMAX_FOLDS = None\n"
            "BOOTSTRAP_B = 2000\nTRANSFORMER_EPOCHS = 50\nTRANSFORMER_PATIENCE = 10\n"
            "USE_DATA_CACHE = False\nUSE_MACRO_FEATURES = True\n",
            f"if TARGET == {fail_target!r}:\n    raise ValueError('synthetic target failure')\n"
            "probability = {'samsung': 0.61, 'sk_hynix': 0.42}[TARGET]\n"
            "payload = dict(target=TARGET, probability=probability, quick=QUICK_MODE, "
            "folds=MAX_FOLDS, bootstrap=BOOTSTRAP_B, epochs=TRANSFORMER_EPOCHS, "
            "patience=TRANSFORMER_PATIENCE, cache=USE_DATA_CACHE, macro=USE_MACRO_FEATURES, "
            "targets=RUN_TARGETS)\n"
            "(STORAGE_ROOT / 'forecast.json').write_text(json.dumps(payload), encoding='utf-8')\n"
            "import pandas as pd\n"
            "native_metrics = pd.DataFrame([dict(accuracy=0.5, balanced_accuracy=0.5, log_loss=0.69)])\n"
            "improvement_table = pd.DataFrame([dict(model=TARGET)])\n"
            "previous_target = TARGET\n",
            # Like the real notebook, the final cell cannot replay without IPython history.
            "if len(RUN_TARGETS) > 1 and 'In' not in globals():\n"
            "    print('additional targets skipped: no IPython history')\n",
        ]
        cells = [{"cell_type": "markdown", "source": ["offline fixture"]}]
        cells.extend({"cell_type": "code", "source": s.splitlines(keepends=True)} for s in sources)
        self.notebook.write_text(json.dumps({"cells": cells}), encoding="utf-8")

    def invoke(self, *args):
        with patch.object(runner, "__file__", str(self.root / "tools" / "run_notebook.py")), \
                patch.object(sys, "argv", ["run_notebook.py", "--storage", str(self.storage), *args]), \
                contextlib.redirect_stdout(io.StringIO()):
            runner.main()

    def read_output(self, target=None):
        path = self.storage / target if target else self.storage
        return json.loads((path / "forecast.json").read_text(encoding="utf-8"))

    def test_default_runs_both_targets_with_distinct_artifacts_and_flags(self):
        with patch.dict(os.environ, {}, clear=True):
            self.invoke("--quick", "--use-cache", "--no-macro")
        for target, probability in [("samsung", 0.61), ("sk_hynix", 0.42)]:
            with self.subTest(target=target):
                self.assertEqual(self.read_output(target), dict(
                    target=target, probability=probability, quick=True, folds=3,
                    bootstrap=400, epochs=8, patience=3, cache=True, macro=False, targets=[target]))
        self.assertFalse((self.storage / "forecast.json").exists())

    def test_single_env_target_preserves_exact_storage_path_and_environment(self):
        original = {"PREDICT_STOCK_TARGETS": "sk_hynix", "PREDICT_STOCK_STORAGE": "original", "MPLBACKEND": "svg"}
        with patch.dict(os.environ, original, clear=True):
            self.invoke()
            self.assertEqual(dict(os.environ), original)
        output = self.read_output()
        self.assertEqual((output["target"], output["probability"]), ("sk_hynix", 0.42))
        self.assertEqual((output["quick"], output["cache"], output["macro"]), (False, False, True))
        self.assertFalse((self.storage / "sk_hynix").exists())

    def test_empty_environment_defaults_to_both_targets(self):
        with patch.dict(os.environ, {"PREDICT_STOCK_TARGETS": "  "}, clear=True):
            self.invoke()
        self.assertEqual(self.read_output("sk_hynix")["target"], "sk_hynix")

    def test_cli_targets_override_environment(self):
        with patch.dict(os.environ, {"PREDICT_STOCK_TARGETS": "samsung"}, clear=True):
            self.invoke("--targets", "sk_hynix")
        self.assertEqual(self.read_output()["target"], "sk_hynix")

    def test_invalid_targets_fail_before_any_notebook_output(self):
        for value in ("samsung,unknown", ", ,", "samsung,samsung"):
            with self.subTest(value=value), patch.dict(os.environ, {"PREDICT_STOCK_TARGETS": value}, clear=True):
                with self.assertRaises((ValueError, SystemExit)):
                    self.invoke()
                self.assertFalse(self.storage.exists())

    def test_second_target_failure_is_loud_and_restores_environment(self):
        self.write_notebook(fail_target="sk_hynix")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "sk_hynix") as caught:
                self.invoke()
            self.assertIsInstance(caught.exception.__cause__, ValueError)
            self.assertEqual(dict(os.environ), {})
        self.assertEqual(self.read_output("samsung")["probability"], 0.61)
        self.assertFalse((self.storage / "sk_hynix" / "forecast.json").exists())

    def test_cli_handles_notebook_unicode_on_legacy_windows_console(self):
        notebook = json.loads(self.notebook.read_text(encoding="utf-8"))
        notebook["cells"].append({"cell_type": "code", "source": ["print('삼성전자 ✅')"]})
        self.notebook.write_text(json.dumps(notebook), encoding="utf-8")
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii")
        self.addCleanup(stream.close)
        with patch.object(runner, "__file__", str(self.root / "tools" / "run_notebook.py")), \
                patch.object(sys, "argv", ["run_notebook.py", "--storage", str(self.storage)]), \
                patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream), \
                patch.dict(os.environ, {"PREDICT_STOCK_TARGETS": "samsung"}):
            runner.main()
        stream.flush()
        self.assertIn("삼성전자 ✅", raw.getvalue().decode("utf-8"))

    def test_callable_returns_separate_namespaces_in_requested_order(self):
        with contextlib.redirect_stdout(io.StringIO()):
            results = runner.run_notebook(self.storage, targets="sk_hynix,samsung",
                                          notebook_path=self.notebook)
        self.assertEqual(list(results), ["sk_hynix", "samsung"])
        self.assertIsNot(results["sk_hynix"], results["samsung"])
        self.assertEqual(results["sk_hynix"]["probability"], 0.42)
        self.assertEqual(results["samsung"]["probability"], 0.61)


if __name__ == "__main__":
    unittest.main()
