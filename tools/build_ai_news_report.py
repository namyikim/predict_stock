# -*- coding: utf-8 -*-
"""오늘의 AI 뉴스·토픽 — Google News RSS 헤드라인을 주제별로 모아 보여 준다.

인증키가 필요 없다(구글 뉴스 RSS). 하는 일은 **모으고 묶는 것**이고, 기사 내용을 요약하거나
해석하지 않는다. 헤드라인과 출처·시각을 그대로 옮기고 원문으로 링크한다 — 요약을 지어내면
원문에 없는 말이 생긴다.

인기 급상승 검색어처럼 **순위**로 보여 준다. 순위는 헤드라인에 나온 말을 다룬 매체 수로 매긴다 —
검색량이 아니라 헤드라인 언급이고 중요도 판단도 아니다(2026-09-13, 주제별 묶음에서 바꿈).

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
from collections import Counter, defaultdict
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

# 순위를 매길 낱말. 한글과 영문이 붙은 말(오픈AI)은 한 덩어리로 본다 — 쪼개면 '오픈'이 1위가 된다
# (2026-09-13 실제 헤드라인에서 확인).
TOKEN = re.compile(r"[가-힣]+[A-Za-z][A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]*[가-힣]+|[가-힣]{2,}|[A-Za-z][A-Za-z0-9.+-]{1,}")
# 조사. 떼어 낸 꼴이 헤드라인 묶음에 따로 나올 때만 뗀다(_base_term).
PARTICLES = ("에서는", "으로는", "에서", "으로", "까지", "부터", "보다", "에게", "와의", "과의", "에는", "에도", "로는",
             "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "엔", "만")
# 동사·서술 꼴. '늦춰야'·'나선다' 같은 말이 순위에 오르면 무엇이 화제인지 알 수 없다.
VERBISH = ("해야", "춰야", "어야", "아야", "한다", "된다", "했다", "이다", "하는", "하고", "하며", "나선", "나서",
           "밝혀", "밝힌")
# 너무 흔해서 순위에 올라도 아무것도 알려주지 않는 말.
STOPWORDS = set((
    "AI 인공지능 생성형 IT CEO 기자 뉴스 속보 단독 종합 그리고 위해 대한 있다 한다 된다 이번 올해 내년 오늘 "
    "관련 통해 최대 최초 가장 개발 속도 투자 공개 논의 자체 시대 경쟁 안전 시장 협력 확산 글로벌 데이터 기업 "
    "발표 출시 도입 추진 확대 강화 지원 계획 이유 산업 기술 서비스 전략 혁신 미래 세계 국내 한국 정부 활용 "
    "기반 분야 성장 필요 가능 전망 대응 역할 가속 본격 선언 개최 포럼 행사 교육 센터 사업 플랫폼 솔루션 모델 "
    "기능 무엇 어떻게 승부수 한목소리 신규 공동 주요 핵심 수요 규모 역대 국가 지역").split())
WINDOW_HOURS = 36          # 이 시간 안에 나온 기사만 '오늘'로 본다
RANK_LIMIT = 10            # 인기 급상승 검색어와 같은 열 개
NEWS_PER_TERM = 3          # 순위마다 보여 줄 기사 수


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


def _base_term(word, vocab):
    """낱말 하나를 순위에 쓸 꼴로 바꾼다. 순위에 쓰지 않을 말이면 None.

    조사는 **떼어 낸 꼴이 같은 헤드라인 묶음에 따로 나올 때만** 뗀다. '엔비디아가'는 '엔비디아'가 따로
    나오니 떼지만, '마이크로'처럼 우연히 조사처럼 끝나는 이름은 자르지 않는다.
    """
    if len(word) < 2 or word in STOPWORDS or word.endswith(VERBISH):
        return None
    if "가" <= word[-1] <= "힣":
        for particle in PARTICLES:
            stem = word[:-len(particle)]
            if word.endswith(particle) and len(stem) >= 2 and vocab.get(stem):
                word = stem
                break
    if word in STOPWORDS:
        return None
    return word


def rank_terms(items, limit=RANK_LIMIT, per_term=NEWS_PER_TERM, min_sources=2, min_articles=2):
    """헤드라인에 나온 말의 순위. [{term, sources, articles, latest, news:[기사, ...]}]

    순위는 **다룬 매체 수**가 먼저이고 기사 수, 가장 최근 시각이 그 다음이다. 한 매체가 같은 기사를
    여러 번 올려도 순위가 오르지 않게 하려는 것이다. 매체 한 곳만 다룬 말은 순위에 넣지 않는다.
    검색량이 아니라 헤드라인 언급이고, 중요도 판단도 아니다.
    """
    raw = [TOKEN.findall(item["title"]) for item in items]
    vocab = Counter(word for words in raw for word in set(words))
    articles, sources, latest = defaultdict(list), defaultdict(set), {}
    for index, words in enumerate(raw):
        seen = set()
        for word in words:
            term = _base_term(word, vocab)
            if not term or term in seen:
                continue                      # 한 헤드라인에서 같은 말은 한 번만 센다
            seen.add(term)
            articles[term].append(index)
            sources[term].add(items[index]["source"] or "출처 미상")
            when = items[index]["time"]
            latest[term] = max(latest.get(term, when), when)
    ranked = sorted((term for term in articles
                     if len(articles[term]) >= min_articles and len(sources[term]) >= min_sources),
                    key=lambda term: (len(sources[term]), len(articles[term]), latest[term], term),
                    reverse=True)
    out = []
    for term in ranked[:limit]:
        news = sorted((items[i] for i in articles[term]), key=lambda it: it["time"], reverse=True)
        out.append({"term": term, "sources": len(sources[term]), "articles": len(articles[term]),
                    "latest": latest[term], "news": news[:per_term]})
    return out


def _safe_url(url):
    """http(s) 링크만 그대로 쓴다. 그 밖의 스킴은 링크를 걸지 않는다."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


