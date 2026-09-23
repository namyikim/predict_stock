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


_ANY = object()   # publish(expected_sha=…) 를 주지 않은 기존 호출의 표시


def fetch_with_sha(path, tok):
    """(텍스트, blob sha). 없으면 (None, None).

    원장처럼 '읽고 → 고치고 → 쓰는' 파일은 여기서 받은 sha 를 publish(expected_sha=…) 에 넘긴다. 그래야 그 사이
    다른 실행이 바꾼 것을 알아챈다.
    """
    try:
        data = _api(path, tok)
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            return None, None
        raise RuntimeError(f"조회 실패({type(exc).__name__} {getattr(exc, 'code', '')})") from None
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def fetch(path, tok):
    """저장소의 텍스트 파일을 돌려준다. 없으면 None."""
    try:
        data = _api(path, tok)
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            return None
        raise RuntimeError(f"조회 실패({type(exc).__name__} {getattr(exc, 'code', '')})") from None
    return base64.b64decode(data["content"]).decode("utf-8")


def publish(path, text, tok, message, attempts=4, expected_sha=_ANY, merge=None):
    """파일을 올리고 **올린 파일의 blob sha** 앞 7자리를 돌려준다(커밋 sha 가 아니다). 오류 문구에 토큰이 섞이지 않게 한다.

    expected_sha 를 주지 않으면(기존 호출) 올리기 직전에 최신 sha 를 읽어 파일 전체를 덮어쓴다. 이 실행이 내용을
    전부 새로 만드는 보고서 HTML 같은 파일에는 맞다. **원장처럼 여러 실행이 행을 더하는 파일에는 쓰면 안 된다** —
    이 실행이 처음 읽은 뒤 다른 실행이 더한 행을 오류 없이 지운다. sha 를 매번 새로 읽으므로 409 도 나지 않는다
    (2026-09-23 검토. 예전 설명 "파일 전체를 덮어쓰므로 재시도가 안전하다"는 추가만 되는 파일에는 틀렸다).

    expected_sha 를 주면(fetch_with_sha 로 처음 읽을 때 받은 값, 그때 없던 파일이면 None) 그 sha 로만 올린다.
    409/422 가 오면 최신을 다시 읽어 **실제로 바뀌었는지** 확인한다 — sha 가 그대로면 경합이 아니라 다른 오류라
    재시도하지 않는다. 바뀌었으면 merge(최신 텍스트) 가 돌려준 합친 내용을 최신 sha 로 다시 올린다. merge 가
    없으면 덮어쓰지 않고 실패한다(다음 실행이 다시 읽어 처리한다).
    """
    import random
    import time
    if expected_sha is not _ANY:
        return _publish_expected(path, text, tok, message, attempts, expected_sha, merge)
    for attempt in range(attempts):
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
            code = getattr(exc, "code", None)
            if code in (409, 422) and attempt < attempts - 1:
                time.sleep(2 * (2 ** attempt) + random.uniform(0, 2))
                continue
            raise RuntimeError(f"업로드 실패({type(exc).__name__} {code or ''})") from None


def _publish_expected(path, text, tok, message, attempts, sha, merge):
    """publish(expected_sha=…) 의 본체. 처음 읽은 sha 로만 쓰고, 바뀌었으면 합치거나 멈춘다."""
    import random
    import time
    for attempt in range(attempts):
        body = {"message": message, "branch": GITHUB_BRANCH,
                "content": base64.b64encode(text.encode("utf-8")).decode()}
        if sha:
            body["sha"] = sha
        try:
            return _api(path, tok, "PUT", body)["content"]["sha"][:7]
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code not in (409, 422):
                raise RuntimeError(f"업로드 실패({type(exc).__name__} {code or ''})") from None
        latest_text, latest_sha = fetch_with_sha(path, tok)
        if latest_sha == sha:
            raise RuntimeError(f"업로드 실패({code}) — {path} 는 그대로인데 거절됐습니다(경합이 아님)")
        if merge is None:
            raise RuntimeError(f"동시 변경 감지({code}) — {path} 가 이 실행이 읽은 뒤 바뀌었습니다. 덮어쓰지 않습니다")
        if attempt == attempts - 1:
            break
        text, sha = merge(latest_text), latest_sha
        time.sleep(1 + attempt + random.uniform(0, 1))
    raise RuntimeError(f"업로드 실패 — {path} 가 {attempts}번 연달아 바뀌어 합치기를 멈췄습니다")


def _git_head():
    """체크아웃이 있으면 로컬 HEAD를 읽는다(Actions의 GITHUB_SHA가 없을 때)."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def code_version(tok=None, branch=GITHUB_BRANCH):
    """이 보고서를 만든 코드가 어느 커밋인지. (sha, short, message, date_kst, url)

    보고서만 보고 있으면 '언제 만든 것인지'는 알아도 '무슨 코드로 만든 것인지'는 알 수 없다.
    고친 내용이 반영됐는지 확인하려면 커밋이 필요하다. Actions는 GITHUB_SHA를 주고,
    체크아웃만 있으면 git에서 읽으며, 둘 다 없으면 원격 브랜치의 최신 커밋을 조회한다.
    """
    import datetime
    import json as _json
    import urllib.request
    sha = os.environ.get("GITHUB_SHA") or _git_head()
    info = {"sha": sha, "short": sha[:7] if sha else None, "message": None, "date_kst": None,
            "url": f"https://github.com/{GITHUB_REPO}/commit/{sha}" if sha else None}
    try:
        path = f"commits/{sha}" if sha else f"commits/{branch}"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/{path}",
            headers={"Accept": "application/vnd.github+json",
                     **({"Authorization": f"Bearer {tok}"} if tok else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = _json.loads(response.read().decode())
        info["sha"] = payload["sha"]
        info["short"] = payload["sha"][:7]
        info["url"] = payload.get("html_url") or info["url"]
        info["message"] = (payload["commit"]["message"] or "").splitlines()[0][:90]
        stamp = datetime.datetime.fromisoformat(
            payload["commit"]["committer"]["date"].replace("Z", "+00:00"))
        info["date_kst"] = stamp.astimezone(
            datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pass                      # 커밋 정보를 못 받아도 보고서는 나와야 한다
    return info


def version_line(tok=None, generated_at=None, extra=""):
    """보고서 상단에 넣을 '생성 시각 · 코드 커밋' 한 줄(HTML)."""
    import html as _html
    info = code_version(tok)
    parts = []
    if generated_at:
        parts.append(f"생성 <b>{_html.escape(generated_at)}</b>")
    if info["short"]:
        link = (f'<a href="{info["url"]}" style="color:#1a5490">{info["short"]}</a>'
                if info["url"] else info["short"])
        text = f"코드 커밋 <code>{link}</code>"
        if info["message"]:
            text += f' “{_html.escape(info["message"])}”'
        if info["date_kst"]:
            text += f' · 커밋 {_html.escape(info["date_kst"])}'
        parts.append(text)
    else:
        parts.append("코드 커밋 정보 없음")
    if extra:
        parts.append(extra)
    return ('<div style="font-size:12px;color:#8a9199;margin:-6px 0 14px">'
            + " · ".join(parts) + "</div>")
