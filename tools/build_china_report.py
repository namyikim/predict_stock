# -*- coding: utf-8 -*-
"""중국 5개년 계획과 주식 수익 — 보고서를 만들고 GitHub Pages에 발행한다.

질문은 하나다. "계획이 발표됐을 때 그 계획이 밀어주는 업종의 대표 종목을 샀다면
계획 기간 동안 얼마를 벌었고, 지수보다 나았는가?" 8차(1991~95)부터 14차(2021~25)까지
계획마다 정책 업종의 대표 종목을 정해 놓고 계획 기간의 수익률을 상해종합과 비교한다.
그 위에 CSI 300(沪深300)을 따로 살피고, 15차(2026~30) 건의·요강이 지목한 산업의
후보 종목을 현재 지표와 함께 놓는다.

정직하게 밝혀야 할 한계
- 종목 선정은 뒤에서 본 것이다. 상장폐지된 종목, 망한 종목은 여기 없다(생존 편향).
  그래서 "정책 종목이 이만큼 벌었다"보다 "정책 종목이라도 지수를 못 이긴 경우가
  이만큼 많다"는 쪽 결론이 더 믿을 만하다.
- 시세는 Yahoo Finance(배당 재투자 반영 조정 종가). 홍콩 종목은 2000년부터만 있다.
  A주는 위안, 홍콩은 홍콩달러 기준이라 환율 효과는 빠져 있다.
- CSI 300 원지수는 Yahoo에 2021년부터만 있어, 2012-05부터는 화타이 CSI300 ETF(510300)를
  대리로 쓴다(배당 포함). 그 전은 상해종합으로 대신한다.

    python tools/build_china_report.py --out runs/china
    python tools/build_china_report.py --out runs/china --publish
"""
import argparse
import html
import json
import math
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_pages  # noqa: E402

warnings.filterwarnings("ignore")
KST = timezone(timedelta(hours=9))
PAGES_DIR = "docs/china"
COUNTER_ENDPOINT = "https://predict-stock-counter.kimname1.workers.dev"
BENCH = "000001.SS"           # 상해종합 (Yahoo 1997-07~)
CSI_ETF = "510300.SS"         # CSI 300 대리 (2012-05~, 배당 포함)
CSI_IDX = "000300.SS"         # CSI 300 원지수 (Yahoo 2021-03~)
PEERS = {"000001.SS": "상해종합", "^HSCE": "항셍 중국기업(H주)", "^KS11": "KOSPI", "^GSPC": "S&P 500"}

