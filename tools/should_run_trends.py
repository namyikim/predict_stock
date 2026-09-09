# -*- coding: utf-8 -*-
"""최근 검색어 보고서가 충분히 새로우면 예약 재시도를 건너뛴다.

주의: 작업 트리의 파일을 보면 안 된다. :22·:37·:52 세 재시도 실행은 거의 같은 시각에 만들어져
각자 '서로가 발행하기 전'의 커밋을 체크아웃한다. 그래서 셋 다 게이트를 통과해 같은 보고서를
세 번 만들었다(2026-09-09: 24시간에 23회 발행). 원격 브랜치의 현재 내용을 봐야 한다.
"""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
MIN_INTERVAL = timedelta(hours=2, minutes=30)
GENERATED_RE = re.compile(r"생성\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+KST")


def published_text(path, ref="origin/main"):
    """원격 브랜치에 지금 올라가 있는 내용. 못 읽으면 작업 트리로 물러선다."""
    import subprocess
    if ref:
        try:
            # --depth 를 주면 안 된다. 전체 복제본에서 실행하면 그 저장소의 origin/main 이 얕은
            # 이력이 되어 이후 rebase 가 모든 파일에서 충돌한다(2026-09-09 실제로 겪었다).
            # Actions 의 얕은 체크아웃에서는 이 fetch 가 이력을 채우지만 이 저장소 크기에서는 몇 초다.
            subprocess.run(["git", "fetch", "--quiet", "origin", ref.split("/", 1)[-1]],
                           check=True, timeout=120)
            out = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True,
                                 text=True, timeout=60)
            if out.returncode == 0:
                return out.stdout
        except Exception:
            pass
    local = Path(path)
    return local.read_text(encoding="utf-8", errors="replace") if local.exists() else None


def should_run(path, now=None, ref="origin/main"):
    """보고서가 없거나 마지막 생성 후 2시간 30분이 지났으면 True."""
    now = now or datetime.now(KST)
    text = published_text(path, ref)
    if text is None:
        return True, "검색어 보고서가 없습니다"
    match = GENERATED_RE.search(text)
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
    parser.add_argument("--ref", default="origin/main",
                        help="이 ref 의 내용을 본다. 빈 문자열이면 작업 트리를 본다(테스트용)")
    args = parser.parse_args()
    run, reason = should_run(args.path, ref=args.ref or None)
    print(reason)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
