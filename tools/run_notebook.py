"""Execute the self-contained notebook locally without Jupyter or interactive plots."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True, help="Persistent output/ledger directory")
    parser.add_argument("--quick", action="store_true", help="Only recent 3 outer folds, smaller bootstrap")
    parser.add_argument("--use-cache", action="store_true", help="Replay cached snapshot; do not use for daily updates")
    parser.add_argument("--no-macro", action="store_true", help="Explicitly run the market-only model")
    args = parser.parse_args()
    os.environ["PREDICT_STOCK_STORAGE"] = str(args.storage.resolve())
    os.environ["MPLBACKEND"] = "Agg"
    root = Path(__file__).resolve().parents[1]
    notebook = json.loads((root / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
    namespace = {"__name__": "__main__", "display": lambda *a, **kw: None}
    for i, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        if source.lstrip().startswith("%%capture"):
            continue  # Dependencies are installed explicitly in the local virtual environment.
        print(f"Running cell {i}", flush=True)
        exec(compile(source, f"notebook-cell-{i}", "exec"), namespace)
        if source.startswith('START_DATE ='):
            if args.quick:
                namespace.update(QUICK_MODE=True, MAX_FOLDS=3, BOOTSTRAP_B=400,
                                 TRANSFORMER_EPOCHS=8, TRANSFORMER_PATIENCE=3)
            namespace["USE_DATA_CACHE"] = args.use_cache
            namespace["USE_MACRO_FEATURES"] = not args.no_macro
    print("\nExternal evaluation:", flush=True)
    print(namespace["native_metrics"][["accuracy", "balanced_accuracy", "log_loss"]].to_string())
    print(namespace["improvement_table"].to_string(index=False))


if __name__ == "__main__":
    main()
