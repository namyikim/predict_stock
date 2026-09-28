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
    """장기 전망 탭의 두 절(장기 전망·영업이익 추정)이 쓴 자료원 표.

    데이터 절의 표는 노트북이 직접 받은 자료만 적는다. 두 절은 별도 도구가 만들어 조각으로 끼워지므로
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
                 f'<td style="padding:6px 10px;border-top:1px solid #eee;color:#8a9199;font-size:13px">'
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
            '<div style="font-size:11px;color:#8a9199;margin-top:4px">장기 전망 탭의 두 절은 별도 도구가 만들어 이 보고서에 '
            '끼워집니다. 위 두 표(시세·월별 지표)는 이 보고서가 직접 받은 자료이고, 이 표는 그 두 절이 쓴 '
            '자료입니다. 빨간 글씨는 조회에 실패해 저장소 보관본을 쓴 것입니다.</div>')


# 보고서는 14만 자에 절이 열 개가 넘는다. h3 에 id 를 붙여 절마다 바로 갈 수 있게 한다.
# 본문을 다시 조립하지 않고 제목만 손대므로 절 순서·내용·태그 균형이 바뀌지 않는다.
# 예전에는 맨 위에 '이 보고서의 구성' 목차도 넣었다. 상단 탭이 생긴 뒤로는 탭이 목차 노릇을 하고,
# 목차는 첫 탭 맨 위에서 쉬운 요약을 한 화면 아래로 밀어낼 뿐이라 뺐다(2026-09-13 지적).
_H3 = re.compile(r'(<h3\b[^>]*>)(.*?)(</h3>)', re.S)


def add_section_ids(html_text):
    """h3 에 id="secN" 을 붙인다. (새 HTML, 절 목록) 반환. 이미 id 가 있는 제목은 그대로 둔다."""
    sections = []

    def tag(match):
        open_tag, inner, close_tag = match.groups()
        title = _section_title(inner)
        if not title:
            return match.group(0)
        anchor_id = f'sec{len(sections) + 1}'
        sections.append({"id": anchor_id, "title": title})
        if 'id=' in open_tag:
            return match.group(0)
        return f'{open_tag[:-1]} id="{anchor_id}">{inner}{close_tag}'

    return _H3.sub(tag, html_text), sections


# 탭으로 따로 떼어 낼 절. 예전에는 이 절들을 <details> 로 접었는데, 14만 자 페이지에서
# '펼쳐 보기'를 찾아 누르는 것이 불편했다(2026-09-13 지적). 이제 상단 탭이 된다.
# 쉬운 요약·방향·수급·가격과 '이 예측을 어떻게 읽어야 하는가'는 기본으로 보이는 첫 탭에 둔다.
# 장기 전망과 이번 분기 영업이익 추정은 월 단위 결론이라 한 탭에 함께 둔다(2026-09-13 제안).
#
# 탭 하나에 절이 여럿 들어갈 수 있다. 절 번호는 탭마다 1부터 새로 매긴다(첫 탭 1~3, 장기 전망 탭
# 1·2, 절이 하나뿐인 탭은 번호 없음). 그래서 절은 번호가 아니라 **번호를 뗀 제목의 앞부분**으로 고른다.
# 탭 이름은 짧게, 그 탭에서 알 수 있는 것으로 짓는다 — 절 제목 그대로는 휴대폰에서 탭 두 개도 한 줄에
# 안 들어가고, '자동 판정'·'읽는 법'만 봐서는 무엇이 들어 있는지 알 수 없었다(2026-09-13 지적).
TAB_GROUPS = (
    # 쉬운 요약(이번 분기 영업이익·앞으로의 흐름·가격 도달 시점) → 1. 장기 전망 → 2. 이번 분기 영업이익
    # → 3. 지난 전망은 맞았나(장기 전망 탭의 전망을 발표된 값으로 채점, 2026-09-27)
    ("장기 전망", ("한눈에 보는 장기 전망 요약", "장기 전망", "이번 분기 영업이익", "지난 전망은 맞았나")),
    # '성적'만으로는 무엇에 대한 성적인지 알 수 없다(2026-09-14 지적). 무엇을 맞히려 한 성적인지
    # 탭 이름에 넣는다.
    # 주간 뉴스는 참고 자료지만 실제로 읽는 거리라 자주 본다. 성격별 묶음(모델 결과 → 참고
    # 자료)보다 '자주 보는 순서'를 따른다(2026-09-15 지적).
    ("주간 뉴스", ("주간 반도체 뉴스",)),               # 주 1회 브리핑. 예측에 쓰지 않는 참고 자료
    # 장 회고(시간대별 뉴스)는 여기 넣지 않는다 — '오늘의 예측' 탭 맨 끝에 둔다(2026-09-28 요청).
    ("예측 성적", ("실제 발행 후 누적 성적", "예측 vs 실제", "이 모델의 예측 성적", "이 예측을 어떻게 읽어야 하는가", "학습·검증 설정")),             # 기준별 판정과 그 근거인 모델별 성능표
    ("사용한 데이터", ("이 보고서의 데이터",)),           # 자산·티커·수집 기간
    ("공시·발표 일정", ("참고 정보",)),                 # 최근 공시와 다가오는 미국 발표·실적
)
DEFAULT_TAB_LABEL = "오늘의 예측"
_SECTION_NUMBER = re.compile(r'^\d+(?:-\d+)?\.\s*')

# 탭은 스크립트가 켠다. 스크립트가 없거나 실패하면 모든 절이 지금처럼 이어져 보이고,
# 탭 막대는 해당 절로 건너뛰는 링크로 동작한다 — 무엇도 숨겨지지 않는 쪽으로 실패한다.
_TAB_STYLE = (
    '<style>'
    # 탭 → 왼쪽 메뉴(2026-09-28 요청). 넓은 화면에서는 메뉴를 왼쪽 세로 목록으로 두고 본문을 오른쪽에 둔다.
    # 메뉴는 따라 내려와(sticky) 긴 절을 읽다가도 바로 옮길 수 있다. 휴대폰 폭에서 왼쪽 메뉴를 두면 본문이
    # 너무 좁아지므로 900px 미만에서는 예전처럼 위쪽에 줄바꿈되는 버튼 막대로 둔다.
    # 버튼 모양은 목록 버튼(테두리 #cedff0·모서리 5px)을 따르고, 고른 메뉴는 파랗게 채운다.
    '#rtabs-root .rtabs{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:6px;'
    'background:#fff;border-bottom:1px solid #d8dce0;margin:16px 0 12px;padding:8px 0 10px}'
    '#rtabs-root .rtabs a{flex:0 0 auto;padding:8px 14px;min-height:36px;box-sizing:border-box;font-size:14px;'
    'font-weight:600;line-height:1.3;color:#1a5490;background:#fff;border:1px solid #cedff0;'
    'border-radius:5px;text-decoration:none;white-space:nowrap}'
    '#rtabs-root .rtabs a:hover{background:#f0f6fc;border-color:#7fa9d4}'
    '#rtabs-root .rtabs a[aria-selected="true"]{color:#fff;background:#1a5490;border-color:#1a5490;'
    'box-shadow:0 1px 4px rgba(26,84,144,.35)}'
    '#rtabs-root .rtabs a:focus-visible{outline:2px solid #1a5490;outline-offset:2px}'
    '@media (max-width:640px){#rtabs-root .rtabs{gap:5px}#rtabs-root .rtabs a{padding:7px 12px;font-size:13px;min-height:34px}}'
    # 목차 링크로 절에 가면 위쪽에 붙은 버튼 막대가 제목을 가린다. 그만큼 띄워 멈춘다.
    '#rtabs-root h3{scroll-margin-top:110px}'
    '#rtabs-root.rtabs-on .rtab-panel{display:none}'
    '#rtabs-root.rtabs-on .rtab-panel.is-active{display:block}'
    # 왼쪽 메뉴: 스크립트가 켜졌을 때만(rtabs-on) 두 칸으로 나눈다. 스크립트가 없으면 모든 절이 이어져 보인다.
    '@media (min-width:900px){'
    '#rtabs-root.rtabs-on{display:grid;grid-template-columns:168px minmax(0,1fr);column-gap:24px;align-items:start}'
    '#rtabs-root.rtabs-on .rtabs{grid-column:1;grid-row:1;flex-direction:column;flex-wrap:nowrap;gap:6px;'
    'top:12px;margin:16px 0 0;padding:0;border-bottom:none;max-height:calc(100vh - 24px);overflow-y:auto}'
    '#rtabs-root.rtabs-on .rtabs a{display:block;white-space:normal;padding:9px 12px}'
    '#rtabs-root.rtabs-on .rtab-panel{grid-column:2;grid-row:1;min-width:0}'
    '#rtabs-root.rtabs-on h3{scroll-margin-top:16px}}'
    # 메뉴는 본문 폭 안에 둔다. 본문 밖 왼쪽으로 내보내면 본문 폭이 페이지마다 달라(종목 980px, 금·은은 더 넓다)
    # 어떤 화면 폭에서는 메뉴가 화면 밖으로 잘린다.
    '@media print{#rtabs-root .rtabs{display:none}#rtabs-root .rtab-panel{display:block!important}'
    '#rtabs-root.rtabs-on{display:block}}'
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
    # 한참 내려와 있을 때 메뉴를 바꾸면 새 탭의 첫머리로 올린다. 왼쪽 메뉴는 top:12px 에 붙으므로 막대가 아니라
    # 탭 묶음의 위치로 판단한다(2026-09-28).
    'if(r.getBoundingClientRect().top<0){window.scrollTo(0,r.getBoundingClientRect().top+window.pageYOffset-4);}'
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
    'var hs,major,minor;for(var j=0;j<ps.length;j++){major=0;minor=0;hs=ps[j].querySelectorAll("h3,h4");'
    'for(var n=0;n<hs.length;n++){var h=hs[n],t=h.firstChild;if(!t||t.nodeType!==3)continue;'
    'var prefix;if(h.tagName==="H3"){major++;minor=0;prefix=major+". ";}'
    'else{minor++;prefix=major+"."+minor+" ";}'
    't.textContent=prefix+t.textContent.replace(/^\\s*(?:\\d+(?:-\\d+)?\\.\\s+|\\d+\\.\\d+\\s+)/,"");}}'
    'route();})();</script>')


def _section_title(inner):
    """h3 안쪽 HTML 에서 부제(회색 span)를 뺀 제목 글자만."""
    title = re.sub(r'<span\b.*?</span>', '', inner, flags=re.S)
    title = re.sub(r'<[^>]+>', '', title)
    title = re.sub(r'&nbsp;?', ' ', title)
    return re.sub(r'\s+', ' ', title).strip(' ·')


def _tab_for(title, groups=TAB_GROUPS):
    """절 제목이 들어갈 (탭 이름, 탭 안 순서). 첫 탭(기본)이면 (None, 0).

    번호를 뗀 제목의 앞부분으로 고른다. 탭 안 순서는 groups 에 적은 열쇠의 순서다.
    """
    bare = _SECTION_NUMBER.sub('', title)
    if '예측 vs 실제' in bare:
        bare = '예측 vs 실제'
    for label, keys in groups:
        for rank, key in enumerate(keys):
            if bare.startswith(key):
                return label, rank
    return None, 0


def tabify_sections(html_text, groups=TAB_GROUPS, default_label=DEFAULT_TAB_LABEL):
    """h3 절을 상단 탭으로 나눈다. 첫 탭에는 기본으로 보이는 절을, 나머지 탭에는 groups 가 고른 절을.

    h3 에서 다음 h3 직전까지를 한 절로 보고 통째로 옮기므로 절 안의 태그 균형은 그대로다.
    마지막 절의 끝에는 바깥 래퍼의 닫는 </div> 가 붙어 있어, 그만큼 떼어 탭 묶음 밖에 둔다
    (안에 두면 여는 태그 없이 닫혀 레이아웃이 무너진다).

    탭 순서는 그 탭의 첫 절이 문서에 나오는 순서다. 같은 탭의 절은 떨어져 있어도 한 탭에 모이고,
    탭 안에서는 groups 에 적은 순서(장기 전망 → 영업이익)로 놓인다 — 원문 순서가 뒤바뀐 옛 발행본에서도
    탭 안 번호가 1·2 순서로 보이게 하려는 것이다. 기본 절이 탭 절 뒤에 나오면 첫 탭으로 끌어올려진다.
    """
    from html import escape
    html_text = _prepare_report_layout(html_text)
    parts = list(_H3.finditer(html_text))
    if len(parts) < 2:
        return html_text
    starts = _section_starts(html_text, parts)
    # Closing update markers belong to the previous block, not the next heading.
    for i in range(1, len(parts)):
        gap = html_text[starts[i]:parts[i].start()]
        ends = list(re.finditer(r'<!--(?:LEDGER_SECTION_END|REVIEW_SECTION_END)-->', gap))
        if ends:
            starts[i] += ends[-1].end()
    head, tail, basic, tabbed = html_text[:starts[0]], "", [], {}
    for index, match in enumerate(parts):
        end = starts[index + 1] if index + 1 < len(parts) else len(html_text)
        chunk = html_text[starts[index]:end]
        if index + 1 == len(parts):
            surplus = len(re.findall(r'</div>', chunk)) - len(re.findall(r'<div\b', chunk))
            for _ in range(max(0, surplus)):
                position = chunk.rindex('</div>')
                tail = chunk[position:] + tail
                chunk = chunk[:position]
        label, rank = _tab_for(_section_title(match.group(2)), groups)
        if label is None:
            basic.append(chunk)
        else:
            tabbed.setdefault(label, []).append((rank, index, chunk))
    if not tabbed or not basic:
        return html_text
    tabbed = {label: tabbed[label] for label, _ in groups if label in tabbed}
    names = [default_label] + list(tabbed)
    bar = ('<nav class="rtabs" aria-label="보고서 메뉴">'
           + "".join(f'<a href="#rtab-{i}" aria-selected="{"true" if i == 0 else "false"}">'
                     f'{escape(name)}</a>' for i, name in enumerate(names))
           + '</nav>')
    panels = (f'<section class="rtab-panel" id="rtab-0">{"".join(basic)}</section>'
              + "".join(f'<section class="rtab-panel" id="rtab-{i}">'
                        f'{"".join(chunk for _, _, chunk in sorted(chunks))}</section>'
                        for i, chunks in enumerate(tabbed.values(), start=1)))
    out = (head + '<div id="rtabs-root">' + _TAB_STYLE + bar + panels + _TAB_SCRIPT + '</div>'
           + tail)
    # All headings restart within their own tab, including dynamically inserted fragments.
    for panel in reversed(panels_for_numbering(out)):
        out = out[:panel['inner_start']] + number_headings(panel['inner']) + out[panel['inner_end']:]
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


# 조각(장기 전망·영업이익 추정)은 월 1회 실행이 만들어 저장소에 남는다. 보고서 절 번호를 바꿔도
# 옛 조각에는 만들 때의 번호(7·8, 그다음 3·4)가 박혀 있어 다음 월간 실행 전까지 번호가 어긋난다.
# 그래서 조각을 끼울 때 제목의 번호만 지금 체계(장기 전망 탭의 1·2절)로 바꾼다. 내용은 건드리지 않는다.
FRAGMENT_NUMBERS = (("장기 전망", "1."), ("이번 분기 영업이익", "2."), ("지난 전망은 맞았나", "3."))


def renumber_fragment(html_text):
    """조각 제목의 절 번호를 현재 체계로 맞춘다(h3 안에서만, 옛 번호가 무엇이었든)."""
    if not html_text:
        return html_text

    def fix(match):
        head = match.group(0)
        for key, number in FRAGMENT_NUMBERS:
            new, count = re.subn(r'\d+\.\s*(?=' + re.escape(key) + ')', number + ' ', head, count=1)
            if count:
                return new
        return head

    return re.sub(r"<h3\b[^>]*>.*?</h3>", fix, html_text, flags=re.S)


def colab_notice_html(prefix, show_ok=False):
    """Colab(한국에서 실행)으로 갱신할 자료가 늦으면 띄우는 공지(2026-09-28). 관리자 페이지(docs/admin)에만 둔다.

    tools/colab_freshness.py 가 발표 일정으로 판정해 docs/colab_status.json 에 남긴다. 페이지는 그 파일을 읽어
    needed 일 때 공지를 보인다 — 페이지를 다시 만들지 않아도 공지가 켜지고 꺼진다. show_ok 면 필요 없을 때도
    '정상'과 자료별 점검표를 보인다. 파일을 못 읽으면 아무것도 보이지 않는다. prefix: docs/ 까지의 상대 경로.
    """
    if show_ok:
        return ('<div id="colab-notice" style="margin:0 0 18px;padding:12px 16px;border-radius:6px;font-size:13px;'
                'line-height:1.6;background:#f5f6f8;border:1px solid #e1e4e8;color:#4a4f55">Colab 갱신 점검을 불러오는 중…</div>'
                '<script>(function(){var el=document.getElementById("colab-notice");if(!el||!window.fetch)return;'
                f'fetch("{prefix}colab_status.json",{{cache:"no-store"}}).then(function(r){{return r.ok?r.json():null;}})'
                '.then(function(s){if(!s){el.textContent="Colab 갱신 점검 결과(colab_status.json)를 읽지 못했습니다.";return;}'
                'var esc=function(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c];});};'
                'var rows=(s.checked||[]).map(function(i){return "<tr><td>"+(i.late?"⚠️ 늦음":"✅ 정상")+"</td><td>"+esc(i.label)+'
                '"</td><td>"+esc(i.have)+"</td><td>"+esc(i.expected)+"</td></tr>";}).join("");'
                'var head=s.needed?"<b>Colab 업데이트가 필요합니다.</b> 한국 정부 자료가 자동 실행(해외 서버)에서 막혀 발표 일정보다 늦었습니다. "'
                '+esc(s.how)+" <a href=\\""+esc(s.colab_url)+"\\" style=\\"color:#1a5490\\">Colab 에서 노트북 열기</a>"'
                ':"<b>Colab 업데이트 필요 없음.</b> 한국 정부 자료 보관본이 발표 일정에 맞게 들어와 있습니다.";'
                'el.style.background=s.needed?"#fff4e5":"#eef7ee";el.style.borderColor=s.needed?"#f0c58a":"#b9ddb9";'
                'el.style.color=s.needed?"#7a4b00":"#1e5b2a";'
                'el.innerHTML=head+" · 점검 "+esc(s.as_of)+"<table style=\\"margin-top:8px;border-collapse:collapse;font-size:13px\\">'
                '<tr><th style=\\"text-align:left;padding-right:12px\\">상태</th><th style=\\"text-align:left;padding-right:12px\\">자료</th>'
                '<th style=\\"text-align:left;padding-right:12px\\">보관본</th><th style=\\"text-align:left\\">있어야 할 것</th></tr>"+rows+"</table>";'
                '}).catch(function(){el.textContent="Colab 갱신 점검 결과를 읽지 못했습니다.";});})();</script>')
    return ('<div id="colab-notice" hidden style="max-width:980px;margin:0 auto 18px;padding:12px 16px;'
            'background:#fff4e5;border:1px solid #f0c58a;border-radius:6px;font-family:-apple-system,\'Malgun Gothic\','
            'sans-serif;font-size:13px;line-height:1.6;color:#7a4b00"></div>'
            '<script>(function(){var el=document.getElementById("colab-notice");if(!el||!window.fetch)return;'
            f'fetch("{prefix}colab_status.json",{{cache:"no-store"}}).then(function(r){{return r.ok?r.json():null;}})'
            '.then(function(s){if(!s||!s.needed||!s.late||!s.late.length)return;'
            'var esc=function(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c];});};'
            'var items=s.late.map(function(i){return "<li>"+esc(i.label)+" — 보관본 "+esc(i.have)+", 있어야 할 것 "+esc(i.expected)+"</li>";}).join("");'
            'el.innerHTML="<b>Colab 업데이트가 필요합니다.</b> 한국 정부 자료가 자동 실행(해외 서버)에서 막혀 아래 자료가 '
            '발표 일정보다 늦었습니다.<ul style=\\"margin:6px 0 6px 18px;padding:0\\">"+items+"</ul>"+esc(s.how)+'
            '" <a href=\\""+esc(s.colab_url)+"\\" style=\\"color:#1a5490\\">Colab 에서 노트북 열기</a> · 확인 "+esc(s.as_of);'
            'el.hidden=false;}).catch(function(){});})();</script>')


BACK_LINK_HTML = ('<a href="../" style="display:inline-block;font-size:12px;color:#1a5490;text-decoration:none;'
                  'border:1px solid #cedff0;border-radius:5px;padding:5px 11px;background:#f0f6fc">← 보고서 목록</a>')


def report_top_bar_html(endpoint, page, name, back=True):
    """종목 보고서 맨 위 줄: 왼쪽 '보고서 목록', 오른쪽 '구독' 버튼(2026-09-28 요청).

    구독을 누르면 이메일 칸이 열리고, 신청은 조회수 카운터 Worker(counter/worker.js)의 POST /subscribe 로 가서
    D1 subscribers 표에 남는다. 목록은 관리자 페이지 '구독자' 메뉴에서 본다. 개인정보 보호법에 따라 수집 항목·목적·
    보관 기간을 칸 아래에 적고 동의를 받아야 보낸다. endpoint 가 비면 구독 버튼 없이 목록 링크만 둔다.
    back=False 는 메인 페이지(docs/index.html)용 — 목록 자체라 '보고서 목록' 링크 없이 구독 버튼만 오른쪽에 둔다.
    """
    back_html = BACK_LINK_HTML if back else "<span></span>"
    if not endpoint:
        return f'<div style="margin-bottom:10px">{BACK_LINK_HTML}</div>'
    e = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    btn = ('border-radius:5px;padding:7px 14px;font-size:13px;font-family:inherit;cursor:pointer;min-height:36px')
    return (
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">'
        f'{back_html}'
        '<button type="button" id="sub-open" aria-expanded="false" aria-controls="sub-box" '
        f'style="{btn};border:1px solid #1a5490;background:#1a5490;color:#fff;font-weight:600">✉ 구독</button></div>'
        '<div id="sub-box" hidden style="border:1px solid #cedff0;background:#f7fafd;border-radius:6px;padding:12px 14px;'
        'margin-bottom:14px;font-size:13px;line-height:1.6;position:relative">'
        '<form id="sub-form" novalidate>'
        f'<div style="font-weight:600;margin-bottom:6px">{e(name)} 보고서 구독</div>'
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
        '<label for="sub-email" style="position:absolute;left:-9999px">이메일</label>'
        '<input id="sub-email" type="email" required maxlength="254" autocomplete="email" inputmode="email" '
        'placeholder="name@example.com" style="flex:1;min-width:200px;padding:8px 10px;font-size:14px;'
        'border:1px solid #ccd2d9;border-radius:5px;font-family:inherit">'
        f'<button type="submit" id="sub-send" style="{btn};border:1px solid #1a5490;background:#1a5490;color:#fff;'
        'font-weight:600">구독 신청</button>'
        f'<button type="button" id="sub-cancel" style="{btn};border:1px solid #ccd2d9;background:#fff;color:#3a4652">'
        '구독 해지</button></div>'
        # 사람에게 보이지 않는 칸. 봇이 폼을 통째로 채우면 값이 들어와 Worker 가 저장하지 않는다.
        '<input id="sub-web" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" '
        'style="position:absolute;left:-9999px;width:1px;height:1px">'
        '<label style="display:flex;gap:6px;align-items:flex-start;margin-top:8px;font-size:12px;color:#3a4652">'
        '<input id="sub-agree" type="checkbox" style="margin-top:3px"> '
        '<span>개인정보 수집·이용에 동의합니다(필수).</span></label>'
        '<div style="font-size:11px;color:#6b7178;margin-top:4px">'
        f'수집 항목: 이메일 주소 · 목적: {e(name)} 보고서 소식 안내 · 보관 기간: 구독 해지 시까지(해지하면 바로 지웁니다). '
        '동의하지 않으면 구독할 수 없습니다. 해지는 같은 주소를 넣고 "구독 해지"를 누르면 됩니다.</div>'
        '<div id="sub-msg" role="status" aria-live="polite" style="margin-top:6px;font-size:12px"></div>'
        '</form></div>'
        '<script>(function(){'
        f'var E="{e(endpoint)}",P="{e(page)}";'
        'var $=function(i){return document.getElementById(i);};'
        'var box=$("sub-box"),open=$("sub-open"),msg=$("sub-msg");if(!box||!open||!window.fetch)return;'
        'open.addEventListener("click",function(){box.hidden=!box.hidden;open.setAttribute("aria-expanded",String(!box.hidden));'
        'if(!box.hidden)$("sub-email").focus();});'
        'function say(t,ok){msg.textContent=t;msg.style.color=ok?"#1e6b34":"#a8322a";}'
        'function send(path,done){var email=$("sub-email").value.trim();'
        'if(!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)){say("이메일 주소를 확인해 주세요.");$("sub-email").focus();return;}'
        '$("sub-send").disabled=$("sub-cancel").disabled=true;say("보내는 중…",true);'
        'fetch(E+path,{method:"POST",headers:{"Content-Type":"application/json"},'
        'body:JSON.stringify({email:email,page:P,website:$("sub-web").value})})'
        '.then(function(r){if(r.status===429)throw new Error("오늘은 더 보낼 수 없습니다. 내일 다시 시도해 주세요.");'
        'if(r.status===400)throw new Error("이메일 주소를 확인해 주세요.");'
        'if(!r.ok)throw new Error("신청을 보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.");say(done,true);$("sub-email").value="";})'
        '.catch(function(err){say(err&&err.message?err.message:"신청을 보내지 못했습니다.");})'
        '.finally(function(){$("sub-send").disabled=$("sub-cancel").disabled=false;});}'
        '$("sub-form").addEventListener("submit",function(ev){ev.preventDefault();'
        'if(!$("sub-agree").checked){say("개인정보 수집·이용에 동의해야 구독할 수 있습니다.");return;}'
        'send("/subscribe","구독 신청이 접수되었습니다.");});'
        '$("sub-cancel").addEventListener("click",function(){send("/unsubscribe","구독 해지 요청이 처리되었습니다.");});'
        '})();</script>')


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


def latest_weekly_brief(repo, branch, today=None):
    """가장 최근 주간 브리핑 마크다운. (본문, 파일명) 또는 (None, None).

    브리핑은 reports/YYYY-MM-DD-memory-semiconductor-brief.md 로 주 단위로 쌓인다.
    목록 API 를 쓰면 인증이 필요할 수 있어, 최근 날짜를 거슬러 올라가며 직접 찾는다.
    """
    import urllib.request
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Seoul").date())
    local = Path.cwd() / "reports"
    if local.exists():
        files = sorted(local.glob("*-memory-semiconductor-brief.md"))
        if files:
            return files[-1].read_text(encoding="utf-8"), files[-1].name
    for back in range(0, 21):                      # 3주 전까지 찾는다
        day = (today - pd.Timedelta(days=back)).date().isoformat()
        name = f"{day}-memory-semiconductor-brief.md"
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/reports/{name}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                return response.read().decode("utf-8"), name
        except Exception:
            continue
    return None, None


def markdown_to_html(text):
    """브리핑 마크다운을 보고서에 넣을 HTML 로. 표·링크·목록·강조만 다룬다.

    외부 라이브러리를 들이지 않는다(폐쇄망 실행과 의존성 최소화). 브리핑이 쓰는 문법이
    정해져 있어 그 범위만 처리하면 충분하다.
    """
    from html import escape

    def inline(line):
        out = escape(line)
        out = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                     r'<a href="\2" style="color:#1a5490" target="_blank" rel="noopener">\1</a>', out)
        out = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', out)
        out = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', out)
        out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
        return out

    html_parts, table, in_list = [], [], False

    def flush_table():
        if not table:
            return
        header, rows = table[0], [r for r in table[1:] if not set(r) <= set(["", "-", ":"])
                                  and not all(re.fullmatch(r':?-{2,}:?', c.strip() or '-') for c in r)]
        body = "".join(
            "<tr>" + "".join(f'<td style="padding:6px 9px;border-top:1px solid #eee">{inline(c)}</td>'
                             for c in row) + "</tr>" for row in rows)
        html_parts.append(
            '<div style="overflow-x:auto;margin:10px 0"><table style="width:100%;min-width:480px;'
            'border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
            '<tr style="background:#fafafa;font-size:11px;color:#6b7178">'
            + "".join(f'<th style="padding:7px 9px;text-align:left">{inline(c)}</th>' for c in header)
            + "</tr>" + body + "</table></div>")
        table.clear()

    def flush_list():
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            flush_list()
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush_table()
        if not line.strip():
            flush_list()
            continue
        heading = re.match(r'^(#{1,4})\s+(.*)$', line)
        if heading:
            flush_list()
            level = len(heading.group(1))
            if level == 1:
                continue                            # 제목은 절 제목이 대신한다
            size = {2: 15, 3: 14, 4: 13}.get(level, 13)
            margin = "18px 0 6px" if level == 2 else "14px 0 5px"
            html_parts.append(f'<h4 style="font-size:{size}px;margin:{margin}">'
                              f'{inline(heading.group(2))}</h4>')
            continue
        item = re.match(r'^[-*]\s+(.*)$', line)
        if item:
            if not in_list:
                html_parts.append('<ul style="margin:6px 0;padding-left:18px;font-size:13px;'
                                  'line-height:1.7">')
                in_list = True
            html_parts.append(f'<li style="margin:4px 0">{inline(item.group(1))}</li>')
            continue
        flush_list()
        html_parts.append(f'<div style="font-size:13px;line-height:1.75;margin:7px 0">'
                          f'{inline(line)}</div>')
    flush_table()
    flush_list()
    return "".join(html_parts)


def weekly_brief_html(repo, branch, today=None):
    """주간 반도체 뉴스 절. 브리핑이 없으면 빈 문자열."""
    from html import escape
    text, name = latest_weekly_brief(repo, branch, today)
    if not text:
        return ""
    date = (re.match(r'(\d{4}-\d{2}-\d{2})', name or "") or [None, ""])[1] if name else ""
    link = (f'https://github.com/{repo}/blob/{branch}/reports/{escape(name)}') if name else ""
    return ('<h3 style="font-size:15px;margin:24px 0 9px;padding-bottom:6px;border-bottom:1px solid #ddd">'
            '주간 반도체 뉴스 <span style="font-weight:400;color:#8a9199;font-size:12px">'
            f'&nbsp;{escape(date)} 기준 · 예측에 쓰지 않는 참고 자료입니다</span></h3>'
            + markdown_to_html(text)
            + (f'<div style="font-size:11px;color:#8a9199;margin-top:10px">원문: '
               f'<a href="{link}" style="color:#1a5490" target="_blank" rel="noopener">'
               f'{escape(name)}</a></div>' if link else ""))


def load_summary_data(name, target, repo, branch):
    # HTML에서 숫자를 추측하지 않고, 상세 보고서와 같은 구조화된 계산 결과를 읽는다.
    try:
        payload = json.loads(load_fragment(name, target, repo, branch) or "{}")
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


# Report layout uses source offsets so moved blocks preserve update markers and HTML.
def _layout_elements(text, predicate):
    from html.parser import HTMLParser
    class Scan(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack, self.found = [], []
            self.lines = [0]
            for line in text.splitlines(keepends=True):
                self.lines.append(self.lines[-1] + len(line))
        def pos(self):
            line, col = self.getpos()
            return self.lines[line - 1] + col
        def handle_starttag(self, tag, attrs):
            if tag not in ('div', 'section', 'details', 'nav'):
                return
            self.stack.append((tag, dict(attrs), self.pos(), self.pos()+len(self.get_starttag_text())))
        def handle_endtag(self, tag):
            if tag not in ('div', 'section', 'details', 'nav'):
                return
            for i in range(len(self.stack)-1, -1, -1):
                name, attrs, start, inner_start = self.stack[i]
                if name == tag:
                    del self.stack[i:]
                    end = text.find('>', self.pos())+1
                    if predicate(name, attrs):
                        self.found.append(dict(start=start, end=end, inner=text[inner_start:self.pos()],
                                               attrs=attrs, inner_start=inner_start, inner_end=self.pos()))
                    break
    scanner=Scan(); scanner.feed(text)
    return sorted(scanner.found, key=lambda x:x['start'])


def panels(text):
    return _layout_elements(text, lambda tag,a:'rtab-panel' in a.get('class','').split())


def number_headings(text):
    major, minor = 0, 0
    def change(m):
        nonlocal major, minor
        if m.group(2) == '3':
            major += 1; minor = 0
            prefix = f'{major}. '
        else:
            minor += 1
            prefix = f'{major}.{minor} ' if major else f'{minor}. '
        inner = re.sub(r'^\s*(?:\d+(?:-\d+)?\.\s+|\d+\.\d+\s+)', '', m.group(3))
        return m.group(1)+prefix+inner+m.group(4)
    return re.sub(r'(<h([34])\b[^>]*>)(.*?)(</h[34]>)',change,text,flags=re.S)


def _prepare_report_layout(text):
    # Extract the mutable scorecard from the easy summary, retaining its markers.
    score = re.search(r'<!--SCORECARD_START-->.*?<!--SCORECARD_END-->',text,re.S)
    if score and 'id="performance-scorecard"' not in text:
        block = score.group()
        text = text[:score.start()] + '<p><a href="#performance-scorecard">지난 예측 결과·누적 성적 보기</a></p>' + text[score.end():]
        # Insert ahead of a top-level h3, never inside the easy-summary wrapper.
        starts = _section_starts(text,list(_H3.finditer(text)))
        at = starts[0] if starts else 0
        text = text[:at] + '<section id="performance-scorecard"><h3>실제 발행 후 누적 성적</h3>'+block+'</section>'+text[at:]
    # Move the training metadata cards out of the current-decision section.
    if 'id="performance-training"' not in text:
        cards = [r for r in _layout_elements(text,lambda tag,a:tag=='div')
                 if '학습 데이터' in r['inner'] and '워크포워드 폴드' in r['inner'] and '<h3' not in r['inner']]
        if cards:
            item=min(cards,key=lambda r:r['end']-r['start'])
            content=text[item['start']:item['end']]
            text=text[:item['start']]+text[item['end']:]
            at=_section_starts(text,list(_H3.finditer(text)))[0]
            text=text[:at]+'<section id="performance-training"><h3>학습·검증 설정</h3><details><summary>상세 설정 보기</summary>'+content+'</details></section>'+text[at:]
    return text


def refresh_layout(text):
    """Reorganize an already published report without changing predictions or scores."""
    roots=_layout_elements(text,lambda tag,a:a.get('id')=='rtabs-root')
    if not roots:
        return tabify_sections(text)
    root=roots[0]
    items=[p for p in panels(text) if root['start']<p['start']<root['end']]
    flat='<div>'+''.join(p['inner'] for p in items)+'</div>'
    rebuilt=tabify_sections(flat)
    return text[:root['start']]+rebuilt[5:-6]+text[root['end']:]


def panels_for_numbering(text):
    return panels(text)