# ---------------------------------------------------------------------------
# 계획별 정책 업종과 대표 종목
# 종목은 "계획 시작 시점에 그 업종을 대표하던 상장사"를 고르되, 계획 기간 중 상장한
# 종목은 상장일부터 계산하고 표에 표시한다. 정책과 무관하지만 그 시기 최대 수혜였던
# 종목(예: 마오타이)은 비교용으로 넣고 그렇게 적는다.
# ---------------------------------------------------------------------------
PLANS = [
    dict(n=8, years=(1991, 1995), title="8차 (1991~1995)",
         summary="1989년 이후의 긴축이 1992년 덩샤오핑 남순강화로 풀리며 '사회주의 시장경제'가 "
                 "공식화된 시기. 계획의 우선순위는 농업·에너지·교통·원자재 등 기초산업과 "
                 "푸둥 개발 같은 개방이었다. 상해(1990.12)·심천(1991.7) 거래소가 이제 막 문을 "
                 "열어 상장사 자체가 몇 십 개였고, 1992년에는 신주 광풍(8·10 사건)이 있었다.",
         priorities=["기초산업(에너지·교통·원자재)", "농업", "개방·푸둥 개발", "국유기업 경영 자율화"],
         picks=[
             ("600651.SS", "페이러음향(飞乐音响)", "老八股·전자", "상해거래소 첫 상장 8종목 중 하나. 초기 시장 자체의 수익률을 보여준다"),
             ("600602.SS", "윈싸이즈롄(옛 真空电子)", "老八股·전자", "老八股. 1992년 첫 B주 발행"),
             ("000002.SZ", "완커(万科)", "부동산·개방", "심천 첫 상장사 중 하나. 개방·도시화의 대표"),
             ("600688.SS", "상해석화(上海石化)", "기초산업·석유화학", "1993년 상장. 계획이 최우선으로 둔 기초산업(석화)의 대표"),
         ]),
    dict(n=9, years=(1996, 2000), title="9차 (1996~2000)",
         summary="'두 개의 근본적 전환'(계획→시장, 조방→집약)을 내걸고 국유기업 개혁(抓大放小), "
                 "삼협댐 등 대형 기간시설, 통신·정보산업 육성, 1998년 주택 상품화를 추진했다. "
                 "1997년 아시아 금융위기와 1999년 '5·19 행정'(정부 부양 랠리)이 있었다. "
                 "홍콩 H주(차이나모바일 1997, 페트로차이나 2000 상장)는 Yahoo 데이터가 2000년부터라 "
                 "여기서는 A주만 계산한다.",
         priorities=["국유기업 개혁·대형화", "기간시설(삼협댐·도로·전력)", "주택 상품화(1998)", "통신·정보산업", "자동차·석화 등 기간산업"],
         picks=[
             ("600104.SS", "상하이자동차(上汽)", "기간산업·자동차", "1997년 상장. 자동차를 '지주산업'으로 지정"),
             ("000002.SZ", "완커(万科)", "주택 상품화", "1998년 복지분방 폐지의 직접 수혜"),
             ("600009.SS", "상해공항(上海机场)", "기간시설", "1998년 상장. 교통 인프라"),
             ("600688.SS", "상해석화(上海石化)", "석유화학", "기간산업"),
             ("000651.SZ", "거리전기(格力)", "가전·소비", "1996년 상장. 정책 업종은 아니나 그 시기 성장주"),
             ("600000.SS", "푸둥발전은행(浦发)", "금융개혁", "1999년 상장. 주식제 은행 개혁"),
         ]),
    dict(n=10, years=(2001, 2005), title="10차 (2001~2005)",
         summary="WTO 가입(2001.12), 서부대개발, 정보화, 도시화, '走出去'. 경제는 중화학 붐"
                 "(철강·자동차·석화·전력 부족)으로 두 자릿수 성장했지만 증시는 국유주 감지(减持) "
                 "문제로 2001~2005년 내내 하락했다. 경제와 주가가 따로 논 대표적 시기.",
         priorities=["WTO 가입·무역", "서부대개발·기간시설", "정보화·통신", "도시화·주택", "에너지·철강 등 중화학"],
         picks=[
             ("600019.SS", "바오산강철(宝钢)", "철강·중화학", "2000년 말 상장. 철강 수요 폭발"),
             ("600104.SS", "상하이자동차(上汽)", "자동차·WTO", "WTO 이후 승용차 대중화"),
             ("0857.HK", "페트로차이나 H(中石油)", "에너지", "2000년 홍콩 상장"),
             ("0386.HK", "시노펙 H(中石化)", "에너지·석화", "2000년 홍콩 상장"),
             ("0941.HK", "차이나모바일 H(中国移动)", "정보화·통신", "1997년 홍콩 상장"),
             ("600585.SS", "하이뤄시멘트(海螺)", "도시화·건자재", "2002년 상장"),
             ("600036.SS", "초상은행(招商银行)", "금융", "2002년 상장"),
             ("600900.SS", "창장전력(长江电力)", "삼협댐·전력", "2003년 상장. 9차 계획의 결과물"),
             ("000002.SZ", "완커(万科)", "도시화·주택", "부동산 상승기의 시작"),
         ]),
    dict(n=11, years=(2006, 2010), title="11차 (2006~2010)",
         summary="'계획(计划)'에서 '규획(规划)'으로 이름을 바꾼 첫 계획. 과학발전관, 절능감배"
                 "(단위 GDP 에너지 20% 감축), 자주혁신, 사회주의 신농촌, 고속철 착공. "
                 "비유통주 개혁이 끝나며 2006~07년 대상승, 2008년 폭락, 4조 위안 부양(2008.11)으로 "
                 "2009년 반등. 부양은 철도·건설·시멘트·건설기계로 흘렀다.",
         priorities=["고속철·철도망", "절능감배·신에너지", "자주혁신", "4조 위안 부양(2008~)", "금융 대형화(은행 상장)"],
         picks=[
             ("601390.SS", "중국중철(中国中铁)", "고속철·건설", "2007년 상장"),
             ("601186.SS", "중국철건(中国铁建)", "고속철·건설", "2008년 상장"),
             ("601766.SS", "중국중차(中国中车·옛 南车)", "고속철 차량", "2008년 상장"),
             ("600031.SS", "싼이중공(三一重工)", "건설기계·부양", "4조 위안 부양의 최대 수혜"),
             ("000157.SZ", "중롄중커(中联重科)", "건설기계·부양", ""),
             ("601088.SS", "중국신화(中国神华)", "에너지·석탄", "2007년 상장"),
             ("600900.SS", "창장전력(长江电力)", "절능감배·수력", ""),
             ("002202.SZ", "골드윈드(金风科技)", "신에너지·풍력", "2007년 말 상장"),
             ("1211.HK", "BYD H(比亚迪)", "신에너지차", "2008년 버핏 투자"),
             ("601398.SS", "공상은행(工商银行)", "금융", "2006년 상장. 당시 세계 최대 IPO"),
             ("600276.SS", "항서제약(恒瑞医药)", "의료개혁(2009)", ""),
         ]),
    dict(n=12, years=(2011, 2015), title="12차 (2011~2015)",
         summary="7대 전략적 신흥산업(절능환보, 신세대 IT, 바이오, 고급 장비, 신에너지, 신재료, "
                 "신에너지차)을 명시한 계획. 소비 확대와 서비스업, 도시화, 의료 개혁도 담았다. "
                 "부양 후유증으로 2011~14년은 지지부진했고, 2014년 말~2015년 상반기 신용 거품 뒤 "
                 "2015년 6월 폭락이 왔다. 태양광은 2012년 유럽 반덤핑과 과잉설비로 한 차례 무너졌다.",
         priorities=["7대 전략적 신흥산업", "소비 확대·서비스업", "도시화", "인터넷+(2015)", "의료 개혁"],
         picks=[
             ("002594.SZ", "BYD A(比亚迪)", "신에너지차", "2011년 6월 A주 상장"),
             ("002415.SZ", "하이크비전(海康威视)", "신세대 IT·안보", "2010년 상장"),
             ("601012.SS", "룽지(隆基绿能)", "신에너지·태양광", "2012년 4월 상장"),
             ("002202.SZ", "골드윈드(金风科技)", "신에너지·풍력", ""),
             ("300274.SZ", "선그로우(阳光电源)", "신에너지·인버터", "2011년 11월 상장"),
             ("600276.SS", "항서제약(恒瑞医药)", "바이오·의약", ""),
             ("600309.SS", "완화화학(万华化学)", "신재료", ""),
             ("300015.SZ", "아이얼안과(爱尔眼科)", "의료 서비스", "창업판"),
             ("000333.SZ", "메이디(美的集团)", "소비", "2013년 9월 상장"),
             ("0700.HK", "텐센트(腾讯)", "인터넷+", "정책보다 앞서 간 민영 성장주"),
             ("600031.SS", "싼이중공(三一重工)", "고급 장비 제조", "부양 후유증 예시"),
         ]),
    dict(n=13, years=(2016, 2020), title="13차 (2016~2020)",
         summary="혁신 구동, '중국제조 2025'(2015.5), 공급측 구조개혁(철강·석탄 과잉 해소), "
                 "빈곤 탈출, 녹색 발전, '건강중국 2030', 일대일로, 반도체 대기금(2014~). "
                 "2018년 미중 무역전쟁, 2020년 코로나. 외국인 자금(후강퉁·MSCI 편입 2018)이 "
                 "'핵심 자산'(마오타이·항서·하이크비전)으로 몰렸다.",
         priorities=["중국제조 2025·반도체", "공급측 개혁(과잉 해소)", "녹색·신에너지", "건강중국", "소비 승급"],
         picks=[
             ("300750.SZ", "CATL(宁德时代)", "신에너지차·배터리", "2018년 6월 상장"),
             ("601012.SS", "룽지(隆基绿能)", "녹색·태양광", ""),
             ("002415.SZ", "하이크비전(海康威视)", "중국제조 2025", ""),
             ("002371.SZ", "북방화창(北方华创)", "반도체 장비", ""),
             ("0981.HK", "SMIC H(中芯国际)", "반도체 제조", ""),
             ("600276.SS", "항서제약(恒瑞医药)", "건강중국", ""),
             ("603259.SS", "우시앱텍(药明康德)", "건강중국·CXO", "2018년 5월 상장"),
             ("300760.SZ", "마인드레이(迈瑞医疗)", "건강중국·의료기기", "2018년 10월 상장"),
             ("601088.SS", "중국신화(中国神华)", "공급측 개혁", "석탄 가격 회복"),
             ("600019.SS", "바오산강철(宝钢)", "공급측 개혁", "철강 통합"),
             ("000333.SZ", "메이디(美的集团)", "소비 승급", ""),
             ("601888.SS", "중국중면(中国中免)", "소비 회류·면세", ""),
             ("600519.SS", "구이저우마오타이(贵州茅台)", "(비교) 소비 승급", "정책 종목이 아니지만 이 시기 최대 수혜"),
         ]),
    dict(n=14, years=(2021, 2025), title="14차 (2021~2025)",
         summary="쌍순환, 과학기술 자립자강(반도체·AI·양자), 탄소 피크·중립(30·60), 디지털 경제, "
                 "공동부유. 2021년 플랫폼·사교육 규제, 헝다 사태로 부동산 위기, 2022년 봉쇄, "
                 "2023년 '신질생산력' 제기, 2024년 9월 부양 랠리, 2025년 DeepSeek 이후 AI·반도체 랠리. "
                 "태양광은 과잉설비로 두 번째 붕괴를 겪었고, 배당 높은 국유기업('중특고')이 뜻밖의 승자였다.",
         priorities=["과학기술 자립자강(반도체·AI)", "탄소 중립·신에너지", "디지털 경제", "공동부유·플랫폼 규제", "에너지·자원 안보"],
         picks=[
             ("002371.SZ", "북방화창(北方华创)", "반도체 장비", ""),
             ("688012.SS", "AMEC(中微公司)", "반도체 장비", ""),
             ("688981.SS", "SMIC A(中芯国际)", "반도체 제조", ""),
             ("688256.SS", "캠브리콘(寒武纪)", "AI 반도체", ""),
             ("688041.SS", "하이곤(海光信息)", "국산 CPU", "2022년 8월 상장"),
             ("002230.SZ", "아이플라이텍(科大讯飞)", "AI", ""),
             ("300750.SZ", "CATL(宁德时代)", "신에너지차·배터리", ""),
             ("002594.SZ", "BYD A(比亚迪)", "신에너지차", ""),
             ("601012.SS", "룽지(隆基绿能)", "태양광", "과잉설비 붕괴 예시"),
             ("300274.SZ", "선그로우(阳光电源)", "에너지 저장", ""),
             ("601899.SS", "즈진광업(紫金矿业)", "자원 안보", ""),
             ("0883.HK", "CNOOC H(中海油)", "에너지 안보·배당", ""),
             ("0941.HK", "차이나모바일 H(中国移动)", "디지털·배당(중특고)", ""),
             ("0700.HK", "텐센트(腾讯)", "(역풍) 플랫폼 규제", "정책이 억누른 쪽"),
             ("000002.SZ", "완커(万科)", "(역풍) 부동산", "정책이 억누른 쪽"),
         ]),
]

