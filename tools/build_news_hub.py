# -*- coding: utf-8 -*-
"""'최신 뉴스 및 트렌드' 한 페이지 — AI 뉴스 · 인기 급상승 검색어 · 장기 관심도를 탭으로 묶는다.

세 페이지는 따로 만들어지고 갱신 주기도 다르다(AI 뉴스·검색어는 3시간마다, 관심도는 하루 한 번).
하나의 파일로 다시 조립하면 한쪽이 갱신될 때마다 나머지를 받아 붙여야 하고, 서로 다른 잡이 같은
파일을 동시에 쓰게 된다. 그래서 이 페이지는 **틀만** 두고, 각 탭이 기존 페이지를 그대로 불러 보인다.
세 페이지의 주소(/ai_news/, /trends/, /interest/)도 그대로 살아 있다.

조회수는 탭 안의 각 페이지가 스스로 센다(탭을 처음 열 때 한 번). 이 틀은 세지 않는다 — 세면
같은 방문이 두 번 잡힌다.

    python tools/build_news_hub.py --write      # docs/news/index.html 을 다시 쓴다
"""
import argparse
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_PATH = ROOT / "docs" / "news" / "index.html"
TITLE = "최신 뉴스 및 트렌드"
# (주소 조각, 탭 이름, 불러올 페이지). 첫 탭이 기본으로 열린다.
TABS = (("ai_news", "최신 AI 뉴스", "../ai_news/"),
        ("trends", "인기 급상승 검색어", "../trends/"),
        ("interest", "장기 관심도", "../interest/"))

_STYLE = """<style>
html,body{overflow-x:hidden;overflow-x:clip}
body{margin:0;padding:24px 20px 48px;background:#fff;font-family:-apple-system,'Malgun Gothic',sans-serif;
line-height:1.65;color:#1a1a1a;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto}a{color:#1a5490}
@media(max-width:640px){body{padding:16px 12px 32px}}
.hub-tabs{position:sticky;top:0;z-index:20;display:flex;gap:2px;overflow-x:auto;background:#fff;
border-bottom:1px solid #d8dce0;margin:0 0 8px;padding-top:6px;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.hub-tabs::-webkit-scrollbar{display:none}
.hub-tabs a{flex:0 0 auto;padding:10px 16px;font-size:14px;line-height:1.2;color:#5b6570;text-decoration:none;
white-space:nowrap;border-bottom:3px solid transparent;margin-bottom:-1px}
.hub-tabs a[aria-selected="true"]{color:#1a1a1a;font-weight:700;border-bottom-color:#1a5490}
.hub-tabs a:focus-visible{outline:2px solid #1a5490;outline-offset:-2px}
.hub-panel iframe{display:block;width:100%;height:80vh;border:0}
.hub-on .hub-panel{display:none}
.hub-on .hub-panel.is-active{display:block}
</style>"""

# 탭 안에 들어간 페이지에 넣는 스타일. 탭 이름이 이미 제목을 말하므로 페이지 자기 제목(.page-title)은 숨기고
# 바깥 여백을 줄인다. 따로 열었을 때는 이 스타일이 들어가지 않으므로 아무것도 바뀌지 않는다.
EMBED_CSS = "body{padding:2px 2px 12px!important}.page-title{display:none!important}"

