# -*- coding: utf-8 -*-
"""오늘의 AI 뉴스·토픽 — Google News RSS 헤드라인을 주제별로 모아 보여 준다.

인증키가 필요 없다(구글 뉴스 RSS). 하는 일은 **모으고 묶는 것**이고, 기사 내용을 요약하거나
해석하지 않는다. 헤드라인과 출처·시각을 그대로 옮기고 원문으로 링크한다 — 요약을 지어내면
원문에 없는 말이 생긴다.

주제 분류는 헤드라인에 실제로 들어 있는 낱말로만 한다. 어느 주제에도 안 걸리면 '기타'로 둔다.
'많이 언급된 말'도 헤드라인을 센 것이지 중요도 판단이 아니다.

    python tools/build_ai_news_report.py --out runs/ai_news
    python tools/build_ai_news_report.py --out runs/ai_news --publish
"""
import argparse
import email.utils
import html
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
GITHUB_REPO = "namyikim/predict_stock"
GITHUB_BRANCH = "main"
PAGES_DIR = "docs/ai_news"
COUNTER_ENDPOINT = "https://predict-stock-counter.kimname1.workers.dev"

# 검색어. 한 주제에 하나씩이 아니라 겹치게 두고 중복은 제목으로 걸러낸다 — 구글 뉴스는 검색어마다
# 다른 기사를 주므로 겹쳐야 놓치는 것이 적다.
QUERIES = (
    "인공지능", "생성형 AI", "AI 반도체", "HBM 메모리", "엔비디아",
    "오픈AI", "구글 딥마인드", "AI 데이터센터", "AI 규제", "AI 투자",
)

# 주제 분류. 위에서부터 먼저 맞는 것으로 정한다(반도체가 이 저장소의 관심사라 맨 앞).
TOPICS = (
    ("반도체·인프라", "메모리·GPU·데이터센터 — 삼성전자·SK하이닉스 수요와 직접 얽힌다",
     ("반도체", "메모리", "HBM", "D램", "디램", "낸드", "파운드리", "GPU", "엔비디아", "NVIDIA",
      "데이터센터", "웨이퍼", "TSMC", "칩", "전력", "서버")),
    ("모델·연구", "새 모델 공개와 성능 발표",
     ("모델", "GPT", "제미나이", "Gemini", "클로드", "Claude", "라마", "Llama", "LLM",
      "딥시크", "오픈소스", "논문", "벤치마크", "추론")),
    ("기업·투자", "투자·인수·실적",
     ("투자", "인수", "합병", "상장", "IPO", "실적", "매출", "영업이익", "펀딩", "기업가치",
      "조 원", "억 달러", "계약", "공급")),
    ("정책·규제", "법·규제·안전",
     ("규제", "법안", "정부", "가이드라인", "저작권", "소송", "개인정보", "안전", "윤리",
      "수출 통제", "제재")),
    ("서비스·응용", "제품과 서비스",
     ("출시", "서비스", "앱", "도입", "적용", "탑재", "공개", "베타", "업데이트")),
)
OTHER = "기타"

# '많이 언급된 말'에서 뺄 낱말. 너무 흔해서 세어도 아무것도 알려주지 않는다.
STOPWORDS = {"AI", "인공지능", "기자", "뉴스", "속보", "단독", "종합", "그리고", "위해", "대한",
             "있다", "한다", "된다", "이번", "올해", "관련", "통해", "최대", "최초", "가장"}
WINDOW_HOURS = 36          # 이 시간 안에 나온 기사만 '오늘'로 본다
PER_TOPIC = 6              # 주제마다 보여 줄 최대 건수


def fetch_rss(query, timeout=30):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=ko&gl=KR&ceid=KR:ko")
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; predict-stock-ai-news/1.0)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_rss(raw):
    """Google News RSS → [{time(KST), title, source, link}]. 시각을 못 읽는 항목은 뺀다."""
    items = []
    for it in ET.fromstring(raw).findall(".//item"):
        try:
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        # 구글은 제목 끝에 " - 매체명"을 붙인다. 매체는 따로 보여 주므로 제목에서 뗀다.
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        if title:
            items.append({"time": when, "title": title, "source": source,
                          "link": (it.findtext("link") or "").strip()})
    return items


def collect(queries=QUERIES, now=None, window_hours=WINDOW_HOURS, fetch=fetch_rss):
    """검색어들을 모아 제목 기준으로 중복을 없앤 최신순 목록."""
    now = now or datetime.now(KST)
    cutoff = now - timedelta(hours=window_hours)
    merged, seen, failed = [], set(), []
    for query in queries:
        try:
            items = parse_rss(fetch(query))
        except Exception as exc:
            failed.append(f"{query}({type(exc).__name__})")
            continue
        for item in items:
            if item["time"] < cutoff or item["time"] > now + timedelta(hours=6):
                continue          # 미래 시각은 피드 오류다. 버린다.
            key = re.sub(r"[^0-9a-z가-힣]+", "", item["title"].lower())
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    merged.sort(key=lambda x: x["time"], reverse=True)
    return merged, failed


