"""구글 트렌드 시계열이 표준 라이브러리만으로 받아지는지 확인하는 탐침.

공식 API가 아니라 웹 UI가 쓰는 내부 엔드포인트다. 문서화되어 있지 않고
언제든 바뀔 수 있으며, 요청이 잦으면 429로 막힌다. 실제로 쓸 수 있는지
확인하는 것이 이 스크립트의 목적이다.
"""
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
EXPLORE = "https://trends.google.com/trends/api/explore"
MULTILINE = "https://trends.google.com/trends/api/widgetdata/multiline"

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
opener.addheaders = [("User-Agent", UA), ("Accept-Language", "ko,en;q=0.9")]


def get(url, timeout=30):
    with opener.open(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def strip_prefix(text):
    """응답은 )]}' 로 시작한다(JSON 하이재킹 방지용 접두사)."""
    return text[text.index("{"):] if "{" in text else text


def probe(keyword, geo="KR", timeframe="2016-09-06 2026-09-06"):
    print(f"■ '{keyword}' geo={geo} time={timeframe}")

    # 1) 쿠키 확보 — 이게 없으면 explore가 429를 준다.
    try:
        status, _ = get("https://trends.google.com/trends/?geo=" + geo)
        print(f"  1) 쿠키 요청        HTTP {status}")
    except Exception as exc:
        print(f"  1) 쿠키 요청 실패    {type(exc).__name__} {getattr(exc, 'code', '')}")
        return False

    # 2) explore 로 위젯 토큰 받기
    req = json.dumps({
        "comparisonItem": [{"keyword": keyword, "geo": geo, "time": timeframe}],
        "category": 0, "property": ""}, ensure_ascii=False)
    url = f"{EXPLORE}?hl=ko&tz=-540&req={urllib.parse.quote(req)}"
    try:
        status, body = get(url)
        print(f"  2) explore          HTTP {status}")
    except Exception as exc:
        print(f"  2) explore 실패      {type(exc).__name__} {getattr(exc, 'code', '')}")
        return False

    try:
        widgets = json.loads(strip_prefix(body))["widgets"]
        ts = next(w for w in widgets if w["id"] == "TIMESERIES")
    except Exception as exc:
        print(f"  2) 응답 해석 실패    {type(exc).__name__}: {exc}")
        print("     앞 200자:", body[:200])
        return False
    print(f"     위젯 {len(widgets)}개 · TIMESERIES 토큰 확보")

    # 3) 실제 시계열
    url = (f"{MULTILINE}?hl=ko&tz=-540"
           f"&req={urllib.parse.quote(json.dumps(ts['request'], ensure_ascii=False))}"
           f"&token={ts['token']}")
    try:
        status, body = get(url)
        print(f"  3) multiline        HTTP {status}")
    except Exception as exc:
        print(f"  3) multiline 실패    {type(exc).__name__} {getattr(exc, 'code', '')}")
        return False

    try:
        points = json.loads(strip_prefix(body))["default"]["timelineData"]
    except Exception as exc:
        print(f"  3) 응답 해석 실패    {type(exc).__name__}: {exc}")
        return False

    print(f"     데이터 포인트 {len(points)}개")
    if points:
        print(f"     최초 {points[0]['formattedTime']} = {points[0]['value'][0]}")
        print(f"     최후 {points[-1]['formattedTime']} = {points[-1]['value'][0]}")
    return bool(points)


if __name__ == "__main__":
    kws = sys.argv[1:] or ["반도체"]
    ok = sum(probe(k) for k in kws)
    print(f"\n성공 {ok}/{len(kws)}")
    sys.exit(0 if ok == len(kws) else 1)
