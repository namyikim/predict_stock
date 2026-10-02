"""일일 통계 갱신 후 기존 종합 보고서의 장기 전망 탭만 교체한다."""
import argparse
import base64
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import github_pages
import outlook_ledger
from forecast_utils import kst_stamp, longterm_easy_summary_html, stamp_panel, summary_level_odds
from report_html import (fold_detail_sections, fragment_sources_html, number_headings, panel_tab_label, panels,
                         renumber_fragment, sections_by_tab)


def replace_panel(page, content):
    """중첩 section을 고려해 장기 탭 내부만 교체. 원장/일일 예측은 그대로 보존."""
    class Panels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.ranges = [], []
            self.offsets = [0]
            for line in page.splitlines(keepends=True):
                self.offsets.append(self.offsets[-1] + len(line))

        def position(self):
            line, col = self.getpos()
            return self.offsets[line - 1] + col

        def handle_starttag(self, tag, attrs):
            if tag == 'section':
                panel = 'rtab-panel' in dict(attrs).get('class', '').split()
                self.stack.append((panel, self.position() + len(self.get_starttag_text())))

        def handle_endtag(self, tag):
            if tag == 'section' and self.stack:
                panel, start = self.stack.pop()
                if panel:
                    self.ranges.append((start, self.position()))

    parser = Panels()
    parser.feed(page)
    matches = [(a, b) for a, b in parser.ranges if 'id="longterm-summary"' in page[a:b]]
    if len(matches) != 1:
        raise ValueError('장기 전망 탭을 하나로 식별하지 못해 기존 보고서를 보존합니다.')
    a, b = matches[0]
    return page[:a] + content + page[b:]


def replace_tab_sections(page, content):
    """새 내용을 절별로 나눠 맞는 메뉴(탭)에 넣는다. (바뀐 페이지, 내용을 넣은 탭 id 목록).

    장기 전망 내용은 '장기 전망' 메뉴 하나에 들어간다(2026-10-02: 둘·셋으로 나눠 봤다가 같은 날 되돌렸다).
    기준은 요약(id="longterm-summary")이 든 탭이다. 절을 TAB_GROUPS 로 나눠 제 메뉴의 탭에 넣고, 페이지에 제 탭이
    없는 절은 기준 탭에 함께 넣는다. 나뉘어 있던 때 발행된 페이지에는 같은 메뉴에 속한 탭이 더 남아 있는데,
    거기에는 낡은 사본 대신 합쳤다는 안내만 남긴다 — 내용이 빠지거나 두 번 들어가지 않는다.
    기준 탭을 하나로 찾지 못하면 replace_panel 과 같이 ValueError 로 멈춘다(기존 보고서를 보존).
    """
    found = [p for p in panels(page) if 'id="longterm-summary"' in p['inner']]
    if len(found) != 1:
        return replace_panel(page, content), []          # 옛 방식(중첩 section 을 직접 센다). 못 찾으면 ValueError
    anchor = found[0]
    anchor_label = panel_tab_label(anchor['inner'])
    by_label, leftovers = {}, []
    for panel in panels(page):
        label = panel_tab_label(panel['inner'])
        if label and panel['start'] != anchor['start']:      # 객체가 아니라 위치로 견준다(목록을 새로 만들어도 같게)
            if label == anchor_label or label in by_label:
                leftovers.append((label, panel))      # 같은 메뉴에 속한 탭이 또 있다 — 옛 구조에서 따로 있던 탭이다
            else:
                by_label[label] = panel
    placed, anchor_html = [], ""
    for label, chunk in sections_by_tab(content):
        target = by_label.get(label)
        if target is None:
            anchor_html += chunk                          # 요약·영업이익, 그리고 제 탭이 없는 절
        else:
            placed.append((target, number_headings(chunk)))
    # 메뉴가 나뉜 페이지에서는 기준 탭도 그 탭 안에서 1부터 번호를 맞춘다 — 일일 보고서가 탭을 나눌 때와 같은 모양이라야
    # 바뀐 것이 없을 때 '바뀜'으로 보이지 않는다. 탭 하나짜리 옛 페이지는 예전처럼 번호를 건드리지 않는다.
    placed.append((anchor, number_headings(anchor_html) if len(placed) else anchor_html))
    # 메뉴를 합친 뒤에도 옛 페이지에는 합쳐지기 전의 탭이 남아 있다(2026-10-02: 나눴던 메뉴를 하나로 되돌렸다).
    # 새 내용은 기준 탭에 들어갔으므로 옛 탭에는 낡은 사본 대신 합쳤다는 안내만 남긴다 — 일일 보고서가
    # 페이지를 다시 만들면 그 탭은 사라진다.
    labels_in_content = {label for label, _ in sections_by_tab(content)}
    for label, panel in leftovers:
        if label in labels_in_content:
            heading = re.search(r'<h3\b[^>]*>.*?</h3>', panel['inner'], re.S)
            placed.append((panel, (heading.group(0) if heading else '')
                           + f'<div style="font-size:13px;color:#6b7178">이 내용은 ‘{label}’ 메뉴로 합쳤습니다. 보고서가 다시 만들어지면 이 메뉴는 사라집니다.</div>'))
    out = page
    for panel, html_text in sorted(placed, key=lambda item: -item[0]['inner_start']):
        out = out[:panel['inner_start']] + html_text + out[panel['inner_end']:]
    return out, [panel['attrs'].get('id') for panel, _ in placed if panel['attrs'].get('id')]


