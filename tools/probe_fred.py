"""FRED에서 G20 CLI 시리즈 코드를 확인한다.

    FRED_API_KEY=... python tools/probe_fred.py                 # 'G20 leading indicator' 검색
    FRED_API_KEY=... python tools/probe_fred.py "OECD total CLI"

기본 시리즈 코드(G20LOLITOAASTSAM)가 틀리면 환경변수 FRED_CLI_SERIES_ID 로 넘긴다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macro_utils as mu  # noqa: E402


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "G20 composite leading indicator amplitude adjusted"
    key = mu.fred_key()
    if not key:
        raise SystemExit("FRED_API_KEY 환경변수(또는 Colab 보안 비밀)가 필요합니다.")
    for sid, title, freq, end in mu.search_fred_series(key, text):
        print(f"{sid:22s} {freq or '?':3s} ~{end}  {title}")
    print(f"\n현재 설정: {mu.FRED_CLI_SERIES_ID}")
    try:
        frame = mu.fetch_fred_monthly(mu.FRED_CLI_SERIES_ID, key, "2024-01-01")
        print(f"조회 성공: {len(frame)}행, 마지막 {frame['month'].max():%Y-%m} = {frame['value'].iloc[-1]}")
    except Exception as exc:
        print("조회 실패:", exc)


if __name__ == "__main__":
    main()
