"""Execute the self-contained notebook locally without Jupyter or interactive plots."""
import argparse
import json
import os
from pathlib import Path
import sys


SUPPORTED_TARGETS = ("samsung", "sk_hynix")


def run_notebook(storage, *, targets=None, notebook_path=None, quick=False,
                 use_cache=False, no_macro=False):
    """Run requested targets in order and return their final namespaces.

    ``targets`` is a comma-separated string, defaulting to the environment or
    both supported stocks. Multi-target runs use ``storage/<target>``; a single
    target retains the exact storage path used by existing automation.
    Environment overrides are scoped to this call; execution is not thread-safe.
    """
    requested = os.environ.get("PREDICT_STOCK_TARGETS", "") if targets is None else targets
    requested = requested.strip() or ",".join(SUPPORTED_TARGETS)
    target_names = [name.strip() for name in requested.split(",") if name.strip()]
    if not target_names or any(name not in SUPPORTED_TARGETS for name in target_names):
        raise ValueError("Targets must contain samsung and/or sk_hynix")
    if len(set(target_names)) != len(target_names):
        raise ValueError("Duplicate targets would overwrite their outputs")

    root = Path(__file__).resolve().parents[1]
    notebook_path = Path(notebook_path) if notebook_path is not None else root / "samsung_direction_model_colab.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    storage = Path(storage).resolve()
    saved_env = {key: os.environ.get(key) for key in
                 ("PREDICT_STOCK_TARGETS", "PREDICT_STOCK_STORAGE", "MPLBACKEND")}
    results = {}
    try:
        for target in target_names:
            target_storage = storage / target if len(target_names) > 1 else storage
            os.environ.update(PREDICT_STOCK_TARGETS=target,
                              PREDICT_STOCK_STORAGE=str(target_storage), MPLBACKEND="Agg")
            # The notebook's final replay cell needs IPython's In history. Give
            # each run exactly one target so the CLI owns all orchestration.
            namespace = {"__name__": "__main__", "display": lambda *a, **kw: None}
            print(f"\nRunning target {target}: {target_storage}", flush=True)
            for i, cell in enumerate(notebook["cells"]):
                if cell["cell_type"] != "code":
                    continue
                source = "".join(cell["source"])
                if source.lstrip().startswith("%%capture"):
                    continue  # Dependencies are installed in the local environment.
                print(f"Running {target} cell {i}", flush=True)
                try:
                    exec(compile(source, f"{target}-notebook-cell-{i}", "exec"), namespace)
                except Exception as exc:
                    raise RuntimeError(f"Target {target} failed in notebook cell {i}") from exc
                if source.startswith('START_DATE ='):
                    if quick:
                        namespace.update(QUICK_MODE=True, MAX_FOLDS=3, BOOTSTRAP_B=400,
                                         TRANSFORMER_EPOCHS=8, TRANSFORMER_PATIENCE=3)
                    namespace["USE_DATA_CACHE"] = use_cache
                    namespace["USE_MACRO_FEATURES"] = not no_macro
            print(f"\nExternal evaluation ({target}):", flush=True)
            print(namespace["native_metrics"][["accuracy", "balanced_accuracy", "log_loss"]].to_string())
            print(namespace["improvement_table"].to_string(index=False))
            results[target] = namespace
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return results


def main():
    # The notebook prints Korean and symbols unavailable in legacy Windows encodings.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True, help="Persistent output directory; multiple targets use target subdirectories")
    parser.add_argument("--targets", help="Comma-separated samsung,sk_hynix; defaults to PREDICT_STOCK_TARGETS or both")
    parser.add_argument("--quick", action="store_true", help="Only recent 3 outer folds, smaller bootstrap")
    parser.add_argument("--use-cache", action="store_true", help="Replay cached snapshot; do not use for daily updates")
    parser.add_argument("--no-macro", action="store_true", help="Explicitly run the market-only model")
    args = parser.parse_args()
    run_notebook(args.storage, targets=args.targets, quick=args.quick,
                 use_cache=args.use_cache, no_macro=args.no_macro)


if __name__ == "__main__":
    main()
