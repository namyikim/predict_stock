"""보고서 HTML 조각 — 노트북과 도구가 함께 쓰는 순수 함수들.

노트북의 보고서 셀(4만 자)에서 자유변수 없이 떼어낼 수 있는 부분을 옮겼다. 전부 명시적 인자만
받으므로 테스트할 수 있고, tools/ 의 다른 보고서에서도 쓸 수 있다. 큰 조립(절 순서·요약)은
아직 노트북에 남아 있다 — 노트북 전역 수십 개에 얽혀 있어 한 번에 옮기면 회귀 위험이 크다.

노트북에는 tools/sync_notebook_helpers.py 가 이 파일을 그대로 넣는다.
"""
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd


def fmt(x, kind="num"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if kind == "won":
        return f"{x:,.0f}원"
    if kind == "pct":
        return f"{x:+.2%}"
    if kind == "bp":
        return f"{x:+.1f}bp"
    return f"{x:.4f}"


def prob_bar(p_down, p_flat, p_up):
    segs = [("하락", p_down, "#b5453c"), ("보합", p_flat, "#7c848c"), ("상승", p_up, "#2b6ca3")]
    cells = "".join(
        f'<td style="width:{v*100:.1f}%;background:{c};color:#fff;text-align:center;'
        f'padding:8px 2px;font-size:12px;white-space:nowrap">'
        f'{n if v > 0.14 else ""} {format(v, ".0%") if v > 0.07 else ""}</td>'
        for n, v, c in segs)
    return ('<table style="width:100%;border-collapse:collapse;border-radius:4px;'
            f'overflow:hidden"><tr>{cells}</tr></table>')


def range_bar(low, center, high, current):
    span = max(high - low, 1e-9)
    pos_center = (center - low) / span * 100
    pos_now = (current - low) / span * 100
    return (
        '<div style="position:relative;height:32px;margin:8px 0 4px">'
        '<div style="position:absolute;top:13px;left:0;right:0;height:8px;border-radius:4px;'
        'background:linear-gradient(90deg,#eef2f7,#c3d4e6,#eef2f7)"></div>'
        f'<div style="position:absolute;top:8px;left:{pos_now:.1f}%;width:2px;height:18px;'
        'background:#8a9199"></div>'
        f'<div style="position:absolute;top:5px;left:{pos_center:.1f}%;width:3px;height:24px;'
        'background:#1a5490"></div>'
        f'<div style="position:absolute;top:0;left:0;font-size:10px;color:#8a9199">{low:,.0f}</div>'
        f'<div style="position:absolute;top:0;right:0;font-size:10px;color:#8a9199">{high:,.0f}</div>'
        '</div>')


def _flash_note(info, applied):
    """10일 잠정치 줄의 비고. 몇 일치를 받았고 그것으로 어느 달을 채웠는지 적는다.

    이 줄은 '빠른 대신 잠정'이라는 성격을 드러내야 한다. 며칠치인지 모르면 읽는 사람이
    확정치와 구별할 수 없다.
    """
    if not info or not info.get("enabled"):
        return ""
    days = info.get("last_days")
    note = f"마지막 {days}일치" if days else ""
    months = ", ".join(str(a.get("month")) for a in (applied or []) if a.get("month"))
    if months:
        note = f'{note} · 이것으로 채운 달 {months}' if note else f"이것으로 채운 달 {months}"
    return note


def fragment_sources_html(longterm, earnings):
    """장기 전망(7절)·영업이익 추정(8절)이 쓴 자료원 표.

    6절의 표는 노트북이 직접 받은 자료만 적는다. 7·8절은 별도 도구가 만들어 조각으로 끼워지므로
    G20 CLI·TSMC 월매출·D램 현물가 같은 자료가 '이 보고서의 데이터'에서 빠져 보였다. 읽는 사람은
    제목을 보고 보고서 전체의 자료원이라고 생각하므로, 조각이 남긴 출처를 읽어 함께 적는다.
    """
    from html import escape
    rows = []

    def add(label, info, extra=""):
        if not info:
            return
        enabled = info.get("enabled", True) and info.get("source")
        if not enabled:
            # 예전에는 80자에서 잘라 실패 사유의 뒷부분(어느 키 형태가 어떻게 실패했는지)이
            # 보이지 않았다. 진단이 목적인 칸이므로 넉넉히 남긴다.
            rows.append((label, "—", "미포함", str(info.get("reason", ""))[:300], True))
            return
        source = str(info.get("source", ""))
        stale = source in ("last_successful_fetch", "explicit_cache_replay")
        period = info.get("last", "") or ""
        if info.get("first") and info.get("first") != period:
            period = f'{info["first"]} ~ {period}'
        note = extra or ("저장소 보관본 사용" if stale else "")
        if stale and info.get("fetch_error"):
            note = f'{note} — {str(info["fetch_error"])[:60]}'
        rows.append((label, source, period, note, stale))

    longterm, earnings = longterm or {}, earnings or {}
    add("G20 경기선행지수", longterm.get("cli_info") or earnings.get("cli_info"))
    add("뉴스심리지수(장기)", (longterm.get("extra_info") or {}).get("nsi"))
    add("장단기 금리차", (longterm.get("extra_info") or {}).get("term_spread"))
    add("TSMC 월매출", earnings.get("tsmc_info"))
    add("D램 현물가", earnings.get("dram_info"))
    add("관세청 수출입실적", earnings.get("customs_info"))
    add("관세청 10일 잠정치", earnings.get("flash_info"),
        extra=_flash_note(earnings.get("flash_info"), earnings.get("flash_applied")))
    if earnings.get("profit_source"):
        rows.append(("분기 영업이익(DART)", str(earnings["profit_source"]),
                     f'{earnings.get("profit_first", "")} ~ {earnings.get("profit_last", "")}',
                     f'{earnings.get("profit_n", "")}개 분기', False))
    if not rows:
        return ""
    body = ""
    for label, source, period, note, stale in rows:
        warn = "color:#a8322a;font-weight:600" if stale else ""
        body += (f'<tr><td style="padding:6px 10px;border-top:1px solid #eee">{escape(label)}</td>'
                 f'<td style="padding:6px 10px;border-top:1px solid #eee;font-family:ui-monospace,monospace;'
                 f'font-size:12px;color:#6b7178;{warn}">{escape(source)}</td>'
                 f'<td style="padding:6px 10px;border-top:1px solid #eee;text-align:right;{warn}">{escape(period)}</td>'
                 f'<td style="padding:6px 10px;border-top:1px solid #eee;color:#8a9199;font-size:11px">'
                 f'{escape(note)}</td></tr>')
    return ('<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:12px">'
            '<table style="width:100%;min-width:560px;border-collapse:collapse;font-size:13px;'
            'border:1px solid #e5e5e5">'
            '<tr style="background:#fafafa;font-size:11px;color:#6b7178;letter-spacing:.5px">'
            '<th style="padding:8px 10px;text-align:left">장기 전망·영업이익 추정 자료</th>'
            '<th style="padding:8px 10px;text-align:left">출처</th>'
            '<th style="padding:8px 10px;text-align:right">기간</th>'
            '<th style="padding:8px 10px;text-align:left">비고</th></tr>'
            f'{body}</table></div>'
            '<div style="font-size:11px;color:#8a9199;margin-top:4px">3·4절은 별도 도구가 만들어 이 보고서에 '
            '끼워집니다. 위 두 표(시세·월별 지표)는 이 보고서가 직접 받은 자료이고, 이 표는 그 두 절이 쓴 '
            '자료입니다. 빨간 글씨는 조회에 실패해 저장소 보관본을 쓴 것입니다.</div>')


# 보고서는 14만 자에 절이 열 개가 넘는다. 목차 없이는 어디에 무엇이 있는지 알 수 없고,
# 필요한 절로 바로 갈 수도 없다. 완성된 HTML 을 받아 h3 에 id 를 붙이고 목차를 만든다.
# 본문을 다시 조립하지 않고 제목만 손대므로 절 순서·내용·태그 균형이 바뀌지 않는다.
_H3 = re.compile(r'(<h3\b[^>]*>)(.*?)(</h3>)', re.S)
# 목차 그룹. 나열 순서가 곧 목차 순서이므로 읽는 순서와 같게 둔다.
# 판정은 '앞부분으로 시작' 또는 '포함' 둘 다 본다 — 채점 절 제목은 날짜로 시작한다
# ("2026-09-11 (금) 예측 vs 실제"), 수급 절은 "1-1." 로 시작한다.
# 그룹은 본문 순서이자 번호 순서와 같아야 한다. 예전에는 '성적'(6·7)이 '해설'(5·8)보다 앞이라
# 목차에서 5번이 6·7번 뒤에 나왔다. 번호가 뒤섞이면 목차를 믿을 수 없다.
# 목차 묶음은 읽는 내용의 성격이고 탭 경계와는 다르다 — 3절은 탭으로, 5절은 첫 탭 맨 아래로 간다.
# 묶음까지 탭에 맞추면 목차 번호가 1·2·4·5·3 으로 뒤섞인다.
NAV_GROUPS = (
    ("요약", ("한눈에", "그 밖에", "예측 vs 실제")),
    ("예측", ("1.", "1-1.", "2.", "3.", "4.")),
    ("해설", ("5.", "6.", "7.", "8.", "참고 정보")),
)


def _nav_group(title):
    for name, keys in NAV_GROUPS:
        if any(title.startswith(k) or k in title for k in keys):
            return name
    return "기타"


def add_report_nav(html_text, title_limit=34):
    """h3 에 id 를 붙이고 맨 위에 목차를 넣는다. (새 HTML, 절 목록) 반환.

    제목의 부제(회색 span)는 목차에서 뺀다 — 목차가 본문만큼 길어지면 목차가 아니다.
    """
    from html import escape
    sections = []

    def tag(match):
        open_tag, inner, close_tag = match.groups()
        # 부제는 회색 <span> 에 들어 있다. 목차에는 제목만 쓴다 — 부제까지 넣으면 목차가
        # 본문만큼 길어진다. span 을 지운 뒤 태그를 벗긴다.
        head = re.sub(r'<span\b.*?</span>', '', inner, flags=re.S)
        plain = re.sub(r'<[^>]+>', '', head)
        plain = re.sub(r'&nbsp;?', ' ', plain)
        plain = re.sub(r'\s+', ' ', plain).replace('\xa0', ' ').strip(' ·')
        if not plain:
            return match.group(0)
        index = len(sections) + 1
        anchor_id = f'sec{index}'
        sections.append({"id": anchor_id, "title": plain, "group": _nav_group(plain)})
        if 'id=' in open_tag:
            return match.group(0)
        return f'{open_tag[:-1]} id="{anchor_id}">{inner}{close_tag}'

    out = _H3.sub(tag, html_text)
    if len(sections) < 4:
        return html_text, sections

    groups = {}
    for section in sections:
        groups.setdefault(section["group"], []).append(section)
    blocks = ""
    for name, _ in NAV_GROUPS:
        items = groups.get(name)
        if not items:
            continue
        # 한 줄에 하나씩. 여러 개를 한 줄에 흘리면 '1-1.'과 '2.'가 같은 줄에 붙어 번호 순서가
        # 눈에 들어오지 않는다(2026-09-12 지적).
        links = "".join(
            f'<a href="#{s["id"]}" style="color:#1a5490;text-decoration:none;display:block;'
            f'padding:1px 0">{escape(s["title"][:title_limit])}</a>' for s in items)
        # 그룹 이름을 링크와 같은 줄에 두면, 링크가 줄바꿈될 때 다음 줄이 이름 자리까지 밀려
        # 들어와 정렬이 무너진다(모바일에서 특히 심하다). 이름을 윗줄로 올리고 링크는 아래에
        # 통째로 흐르게 한다.
        blocks += (f'<div style="margin:7px 0 0"><div style="color:#8a9199;font-size:11px;'
                   f'margin-bottom:1px">{escape(name)}</div>'
                   f'<div style="padding-left:2px">{links}</div></div>')
    nav = ('<div style="border:1px solid #e5e5e5;border-radius:6px;padding:11px 14px;margin:14px 0 4px;'
           'background:#fafafa;font-size:12px;line-height:1.8">'
           '<div style="font-size:11px;color:#8a9199;margin-bottom:4px">이 보고서의 구성</div>'
           + blocks + '</div>')
    # 첫 h3(쉬운 요약) 바로 앞에 넣는다 — 제목·생성 시각 다음이다.
    first = _H3.search(out)
    return (out[:first.start()] + nav + out[first.start():], sections) if first else (out, sections)


# 탭으로 따로 떼어 낼 절. 예전에는 이 절들을 <details> 로 접었는데, 14만 자 페이지에서
# '펼쳐 보기'를 찾아 누르는 것이 불편했다(2026-09-13 지적). 이제 상단 탭 하나씩이 된다.
# 요약과 1·2·4절(방향·수급·가격·영업이익)은 기본으로 보이는 첫 탭에 모은다. 5절(이 예측을 어떻게
# 읽어야 하는가)은 오늘 예측을 읽는 법이라 첫 탭 맨 아래에 두고, 3절 장기 전망은 월 단위라 따로
# 탭으로 뗀다. 3절이 6만 자라 첫 탭도 그만큼 가벼워진다(2026-09-13 제안).
TAB_PREFIXES = ("3.", "6.", "7.", "8.", "참고 정보")
# 탭 이름은 짧아야 한다. 절 제목을 그대로 쓰면 휴대폰에서 탭 두 개도 한 줄에 안 들어간다.
# 번호는 뺀다 — 탭으로 떨어져 나오면 5~8 이라는 순서가 읽는 데 도움이 되지 않는다(2026-09-13).
# 절 제목의 번호는 목차 순서를 위해 남긴다.
# 이름은 절 제목이 아니라 **그 탭에서 알 수 있는 것**으로 짓는다. '자동 판정'·'읽는 법'만 봐서는
# 무엇이 들어 있는지 알 수 없었다(2026-09-13 지적).
TAB_LABELS = (("3.", "장기 전망"),        # 월 단위 전망 — 수출 사이클·선행지수와 3·6·12개월 수익률
              ("6.", "과거 성적"),          # 백테스트 성능표
              ("7.", "검증 결과"),          # 쓸 만한가를 기준별로 통과·미달로 판정
              ("8.", "사용한 데이터"),       # 자산·티커·수집 기간
              ("참고 정보", "공시·발표 일정"))  # 최근 공시와 다가오는 미국 발표·실적
DEFAULT_TAB_LABEL = "오늘의 예측"

# 탭은 스크립트가 켠다. 스크립트가 없거나 실패하면 모든 절이 지금처럼 이어져 보이고,
# 탭 막대는 해당 절로 건너뛰는 링크로 동작한다 — 무엇도 숨겨지지 않는 쪽으로 실패한다.
_TAB_STYLE = (
    '<style>'
    '#rtabs-root .rtabs{position:sticky;top:0;z-index:20;display:flex;gap:2px;overflow-x:auto;'
    'background:#fff;border-bottom:1px solid #d8dce0;margin:16px 0 10px;padding-top:6px;'
    'scrollbar-width:none;-webkit-overflow-scrolling:touch}'
    '#rtabs-root .rtabs::-webkit-scrollbar{display:none}'
    '#rtabs-root .rtabs a{flex:0 0 auto;padding:9px 14px;font-size:13px;line-height:1.2;color:#5b6570;'
    'text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent;margin-bottom:-1px}'
    '#rtabs-root .rtabs a[aria-selected="true"]{color:#1a1a1a;font-weight:700;border-bottom-color:#1a5490}'
    '#rtabs-root .rtabs a:focus-visible{outline:2px solid #1a5490;outline-offset:-2px}'
    # 목차 링크로 절에 가면 붙어 있는 탭 막대가 제목을 가린다. 그만큼 띄워 멈춘다.
    '#rtabs-root h3{scroll-margin-top:56px}'
    '#rtabs-root.rtabs-on .rtab-panel{display:none}'
    '#rtabs-root.rtabs-on .rtab-panel.is-active{display:block}'
    '@media print{#rtabs-root .rtabs{display:none}#rtabs-root .rtab-panel{display:block!important}}'
    '</style>')

_TAB_SCRIPT = (
    '<script>(function(){var r=document.getElementById("rtabs-root");if(!r)return;'
    'var bar=r.querySelector(".rtabs"),tabs=bar.querySelectorAll("a"),ps=r.querySelectorAll(".rtab-panel");'
    'if(!ps.length)return;r.className+=" rtabs-on";'
    'function show(id){var hit=false,i;for(i=0;i<ps.length;i++){var on=ps[i].id===id;'
    'ps[i].classList.toggle("is-active",on);if(on)hit=true;}'
    'if(!hit){id=ps[0].id;ps[0].classList.add("is-active");}'
    'for(i=0;i<tabs.length;i++){var sel=tabs[i].getAttribute("href")==="#"+id;'
    'tabs[i].setAttribute("aria-selected",sel?"true":"false");'
    # 휴대폰에서 탭 막대가 가로로 넘치면 고른 탭이 화면 밖에 있을 수 있다. 보이게 민다.
    'if(sel&&bar.scrollWidth>bar.clientWidth){bar.scrollLeft=Math.max(0,tabs[i].offsetLeft-16);}}}'
    # 주소의 #조각이 가리키는 요소가 들어 있는 탭을 연다. 목차 링크(#sec10 등)도 이 길로 온다.
    'function route(){var h="";try{h=decodeURIComponent(location.hash.slice(1));}catch(e){}'
    'var el=h?document.getElementById(h):null,p=el&&el.closest?el.closest(".rtab-panel"):null;'
    'if(!p){show(ps[0].id);return;}show(p.id);'
    'if(el===p){window.scrollTo(0,r.getBoundingClientRect().top+window.pageYOffset-4);}'
    'else{el.scrollIntoView();}}'
    'for(var k=0;k<tabs.length;k++){tabs[k].addEventListener("click",function(e){e.preventDefault();'
    'var id=this.getAttribute("href").slice(1);show(id);'
    'if(history.replaceState)history.replaceState(null,"","#"+id);'
    # 한참 내려와 탭 막대가 붙어 있을 때 탭을 바꾸면 새 탭의 첫머리로 올린다.
    'if(bar.getBoundingClientRect().top<=0){window.scrollTo(0,r.getBoundingClientRect().top+window.pageYOffset-4);}'
    '});}'
    # 목차처럼 탭 안의 절을 가리키는 링크는 여기서 직접 그 탭을 연다. hashchange 에만 기대면
    # #조각이 주소에 붙지 않는 환경(미리보기·일부 앱 안 브라우저)에서 목차가 먹통이 된다(2026-09-13 확인).
    'document.addEventListener("click",function(e){'
    'var a=e.target&&e.target.closest?e.target.closest("a"):null;if(!a||a.parentNode===bar)return;'
    'var h=a.getAttribute("href")||"";if(h.charAt(0)!=="#")return;'
    'var el=document.getElementById(h.slice(1)),p=el&&el.closest?el.closest(".rtab-panel"):null;'
    'if(!p)return;e.preventDefault();show(p.id);'
    'try{history.pushState(null,"",h);}catch(err){}'
    'if(el===p){window.scrollTo(0,r.getBoundingClientRect().top+window.pageYOffset-4);}'
    'else{el.scrollIntoView();}});'
    'window.addEventListener("hashchange",route);window.addEventListener("popstate",route);'
    'route();})();</script>')


def _section_title(inner):
    """h3 안쪽 HTML 에서 부제(회색 span)를 뺀 제목 글자만."""
    title = re.sub(r'<span\b.*?</span>', '', inner, flags=re.S)
    title = re.sub(r'<[^>]+>', '', title)
    title = re.sub(r'&nbsp;?', ' ', title)
    return re.sub(r'\s+', ' ', title).strip(' ·')


def _tab_label(title, labels=TAB_LABELS):
    for prefix, label in labels:
        if title.startswith(prefix):
            return label
    return title[:16]


def tabify_sections(html_text, prefixes=TAB_PREFIXES, labels=TAB_LABELS,
                    default_label=DEFAULT_TAB_LABEL):
    """h3 절을 상단 탭으로 나눈다. 첫 탭에는 기본으로 보이던 절을, 나머지 탭에는 접던 절을 하나씩.

    h3 에서 다음 h3 직전까지를 한 절로 보고 통째로 옮기므로 절 안의 태그 균형은 그대로다.
    마지막 절의 끝에는 바깥 래퍼의 닫는 </div> 가 붙어 있어, 그만큼 떼어 탭 묶음 밖에 둔다
    (안에 두면 여는 태그 없이 닫혀 레이아웃이 무너진다).

    기본 절이 탭 절 뒤에 나오면 첫 탭으로 끌어올려진다. 지금 보고서는 3절을 탭으로 떼므로
    4·5절이 첫 탭에서 2절 바로 뒤로 온다.
    """
    from html import escape
    parts = list(_H3.finditer(html_text))
    if len(parts) < 2:
        return html_text
    starts = _section_starts(html_text, parts)
    head, tail, chunks = html_text[:starts[0]], "", []
    for index, match in enumerate(parts):
        end = starts[index + 1] if index + 1 < len(parts) else len(html_text)
        chunk = html_text[starts[index]:end]
        if index + 1 == len(parts):
            surplus = len(re.findall(r'</div>', chunk)) - len(re.findall(r'<div\b', chunk))
            for _ in range(max(0, surplus)):
                position = chunk.rindex('</div>')
                tail = chunk[position:] + tail
                chunk = chunk[:position]
        chunks.append((_section_title(match.group(2)), chunk))
    split = [(title, chunk, any(title.startswith(p) for p in prefixes)) for title, chunk in chunks]
    tabbed = [(title, chunk) for title, chunk, is_tab in split if is_tab]
    basic = [chunk for _, chunk, is_tab in split if not is_tab]
    if not tabbed or not basic:
        return html_text
    names = [default_label] + [_tab_label(title, labels) for title, _ in tabbed]
    bar = ('<nav class="rtabs" aria-label="보고서 탭">'
           + "".join(f'<a href="#rtab-{i}" aria-selected="{"true" if i == 0 else "false"}">'
                     f'{escape(name)}</a>' for i, name in enumerate(names))
           + '</nav>')
    panels = (f'<section class="rtab-panel" id="rtab-0">{"".join(basic)}</section>'
              + "".join(f'<section class="rtab-panel" id="rtab-{i}">{chunk}</section>'
                        for i, (_, chunk) in enumerate(tabbed, start=1)))
    out = (head + '<div id="rtabs-root">' + _TAB_STYLE + bar + panels + _TAB_SCRIPT + '</div>'
           + tail)
    # 브라우저가 실제로 쌓을 모양으로 한 번 더 본다. 제목 하나라도 탭 밖에 떨어지거나 탭 안에 탭이
    # 생기면 쓰지 않고 원래 페이지를 돌려준다 — 탭 없이 모든 절이 보이는 쪽이 깨진 탭보다 낫다.
    if any(problem.startswith(BLOCKING_TAB_PROBLEMS) for problem in tab_structure_problems(out)):
        return html_text
    return out


# 태그 개수만 세면 못 잡는 깨짐이 있다. 쉬운 요약은 <section id="easy-summary"><h3>…</h3>…</section>
# 처럼 감싸개가 제목보다 먼저 열리는데, 제목에서 자르면 </section> 만 첫 탭 안에 남는다. 개수는 맞지만
# 브라우저는 그 </section> 에서 첫 탭을 닫아 버려 1~4절이 탭 밖으로 쏟아졌다(2026-09-13 미리보기에서 확인).
_BLOCK_TAGS = ("div", "section", "details", "table", "ul", "ol", "nav", "article", "header",
               "footer", "aside", "main", "figure")
_BLOCK = re.compile(r'<(/?)(' + "|".join(_BLOCK_TAGS) + r')\b[^>]*>', re.I)
# 주석은 다른 내용을 건너뛰어 이어 붙지 않도록 --> 를 넘지 못하게 한다.
_TRAILING_GAP = re.compile(r'(?:\s|<!--(?:(?!-->).)*-->)*\Z', re.S)
_TRAILING_OPENER = re.compile(r'<(div|section|article|header)\b[^>]*>\Z', re.I)
BLOCKING_TAB_PROBLEMS = ("탭 밖 제목", "탭 안에 탭")


def _stray_closers(text, name):
    """text 안에서 짝이 되는 여는 태그 없이 닫히는 name 태그의 수."""
    depth = stray = 0
    for match in _BLOCK.finditer(text):
        if match.group(2).lower() != name:
            continue
        if match.group(1):
            if depth:
                depth -= 1
            else:
                stray += 1
        else:
            depth += 1
    return stray


def _section_starts(html_text, parts):
    """절이 실제로 시작하는 위치들.

    제목 바로 앞의 주석(<!--LEDGER_SECTION_START--> 같은 표시)은 뒤 절에 붙인다 — 오후 채점 갱신이
    START~END 사이를 통째로 바꾸므로 두 표시가 같은 탭에 있어야 한다. 제목 바로 앞에서 열리는
    감싸개는 **이 절 안에서 닫힐 때만** 끌어온다. 페이지 전체를 감싸는 래퍼처럼 다른 곳에서
    닫히는 것은 끌어오지 않는다.
    """
    starts = []
    for index, match in enumerate(parts):
        end = parts[index + 1].start() if index + 1 < len(parts) else len(html_text)
        floor = parts[index - 1].end() if index else 0      # 앞 절의 제목 안으로는 들어가지 않는다
        start = match.start()
        while True:
            gap_start = _TRAILING_GAP.search(html_text, floor, start).start()
            opener = _TRAILING_OPENER.search(html_text, floor, gap_start)
            if opener:
                name = opener.group(1).lower()
                if (_stray_closers(html_text[opener.start():end], name)
                        < _stray_closers(html_text[start:end], name)):
                    start = opener.start()
                    continue
            start = gap_start
            break
        starts.append(start)
    return starts


def tab_structure_problems(html_text):
    """브라우저처럼 태그를 쌓아 보고 탭 구조의 문제를 돌려준다. 문제가 없으면 빈 목록.

    '탭 밖 제목'과 '탭 안에 탭'은 화면이 실제로 깨지는 경우라 tabify_sections 가 탭을 포기한다.
    짝 없는 닫는 태그는 브라우저가 무시하므로 알리기만 한다.
    """
    from html.parser import HTMLParser
    tracked = set(_BLOCK_TAGS)

    class Walker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.problems, self.title, self.in_h3, self.h3_inside = [], [], [], False, False

        def handle_starttag(self, tag, attrs):
            if tag in tracked:
                is_panel = "rtab-panel" in (dict(attrs).get("class") or "").split()
                if is_panel and any(panel for _, panel in self.stack):
                    self.problems.append("탭 안에 탭: " + (dict(attrs).get("id") or ""))
                self.stack.append((tag, is_panel))
            elif tag == "h3":
                self.in_h3, self.title = True, []
                self.h3_inside = any(panel for _, panel in self.stack)

        def handle_endtag(self, tag):
            if tag == "h3" and self.in_h3:
                self.in_h3 = False
                if not self.h3_inside:
                    self.problems.append("탭 밖 제목: " + re.sub(r"\s+", " ", "".join(self.title)).strip()[:30])
            elif tag in tracked:
                if tag not in [name for name, _ in self.stack]:
                    self.problems.append(f"짝 없는 </{tag}>")
                    return
                while self.stack:
                    name, _ = self.stack.pop()
                    if name == tag:
                        break

        def handle_data(self, data):
            if self.in_h3:
                self.title.append(data)

    walker = Walker()
    walker.feed(html_text)
    walker.close()
    return walker.problems


# 조각(7·8절)은 월 1회 실행이 만들어 저장소에 남는다. 보고서 절 번호를 바꿔도 옛 조각에는
# 옛 번호가 박혀 있어 다음 월간 실행 전까지 번호가 겹친다. 그래서 조각을 끼울 때 제목의
# 번호만 지금 체계로 바꾼다. 조각 내용 자체는 건드리지 않는다.
FRAGMENT_RENUMBER = (("7. 장기 전망", "3. 장기 전망"),
                     ("8. 이번 분기 영업이익", "4. 이번 분기 영업이익"))


def renumber_fragment(html_text):
    """조각 제목의 절 번호를 현재 체계로 맞춘다(h3 안에서만)."""
    if not html_text:
        return html_text

    def fix(match):
        head = match.group(0)
        for old, new in FRAGMENT_RENUMBER:
            if old in head:
                return head.replace(old, new, 1)
        return head

    return re.sub(r"<h3\b[^>]*>.*?</h3>", fix, html_text, flags=re.S)


def event_notice_html(flags):
    flags = list(flags or [])
    if not flags:
        return ""
    words = {"실적시즌": "분기 실적 발표 시즌(분기 종료 후 2주 안)", "공시:잠정실적": "잠정실적 공시 직후",
             "미국지표:미국 소비자물가(CPI)": "미국 CPI 발표 다음 거래일",
             "미국지표:미국 생산자물가(PPI)": "미국 PPI 발표 다음 거래일",
             "미국지표:FOMC 금리 결정": "FOMC 금리 결정 다음 거래일",
             "미국지표:마이크론 실적": "마이크론 실적 발표 다음 거래일(같은 메모리 업종)",
             "미국지표:엔비디아 실적": "엔비디아 실적 발표 다음 거래일",
             "미국지표:TSMC 실적": "TSMC 실적 발표 다음 거래일",
             "미국지표:AMD 실적": "AMD 실적 발표 다음 거래일",
             "공시:배당": "배당 관련 공시 직후", "공시:자사주": "자사주 공시 직후",
             "공시:공급계약": "공급계약 공시 직후", "공시:자본변동": "자본 변동 공시 직후",
             "공시:정기보고서": "정기보고서 제출 직후"}
    described = " · ".join(words.get(f, f) for f in flags)
    return ('<div style="background:#fdf8ec;border-left:4px solid #c8952a;padding:10px 14px;'
            'border-radius:0 5px 5px 0;font-size:13px;margin-bottom:12px">'
            f'<b>이벤트일</b> — {described}. 이런 날은 방향과 무관하게 <b>변동폭이 커지는 경향</b>이 있어 '
            '구간이 실제보다 좁을 수 있습니다. 원장에 표시가 남으므로 나중에 평일과 나눠 채점됩니다.</div>')


def upcoming_events_html(events, pending=None):
    """다가오는 미국 지표·실적 일정. 예측에 쓰지 않고, 며칠 안에 변동성이 커질 날을 미리 알린다.

    pending 은 아직 회사가 공지하지 않은 실적(추정 날짜를 넣지 않는 이유를 함께 적는다).
    """
    if not events and not pending:
        return ""
    parts = []
    if events:
        items = " · ".join(f'<b>{e["date"]}</b> {e["label"]}' for e in events[:6])
        parts.append(f'다가오는 발표 — {items}. 발표 자체는 예측에 쓰지 않지만, 그 다음 거래일은 '
                     '방향과 무관하게 변동폭이 커지는 경향이 있습니다.')
    if pending:
        parts.append('날짜 미확정 — ' + " · ".join(pending.values())
                     + '. 회사가 공지하면 <code>macro_inputs/us_calendar.csv</code>에 적어 주세요. '
                       '제3자 캘린더의 추정 날짜는 서로 어긋나 넣지 않습니다.')
    return ('<div style="font-size:12px;color:#6b7178;margin:8px 0 0;padding:8px 12px;'
            'background:#f7f8fa;border-radius:5px">' + "<br>".join(parts) + '</div>')


def disclosure_section_html(disclosures, disclosure_info, classify):
    import html as _html
    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '참고 정보: 최근 공시와 예정 발표 <span style="font-weight:400;color:#8a9199;font-size:12px">&nbsp;DART · 최근 10일 · '
            '예측에 쓰지 않음, 빗나간 날의 이유를 찾는 참고용</span></h3>')
    if not disclosure_info.get("enabled"):
        return head + f'<div style="font-size:13px;color:#6b7178">공시 목록 없음 — {disclosure_info.get("reason", "")}</div>'
    if not disclosures:
        return head + '<div style="font-size:13px;color:#6b7178">최근 10일 공시가 없습니다.</div>'
    rows = ""
    for d in disclosures[:15]:
        label = classify(d["report_nm"])
        tag = (f'<span style="background:#fdf8ec;color:#7a4b00;font-size:11px;padding:1px 7px;border-radius:9px;margin-left:6px">{label}</span>'
               if label else "")
        date = f'{d["rcept_dt"][:4]}-{d["rcept_dt"][4:6]}-{d["rcept_dt"][6:]}' if len(d["rcept_dt"]) == 8 else d["rcept_dt"]
        rows += (f'<tr><td style="padding:6px 10px;border-top:1px solid #eee;white-space:nowrap;color:#6b7178">{date}</td>'
                 f'<td style="padding:6px 10px;border-top:1px solid #eee"><a href="{d["url"]}" style="color:#1a5490">'
                 f'{_html.escape(d["report_nm"])}</a>{tag}</td></tr>')
    return head + ('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;'
                   f'font-size:13px;border:1px solid #e5e5e5">{rows}</table></div>')


