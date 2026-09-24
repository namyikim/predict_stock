"""GitHub Contents API로 파일 하나를 저장소에 올린다.

보고서 도구들이 같은 코드를 각자 갖고 있어서 여기로 모은다. 노트북은 Colab에서 자립해야 하므로
tools/sync_notebook_helpers.py 가 이 파일을 그대로 노트북 셀에 넣는다(모듈 github_pages, 2026-09-24).
그래서 이 파일은 표준 라이브러리만 쓰고, 노트북에 없는 모듈은 함수 안에서만 import 한다.
"""
import base64
import contextlib
import contextvars
import hashlib
import json
import os
import urllib.parse
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
    pending = _BATCH.get()
    if pending is not None:          # with batch(...) 안: 모았다가 블록이 끝날 때 한 커밋으로 올린다
        return pending.add(path, text, tok, message, expected_sha, merge)
    if callable(text):               # 묶음 밖에서는 곧바로 만든다(묶음 안에서는 커밋할 때 만든다)
        text = text()
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


def publish_history(path, new_text, tok, message):
    """이력 보관본(macro_history/*.csv)을 덮어쓰지 않고 날짜로 합쳐 올린다(2026-09-24).

    같은 보관본을 여러 도구가 서로 다른 기간으로 받아 통째로 덮어써 매 실행 이력이 지워졌다 되살아났다. 여기서는
    지금 저장소의 파일과 합쳐, 바뀐 것이 없으면 올리지 않고('unchanged'), 바뀌었으면 처음 읽은 sha 로 올린다 —
    그 사이 다른 실행이 바꿨으면 그 최신본과 다시 합친다. 합치는 규칙은 forecast_utils.merge_history_csv(노트북과 같다).
    """
    from forecast_utils import merge_history_csv
    existing, sha = fetch_with_sha(path, tok)
    text = merge_history_csv(existing, new_text)
    if existing is not None and text == existing:
        return "unchanged"
    return publish(path, text, tok, message, expected_sha=sha,
                   merge=lambda latest: merge_history_csv(latest, new_text))


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


# ---- 발행 묶기: 도구 한 번 = 커밋 한 번 (2026-09-23, guides/publish-batching-plan.md ①) ----------------------
# publish() 는 파일 하나마다 커밋을 하나 만든다. 하루 커밋 300~600개, 커밋마다 Pages 빌드가 시작됐다가 다음
# 커밋에 취소됐다(최근 100건 중 성공 9). batch() 블록 안의 publish() 는 모았다가 블록이 끝날 때 Git Data API 로
# 한 커밋에 올린다. 블록 밖 호출은 예전처럼 곧바로 커밋한다 — 도구를 하나씩 옮길 수 있다.
_BATCH = contextvars.ContextVar("github_pages_batch", default=None)
PENDING = "대기(묶음 발행)"


def blob_sha(text):
    """git 이 이 내용에 붙일 blob sha. Contents API 가 돌려주는 sha 와 같은 값이다."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _repo_api(suffix, tok, method="GET", body=None):
    """/repos/{저장소}/{suffix} 호출. git 데이터·비교·ref 에 쓴다."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/{suffix}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def _blob_sha_at(path, commit, tok):
    """그 커밋에서 path 의 blob sha. 없으면 None."""
    try:
        return _repo_api(f"contents/{urllib.parse.quote(path)}?ref={commit}", tok)["sha"]
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            return None
        raise RuntimeError(f"조회 실패({type(exc).__name__} {getattr(exc, 'code', '')})") from None


def _blob_text(sha, tok):
    return base64.b64decode(_repo_api(f"git/blobs/{sha}", tok)["content"]).decode("utf-8")


def _head(tok):
    return _repo_api(f"git/ref/heads/{GITHUB_BRANCH}", tok)["object"]["sha"]


def _landed(commit, tok):
    """이 커밋이 이미 main 에 들어가 있는가(ref 갱신 응답만 유실된 경우). 확인하지 못하면 False."""
    try:
        return _repo_api(f"compare/{commit}...{GITHUB_BRANCH}", tok).get("status") in ("identical", "ahead")
    except Exception:
        return False


def _mark_published():
    """워크플로가 '이번 실행에서 실제로 커밋했는가'를 알 수 있게 표시 파일을 남긴다(Pages 재빌드 판단용)."""
    flag = os.environ.get("PAGES_CHANGED_FLAG", "").strip()
    if flag:
        try:
            os.makedirs(os.path.dirname(flag) or ".", exist_ok=True)
            with open(flag, "a", encoding="utf-8") as handle:
                handle.write("1\n")
        except OSError:
            pass


