"""보고서 공통 화면. 데이터 재수집 없이 기존 발행본에도 같은 디자인을 적용한다."""
from functools import wraps
from pathlib import Path
import re

START = '<!-- REPORT_UI_START -->'
END = '<!-- REPORT_UI_END -->'
STYLE = """<style id="report-simple-ui">
:root{color-scheme:light;--surface:#fff!important;--surface-2:#f5f8fc!important;--border:#e1e8f2!important;--border-strong:#cbd8ea!important;--text:#182b46!important;--text-2:#465973!important;--text-3:#62748b!important;--accent:#2463d4!important}
html{background:#f2f6fc;scroll-padding-top:90px}
body{margin:0!important;padding:32px 20px 64px!important;zoom:1!important;background:#f2f6fc!important;color:#182b46;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif!important;font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
*,*:before,*:after{box-sizing:border-box}
body>.wrap{max-width:1120px!important;margin:0 auto!important;background:#fff;border:1px solid #e1e8f2;border-radius:24px;padding:32px!important;box-shadow:0 12px 40px #203d6b08;min-width:0}
body>.wrap:has(>a.card){max-width:740px!important}
.embed body{padding:4px!important;background:#fff!important}.embed body>.wrap{border:0;box-shadow:none;padding:12px!important}
body>div[style*="max-width"]{margin-left:auto!important;margin-right:auto!important}
body>div[style*="max-width"]:not(#stale-note){background:#fff;border:1px solid #e1e8f2;border-radius:20px;padding:28px!important;box-shadow:0 12px 40px #203d6b08}
h1{font-size:clamp(25px,4vw,34px)!important;line-height:1.35!important;color:#2158ad!important;letter-spacing:-.7px;margin-top:8px!important}
h2{font-size:23px!important;line-height:1.45!important;color:#243e64}
h3{font-size:20px!important;line-height:1.5!important;border-left:4px solid #3979de!important;padding-left:12px!important;margin-top:28px!important;margin-bottom:16px!important}
h4{font-size:17px!important;line-height:1.5!important}
a{color:#245fbd}a:hover{text-decoration:underline}button,input,select{font:inherit}button{cursor:pointer;border-radius:10px!important;min-height:40px;padding:8px 14px!important}input:not([type=checkbox]):not([type=radio]),select{border-radius:9px!important;min-height:40px;max-width:100%;padding:8px 10px!important}button:disabled{cursor:wait;opacity:.6}
:where(a,button,input,select,summary):focus-visible{outline:3px solid #4384ee;outline-offset:3px}
a.card{display:block;border:1px solid #dfe7f2!important;border-radius:16px!important;padding:20px 22px!important;margin:14px 0!important;background:#fff;box-shadow:0 3px 12px #203d6b05;transition:border-color .15s,box-shadow .15s}a.card:hover{border-color:#8fb6ef!important;box-shadow:0 6px 20px #2463d410;text-decoration:none}.card .name{font-size:19px!important;color:#224d8b}.card .note{line-height:1.7!important;color:#60738d}
.admin-menu a{border-radius:10px!important;font-size:14px!important;line-height:1.5!important;padding:11px 14px!important}.admin-menu a.active{background:#eaf2ff!important;color:#245fbd!important}
.rtabs,.hub-tabs,.tabs,.stock-tabs{gap:8px!important;padding:12px 0!important;background:#fff!important;flex-wrap:wrap}.rtabs a,.hub-tabs a,.tab,.stock-tabs button{font-size:14px!important;line-height:1.5!important;border-radius:10px!important;padding:10px 15px!important;font-weight:600!important}.rtabs a[aria-selected=true],.hub-tabs a[aria-selected=true],.tab.active,.stock-tabs button.on{background:#2463d4!important;color:#fff!important;border-color:#2463d4!important}
#easy-summary{background:#f5f9ff!important;border:1px solid #dce8f9!important;border-radius:18px!important;padding:22px!important;margin:18px 0 28px!important}#easy-summary>h3{margin-top:0!important}#easy-summary>div{line-height:1.7!important}
.forecast-hero{background:linear-gradient(120deg,#285cca,#448af1)!important;color:white!important;border:0!important;border-radius:16px!important;padding:20px!important;margin:18px 0!important}.forecast-date{font-size:20px!important;font-weight:700!important;margin-bottom:16px!important}.forecast-grid{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px!important}.forecast-card{background:#ffffff!important;border:0!important;border-radius:12px!important;padding:16px!important;min-width:0!important}.forecast-label{color:#526985!important;font-size:13px!important;line-height:1.6!important}.forecast-value{font-size:28px!important;color:#203f6f!important;line-height:1.5!important;letter-spacing:-.7px}.forecast-note{font-size:12px!important;color:#526985!important;line-height:1.6!important}
details.report-detail{border:1px solid #dce5f2;border-radius:12px;background:#fff;margin:16px 0;padding:14px 16px}details.report-detail>summary{cursor:pointer;font-weight:600;color:#2d568c;font-size:15px}details.report-detail[open]>summary{margin-bottom:14px}
.tile,.chart-box{border-radius:14px!important;border-color:#e1e8f2!important;background:#fff}.tile .k{font-size:14px!important}.tile .v{font-size:26px!important}.tile .n{font-size:13px!important}.admin-panel h3,.rtab-panel h3{font-size:20px!important}
.report-table-scroll{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;margin:12px 0}.report-table-scroll>table{margin:0!important}table{border-spacing:0}th{font-size:13px!important}td{font-size:13px}th,td{line-height:1.65}img{max-width:100%;height:auto}svg{max-width:100%}
@media(max-width:640px){body{padding:12px 10px 32px!important}body>.wrap,body>div[style*="max-width"]:not(#stale-note){padding:20px 14px!important;border-radius:18px}h1{font-size:25px!important}h3{font-size:19px!important}#easy-summary{padding:16px 12px!important}.forecast-grid{grid-template-columns:1fr}.forecast-hero{padding:16px!important}.forecast-value{font-size:29px!important}.rtabs a,.hub-tabs a,.tab{padding:9px 11px!important}.tiles{grid-template-columns:1fr!important}}
@media print{body{background:white!important;padding:0!important}body>.wrap{border:0;box-shadow:none;padding:0!important}.forecast-hero{background:#eef4ff!important;color:#182b46!important}.report-table-scroll{overflow:visible}}
</style>"""
# 기존 발행본은 숫자를 다시 계산하지 않고 의미별 클래스만 붙인다. 숨김·발송 상태는 건드리지 않는다.
SCRIPT = """<script id="report-simple-ui-script">
(function(){
function enhance(){
 const summary=document.getElementById('easy-summary');
 if(summary){
  Array.from(summary.children).forEach(function(box){
   const title=box.firstElementChild;
   if(!title)return;
   if(/^다음 거래일 .* 예측$/.test(title.textContent.trim())){
    box.classList.add('forecast-hero');title.classList.add('forecast-date');
    const grid=title.nextElementSibling;
    if(grid){grid.classList.add('forecast-grid');Array.from(grid.children).forEach(function(card){
     card.classList.add('forecast-card');
     const label=card.firstElementChild;
     if(label&&label.firstChild&&label.firstChild.nodeType===3)label.firstChild.textContent=label.firstChild.textContent.replace(/^시초가 ·/,'시초가 예측 ·').replace(/^종가 ·/,'종가 예측 ·');
     ['forecast-label','forecast-value','forecast-note'].forEach(function(name,i){if(card.children[i])card.children[i].classList.add(name);});
    });}
   }
   if(box.tagName==='DIV'&&title.textContent.trim()==='중장기 전망'){
    const detail=document.createElement('details');detail.className='report-detail';
    const caption=document.createElement('summary');caption.textContent='중장기 전망과 회사 실적 · 자세히 보기';
    box.before(detail);detail.append(caption,box);
   }
  });
  const hero=summary.querySelector('.forecast-hero');
  const lead=Array.from(summary.children).find(function(box){return box.firstElementChild&&box.firstElementChild.textContent.trim()==='전체 결론';});
  if(hero&&lead)hero.before(lead);
 }
 document.querySelectorAll('table').forEach(function(table){
  if(!table.querySelector('th')||table.parentElement.classList.contains('report-table-scroll'))return;
  const scroll=document.createElement('div');scroll.className='report-table-scroll';scroll.tabIndex=0;
  scroll.setAttribute('role','region');scroll.setAttribute('aria-label','표 · 좌우로 이동해 전체 내용 보기');
  table.before(scroll);scroll.appendChild(table);
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhance);else enhance();
})();
</script>"""


def apply_simple_ui(document):
    """완성 HTML의 공통 화면만 갱신한다. 원문·실행 시각·기존 스크립트는 보존한다."""
    if not re.search(r'</head\s*>', document, re.I):
        return document
    document = re.sub(re.escape(START) + r'.*?' + re.escape(END), '', document, flags=re.S)
    block = START + STYLE + SCRIPT + END
    return re.sub(r'</head\s*>', lambda m: block + m.group(), document, count=1, flags=re.I)


def simple_document(builder):
    """모든 생성기가 같은 화면으로 발행하도록 반환값에 적용한다."""
    @wraps(builder)
    def wrapped(*args, **kwargs):
        return apply_simple_ui(builder(*args, **kwargs))
    return wrapped


def refresh_existing(root):
    """수집·예측·원장 기록 없이 기존 보고서의 화면만 재생성한다."""
    changed = 0
    for path in Path(root).rglob('*.html'):
        before = path.read_text(encoding='utf-8')
        after = apply_simple_ui(before)
        if after != before:
            path.write_text(after, encoding='utf-8')
            changed += 1
    return changed


if __name__ == '__main__':
    print(f"공통 화면 적용: {refresh_existing(Path(__file__).resolve().parents[1] / 'docs')}개")
