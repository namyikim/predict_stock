"""GitHub Contents API로 파일 하나를 저장소에 올린다.

보고서 도구들이 같은 코드를 각자 갖고 있어서 여기로 모은다. 노트북은 Colab에서
자립해야 하므로 자기 복사본을 그대로 둔다.
"""
import base64
import json
import os
import urllib.request

GITHUB_REPO = "namyikim/predict_stock"
GITHUB_BRANCH = "main"


def token():
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if not value:
        raise RuntimeError("GITHUB_TOKEN이 없습니다.")
    return value


def _api(path, tok, method="GET", body=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={GITHUB_BRANCH}"
    request = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def publish(path, text, tok, message):
    """파일을 올리고 커밋 sha 앞 7자리를 돌려준다. 오류 문구에 토큰이 섞이지 않게 한다."""
    sha = None
    try:
        sha = _api(path, tok)["sha"]
    except Exception as exc:            # 404면 새 파일이다.
        if getattr(exc, "code", None) != 404:
            raise RuntimeError(f"조회 실패({type(exc).__name__} "
                               f"{getattr(exc, 'code', '')})") from None
    body = {"message": message, "branch": GITHUB_BRANCH,
            "content": base64.b64encode(text.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    try:
        return _api(path, tok, "PUT", body)["content"]["sha"][:7]
    except Exception as exc:
        raise RuntimeError(f"업로드 실패({type(exc).__name__} "
                           f"{getattr(exc, 'code', '')})") from None