def build_html(ranked, now, total, failed):
    """순위표 한 장. 인기 급상승 검색어 페이지와 같은 모양(순위 · 말 · 기사 세 건)."""
    e = html.escape
    rows = []
    for rank, row in enumerate(ranked, 1):
        news = []
        for item in row["news"]:
            link = _safe_url(item["link"])
            title = (f'<a href="{e(link, quote=True)}" target="_blank" rel="noopener noreferrer nofollow"'
                     f' style="color:#1a5490;text-decoration:none">{e(item["title"])}</a>'
                     if link else e(item["title"]))
            meta = " · ".join(x for x in (e(item["source"]) if item["source"] else "",
                                          f'{item["time"]:%m-%d %H:%M}') if x)
            news.append(f'<li style="margin:3px 0">{title}'
                        f' <span style="color:#a5abb2;font-size:12px">· {meta}</span></li>')
        rows.append(
            '<tr><td style="padding:14px 12px;border-top:1px solid #e8e8e8;vertical-align:top;'
            'width:38px;text-align:right;color:#a5abb2;font-size:15px;font-weight:600">'
            f'{rank}</td>'
            '<td style="padding:14px 12px;border-top:1px solid #e8e8e8">'
            f'<div style="font-size:16px;font-weight:600">{e(row["term"])}</div>'
            '<div style="font-size:11px;color:#8a9199;margin-top:2px">'
            f'매체 {row["sources"]}곳 · 기사 {row["articles"]}건 · 최근 {row["latest"]:%H:%M}</div>'
            '<ul style="margin:8px 0 0;padding-left:17px;font-size:13px;line-height:1.6">'
            f'{"".join(news)}</ul></td></tr>')

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

    if rows:
        body = ('<table style="width:100%;border-collapse:collapse;border:1px solid #e5e5e5">'
                + "".join(rows) + "</table>")
    elif total:
        body = ('<div style="padding:24px 12px;color:#8a9199;font-size:14px">'
                '이번 조회에서는 순위를 매길 만큼 여러 매체가 함께 다룬 말이 없습니다.</div>')
    else:
        body = ('<div style="padding:24px 12px;color:#8a9199;font-size:14px">'
                '이번 조회에서는 받은 기사가 없습니다.</div>')

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
        f'최근 {WINDOW_HOURS}시간 헤드라인 {total}건</div></div>'
        + warn +
        '<div style="background:#f5f6f8;border-radius:6px;padding:12px 16px;margin-bottom:18px;'
        'font-size:13px;color:#6b7178">'
        f'구글 뉴스에서 AI 관련 검색어로 받은 최근 {WINDOW_HOURS}시간 헤드라인에서 '
        '<b>가장 많은 매체가 다룬 말</b>을 순서대로 보여 줍니다. <b>검색량 순위가 아니라 헤드라인 언급 순위</b>이며, '
        '한 매체가 여러 번 쓴 것보다 여러 매체가 함께 다룬 말이 위로 올라갑니다. '
        '기사 내용을 요약하거나 해석하지 않았고 제목·출처·시각을 그대로 옮겨 원문으로 링크합니다. '
        '제목의 낱말만 세므로 순위가 완벽하지는 않습니다.</div>'
        + body
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
    ranked = rank_terms(items)
    print(f"AI 뉴스 {len(items)}건 · 순위 {len(ranked)}개" + (f" · 실패 {failed}" if failed else ""))
    for rank, row in enumerate(ranked, 1):
        print(f"  {rank:2}. {row['term']} (매체 {row['sources']} · 기사 {row['articles']})")
        for item in row["news"][:2]:
            print(f"        {item['time']:%m-%d %H:%M} {item['title'][:60]}")

    page = build_html(ranked, now, len(items), failed)
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