def classify(title, topics=TOPICS):
    """헤드라인에 실제로 들어 있는 낱말로만 주제를 정한다. 없으면 '기타'."""
    for name, _, keywords in topics:
        if any(k.lower() in title.lower() for k in keywords):
            return name
    return OTHER


def group_by_topic(items, topics=TOPICS, per_topic=PER_TOPIC):
    """[(주제, 설명, [기사])]. 기사가 없는 주제는 뺀다. '기타'는 맨 뒤."""
    buckets = {name: [] for name, _, _ in topics}
    buckets[OTHER] = []
    for item in items:
        buckets[classify(item["title"], topics)].append(item)
    out = []
    for name, note, _ in topics:
        if buckets[name]:
            out.append((name, note, buckets[name][:per_topic]))
    if buckets[OTHER]:
        out.append((OTHER, "위 주제에 걸리지 않은 것", buckets[OTHER][:per_topic]))
    return out


def hot_terms(items, limit=12, stopwords=STOPWORDS):
    """헤드라인에 자주 나온 말. 중요도 판단이 아니라 단순 빈도다."""
    counter = Counter()
    for item in items:
        for word in re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.+-]{1,}", item["title"]):
            if word in stopwords or len(word) < 2:
                continue
            counter[word] += 1
    return [(w, n) for w, n in counter.most_common(limit) if n >= 2]