# 15차: 후보 종목. '추천'이 아니라 계획이 지목한 산업과 겹치는 상장사 목록이다.
PLAN15 = dict(
    summary="2025년 10월 4중전회가 '15차 5개년 계획 건의'를 채택했고, 2026년 3월 전인대가 요강을 "
            "통과시켰다. 핵심 어휘는 '고품질 발전', '신질생산력', '과학기술 자립자강', "
            "'선진 제조업을 근간으로 한 현대화 산업체계', 'AI+ 행동', '내수·소비 확대', "
            "'반내권(反内卷, 과잉경쟁 정비)', '녹색 전환·신형 에너지 체계', 그리고 미래산업"
            "(양자·바이오제조·수소·핵융합·뇌-기계 인터페이스·구현 AI·6G)과 저공경제·상업우주다.",
    themes=[
        ("반도체·AI 인프라", "자립자강의 1순위. 장비·소재·설계·파운드리. 미국 수출통제가 오히려 국산화 수요를 만든다.",
         ["002371.SZ", "688012.SS", "688981.SS", "688041.SS", "688256.SS"]),
        ("AI 응용·플랫폼", "'AI+ 행동'. 규제 국면(2021~23)이 끝나고 플랫폼이 다시 성장 축으로 호명됐다.",
         ["0700.HK", "9988.HK", "002230.SZ"]),
        ("전력·에너지 저장", "신형 에너지 체계·전력망 개조. 태양광 셀보다 저장·인버터·전력망 쪽이 과잉이 덜하다.",
         ["300274.SZ", "300750.SZ", "600900.SS"]),
        ("자동화·로봇(구현 AI)", "미래산업 목록의 '구현 지능'. 산업 자동화 부품이 실적으로 연결되는 경로가 가장 짧다.",
         ["300124.SZ", "002008.SZ"]),
        ("자원·에너지 안보", "계획이 '안보'를 별도 장으로 뒀다. 구리·금·석유 등 전략 자원.",
         ["601899.SS", "0883.HK"]),
        ("소비·실버 경제·의료", "투자 중심에서 소비 중심으로의 전환, 고령화 대응(실버 경제), 혁신약.",
         ["000333.SZ", "300760.SZ", "600276.SS"]),
    ],
    names={
        "002371.SZ": "북방화창", "688012.SS": "AMEC(中微)", "688981.SS": "SMIC A", "688041.SS": "하이곤",
        "688256.SS": "캠브리콘", "0700.HK": "텐센트", "9988.HK": "알리바바 HK", "002230.SZ": "아이플라이텍",
        "300274.SZ": "선그로우", "300750.SZ": "CATL", "600900.SS": "창장전력", "300124.SZ": "이노밴스(汇川技术)",
        "002008.SZ": "한스레이저(大族激光)", "601899.SS": "즈진광업", "0883.HK": "CNOOC H", "000333.SZ": "메이디",
        "300760.SZ": "마인드레이", "600276.SS": "항서제약",
    },
)

