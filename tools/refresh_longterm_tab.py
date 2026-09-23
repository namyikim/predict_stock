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
from forecast_utils import longterm_easy_summary_html
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


def publish_tab(target, content, sources, token, attempts=4):
    """409 발생 시 최신 페이지에 다시 적용한다. 다른 실행의 일일 예측을 덮어쓰지 않는다."""
    path = f'docs/{target}/index.html'
    for attempt in range(attempts):
        current = github_pages._api(path, token)
        page = base64.b64decode(current['content']).decode('utf-8')
        updated = replace_sources(replace_panel(page, content), sources)
        if page == updated:
            return 'unchanged'
        body = {'message': f'report: {target} daily export refresh', 'branch': github_pages.GITHUB_BRANCH,
                'sha': current['sha'], 'content': base64.b64encode(updated.encode()).decode()}
        try:
            return github_pages._api(path, token, 'PUT', body)['commit']['sha']
        except Exception as exc:
            if getattr(exc, 'code', None) not in (409, 422) or attempt == attempts - 1:
                raise


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
    summary = longterm_easy_summary_html(name=lt['name'], price_date=prices.index[-1], close=prices,
                                        longterm=lt, earnings=er,
                                        levels=(300000, 400000) if args.target == 'samsung' else None)
    content = summary + renumber_fragment((lt_dir / 'longterm.html').read_text()) + renumber_fragment((er_dir / 'earnings.html').read_text())
    (lt_dir / 'longterm_tab.html').write_text(content, encoding='utf-8')
    if args.publish:
        print(publish_tab(args.target, content, fragment_sources_html(lt, er), github_pages.token()))


if __name__ == '__main__':
    main()