def _safe_url(url):
    """http(s) 링크만 그대로 쓴다. 그 밖의 스킴은 링크를 걸지 않는다."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


def build_html(groups, terms, now, total, failed):
    e = html.escape
    blocks = []
    for name, note, items in groups:
        rows = []
        for item in items:
            link = _safe_url(item["link"])
            title = (f'<a href="{e(link, quote=True)}" target="_blank" rel="noopener noreferrer nofollow"'
                     f' style="color:#1a5490;text-decoration:none">{e(item["title"])}</a>'
                     if link else e(item["title"]))
            meta = " · ".join(x for x in (e(item["source"]) if item["source"] else "",
                                          f'{item["time"]:%m-%d %H:%M}') if x)
            rows.append('<li style="margin:7px 0;line-height:1.55">' + title
                        + f'<div style="font-size:11px;color:#8a9199">{meta}</div></li>')
        blocks.append(
            '<div style="border-top:1px solid #e8e8e8;padding:14px 0 4px">'
            f'<div style="font-size:16px;font-weight:600">{e(name)}'
            f'<span style="font-size:12px;color:#8a9199;font-weight:400"> · {len(items)}건</span></div>'
            f'<div style="font-size:12px;color:#8a9199;margin:1px 0 6px">{e(note)}</div>'
            f'<ul style="margin:0;padding-left:18px;font-size:14px">{"".join(rows)}</ul></div>')

    chips = "".join(
        '<span style="display:inline-block;margin:3px 5px 3px 0;padding:3px 10px;background:#eef2f7;'
        f'border-radius:12px;font-size:13px">{e(w)}<span style="color:#8a9199"> {n}</span></span>'
        for w, n in terms)
    terms_block = (
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:16px">'
        '<div style="font-size:12px;color:#6b7178;margin-bottom:5px">'
        f'오늘 헤드라인에 자주 나온 말 <span style="color:#a5abb2">(단순 빈도 · 중요도 아님)</span></div>'
        f'{chips}</div>') if chips else ""

    counter = ""
    if COUNTER_ENDPOINT and "WORKERS-SUBDOMAIN" not in COUNTER_ENDPOINT:
        counter = ('<div style="margin-top:10px;font-variant-numeric:tabular-nums">'
                   '조회 <span id="view-count">—</span></div>'
                   '<script>(function(){'
                   f'var E="{COUNTER_ENDPOINT}",P="ai_news";'
                   'var el=document.getElementById("view-count");if(!el||!E)return;'
                   'fetch(E+"/hit?page="+encodeURIComponent(P))'
                   '.then(function(r){return r.ok?r.json():null;})'
                   '.then(function(d){if(d&&typeof d.total==="number")'
                   'el.textContent=d.total.toLocaleString("ko-KR");})'
                   '.catch(function(){});})();</script>')

    warn = (f'<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:10px 14px;'
            f'border-radius:0 5px 5px 0;font-size:12px;color:#6b7178;margin-bottom:14px">'
            f'검색어 {e(", ".join(failed))} 는 이번 조회에서 받지 못했습니다 — 그만큼 빠져 있습니다.</div>'
            ) if failed else ""

    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>최신 AI 트렌드 및 뉴스 {now:%Y-%m-%d}</title>'
        '<style>html,body{overflow-x:hidden}'
        'body{margin:0;padding:24px 20px 48px;background:#fff;max-width:100%;'
        "font-family:-apple-system,'Malgun Gothic',sans-serif;line-height:1.65;color:#1a1a1a;"
        '-webkit-font-smoothing:antialiased}'
        '.wrap{max-width:760px;margin:0 auto}a{color:#1a5490}'
        '@media(max-width:640px){body{padding:16px 12px 32px}}</style></head><body>'
        '<div class="wrap">'
        '<div style="border-bottom:3px solid #1a1a1a;padding-bottom:11px;margin-bottom:18px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">AI TRENDS &amp; NEWS</div>'
        '<h2 style="margin:6px 0 5px;font-size:27px">최신 AI 트렌드 및 뉴스</h2>'
        f'<div style="font-size:12px;color:#8a9199">{now:%Y-%m-%d %H:%M} KST 기준 · '
        f'최근 {WINDOW_HOURS}시간 {total}건</div></div>'
        + warn +
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:16px;'
        'font-size:13px;color:#6b7178">'
        '구글 뉴스에서 AI 관련 검색어로 받은 <b>헤드라인</b>을 주제별로 묶은 것입니다. '
        '기사 내용을 요약하거나 해석하지 않았고, 제목·출처·시각을 그대로 옮겨 원문으로 링크합니다. '
        '주제 분류는 제목에 들어 있는 낱말로만 하므로 완벽하지 않습니다.</div>'
        + terms_block
        + ("".join(blocks) if blocks else
           '<div style="padding:24px 12px;color:#8a9199;font-size:14px">'
           '이번 조회에서는 받은 기사가 없습니다.</div>')
        + '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-size:12px;color:#8a9199">'
        f'생성 {now:%Y-%m-%d %H:%M} KST · 출처 '
        '<a href="https://news.google.com/">Google News</a> · '
        f'<a href="https://github.com/{GITHUB_REPO}">저장소</a> · '
        '<a href="../">예측 보고서</a><br>'
        '헤드라인 모음입니다. 이 저장소의 예측 모델에는 쓰이지 않으며 투자 자문이 아닙니다.'
        f'{counter}</div>'
        "</div></body></html>")


# ---- GitHub 발행 -------------------------------------------------------------
def publish(path, text, token, message):
    """공용 발행기에 맡긴다 — 동시 커밋으로 sha 가 어긋나면 다시 읽어 재시도한다."""
    import github_pages
    return github_pages.publish(path, text, token, message)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="보고서를 저장할 폴더")
    parser.add_argument("--publish", action="store_true", help="GITHUB_TOKEN으로 저장소에 발행한다")
    parser.add_argument("--hours", type=int, default=WINDOW_HOURS, help=f"최근 몇 시간(기본 {WINDOW_HOURS})")
    args = parser.parse_args()

    now = datetime.now(KST)
    items, failed = collect(now=now, window_hours=args.hours)
    groups = group_by_topic(items)
    terms = hot_terms(items)
    print(f"AI 뉴스 {len(items)}건 · 주제 {len(groups)}개" + (f" · 실패 {failed}" if failed else ""))
    for name, _, rows in groups:
        print(f"  [{name}] {len(rows)}건")
        for item in rows[:3]:
            print(f"     {item['time']:%m-%d %H:%M} {item['title'][:60]}")

    page = build_html(groups, terms, now, len(items), failed)
    args.out.mkdir(parents=True, exist_ok=True)
    local = args.out / "report.html"
    local.write_text(page, encoding="utf-8")
    print("보고서 저장:", local)

    if not args.publish:
        print("ℹ️ --publish 가 없어 저장소에 올리지 않습니다.")
        return
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("⚠️ GITHUB_TOKEN이 없어 발행을 건너뜁니다.")
        return
    # 한 건도 못 받았으면 멀쩡한 보고서를 빈 것으로 덮지 않는다(검색어 전체 실패·일시 장애).
    if not items:
        print("⚠️ 받은 기사가 없어 발행하지 않습니다(기존 보고서를 유지합니다).")
        return
    sha = publish(f"{PAGES_DIR}/index.html", page, token,
                  f"ai-news: {now:%Y-%m-%d %H:%M} KST ({len(items)}건)")
    print(f"GitHub Pages 발행: {PAGES_DIR}/index.html @ {sha}")


if __name__ == "__main__":
    main()
