"""Keep the downloadable Colab notebook self-contained and identical to tested helpers."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# data_sources/ 의 모듈 순서. _common 이 먼저여야 나머지가 참조하는 이름이 정의된다.
DATA_SOURCE_MODULES = ["_common", "kosis", "ecos", "oecd", "exports", "flows", "dart"]
_PACKAGE_IMPORT = re.compile(r"^from data_sources[.\w]* import .*$", re.M)


def helper_source(tag):
    """노트북에 넣을 본문. macro_utils 는 facade 라서 data_sources/ 모듈을 이어 붙인다."""
    if tag != "macro_utils":
        return (ROOT / f"{tag}.py").read_text(encoding="utf-8")
    parts = []
    for name in DATA_SOURCE_MODULES:
        text = (ROOT / "data_sources" / f"{name}.py").read_text(encoding="utf-8")
        text = _PACKAGE_IMPORT.sub("", text)          # 한 네임스페이스에 들어가므로 패키지 import 는 뺀다
        parts.append(f"# ==== data_sources/{name}.py ====\n{text.strip()}\n")
    return "\n\n".join(parts) + "\n"


def sync():
    path = ROOT / "samsung_direction_model_colab.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for tag in ["forecast_utils", "macro_utils", "report_html"]:
        source = helper_source(tag)
        cells = [c for c in notebook["cells"] if tag in c.get("metadata", {}).get("tags", [])]
        if not cells:
            # 새 헬퍼 셀은 기존 헬퍼 셀들 바로 뒤에 끼운다(설정 셀보다 앞이어야 한다).
            tagged = [i for i, c in enumerate(notebook["cells"])
                      if set(c.get("metadata", {}).get("tags", [])) & {"forecast_utils", "macro_utils", "report_html"}]
            position = (max(tagged) + 1) if tagged else 4
            cell = {"cell_type": "code", "execution_count": None,
                    "metadata": {"tags": [tag]}, "outputs": [], "source": []}
            notebook["cells"].insert(position, cell)
        else:
            cell = cells[0]
        cell["source"] = source.splitlines(keepends=True)
    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sync()
