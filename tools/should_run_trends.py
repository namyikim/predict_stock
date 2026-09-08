# -*- coding: utf-8 -*-
"""최근 검색어 보고서가 충분히 새로우면 예약 재시도를 건너뛴다."""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
MIN_INTERVAL = timedelta(hours=2, minutes=30)
GENERATED_RE = re.compile(r"생성\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+KST")


def should_run(path, now=None):
    """보고서가 없거나 마지막 생성 후 2시간 30분이 지났으면 True."""
    path = Path(path)
    now = now or datetime.now(KST)
    if not path.exists():
        return True, "검색어 보고서가 없습니다"
    match = GENERATED_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        return True, "검색어 보고서에서 생성 시각을 찾지 못했습니다"
    generated = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    age = now - generated
    if age < timedelta(0):
        return True, f"검색어 보고서 생성 시각이 미래입니다({generated:%Y-%m-%d %H:%M} KST)"
    if age < MIN_INTERVAL:
        return False, f"검색어 보고서가 최근에 생성됐습니다({generated:%Y-%m-%d %H:%M} KST)"
    return True, f"검색어 보고서 갱신 시각이 됐습니다(마지막 {generated:%Y-%m-%d %H:%M} KST)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="docs/trends/index.html")
    args = parser.parse_args()
    run, reason = should_run(args.path)
    print(reason)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