def flow_section_html(flow_frame, flow_info, active, close_series, last_date, comparison):
    """외국인·기관 수급 절. close_series 는 종가(원화 환산용), comparison 은 쌍체 비교표(DataFrame)."""
    head = ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '1-1. 외국인·기관 수급 <span style="font-weight:400;color:#8a9199;font-size:12px">&nbsp;방향 판단 보조자료 · 전일까지 · '
            '투자자별 순매수(주식 수)와 외국인 지분율</span></h3>')
    if not active or flow_frame is None or flow_frame.empty:
        return head + ('<div style="font-size:13px;color:#6b7178">이번 실행에는 수급 자료가 없습니다 — '
                       f'{flow_info.get("reason", "")}</div>')
    fl = flow_frame.set_index("date").sort_index()
    fl = fl[fl.index <= last_date]
    close = close_series.reindex(fl.index).ffill()
    approx_krw = lambda shares: shares * close.reindex(shares.index).ffill()
    rows = ""
    for label, n in (("1일", 1), ("5일", 5), ("20일", 20), ("60일", 60)):
        tail = fl.tail(n)
        f_sh = float(tail["foreign_net"].sum())
        i_sh = float(tail["inst_net"].sum()) if tail["inst_net"].notna().any() else float("nan")
        f_krw = float((tail["foreign_net"] * close.reindex(tail.index)).sum()) / 1e8
        color = "#1e6b34" if f_sh > 0 else "#a8322a"
        rows += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">최근 {label}</td>'
                 f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right;color:{color};font-weight:600">'
                 f'{f_sh:+,.0f}주 <span style="font-weight:400;color:#8a9199">(≈{f_krw:+,.0f}억원)</span></td>'
                 f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">'
                 f'{"—" if pd.isna(i_sh) else format(i_sh, "+,.0f") + "주"}</td></tr>')
    ratio_now = fl["foreign_ratio"].dropna()
    ratio_line = ""
    if len(ratio_now):
        r0 = float(ratio_now.iloc[-1])
        r20 = float(ratio_now.iloc[-21]) if len(ratio_now) > 20 else float("nan")
        ratio_line = (f'<div style="font-size:13px;margin-top:8px">외국인 지분율 <b>{r0:.2f}%</b>'
                      + (f' <span style="color:#6b7178">(20거래일 전 {r20:.2f}%, {r0 - r20:+.2f}%p)</span>' if pd.notna(r20) else "")
                      + f' · 기준 {ratio_now.index[-1].date()}</div>')
    streak_sign = np.sign(fl["foreign_net"].fillna(0)).iloc[::-1]
    streak = 0
    for v in streak_sign:
        if v == 0 or (streak and np.sign(streak) != v):
            break
        streak += int(v)
    streak_text = (f'{abs(streak)}거래일 연속 {"순매수" if streak > 0 else "순매도"}' if streak else "전일 순매수 0")
    table = ('<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
             '<tr style="background:#fafafa;font-size:11px;color:#6b7178"><th style="padding:8px 11px;text-align:left">기간</th>'
             '<th style="padding:8px 11px;text-align:right">외국인 순매수</th><th style="padding:8px 11px;text-align:right">기관 순매수</th></tr>'
             f'{rows}</table></div>'
             f'<div style="font-size:13px;margin-top:8px">외국인 {streak_text} · 마지막 자료 {fl.index[-1].date()} · 출처 {flow_info.get("source", "")}</div>'
             + ratio_line)
    # 60일 그림: 외국인 누적 순매수(막대 누적) vs 종가
    window = fl.tail(60)
    if len(window) >= 20:
        # 제목은 그림 영역(TOP 아래) 바깥에 두고, 그림 요소는 clipPath 로 영역 안에 가둔다.
        W, L, R, TOP, PH, BOT = 900, 66, 66, 40, 200, 30
        H = TOP + PH + BOT
        cum = window["foreign_net"].fillna(0).cumsum()
        px = close.reindex(window.index)
        xs = np.linspace(L, W - R, len(window))
        clo, chi = float(min(cum.min(), 0)), float(max(cum.max(), 0)); pad = (chi - clo) * .1 or 1
        plo, phi = float(px.min()), float(px.max()); ppad = (phi - plo) * .1 or 1
        YC = lambda v: TOP + PH - (v - (clo - pad)) / ((chi + pad) - (clo - pad)) * PH
        YP = lambda v: TOP + PH - (v - (plo - ppad)) / ((phi + ppad) - (plo - ppad)) * PH
        svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:11px">',
               f'<defs><clipPath id="flowplot"><rect x="{L}" y="{TOP}" width="{W - L - R}" height="{PH}"/></clipPath></defs>',
               '<g clip-path="url(#flowplot)">']
        svg.append(f'<line x1="{L}" x2="{W - R}" y1="{YC(0):.1f}" y2="{YC(0):.1f}" stroke="#999" stroke-dasharray="3,3"/>')
        step = (W - L - R) / len(window)
        # 일별 막대는 누적선 축이 아니라 자체 축으로 그린다. 예전에는 누적 축에 5배 확대해 그려서
        # 큰 날이 그림 영역 위 제목 자리까지 삐져나갔다. 가장 큰 날이 그림 높이의 40%가 되게 한다.
        daily = window["foreign_net"].fillna(0)
        bar_scale = (0.4 * PH) / max(float(daily.abs().max()), 1e-9)
        for x, v in zip(xs, daily):
            y0 = YC(0)
            hgt = abs(float(v)) * bar_scale
            top = y0 - hgt if v > 0 else y0
            svg.append(f'<rect x="{x - step * .3:.1f}" y="{top:.1f}" width="{step * .6:.1f}" height="{max(hgt, .5):.1f}" fill="{"#4c78a8" if v > 0 else "#b5453c"}" opacity="0.35"/>')
        svg.append(f'<polyline points="{" ".join(f"{x:.1f},{YC(v):.1f}" for x, v in zip(xs, cum))}" fill="none" stroke="#1a5490" stroke-width="2"/>')
        svg.append(f'<polyline points="{" ".join(f"{x:.1f},{YP(v):.1f}" for x, v in zip(xs, px))}" fill="none" stroke="#c8952a" stroke-width="1.6"/>')
        svg.append('</g>')
        for v in (clo, 0, chi):
            svg.append(f'<text x="{L - 6}" y="{YC(v) + 4:.1f}" text-anchor="end" fill="#1a5490">{v / 1e4:+,.0f}만주</text>')
        for v in (plo, phi):
            svg.append(f'<text x="{W - R + 6}" y="{YP(v) + 4:.1f}" fill="#c8952a">{v:,.0f}</text>')
        svg.append(f'<text x="{L}" y="16" fill="#1a1a1a" font-weight="600">최근 60거래일 — 외국인 누적 순매수(파랑, 왼쪽) · 일별 순매수(막대, 자체 눈금) · 종가(주황, 오른쪽)</text>')
        for k in range(0, len(window), 10):
            svg.append(f'<text x="{xs[k]:.1f}" y="{H - 8}" text-anchor="middle" fill="#8a9199">{window.index[k].strftime("%m/%d")}</text>')
        svg.append(f'<rect x="{L}" y="{TOP}" width="{W - L - R}" height="{PH}" fill="none" stroke="#ddd"/></svg>')
        table += f'<div style="border:1px solid #e5e5e5;border-radius:6px;padding:8px;margin-top:10px">{"".join(svg)}</div>'
    # 효과
    effect = ""
    if len(comparison):
        rows_e = ""
        for _, rr in comparison.iterrows():
            ci = f'[{rr["lo"]:+.4f}, {rr["hi"]:+.4f}]' if pd.notna(rr.get("lo")) else "—"
            verdict = ("동률" if (pd.isna(rr.get("lo")) or rr["lo"] <= 0 <= rr["hi"]) else
                       ("<b style=\'color:#1e6b34\'>우위</b>" if (rr["delta"] > 0) != (rr["metric"] == "log_loss") else "<b style=\'color:#a8322a\'>열위</b>"))
            rows_e += (f'<tr><td style="padding:7px 11px;border-top:1px solid #eee">{rr["metric"]}</td>'
                       f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">{rr["delta"]:+.4f}</td>'
                       f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">{ci}</td>'
                       f'<td style="padding:7px 11px;border-top:1px solid #eee;text-align:right">{verdict}</td></tr>')
        effect = ('<div style="font-size:12px;color:#6b7178;margin:12px 0 4px">수급 특징을 넣은 모델(Mean ensemble) − 뺀 모델(No flow ensemble), 같은 날짜 쌍체 비교. '
                  'log loss는 음수가 유리, CI가 0을 포함하면 동률.</div>'
                  '<div style="overflow-x:auto"><table style="width:100%;min-width:420px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
                  '<tr style="background:#fafafa;font-size:11px;color:#6b7178"><th style="padding:8px 11px;text-align:left">지표</th>'
                  '<th style="padding:8px 11px;text-align:right">차이</th><th style="padding:8px 11px;text-align:right">95% CI</th>'
                  '<th style="padding:8px 11px;text-align:right">판정</th></tr>' + rows_e + '</table></div>')
    note = ('<div style="font-size:11px;color:#8a9199;margin-top:6px">외국인 순매수와 주가는 <b>같은 날</b> 같이 움직입니다. '
            '이 절이 재는 것은 "전일까지의 순매수가 다음 날 방향을 맞히는가"이고, 그 답은 위 비교표입니다. '
            '투자자별 실적은 장 마감 뒤 확정되므로 07:00 예측에는 전일까지만 들어갑니다.</div>')
    return head + table + effect + note