# 계획별 해설. 2026-09-04 시세로 계산한 결과를 보고 썼다. 숫자가 크게 달라지면 다시 써야 한다.
COMMENT = {
    8: "지수 데이터가 없어 종목만 보인다. 老八股 페이러음향은 +427%인데 같은 老八股 전공전자는 +16%, 완커는 -21%, "
       "1993년 상장한 상해석화는 -56%다. 1992년 광풍 뒤 1994년 7월 바닥(상해종합 325p)까지의 하락이 다 들어 있다. "
       "시장 초기에는 정책보다 신주 물량과 유동성이 주가를 결정했다. '지금까지' 열이 흥미롭다 — 30년 보유 시 "
       "초기 소형주는 +1,000~4,000%인데 계획이 최우선으로 둔 기초산업(상해석화)은 +105%에 그쳤다.",
    9: "6개 중 2개만 지수를 이겼다. 이긴 둘은 주택 상품화의 직접 수혜 완커(+547%)와 기간산업 상해석화(+123%). "
       "진 넷(상하이자동차·상해공항·거리·푸둥발전)은 전부 계획 기간 중 상장한 종목이다 — 상장 직후의 고평가에서 "
       "출발해 지수를 밑돌았다. 정책 업종이라도 <b>IPO 직후에 사면 정책 효과보다 공모가 거품이 컸다</b>. "
       "거리전기는 이 5년간 +33%였지만 '지금까지' +18,000%로, 정책과 무관한 소비재가 30년의 승자였다.",
    10: "9개 전부 지수를 이겼지만 지수가 -45%였다. 대부분은 '덜 빠졌다'(바오강 -9%, 상하이자동차 -26%, "
        "차이나모바일 -7%)이고, 절대 수익을 낸 것은 홍콩 상장 에너지주(페트로차이나 +581%, 시노펙 +299%)와 "
        "완커·하이뤄뿐이다. 실물은 두 자릿수 성장하는데 A주는 비유통주 문제로 5년 내내 빠진 시기라, "
        "<b>같은 정책 수혜라도 어느 시장에 상장돼 있느냐</b>가 결과를 갈랐다.",
    11: "11개 중 6개. 승자는 부양의 수요가 매출로 직결된 건설기계(싼이 +2,536%, 중롄 +1,712%)와 BYD(+1,349%), "
        "항서(+1,388%). 패자는 고속철 시공사(중국중철·철건)와 신화·골드윈드 — <b>모두 2007~08년 거품 꼭대기에 "
        "상장</b>했다. '지금까지' 열의 중철 -21%, 철건 -19%는 18년을 보유해도 공모가를 회복하지 못했다는 뜻이다. "
        "정책이 밀어주는 업종이라도 고점에 상장한 종목은 정책의 수혜를 주주에게 주지 않았다.",
    12: "11개 중 9개. 7대 신흥산업이 대체로 지수를 압도했다(하이크비전 +205%, 룽지 +279%, 선그로우 +189%, BYD +153%). "
        "예외는 풍력 과잉의 골드윈드(+10%)와 부양 후유증의 싼이(-50%). 룽지의 수익은 2012년 유럽 반덤핑으로 "
        "태양광이 한 번 무너진 뒤 바닥에서 상장한 덕이 크다. 정책 목록에 없던 텐센트가 +344%로 가장 좋았다.",
    13: "13개 전부, 그것도 지수 +5%에 종목은 +500~1,200%. <b>이 표가 가장 편향돼 있다</b> — 2016~20년 '핵심 자산' "
        "강세장의 승자를 뒤에서 골랐기 때문이다. 그래도 지수가 5년간 5% 오를 때 종목이 10배가 됐다는 사실은 "
        "이 시기 A주가 '지수는 죽고 종목은 산' 극단적 양극화였음을 보여 준다. 공급측 개혁 수혜(신화 +79%, "
        "바오강 +43%)는 지수는 이겼지만 성장주에 비하면 작았다. '지금까지' 열을 보라: 룽지는 2020년 말 +1,228%에서 "
        "+243%로, 중국중면은 +960%에서 +120%로 — <b>13차의 승자 다수가 14차에 반토막 이상 났다</b>.",
    14: "15개 중 13개. 자립자강(캠브리콘 +819%, 하이곤 +275%, 북방화창 +224%)과 안보·배당(CNOOC +390%, 즈진 +277%, "
        "차이나모바일 +166%)이 승자. 탄소중립은 갈렸다 — 저장·인버터의 선그로우 +218%, 태양광 셀 과잉의 룽지 -63%. "
        "정책이 억누른 쪽은 완커 -80%, 텐센트 +17%(지수 수준). 14차는 <b>'무엇을 밀어주는가'만큼 '무엇을 "
        "억누르는가'</b>가 수익을 가른 첫 계획이었다.",
    "csi": "2012년 5월부터 14년간 +113%, 연 5.4%. 같은 기간 KOSPI 8.8%, S&P 500 12.8%에 못 미치고, 홍콩의 H주 지수는 "
           "-21%다. 변동성 22%에 최대 낙폭 -45%라 위험 대비 보상이 나빴다. 연도별로 +54%(2014)·+39%(2019)와 "
           "-24%(2018)·-20%(2022)가 번갈아 나오는데, 이는 추세가 아니라 정책·유동성 사이클이다. 14차 5년간은 -3%였고, "
           "현재는 2021년 2월 고점 대비 -12%. <b>중국은 지수를 사서 기다리는 시장이 아니었다</b>. 2절이 보여 주듯 "
           "성과는 정책 테마 안의 종목 선택에서 갈렸다. 원화 투자자는 위안/원 환율 변동을 따로 얹어야 한다.",
    "15": "후보를 현재 상태로 나누면 셋이다. <b>(가) 정책 정합성이 가장 높고 이미 많이 오른 것</b> — 반도체 장비"
          "(북방화창 5년 +164%, AMEC +226%, 캠브리콘 +1,649%). 가격이 정책을 상당 부분 반영했고, 지금은 5년 고점 대비 "
          "-30% 안팎의 조정 국면이다. 13차 승자들이 14차에 겪은 일을 기억할 것. <b>(나) 정합성은 높은데 가격이 눌린 것</b> — "
          "텐센트·알리바바(올해 -25%·-23%), 선그로우(올해 -48%, 반내권 정리 국면), 이노밴스(-21%), 항서(-22%), "
          "마인드레이(5년 고점 대비 -53%). 정책이 다시 수요를 만들면 회복 여지가 큰 쪽이지만, 눌린 데는 이유가 있다. "
          "<b>(다) 꾸준한 것</b> — 창장전력, 메이디, CNOOC, 즈진광업. 배당과 실적이 받쳐 준다. "
          "2절의 교훈을 15차에 적용하면: 정책이 <i>수요</i>를 만드는 곳은 반도체 국산화 조달·전력망·AI 인프라이고, "
          "<i>공급</i>을 정리하는 곳(반내권)은 태양광·배터리·전기차다. 후자는 설비가 줄어드는 국면에서 살아남는 "
          "1~2위(CATL·BYD)만 의미가 있다. 이 목록은 연구·교육용이며 개인의 상황을 고려한 투자 자문이 아니다.",
}


# ---------------------------------------------------------------------------
# 시세
# ---------------------------------------------------------------------------
def load_prices(tickers, cache_dir, fetch=True):
    """조정 종가(배당 재투자 반영). 캐시 CSV가 있고 fetch=False면 그것을 쓴다."""
    import yfinance as yf
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for t in tickers:
        path = cache_dir / f"{t}.csv"
        s = None
        if not fetch and path.exists():
            s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        else:
            try:
                h = yf.Ticker(t).history(period="max", auto_adjust=True)
                if not h.empty:
                    s = h["Close"].copy()
                    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                    s = s[~s.index.duplicated()].dropna()
                    s.to_csv(path, header=["close"])
            except Exception as exc:
                print(f"  ⚠️ {t}: {type(exc).__name__}", flush=True)
                if path.exists():
                    s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        if s is not None and len(s):
            out[t] = s.sort_index()
    return out


def window(s, start, end):
    w = s.loc[(s.index >= start) & (s.index <= end)]
    return w if len(w) >= 2 else None


def stats(w):
    ret = w.iloc[-1] / w.iloc[0] - 1
    years = (w.index[-1] - w.index[0]).days / 365.25
    cagr = (1 + ret) ** (1 / years) - 1 if years > 0.5 else float("nan")
    mdd = (w / w.cummax() - 1).min()
    return dict(ret=ret, cagr=cagr, mdd=mdd, years=years,
                start=w.index[0].date(), end=w.index[-1].date())


