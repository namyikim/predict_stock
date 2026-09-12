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
# 경계는 접기 경계와 같다: 1~4 는 결론(펼침), 5~8 과 참고 정보는 해설·검증(접힘).
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


# 첫 화면에서 접어 둘 절. 1~4절(방향·수급·가격·장기 전망·영업이익)은 결론이라 펼쳐 두고,
# 5~8절과 참고 정보는 해설·검증·데이터라 접는다. 근거는 원하는 사람만 보면 된다.
COLLAPSE_PREFIXES = ("5.", "6.", "7.", "8.", "참고 정보")


def collapse_sections(html_text, prefixes=COLLAPSE_PREFIXES, summary="펼쳐 보기"):
    """지정한 절의 본문을 <details> 로 감싼다. 제목은 그대로 보이고 내용만 접힌다.

    h3 로 절을 나누고 각 절의 본문만 감싸므로 태그 균형이 유지된다. 마지막 절은 바깥 래퍼의
    닫는 태그를 품고 있어 건드리지 않는다(감싸면 </div> 가 details 안에 갇힌다).
    """
    parts = list(_H3.finditer(html_text))
    if len(parts) < 2:
        return html_text
    out, cursor = [], 0
    for index, match in enumerate(parts):
        title = re.sub(r'<span\b.*?</span>', '', match.group(2), flags=re.S)
        title = re.sub(r'<[^>]+>', '', title)
        title = re.sub(r'&nbsp;?', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip(' ·')
        body_start = match.end()
        body_end = parts[index + 1].start() if index + 1 < len(parts) else None
        out.append(html_text[cursor:body_start])
        tail = ""
        if body_end is None:
            # 마지막 절의 본문 끝에는 바깥 래퍼의 닫는 태그가 붙어 있다. 그 부분을 떼어
            # details 밖에 두어야 태그 균형이 유지된다(여는 태그 없이 닫히면 안 된다).
            body = html_text[body_start:]
            opens = len(re.findall(r'<div\b', body))
            closes = len(re.findall(r'</div>', body))
            for _ in range(max(0, closes - opens)):
                position = body.rindex('</div>')
                tail = body[position:] + tail
                body = body[:position]
        else:
            body = html_text[body_start:body_end]
        if any(title.startswith(prefix) for prefix in prefixes):
            out.append('<details style="margin-top:4px"><summary style="font-size:12px;color:#6b7178;'
                       f'cursor:pointer;padding:2px 0">{summary}</summary>{body}</details>{tail}')
        else:
            out.append(body + tail)
        cursor = body_end if body_end is not None else len(html_text)
    out.append(html_text[cursor:])
    return "".join(out)


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