def code_version(repo, branch, token=None):
    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        try:
            import subprocess
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                 text=True, timeout=10).stdout.strip() or None
        except Exception:
            sha = None
    info = {"sha": sha, "short": sha[:7] if sha else None, "message": None, "date_kst": None,
            "url": f"https://github.com/{repo}/commit/{sha}" if sha else None}
    try:
        import urllib.request
        _tok = token
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/commits/{sha or branch}",
            headers={"Accept": "application/vnd.github+json",
                     **({"Authorization": f"Bearer {_tok}"} if _tok else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        info.update(sha=payload["sha"], short=payload["sha"][:7],
                    url=payload.get("html_url") or info["url"],
                    message=(payload["commit"]["message"] or "").splitlines()[0][:90])
        info["date_kst"] = (pd.Timestamp(payload["commit"]["committer"]["date"])
                            .tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"))
    except Exception:
        pass                      # 커밋 정보를 못 받아도 보고서는 나와야 한다
    return info


def load_fragment(name, target, repo, branch):
    """저장소 체크아웃(Actions)이면 파일에서, 아니면 GitHub raw에서 읽는다. 없으면 None."""
    candidates = [Path.cwd() / "docs" / target / name]
    for path in candidates:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
    try:
        import urllib.request
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/docs/{target}/{name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8")
    except Exception:
        return None


def load_summary_data(name, target, repo, branch):
    # HTML에서 숫자를 추측하지 않고, 상세 보고서와 같은 구조화된 계산 결과를 읽는다.
    try:
        payload = json.loads(load_fragment(name, target, repo, branch) or "{}")
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}