def analyse_plans(prices, today):
    bench = prices.get(BENCH)
    results = []
    for plan in PLANS:
        y0, y1 = plan["years"]
        p_start, p_end = pd.Timestamp(y0, 1, 1), min(pd.Timestamp(y1, 12, 31), today)
        rows = []
        for ticker, name, sector, why in plan["picks"]:
            s = prices.get(ticker)
            if s is None:
                rows.append(dict(ticker=ticker, name=name, sector=sector, why=why, missing=True))
                continue
            w = window(s, p_start, p_end)
            if w is None:
                rows.append(dict(ticker=ticker, name=name, sector=sector, why=why, missing=True))
                continue
            st = stats(w)
            late = s.index[0] > p_start + pd.Timedelta(days=10)
            b = window(bench, pd.Timestamp(st["start"]), pd.Timestamp(st["end"])) if bench is not None else None
            b_ret = (b.iloc[-1] / b.iloc[0] - 1) if b is not None else float("nan")
            to_today = s.iloc[-1] / w.iloc[0] - 1
            rows.append(dict(ticker=ticker, name=name, sector=sector, why=why, missing=False,
                             late=late, bench_ret=b_ret, to_today=to_today, **st))
        ok = [r for r in rows if not r["missing"]]
        with_b = [r for r in ok if not math.isnan(r["bench_ret"])]
        agg = dict(
            n=len(ok),
            beat=sum(r["ret"] > r["bench_ret"] for r in with_b),
            n_b=len(with_b),
            median_excess=(pd.Series([r["ret"] - r["bench_ret"] for r in with_b]).median() if with_b else float("nan")),
            basket=(pd.Series([r["ret"] for r in ok]).mean() if ok else float("nan")),
            bench=(window(bench, p_start, p_end) if bench is not None else None),
        )
        agg["bench_ret"] = (agg["bench"].iloc[-1] / agg["bench"].iloc[0] - 1) if agg["bench"] is not None else float("nan")
        results.append(dict(plan=plan, rows=rows, agg=agg, p_start=p_start, p_end=p_end))
    return results


def analyse_csi(prices, today):
    etf = prices[CSI_ETF]
    out = dict(series=etf, start=etf.index[0].date())
    out.update(stats(etf))
    daily = etf.pct_change().dropna()
    out["vol"] = daily.std() * math.sqrt(252)
    out["from_peak"] = etf.iloc[-1] / etf.max() - 1
    out["peak_date"] = etf.idxmax().date()
    yearly = etf.resample("YE").last()
    yr = (yearly / yearly.shift(1) - 1).dropna()
    first_year_ret = yearly.iloc[0] / etf.iloc[0] - 1
    out["yearly"] = [(yearly.index[0].year, first_year_ret, True)] + [(d.year, v, False) for d, v in yr.items()]
    # 같은 구간의 다른 지수
    peers = []
    for t, name in PEERS.items():
        s = prices.get(t)
        w = window(s, etf.index[0], today) if s is not None else None
        if w is not None:
            st = stats(w)
            peers.append(dict(name=name, ticker=t, **st, vol=w.pct_change().dropna().std() * math.sqrt(252)))
    out["peers"] = peers
    # 계획 구간별
    plan_rows = []
    for y0, y1, label in [(2011, 2015, "12차(2012-05부터)"), (2016, 2020, "13차"), (2021, 2025, "14차"), (2026, 2030, "15차(진행 중)")]:
        w = window(etf, pd.Timestamp(y0, 1, 1), min(pd.Timestamp(y1, 12, 31), today))
        b = window(prices[BENCH], pd.Timestamp(y0, 1, 1), min(pd.Timestamp(y1, 12, 31), today))
        if w is not None:
            st = stats(w)
            plan_rows.append(dict(label=label, **st, bench=(b.iloc[-1] / b.iloc[0] - 1) if b is not None else float("nan")))
    out["plans"] = plan_rows
    idx = prices.get(CSI_IDX)
    out["index_level"] = float(idx.iloc[-1]) if idx is not None else float("nan")
    out["index_date"] = idx.index[-1].date() if idx is not None else None
    out["ma200"] = float(etf.rolling(200).mean().iloc[-1])
    return out


def analyse_15(prices, today):
    rows = []
    for theme, why, tickers in PLAN15["themes"]:
        for t in tickers:
            s = prices.get(t)
            if s is None:
                rows.append(dict(theme=theme, ticker=t, name=PLAN15["names"].get(t, t), missing=True))
                continue
            last = s.iloc[-1]

            def ret_since(days):
                w = s.loc[s.index <= today - pd.Timedelta(days=days)]
                return last / w.iloc[-1] - 1 if len(w) else float("nan")

            ytd_base = s.loc[s.index < pd.Timestamp(today.year, 1, 1)]
            five = s.loc[s.index >= today - pd.Timedelta(days=5 * 365)]
            rows.append(dict(theme=theme, why=why, ticker=t, name=PLAN15["names"].get(t, t), missing=False,
                             price=last, ytd=(last / ytd_base.iloc[-1] - 1) if len(ytd_base) else float("nan"),
                             r1=ret_since(365), r3=ret_since(3 * 365), r5=ret_since(5 * 365),
                             from_5y_peak=last / five.max() - 1, listed=s.index[0].date()))
    return rows


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def pct(x, digits=0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:+.{digits}f}%"


