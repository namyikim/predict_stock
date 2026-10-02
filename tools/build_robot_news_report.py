# -*- coding: utf-8 -*-
"""최신 로봇 트렌드 및 뉴스 — AI 뉴스와 같은 방식으로 로봇 관련 헤드라인을 모아 순위로 보여 준다(2026-10-02 요청).

모으고 묶는 코드는 build_ai_news_report.py 를 그대로 쓴다. 여기에는 주제 설정(검색어·제목·빼는 말)만 있다.
기사 내용을 요약하거나 해석하지 않는다 — 제목·출처·시각을 그대로 옮겨 원문으로 링크한다.

    python tools/build_robot_news_report.py --out runs/robot_news
    python tools/build_robot_news_report.py --out runs/robot_news --publish
"""
import build_ai_news_report as base

# 검색어. AI 뉴스처럼 겹치게 두고 중복은 제목으로 걸러낸다.
QUERIES = (
    "로봇", "휴머노이드 로봇", "산업용 로봇", "협동로봇", "서비스 로봇", "물류 로봇",
    "로봇 자동화", "피지컬 AI", "테슬라 옵티머스", "보스턴다이내믹스",
)
# 어느 로봇 헤드라인에나 나오는 말. 순위에 오르면 무엇이 화제인지 알려 주지 않는다.
STOPWORDS = frozenset("로봇 로보틱스 robot robotics Robot Robotics 자동화 차세대".split())

ROBOT_TOPIC = {
    "key": "robot_news", "title": "최신 로봇 트렌드 및 뉴스", "eyebrow": "ROBOT TRENDS &amp; NEWS", "subject": "로봇",
    "queries": QUERIES, "stopwords": STOPWORDS, "commit": "robot-news", "label": "로봇 뉴스",
}


def build_html(ranked, now, total, failed):
    return base.build_html(ranked, now, total, failed, ROBOT_TOPIC)


if __name__ == "__main__":
    base.main(ROBOT_TOPIC)
