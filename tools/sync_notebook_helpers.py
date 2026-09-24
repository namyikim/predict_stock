"""Keep the downloadable Colab notebook self-contained and identical to tested helpers."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# data_sources/ 의 모듈 순서. _common 이 먼저여야 나머지가 참조하는 이름이 정의된다.
DATA_SOURCE_MODULES = ["_common", "kosis", "ecos", "oecd", "exports", "flows", "dart", "us_calendar", "dram_spot", "fred", "fx_inputs"]
_PACKAGE_IMPORT = re.compile(r"^from data_sources[.\w]* import .*$", re.M)


HELPER_TAGS = ["forecast_utils", "macro_utils", "report_html", "github_pages"]


def github_pages_cell():
    """tools/github_pages.py 를 노트북 이름과 섞이지 않게 모듈 하나로 넣는다(발행 묶기 ④, 2026-09-24).

    노트북은 Colab 에서 자립해야 해서 tools/ 를 import 하지 않는다. 그렇다고 발행 코드를 따로 두면 두 벌이 어긋난다
    (예전 github_put 은 재시도 규칙이 달랐다). 그래서 같은 소스를 문자열로 넣어 모듈로 실행한다.
    """
    source = (ROOT / "tools" / "github_pages.py").read_text(encoding="utf-8")
    if "'''" in source or source.rstrip().endswith("\\"):
        raise ValueError("github_pages.py 를 r'''…''' 문자열로 넣을 수 없습니다")
    return ("# tools/github_pages.py 를 그대로 넣는다(tools/sync_notebook_helpers.py 가 만든다 — 여기서 고치지 마세요).\n"
            "# 노트북 이름과 섞이지 않게 모듈 github_pages 로 둔다. 발행은 github_pages.batch()/publish() 를 쓴다.\n"
            "import sys as _sys\n"
            "import types as _types\n"
            "_GITHUB_PAGES_SOURCE = r'''" + source + "'''\n"
            "github_pages = _types.ModuleType(\"github_pages\")\n"
            "exec(compile(_GITHUB_PAGES_SOURCE, \"github_pages.py\", \"exec\"), github_pages.__dict__)\n"
            "_sys.modules[\"github_pages\"] = github_pages\n")


def helper_source(tag):
    """노트북에 넣을 본문. macro_utils 는 facade 라서 data_sources/ 모듈을 이어 붙인다."""
    if tag == "github_pages":
        return github_pages_cell()
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
    for tag in HELPER_TAGS:
        source = helper_source(tag)
        cells = [c for c in notebook["cells"] if tag in c.get("metadata", {}).get("tags", [])]
        if not cells:
            # 새 헬퍼 셀은 기존 헬퍼 셀들 바로 뒤에 끼운다(설정 셀보다 앞이어야 한다).
            tagged = [i for i, c in enumerate(notebook["cells"])
                      if set(c.get("metadata", {}).get("tags", [])) & set(HELPER_TAGS)]
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
