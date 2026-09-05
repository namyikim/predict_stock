"""Keep the downloadable Colab notebook self-contained and identical to tested helpers."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync():
    path = ROOT / "samsung_direction_model_colab.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for tag in ["forecast_utils", "macro_utils"]:
        source = (ROOT / f"{tag}.py").read_text(encoding="utf-8")
        cells = [c for c in notebook["cells"] if tag in c.get("metadata", {}).get("tags", [])]
        if not cells:
            cell = {"cell_type": "code", "execution_count": None,
                    "metadata": {"tags": [tag]}, "outputs": [], "source": []}
            notebook["cells"].insert(4, cell)
        else:
            cell = cells[0]
        cell["source"] = source.splitlines(keepends=True)
    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sync()