class Batch:
    """모아 둔 파일을 한 커밋으로 올린다. 안전 조건(계획 문서 '검토 보완 사항'):

    - 같은 경로는 마지막 것만 쓴다. 내용이 이미 같은 파일은 건너뛰고, 바뀐 파일이 없으면 커밋하지 않는다.
    - expected_sha 를 준 파일(원장·일부만 고치는 HTML)은 커밋할 main 에서 그 sha 가 그대로여야 한다. 바뀌었으면
      merge(최신 텍스트) 로 다시 만들고, merge 가 없으면 **묶음 전체를 올리지 않는다**(덮어쓰지 않는다).
    - main 이 그 사이 움직여 ref 갱신이 거절되면 최신 main 을 기준으로 다시 짠다(다른 파일의 변경은 보존된다).
      ref 가 그대로인데 거절됐으면 경합이 아니라 다른 오류로 멈춘다.
    - ref 갱신 응답이 유실돼도 그 커밋이 이미 main 에 있으면 성공으로 본다(중복 커밋을 만들지 않는다).
    - 검증은 **만든 커밋**의 트리로 한다. 최신 main 과 비교하면 다른 실행의 정상적인 뒤 커밋을 오류로 본다.
    """

    def __init__(self, message, tok=None):
        self.message, self.tok, self.items, self.commit_sha = message, tok, {}, None

    def add(self, path, text, tok, message, expected_sha=_ANY, merge=None):
        self.tok = self.tok or tok
        self.items.pop(path, None)          # 같은 경로는 마지막 것만(순서도 마지막 위치로)
        self.items[path] = {"text": text, "message": message, "expected_sha": expected_sha, "merge": merge}
        return PENDING

    def _message(self, entries):
        lines = [f"- {e['path']} — {self.items[e['path']]['message']}" for e in entries]
        return self.message + "\n\n" + "\n".join(lines)

    def commit(self, attempts=4):
        import random
        import time
        if not self.items:
            return None
        tok = self.tok
        for attempt in range(attempts):
            head = _head(tok)
            base_tree = _repo_api(f"git/commits/{head}", tok)["tree"]["sha"]
            entries, unchanged = [], []
            for path, item in self.items.items():
                current = _blob_sha_at(path, head, tok)
                if item["expected_sha"] is not _ANY and current != item["expected_sha"]:
                    if item["merge"] is None:
                        raise RuntimeError(f"동시 변경 감지 — {path} 가 이 실행이 읽은 뒤 바뀌었습니다. "
                                           "덮어쓰지 않고 묶음 전체를 올리지 않습니다")
                    item["text"] = item["merge"](_blob_text(current, tok) if current else None)
                    item["expected_sha"] = current
                # 함수로 준 내용은 여기서(앞 파일의 합치기가 끝난 뒤) 만든다 — 원장을 합치면 원장에서 다시 계산하는
                # 파생 파일도 합친 원장 기준이어야 한다. 넣은 순서대로 처리하므로 원장을 먼저 넣는다.
                text = item["text"]() if callable(item["text"]) else item["text"]
                want = blob_sha(text)
                if want == current:
                    unchanged.append(path)
                    continue
                created = _repo_api("git/blobs", tok, "POST", {
                    "content": base64.b64encode(text.encode("utf-8")).decode(), "encoding": "base64"})["sha"]
                if created != want:
                    raise RuntimeError(f"blob 불일치 — {path} (로컬 {want[:7]} · 서버 {created[:7]})")
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": created})
            if not entries:
                print(f"발행 묶음: 바뀐 파일이 없어 커밋하지 않습니다({len(unchanged)}개 그대로).", flush=True)
                return None
            tree = _repo_api("git/trees", tok, "POST", {"base_tree": base_tree, "tree": entries})["sha"]
            commit = _repo_api("git/commits", tok, "POST",
                               {"message": self._message(entries), "tree": tree, "parents": [head]})["sha"]
            try:
                _repo_api(f"git/refs/heads/{GITHUB_BRANCH}", tok, "PATCH", {"sha": commit, "force": False})
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not _landed(commit, tok):
                    moved = code in (409, 422) and _head(tok) != head
                    if not moved:
                        raise RuntimeError(f"발행 실패({type(exc).__name__} {code or ''})") from None
                    if attempt == attempts - 1:
                        break
                    time.sleep(1 + attempt + random.uniform(0, 1))
                    continue
            if _repo_api(f"git/commits/{commit}", tok)["tree"]["sha"] != tree:
                raise RuntimeError(f"검증 실패 — 만든 커밋 {commit[:7]} 의 트리가 올린 트리와 다릅니다")
            self.commit_sha = commit
            _mark_published()
            print(f"발행 묶음: 커밋 {commit[:7]} · 파일 {len(entries)}개"
                  + (f" · 그대로 {len(unchanged)}개" if unchanged else ""), flush=True)
            return commit
        raise RuntimeError(f"발행 실패 — main 이 {attempts}번 연달아 바뀌어 멈췄습니다")


@contextlib.contextmanager
def batch(message, tok=None):
    """with batch("…"): 안의 publish() 를 모아 블록이 끝날 때 한 커밋으로 올린다. 블록 안에서 예외가 나면
    아무것도 올리지 않는다(반쯤 만든 보고서를 올리지 않는다 — 실패하면 지난 조각이 그대로 남는다)."""
    current = Batch(message, tok)
    reset = _BATCH.set(current)
    try:
        yield current
    except BaseException:
        _BATCH.reset(reset)
        if current.items:
            print(f"⚠️ 발행 묶음 안에서 오류 — 모아 둔 {len(current.items)}개 파일을 올리지 않습니다.", flush=True)
        raise
    _BATCH.reset(reset)
    current.commit()


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
