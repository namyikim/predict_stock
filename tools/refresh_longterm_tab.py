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
from report_html import fragment_sources_html, renumber_fragment


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

    def plain(latest):
        return replace_sources(replace_panel(latest, content), sources)

    def apply(latest):
        # 탭 제목 아래 생성 시각(2026-09-30). 내용이 바뀐 때만 찍는다 — 시각만 달라진 페이지를 올리면 커밋만 는다.
        out = plain(latest)
        panel = longterm_panel_id(out)
        return stamp_panel(out, panel, kst_stamp()) if panel else out

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
    outlook_html = outlook_ledger.render(ledger)
    (lt_dir / 'outlook.html').write_text(outlook_html, encoding='utf-8')
    # 순서: 요약 → 1. 이번 분기 영업이익 → 2. 장기 전망 → 3. 지난 전망(2026-09-29). 노트북의 조립 순서와 같아야 한다.
    content = (summary + renumber_fragment((er_dir / 'earnings.html').read_text())
               + renumber_fragment((lt_dir / 'longterm.html').read_text()) + outlook_html)
    (lt_dir / 'longterm_tab.html').write_text(content, encoding='utf-8')
    if args.publish:
        with github_pages.batch(f'report: {args.target} daily export refresh'):
            ours = ledger.copy()
            # 원장은 처음 읽은 sha 로만 올리고, 그 사이 바뀌었으면 최신 원장에 이 실행의 기록·채점을 합친다.
            github_pages.publish(ledger_path, outlook_ledger.to_csv(ledger), token,
                                 f'outlook: {args.target} 전망 기록 {added} · 채점 {scored}', expected_sha=ledger_sha,
                                 merge=lambda latest: outlook_ledger.to_csv(outlook_ledger.merge_ledgers(latest, ours)))
            github_pages.publish(f'docs/{args.target}/outlook.html', outlook_html, token,
                                 f'outlook: {args.target} 지난 전망 채점')
            print(publish_tab(args.target, content, fragment_sources_html(lt, er), token))


if __name__ == '__main__':
    main()