_SCRIPT = """<script>(function(){
var root=document.getElementById("hub");if(!root)return;
var tabs=root.querySelectorAll(".hub-tabs a"),panels=root.querySelectorAll(".hub-panel");
if(!panels.length)return;root.className+=" hub-on";
function fit(frame){try{var d=frame.contentDocument;if(!d||!d.documentElement)return;
var h=Math.max(d.documentElement.scrollHeight,d.body?d.body.scrollHeight:0);if(h>0)frame.style.height=h+"px";}catch(e){}}
function dress(frame){try{var d=frame.contentDocument;if(!d||!d.head)return;
if(!d.getElementById("hub-embed")){var s=d.createElement("style");s.id="hub-embed";s.textContent=__EMBED_CSS__;d.head.appendChild(s);}
var links=d.querySelectorAll("a[href]");
for(var i=0;i<links.length;i++){var h=links[i].getAttribute("href")||"";
if(h.charAt(0)!=="#"&&!links[i].getAttribute("target"))links[i].setAttribute("target","_top");}}catch(e){}}
function watch(frame){frame.addEventListener("load",function(){dress(frame);fit(frame);
try{if(window.ResizeObserver){new ResizeObserver(function(){fit(frame);}).observe(frame.contentDocument.body);}}catch(e){}
var n=0,t=setInterval(function(){fit(frame);if(++n>=12)clearInterval(t);},500);});}
function show(key){var hit=null,i;
for(i=0;i<panels.length;i++){var on=panels[i].getAttribute("data-tab")===key;panels[i].classList.toggle("is-active",on);if(on)hit=panels[i];}
if(!hit){hit=panels[0];hit.classList.add("is-active");key=hit.getAttribute("data-tab");}
for(i=0;i<tabs.length;i++){tabs[i].setAttribute("aria-selected",tabs[i].getAttribute("data-tab")===key?"true":"false");}
var frame=hit.querySelector("iframe");
if(frame&&!frame.getAttribute("src")){watch(frame);frame.setAttribute("src",frame.getAttribute("data-src"));}
else if(frame){fit(frame);}
return key;}
for(var k=0;k<tabs.length;k++){tabs[k].addEventListener("click",function(e){e.preventDefault();
var key=show(this.getAttribute("data-tab"));try{history.replaceState(null,"","#"+key);}catch(err){}});}
function route(){show((location.hash||"").slice(1));}
window.addEventListener("hashchange",route);route();
})();</script>"""


def build_hub():
    """틀 페이지 HTML. 자료를 받지 않으므로 같은 입력이면 늘 같은 출력이다."""
    import json
    buttons = "".join(
        f'<a href="#{key}" data-tab="{key}" aria-selected="{"true" if index == 0 else "false"}">'
        f'{escape(label)}</a>'
        for index, (key, label, _) in enumerate(TABS))
    # 스크립트가 없으면 틀을 채울 수 없다. 그때는 각 페이지로 가는 링크를 보인다.
    panels = "".join(
        f'<section class="hub-panel" id="panel-{key}" data-tab="{key}">'
        f'<iframe title="{escape(label)}" data-src="{src}"></iframe>'
        f'<noscript><p><a href="{src}">{escape(label)} 열기</a></p></noscript></section>'
        for key, label, src in TABS)
    script = _SCRIPT.replace("__EMBED_CSS__", json.dumps(EMBED_CSS))
    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(TITLE)}</title>{_STYLE}</head><body>'
        '<div class="wrap" id="hub">'
        '<div style="padding-bottom:8px;margin-bottom:6px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">NEWS &amp; TRENDS</div>'
        f'<h2 style="margin:6px 0 4px;font-size:27px">{escape(TITLE)}</h2>'
        '<div style="font-size:12px;color:#8a9199">세 탭은 따로 갱신됩니다 — AI 뉴스와 검색어는 약 3시간마다, '
        '장기 관심도는 하루 한 번. 각 탭 안에 기준 시각이 적혀 있습니다.</div></div>'
        f'<nav class="hub-tabs" aria-label="뉴스와 트렌드 탭">{buttons}</nav>'
        f'{panels}'
        '<div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e5e5;font-size:12px;color:#8a9199">'
        '<a href="../">예측 보고서</a> · 헤드라인과 검색 지표 모음입니다. 투자 자문이 아닙니다.</div>'
        f'</div>{script}</body></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help=f"{HUB_PATH.relative_to(ROOT)} 을 다시 쓴다")
    args = parser.parse_args()
    page = build_hub()
    if args.write:
        HUB_PATH.parent.mkdir(parents=True, exist_ok=True)
        HUB_PATH.write_text(page, encoding="utf-8")
        print(f"저장: {HUB_PATH.relative_to(ROOT)} ({len(page):,} bytes)")
    else:
        print(f"{len(page):,} bytes — --write 로 저장합니다.")


if __name__ == "__main__":
    main()
