"""ECOS에서 뉴스심리지수 통계표·항목 코드를 찾는다.

2026-09-01 신(新)뉴스심리지수로 바뀌면서 통계표 코드가 달라졌을 수 있다. 기본값
(521Y001 / A001)으로 조회가 실패하면 이 스크립트로 코드를 확인하고 환경변수
ECOS_NSI_STAT_CODE, ECOS_NSI_ITEM_CODE 로 넘긴다.

    ECOS_API_KEY=... python tools/probe_ecos.py            # '뉴스심리' 검색
    ECOS_API_KEY=... python tools/probe_ecos.py 심리        # 다른 키워드
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macro_utils as mu  # noqa: E402


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "뉴스심리"
    key = mu.ecos_key()
    if not key:
        raise SystemExit("ECOS_API_KEY 환경변수(또는 Colab 보안 비밀)가 필요합니다.")
    hits = mu.search_ecos_tables(key, keyword)
    if not hits:
        print(f"'{keyword}'가 이름에 든 통계표가 없습니다.")
        return
    for r in hits:
        print(f"{r.get('STAT_CODE')}  {r.get('STAT_NAME')}  (주기 {r.get('CYCLE', '?')})")
        for code, name, cycle in r.get("items", []):
            print(f"    항목 {code}  {name}  [{cycle}]")
    print("\n일별(D) 항목의 코드를 ECOS_NSI_STAT_CODE / ECOS_NSI_ITEM_CODE 로 지정하세요.")
    try:
        sample = mu.fetch_ecos_daily(mu.NSI_STAT_CODE, mu.NSI_ITEM_CODE, "2026-08-01", "2026-09-30", key)
        print(f"\n현재 설정({mu.NSI_STAT_CODE}/{mu.NSI_ITEM_CODE}) 조회 성공: {len(sample)}행, 마지막 {sample['date'].max().date()}")
    except Exception as exc:
        print(f"\n현재 설정({mu.NSI_STAT_CODE}/{mu.NSI_ITEM_CODE}) 조회 실패: {exc}")


if __name__ == "__main__":
    main()
