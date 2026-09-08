"""G20 경기선행지수 자료원을 확인한다: OECD 데이터 API(기본, 키 불필요) 또는 FRED.

    python tools/probe_fred.py                      # OECD G20 조회
    FRED_API_KEY=... FRED_CLI_SERIES_ID=G7LOLITOAASTSAM python tools/probe_fred.py   # FRED로 바꿔 쓸 때

FRED에는 G20 집계가 없다(G7·개별국만, OECD 전체는 2022-11에서 끊김). 그래서 기본은 OECD다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macro_utils as mu  # noqa: E402


def main():
    if mu.FRED_CLI_SERIES_ID:
        key = mu.fred_key()
        if not key:
            raise SystemExit("FRED_CLI_SERIES_ID를 쓰려면 FRED_API_KEY가 필요합니다.")
        frame = mu.fetch_fred_monthly(mu.FRED_CLI_SERIES_ID, key, "2024-01-01")
        print(f"FRED {mu.FRED_CLI_SERIES_ID}: {len(frame)}행, 마지막 {frame['month'].max():%Y-%m} = {frame['value'].iloc[-1]}")
        return
    frame = mu.fetch_oecd_cli(mu.CLI_REF_AREA, "2024-01-01")
    print(f"OECD {mu.CLI_REF_AREA}: {len(frame)}행, 마지막 {frame['month'].max():%Y-%m} = {frame['value'].iloc[-1]}")
    print(frame.tail(4).to_string(index=False))


if __name__ == "__main__":
    main()
