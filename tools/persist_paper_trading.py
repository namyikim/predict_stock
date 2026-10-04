"""모의원장 저장 재시도와 실패 실행 이후 재개 차단. 거래를 다시 계산하지 않는다."""
import argparse
import json
import os
import subprocess
import sys


def check_previous(runs, current_id, recovery_confirmed=False, attempt=1):
    if attempt > 1 and not recovery_confirmed:
        raise RuntimeError('실패 실행의 재실행도 원장 복구 확인 후에만 허용됩니다.')
    completed=[r for r in runs if r['id'] != current_id and r['status']=='completed']
    latest=max(completed,key=lambda r:r['id']) if completed else None
    if latest and latest['conclusion'] != 'success' and not recovery_confirmed:
        raise RuntimeError('이전 모의운용 실패: 보존된 원장과 main의 상태를 대조·복구한 뒤 recovery_confirmed로 재개하세요.')


def push_with_retries(root, attempts=3, run=subprocess.run):
    for _ in range(attempts):
        if run(['git','pull','--rebase','origin','main'],cwd=root).returncode:
            raise RuntimeError('원장 rebase 실패: 충돌을 덮어쓰지 않습니다.')
        if run(['git','push','origin','HEAD:main'],cwd=root).returncode == 0:
            return
    raise RuntimeError('모의원장 push 재시도 실패: 보존 아티팩트에서 복구가 필요합니다.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-run',type=int)
    args=parser.parse_args()
    if args.check_run:
        check_previous(json.load(sys.stdin)['workflow_runs'],args.check_run,os.environ.get('RECOVERY_CONFIRMED')=='true',int(os.environ.get('GITHUB_RUN_ATTEMPT','1')))
    else:
        push_with_retries('.')


if __name__ == '__main__':
    main()