def cls(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return "pos" if x > 0 else "neg"


TD = 'style="padding:6px 10px;border-top:1px solid #eee"'
TDR = 'style="padding:6px 10px;border-top:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums"'
TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7178;letter-spacing:.5px;background:#fafafa"'
THR = TH.replace("text-align:left", "text-align:right")


def table(head, body):
    return ('<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
            '<table style="width:100%;min-width:640px;border-collapse:collapse;font-size:13px;border:1px solid #e5e5e5">'
            f'<tr>{head}</tr>{body}</table></div>')


def h3(text):
    return f'<h3 style="font-size:16px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #ddd">{text}</h3>'


def note(text):
    return f'<div style="margin:10px 0 0;padding:12px 16px;background:#f5f6f8;border-radius:6px;font-size:13px;color:#4a4f55;line-height:1.65">{text}</div>'


def render(plan_results, csi, rows15, today, fetched):
    e = html.escape
    parts = []
    parts.append(
        '<div style="max-width:980px;margin:0 auto;font-family:-apple-system,\'Malgun Gothic\',sans-serif;'
        'line-height:1.65;color:#1a1a1a">'
        '<style>.pos{color:#1a7f37}.neg{color:#a8322a}code{font-family:ui-monospace,monospace;font-size:12px;'
        'word-break:break-all}a{color:#1a5490}</style>'
        '<div style="font-size:11px;letter-spacing:2px;color:#8a9199">CHINA · FIVE-YEAR PLANS &amp; EQUITY RETURNS</div>'
        '<h1 style="font-size:24px;margin:6px 0 4px">중국 5개년 계획과 주식 수익</h1>'
        f'<div style="font-size:13px;color:#6b7178">시세 기준일 {fetched} · 8차(1991)부터 14차(2025)까지 계획별 정책 종목의 '
        '수익률, CSI 300 분석, 15차(2026~2030) 후보</div>')

    # ---- 요약
    with_b = [r for pr in plan_results for r in pr["rows"] if not r["missing"] and not math.isnan(r["bench_ret"])]
    beat = sum(r["ret"] > r["bench_ret"] for r in with_b)
    excess = pd.Series([r["ret"] - r["bench_ret"] for r in with_b])
    parts.append(h3("1. 한눈에"))
    parts.append(note(
        f"9차~14차에서 지수 비교가 가능한 정책 종목 <b>{len(with_b)}건</b> 중 계획 기간에 상해종합을 이긴 것은 "
        f"<b>{beat}건({beat / len(with_b):.0%})</b>, 초과수익의 중앙값은 <b>{pct(excess.median())}</b>였다. "
        "숫자만 보면 '계획을 따라 사면 된다'지만, <b>종목을 뒤에서 골랐기 때문에 이 적중률은 부풀려져 있다</b> — "
        "망한 정책 종목은 이 표에 없다. 믿을 만한 것은 실패의 패턴이다. "
        "① 정책이 <i>공급</i>(설비 증설)을 부추긴 업종은 과잉으로 끝났다 — 태양광 두 차례, 풍력, 철강, 고속철 시공. "
        "② 정책 수혜주라도 거품 꼭대기에 상장한 종목은 10년 넘게 공모가를 회복하지 못했다(중국중철·철건, 신화). "
        "③ 정책이 억누른 쪽(부동산·플랫폼)은 지수 이하였다. "
        "통한 쪽은 정책이 <i>수요</i>를 만들어 준 업종이다 — 부양기의 건설기계, 배터리·전기차 초기, 반도체 국산화 조달, "
        "에너지 안보와 배당. 그리고 지수 자체가 약했다(3절): 중국에서 수익은 지수가 아니라 종목 선택에서 나왔다."))

    # ---- 계획별
    parts.append(h3("2. 계획별: 정책 업종 대표 종목의 계획 기간 수익률"))
    parts.append('<div style="font-size:13px;color:#6b7178;margin-bottom:8px">수익률은 계획 첫 거래일 → 마지막 거래일'
                 '(배당 재투자 반영). "상장 후"는 계획 시작 뒤에 상장해 상장일부터 계산한 것. 지수는 같은 구간의 상해종합. '
                 '"지금까지"는 그 시작점에서 시세 기준일까지의 누적.</div>')
    for pr in plan_results:
        plan, agg = pr["plan"], pr["agg"]
        parts.append(f'<h4 style="font-size:15px;margin:26px 0 6px">{e(plan["title"])}</h4>')
        parts.append(f'<div style="font-size:13px;margin-bottom:8px">{e(plan["summary"])}</div>')
        parts.append('<div style="font-size:12px;color:#6b7178;margin-bottom:8px">계획의 우선순위: ' +
                     " · ".join(e(p) for p in plan["priorities"]) + "</div>")
        body = ""
        for r in pr["rows"]:
            if r["missing"]:
                body += f'<tr><td {TD}>{e(r["name"])}<br><code>{r["ticker"]}</code></td><td {TD}>{e(r["sector"])}</td>' \
                        f'<td {TDR} colspan="6">데이터 없음</td></tr>'
                continue
            span = f'{r["start"]} ~ {r["end"]}' + (' <span style="color:#a8322a;font-size:11px">상장 후</span>' if r["late"] else "")
            body += (f'<tr><td {TD}>{e(r["name"])}<br><code>{r["ticker"]}</code></td>'
                     f'<td {TD}>{e(r["sector"])}' + (f'<br><span style="font-size:11px;color:#8a9199">{e(r["why"])}</span>' if r["why"] else "") + '</td>'
                     f'<td {TDR}><span style="font-size:11px;color:#8a9199">{span}</span></td>'
                     f'<td {TDR} class="{cls(r["ret"])}"><b>{pct(r["ret"])}</b></td>'
                     f'<td {TDR}>{pct(r["cagr"], 1)}</td>'
                     f'<td {TDR}>{pct(r["bench_ret"])}</td>'
                     f'<td {TDR} class="{cls(r["ret"] - r["bench_ret"]) if not math.isnan(r["bench_ret"]) else ""}">'
                     f'{pct(r["ret"] - r["bench_ret"]) if not math.isnan(r["bench_ret"]) else "—"}</td>'
                     f'<td {TDR}>{pct(r["mdd"])}</td>'
                     f'<td {TDR} class="{cls(r["to_today"])}">{pct(r["to_today"])}</td></tr>')
        head = (f'<th {TH}>종목</th><th {TH}>정책 업종</th><th {THR}>구간</th><th {THR}>수익률</th><th {THR}>연환산</th>'
                f'<th {THR}>상해종합</th><th {THR}>초과</th><th {THR}>최대낙폭</th><th {THR}>지금까지</th>')
        parts.append(table(head, body))
        if agg["n_b"]:
            parts.append(f'<div style="font-size:13px;margin-top:8px">이 계획: 종목 {agg["n"]}개 중 지수를 이긴 것 '
                         f'<b>{agg["beat"]}/{agg["n_b"]}</b> · 초과수익 중앙값 <b class="{cls(agg["median_excess"])}">'
                         f'{pct(agg["median_excess"])}</b> · 동일가중 바구니 {pct(agg["basket"])} vs 상해종합 {pct(agg["bench_ret"])}</div>')
        elif agg["n"]:
            parts.append(f'<div style="font-size:13px;margin-top:8px">이 구간은 Yahoo에 상해종합 지수가 없어(1997-07부터) '
                         f'종목 수익률만 보인다. 동일가중 바구니 {pct(agg["basket"])}.</div>')
        if plan["n"] in COMMENT:
            parts.append(note(COMMENT[plan["n"]]))

    # ---- CSI 300
    parts.append(h3("3. CSI 300(沪深300) 분석"))
    parts.append(f'<div style="font-size:13px;margin-bottom:8px">상해·심천 양 시장 대형주 300종목 지수. 시가총액 상위에 금융·'
                 f'제조·소비·IT가 고루 들어 있어 "중국 본토 대형주"의 대표다. 원지수의 Yahoo 데이터가 2021년부터라 '
                 f'여기서는 <b>화타이 CSI300 ETF(510300, 2012-05 상장, 배당 포함)</b>를 대리로 쓴다. '
                 f'원지수 현재값 <b>{csi["index_level"]:,.0f}</b> ({csi["index_date"]}).</div>')
    body = (f'<tr><td {TD}>구간</td><td {TDR}>{csi["start"]} ~ {csi["end"]} ({csi["years"]:.1f}년)</td></tr>'
            f'<tr><td {TD}>누적 수익률</td><td {TDR} class="{cls(csi["ret"])}"><b>{pct(csi["ret"])}</b></td></tr>'
            f'<tr><td {TD}>연환산(CAGR)</td><td {TDR}>{pct(csi["cagr"], 1)}</td></tr>'
            f'<tr><td {TD}>연환산 변동성</td><td {TDR}>{csi["vol"] * 100:.0f}%</td></tr>'
            f'<tr><td {TD}>최대 낙폭</td><td {TDR}>{pct(csi["mdd"])}</td></tr>'
            f'<tr><td {TD}>고점({csi["peak_date"]}) 대비 현재</td><td {TDR} class="{cls(csi["from_peak"])}">{pct(csi["from_peak"])}</td></tr>'
            f'<tr><td {TD}>200일 이동평균 대비</td><td {TDR}>{pct(csi["series"].iloc[-1] / csi["ma200"] - 1, 1)}</td></tr>')
    parts.append(table(f'<th {TH}>CSI 300 (ETF 대리)</th><th {THR}></th>', body))

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">연도별 수익률</h4>')
    cells = "".join(f'<td {TDR} class="{cls(v)}">{y}{"*" if partial else ""}<br><b>{pct(v)}</b></td>'
                    for y, v, partial in csi["yearly"])
    parts.append('<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:12px;border:1px solid #e5e5e5">'
                 f'<tr>{cells}</tr></table></div><div style="font-size:11px;color:#8a9199">* 부분 연도(첫 해는 2012-05부터, 마지막 해는 기준일까지)</div>')

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">같은 구간의 다른 지수와 비교 (각 현지 통화, 2012-05 ~ 기준일)</h4>')
    body = "".join(f'<tr><td {TD}>{e(p["name"])} <code>{p["ticker"]}</code></td><td {TDR} class="{cls(p["ret"])}">{pct(p["ret"])}</td>'
                   f'<td {TDR}>{pct(p["cagr"], 1)}</td><td {TDR}>{p["vol"] * 100:.0f}%</td><td {TDR}>{pct(p["mdd"])}</td></tr>'
                   for p in [dict(name="CSI 300 (ETF)", ticker=CSI_ETF, **{k: csi[k] for k in ("ret", "cagr", "vol", "mdd")})] + csi["peers"])
    parts.append(table(f'<th {TH}>지수</th><th {THR}>누적</th><th {THR}>연환산</th><th {THR}>변동성</th><th {THR}>최대낙폭</th>', body))

    parts.append('<h4 style="font-size:14px;margin:18px 0 6px">계획 구간별</h4>')
    body = "".join(f'<tr><td {TD}>{e(p["label"])}</td><td {TDR}>{p["start"]} ~ {p["end"]}</td>'
                   f'<td {TDR} class="{cls(p["ret"])}"><b>{pct(p["ret"])}</b></td><td {TDR}>{pct(p["cagr"], 1)}</td>'
                   f'<td {TDR}>{pct(p["bench"])}</td><td {TDR}>{pct(p["mdd"])}</td></tr>' for p in csi["plans"])
    parts.append(table(f'<th {TH}>계획</th><th {THR}>구간</th><th {THR}>CSI 300</th><th {THR}>연환산</th><th {THR}>상해종합</th><th {THR}>최대낙폭</th>', body))
    if "csi" in COMMENT:
        parts.append(note(COMMENT["csi"]))

    # ---- 15차
    parts.append(h3("4. 15차 계획(2026~2030)과 후보 종목"))
    parts.append(f'<div style="font-size:13px;margin-bottom:8px">{e(PLAN15["summary"])}</div>')
    parts.append(note("아래는 <b>추천 목록이 아니라 계획이 지목한 산업과 겹치는 상장사 목록</b>이다. 2절의 교훈을 그대로 적용하면: "
                      "정책이 <i>수요</i>를 만드는 쪽(반도체 국산화 조달, 전력망 투자, AI 인프라 지출)은 통할 확률이 높았고, "
                      "정책이 <i>공급</i>을 부추기는 쪽(설비 증설 보조)은 과잉으로 끝나는 일이 많았다. 15차의 '반내권'은 "
                      "그 과잉을 정부가 직접 정리하겠다는 뜻이라, 이미 과잉인 업종(태양광·일부 배터리)은 <i>설비가 줄어드는 국면</i>에서 "
                      "살아남는 1~2위만 의미가 있다. 이 페이지는 연구·교육용이며 투자 자문이 아니다."))
    for theme, why, tickers in PLAN15["themes"]:
        parts.append(f'<h4 style="font-size:14px;margin:18px 0 4px">{e(theme)}</h4>'
                     f'<div style="font-size:12px;color:#6b7178;margin-bottom:6px">{e(why)}</div>')
        body = ""
        for r in [x for x in rows15 if x["theme"] == theme]:
            if r["missing"]:
                body += f'<tr><td {TD}>{e(r["name"])}<br><code>{r["ticker"]}</code></td><td {TDR} colspan="6">데이터 없음</td></tr>'
                continue
            body += (f'<tr><td {TD}>{e(r["name"])}<br><code>{r["ticker"]}</code></td>'
                     f'<td {TDR}>{r["price"]:,.2f}</td>'
                     f'<td {TDR} class="{cls(r["ytd"])}">{pct(r["ytd"])}</td>'
                     f'<td {TDR} class="{cls(r["r1"])}">{pct(r["r1"])}</td>'
                     f'<td {TDR} class="{cls(r["r3"])}">{pct(r["r3"])}</td>'
                     f'<td {TDR} class="{cls(r["r5"])}">{pct(r["r5"])}</td>'
                     f'<td {TDR}>{pct(r["from_5y_peak"])}</td></tr>')
        parts.append(table(f'<th {TH}>종목</th><th {THR}>현재가(현지)</th><th {THR}>올해</th><th {THR}>1년</th>'
                           f'<th {THR}>3년</th><th {THR}>5년</th><th {THR}>5년 고점 대비</th>', body))
    if "15" in COMMENT:
        parts.append(note(COMMENT["15"]))

    # ---- 데이터·방법
    parts.append(h3("5. 데이터와 방법"))
    parts.append(
        '<div style="font-size:13px;line-height:1.7">'
        f'<b>시세</b> Yahoo Finance, 배당 재투자 반영 조정 종가(auto_adjust). 기준일 {fetched}. A주 위안, 홍콩 홍콩달러 — 환율 효과는 빠져 있다.<br>'
        f'<b>지수</b> 상해종합 <code>{BENCH}</code>(1997-07~), CSI 300은 ETF <code>{CSI_ETF}</code>(2012-05~) 대리 + 원지수 <code>{CSI_IDX}</code>(2021-03~) 현재값.<br>'
        '<b>계획 구간</b> 계획 첫 해 1월 첫 거래일 → 마지막 해 12월 마지막 거래일. 계획 기간 중 상장한 종목은 상장일부터("상장 후").<br>'
        '<b>종목 선정</b> 각 계획 문서가 지목한 업종의 대표 상장사를 <i>사후에</i> 골랐다. 상장폐지·부도 종목이 빠져 있어(생존 편향) '
        '"정책 종목의 수익률"은 실제보다 높게 보인다. 반면 "정책 종목도 지수를 못 이긴 비율"은 그 편향에도 불구하고 나온 값이라 더 믿을 만하다.<br>'
        '<b>계획 내용</b> 각 계획 요강과 4중전회 건의(2025-10)에 대한 공개 자료를 요약한 것. 원문은 '
        '<a href="https://www.gov.cn/">中国政府网</a>에서 확인할 수 있다.<br>'
        '<b>생성</b> <code>tools/build_china_report.py</code>가 매 실행마다 시세를 새로 받아 만든다.'
        '</div>')
    parts.append("</div>")
    return "".join(parts)


def page(inner, today):
    counter = (
        '<div style="margin-top:10px;font-variant-numeric:tabular-nums">조회 <span id="view-count">—</span></div>'
        '<script>(function(){'
        f'var E="{COUNTER_ENDPOINT}",P="china";'
        'var el=document.getElementById("view-count");if(!el)return;'
        'fetch(E+"/hit?page="+encodeURIComponent(P))'
        '.then(function(r){return r.ok?r.json():null;})'
        '.then(function(d){if(d&&typeof d.total==="number")el.textContent=d.total.toLocaleString("ko-KR");})'
        '.catch(function(){});})();</script>') if COUNTER_ENDPOINT else ""
    return (
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>중국 5개년 계획과 주식 수익 {today.date()}</title>'
        '<style>html,body{overflow-x:hidden}body{margin:0;padding:24px 20px 48px;background:#fff;max-width:100%;'
        '-webkit-font-smoothing:antialiased}img{max-width:100%}@media(max-width:640px){body{padding:16px 12px 32px}}</style>'
        '</head><body>'
        f'{inner}'
        '<div style="max-width:980px;margin:28px auto 0;padding-top:14px;border-top:1px solid #e5e5e5;'
        'font-family:-apple-system,\'Malgun Gothic\',sans-serif;font-size:12px;color:#8a9199">'
        f'생성 {datetime.now(KST).strftime("%Y-%m-%d %H:%M")} KST · '
        f'<a href="https://github.com/{github_pages.GITHUB_REPO}" style="color:#1a5490">저장소</a> · '
        '<a href="../" style="color:#1a5490">보고서 목록</a> · 연구·교육용입니다. 투자 자문이 아닙니다.'
        f'{counter}</div></body></html>')


def dump(plan_results, csi, rows15):
    """해설을 쓰기 위한 숫자 요약(표준 출력)."""
    for pr in plan_results:
        a = pr["agg"]
        print(f"\n== {pr['plan']['title']}  지수 {pct(a['bench_ret'])}  바구니 {pct(a['basket'])}  "
              f"이김 {a['beat']}/{a['n_b']}  초과중앙 {pct(a['median_excess'])}")
        for r in pr["rows"]:
            if r["missing"]:
                print(f"  {r['ticker']:<10} {r['name']:<22} 데이터 없음"); continue
            print(f"  {r['ticker']:<10} {r['name']:<22} {str(r['start'])}~{str(r['end'])} {'후' if r['late'] else ' '} "
                  f"수익 {pct(r['ret']):>7} 지수 {pct(r['bench_ret']):>7} 초과 {pct(r['ret'] - r['bench_ret']) if not math.isnan(r['bench_ret']) else '—':>7} "
                  f"MDD {pct(r['mdd']):>6} 지금 {pct(r['to_today']):>8}")
    print(f"\n== CSI300 {csi['start']}~{csi['end']} 누적 {pct(csi['ret'])} CAGR {pct(csi['cagr'],1)} vol {csi['vol']:.0%} "
          f"MDD {pct(csi['mdd'])} 고점대비 {pct(csi['from_peak'])} ({csi['peak_date']}) 원지수 {csi['index_level']:.0f}")
    print("  연도:", " ".join(f"{y}{'*' if p else ''}:{pct(v)}" for y, v, p in csi["yearly"]))
    for p in csi["peers"]:
        print(f"  peer {p['name']:<16} {pct(p['ret']):>7} CAGR {pct(p['cagr'],1):>7} vol {p['vol']:.0%} MDD {pct(p['mdd'])}")
    for p in csi["plans"]:
        print(f"  {p['label']:<16} {p['start']}~{p['end']} {pct(p['ret']):>7} 상해 {pct(p['bench']):>7} MDD {pct(p['mdd'])}")
    print("\n== 15차 후보")
    for r in rows15:
        if r["missing"]:
            print(f"  {r['ticker']:<10} {r['name']:<16} 없음"); continue
        print(f"  {r['ticker']:<10} {r['name']:<16} {r['price']:>10,.2f} YTD {pct(r['ytd']):>6} 1y {pct(r['r1']):>6} "
              f"3y {pct(r['r3']):>6} 5y {pct(r['r5']):>6} 5y고점 {pct(r['from_5y_peak']):>6}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="GITHUB_TOKEN으로 docs/china/에 발행")
    parser.add_argument("--no-fetch", action="store_true", help="캐시된 시세만 사용")
    parser.add_argument("--dump", action="store_true", help="숫자 요약을 출력")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    tickers = sorted({t for p in PLANS for t, *_ in p["picks"]} | {t for _, _, ts in PLAN15["themes"] for t in ts}
                     | {BENCH, CSI_ETF, CSI_IDX} | set(PEERS))
    print(f"시세 {len(tickers)}종목 수집", flush=True)
    prices = load_prices(tickers, args.out / "cache", fetch=not args.no_fetch)
    missing = [t for t in tickers if t not in prices]
    if missing:
        print("  데이터 없음:", missing)
    today = max(s.index[-1] for s in prices.values())
    fetched = str(today.date())

    plan_results = analyse_plans(prices, today)
    csi = analyse_csi(prices, today)
    rows15 = analyse_15(prices, today)
    if args.dump:
        dump(plan_results, csi, rows15)

    doc = page(render(plan_results, csi, rows15, today, fetched), today)
    out = args.out / "index.html"
    out.write_text(doc, encoding="utf-8")
    print("보고서:", out, f"{len(doc):,} bytes")

    if args.publish:
        tok = github_pages.token()
        sha = github_pages.publish(f"{PAGES_DIR}/index.html", doc, tok, f"china: {fetched}")
        print(f"GitHub Pages 발행: {PAGES_DIR}/index.html @ {sha}")
        print("→ https://namyikim.github.io/predict_stock/china/")


if __name__ == "__main__":
    main()
