"""추가 수집 없이 이미 게시된 공개 메뉴를 이메일용 짧은 요약으로 읽는다."""
import re
from html.parser import HTMLParser
from urllib.error import URLError

SITE_REPORTS = {
    'metals':'금·은 예측', 'china':'중국 주식·5개년 계획', 'macro':'거시 경제',
    'ai_news':'AI 뉴스', 'robot_news':'로봇 뉴스', 'trends':'인기 급상승 검색어', 'interest':'장기 관심도',
}
BASE_URL='https://namyikim.github.io/predict_stock/'


class Node:
    def __init__(self,tag='',attrs=(),parent=None):
        self.tag=tag
        self.attrs=dict(attrs)
        self.parent=parent
        self.children=[]

    def nodes(self):
        for child in self.children:
            if isinstance(child,Node):
                yield child
                yield from child.nodes()

    def text(self):
        # SVG 축 눈금·스크립트·구독 폼·탐색 메뉴는 요약 본문이 아니다.
        if self.tag in ('script','style','svg','nav','form','head','button','iframe'):
            return ''
        return ' '.join(' '.join(c.text() if isinstance(c,Node) else c for c in self.children).split())


class PublishedHTML(HTMLParser):
    def __init__(self,page):
        super().__init__(convert_charrefs=True)
        self.root=Node()
        self.current=self.root
        self.feed(page)

    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs,self.current)
        self.current.children.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.current=node

    def handle_endtag(self,tag):
        node=self.current
        while node.parent is not None:
            if node.tag==tag:
                self.current=node.parent
                return
            node=node.parent

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        self.handle_endtag(tag)

    def handle_data(self,data):
        self.current.children.append(data)


def short(text,limit=700):
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'


def build_site_report(key,page):
    root=PublishedHTML(page).root
    nodes=list(root.nodes())
    texts=[n.text() for n in nodes if n.tag in ('p','div','h1','h2','h3','h4')]
    # 문서 머리의 기준·생성 시각을 사용한다. 시세 기준일과 발행일을 같은 것으로 만들지 않는다.
    dated=[t for t in texts if len(t)<350 and re.search(r'20\d\d-\d\d-\d\d \d\d:\d\d KST',t)]
    as_of=short(min(dated,key=len).split(' · 코드 커밋')[0],260) if dated else '게시 기준 시각 미확인'
    market=next((short(t,180) for t in texts if len(t)<300 and '시세 기준일' in t),'')
    if market and market not in as_of:
        as_of=short(market+' / '+as_of,260)
    lines=[]
    if key=='metals':
        metal=''
        for n in nodes:
            t=n.text()
            if n.tag=='h3' and '단기 예측' in t:
                metal='은' if re.search(r'\b은\s*·',t) else '금'
            elif metal and n.tag in ('div','p') and (t.startswith('기준 봉 ') or t.startswith('방향 판정:')) and len(t)<1000:
                line=short(metal+': '+t)
                if line not in lines:lines.append(line)
    elif key=='china':
        for marker in ('정책 종목이 지수를 이긴 비율','숫자만 보면'):
            candidates=[t for t in texts if marker in t and (marker!='정책 종목이 지수를 이긴 비율' or '초과수익 중앙값' in t)]
            if candidates:lines.append(short(min(candidates,key=len)))
    elif key=='macro':
        candidates=[t for t in texts if '한 문단 요약' in t and '지금은' in t]
        if candidates:lines.append(short(min(candidates,key=len).replace('한 문단 요약 자료가 바뀌면 문장도 바뀝니다','').strip()))
        states=[t for t in texts if '판정에 쓴 지표' in t]
        if states:lines.append(short(min(states,key=len)))
    else:
        for row in (n for n in nodes if n.tag=='tr'):
            cells=[n for n in row.children if isinstance(n,Node) and n.tag=='td']
            if len(cells)<2:continue
            blocks=[n for n in cells[1].children if isinstance(n,Node) and n.tag=='div']
            if not blocks:continue
            label=blocks[0].text()
            detail=blocks[1].text() if len(blocks)>1 else ''
            headline=next((n.text() for n in cells[1].nodes() if n.tag=='li'),'') if key in ('ai_news','robot_news') else ''
            lines.append(short(' · '.join(x for x in (label,detail,headline) if x)))
            if len(lines)==3:break
        if key in ('ai_news','robot_news'):
            lines.append('헤드라인 언급 순위이며 검색량 순위나 기사 사실 검증 결과가 아닙니다.')
        elif key=='trends':
            lines.append('조회 시점의 최근 급상승 검색어이며 하루 전체 순위가 아닙니다. 검색량은 근사치입니다.')
        elif key=='interest':
            lines.append('문서 조회수는 관심도의 대리지표이며 구글 검색 지수는 0~100의 상대값입니다.')
    if not lines or (key in ('ai_news','robot_news','trends','interest') and len(lines)==1):
        lines=['게시된 요약 내용을 확인하지 못했습니다. 전체 보고서에서 확인하세요.']
    return dict(key=key,title=SITE_REPORTS[key],as_of=as_of,lines=lines[:5],url=BASE_URL+key+'/')


def read_site_reports(read):
    reports=[]
    for key in SITE_REPORTS:
        try:
            page=read(f'docs/{key}/index.html')
        except (FileNotFoundError,URLError,TimeoutError):
            page=''
        reports.append(build_site_report(key,page))
    return reports