def replace_sources(page, source_html):
    pattern = r'<table\b[^>]*>(?:(?!<table\b).)*?</table>'
    new = re.search(pattern, source_html, flags=re.S)
    if not new:
        return page
    return re.sub(pattern, lambda m: new.group() if '장기 전망·영업이익 추정 자료' in m.group() else m.group(),
                  page, flags=re.S)


def longterm_panel_id(page):
    """장기 전망 요약이 든 탭의 id(없으면 None)."""
    at = page.find('id="longterm-summary"')
    panels = list(re.finditer(r'<section class="rtab-panel" id="([\w-]+)">', page[:at])) if at >= 0 else []
    return panels[-1].group(1) if panels else None


def without_stamps(page):
    """생성 시각 줄을 뺀 페이지 — 내용이 바뀌었는지 볼 때 시각 차이는 무시한다."""
    return re.sub(r'<div class="gen-stamp"[^>]*>.*?</div>', '', page, flags=re.S)


def publish_tab(target, content, sources, token, attempts=4):
    """장기 전망 탭 부분만 바꿔 올린다. 그 사이 페이지가 바뀌었으면 최신 페이지에 다시 적용한다 — 다른 실행의
    일일 예측을 덮어쓰지 않는다. 공용 publish(expected_sha, merge) 를 써서 발행 묶음에도 들어간다(2026-09-23).
    돌려주는 값은 올린 파일의 blob sha(묶음 안이면 '대기')이거나, 바뀐 것이 없으면 'unchanged'."""
    path = f'docs/{target}/index.html'
    page, sha = github_pages.fetch_with_sha(path, token)
    if page is None:
        raise RuntimeError(f'{path} 이 없습니다 — 일일 보고서가 먼저 발행돼야 탭을 바꿀 수 있습니다')

    def replaced(latest):
        # 검증·방법 소제목은 접어서 넣는다(2026-10-02) — 일일 보고서가 탭을 만들 때와 같은 모양이어야 한다.
        body = content() if callable(content) else content
        out, touched = replace_tab_sections(latest, fold_detail_sections(body))
        return replace_sources(out, sources), touched

    def plain(latest):
        return replaced(latest)[0]

    def apply(latest):
        # 탭 제목 아래 생성 시각(2026-09-30). 내용이 바뀐 때만 찍는다 — 시각만 달라진 페이지를 올리면 커밋만 는다.
        out, touched = replaced(latest)
        if without_stamps(out) == without_stamps(latest):
            return latest
        # 내용을 넣은 메뉴마다 생성 시각을 찍는다(메뉴가 셋으로 나뉘었다, 2026-10-02).
        stamp = kst_stamp()
        for panel in touched or [longterm_panel_id(out)]:
            if panel:
                out = stamp_panel(out, panel, stamp)
        return out

    if callable(content):
        # 원장 병합 뒤에 만들고, 페이지 충돌 때도 최신 페이지를 잡은 함수로 남긴다.
        return github_pages.publish(path, lambda: apply(page), token,
                                    f'report: {target} daily export refresh', attempts=attempts,
                                    expected_sha=sha, merge=lambda latest: lambda: apply(latest))
    if without_stamps(plain(page)) == without_stamps(page):
        return 'unchanged'
    updated = apply(page)
    return github_pages.publish(path, updated, token, f'report: {target} daily export refresh', attempts=attempts,
                                expected_sha=sha, merge=apply)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['samsung', 'sk_hynix'], required=True)
    parser.add_argument('--earnings', type=Path, required=True)
    parser.add_argument('--longterm', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    lt_dir, er_dir = args.longterm / args.target, args.earnings / args.target
    lt = json.loads((lt_dir / 'longterm.json').read_text())
    er = json.loads((er_dir / 'earnings.json').read_text())
    digest = er.get('exports_snapshot_hash')
    if not digest or lt.get('live_exports', {}).get('snapshot_hash') != digest:
        raise ValueError('실적 예상과 장기 전망의 수출 스냅샷이 달라 갱신을 중단합니다.')
    ticker = '005930_KS' if args.target == 'samsung' else '000660_KS'
    prices = pd.read_csv(lt_dir / 'cache' / f'{ticker}_daily.csv', index_col=0, parse_dates=True)['close']
    levels = (300000, 400000) if args.target == 'samsung' else None
    summary = longterm_easy_summary_html(name=lt['name'], price_date=prices.index[-1], close=prices,
                                        longterm=lt, earnings=er, levels=levels)

    # 전망 원장(2026-09-27): 이번에 화면에 나온 전망을 처음 값만 남기고, 발표된 값으로 채점해 3절에 보인다.
    token = github_pages.token() if args.publish else None
    ledger_path = f'forecast_history/{args.target}/{outlook_ledger.LEDGER_NAME}'
    local = lt_dir / outlook_ledger.LEDGER_NAME
    ledger_sha = None
    if token:
        remote, ledger_sha = github_pages.fetch_with_sha(ledger_path, token)
        ledger = outlook_ledger.read_ledger_text(remote)
    else:
        ledger = outlook_ledger.read_ledger(ROOT / ledger_path if not local.exists() else local)
    odds = summary_level_odds(prices, levels)
    rows = outlook_ledger.collect_forecasts(lt, er, odds, prices.index[-1])
    ledger, added = outlook_ledger.record(ledger, rows)
    ledger, scored = outlook_ledger.score(ledger, outlook_ledger.actuals_from(lt_dir, er_dir, lt, prices))
    print(f'전망 원장: {len(ledger)}행 (이번에 기록 {added}, 채점 {scored})')
    local.write_text(outlook_ledger.to_csv(ledger), encoding='utf-8')

    # 채점 절은 조각(docs/<종목>/outlook.html)으로도 올린다 — 일일 보고서 노트북이 탭을 다시 조립할 때 끼운다.
    outlook_html = outlook_ledger.render(ledger, prices=prices)
    (lt_dir / 'outlook.html').write_text(outlook_html, encoding='utf-8')
    # 순서: 요약 → 1. 이번 분기 영업이익 → 2. 장기 전망 → 3. 지난 전망(2026-09-29). 노트북의 조립 순서와 같아야 한다.
    prefix = (summary + renumber_fragment((er_dir / 'earnings.html').read_text())
              + renumber_fragment((lt_dir / 'longterm.html').read_text()))
    content = prefix + outlook_html
    (lt_dir / 'longterm_tab.html').write_text(content, encoding='utf-8')
    if args.publish:
        with github_pages.batch(f'report: {args.target} daily export refresh'):
            state = outlook_ledger.PendingLedger(ledger)

            def final_outlook():
                result = outlook_ledger.render(state.frame, prices=prices)
                local.write_text(outlook_ledger.to_csv(state.frame), encoding='utf-8')
                (lt_dir / 'outlook.html').write_text(result, encoding='utf-8')
                return result

            def final_content():
                result = prefix + final_outlook()
                (lt_dir / 'longterm_tab.html').write_text(result, encoding='utf-8')
                return result
            # 원장은 처음 읽은 sha 로만 올리고, 그 사이 바뀌었으면 최신 원장에 이 실행의 기록·채점을 합친다.
            github_pages.publish(ledger_path, outlook_ledger.to_csv(ledger), token,
                                 f'outlook: {args.target} 전망 기록 {added} · 채점 {scored}', expected_sha=ledger_sha,
                                 merge=state.merge)
            github_pages.publish(f'docs/{args.target}/outlook.html', final_outlook, token,
                                 f'outlook: {args.target} 지난 전망 채점')
            print(publish_tab(args.target, final_content, fragment_sources_html(lt, er), token))


if __name__ == '__main__':
    main()
