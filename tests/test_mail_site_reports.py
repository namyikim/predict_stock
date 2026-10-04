"""전체 보고서 구독 메일에 공개 메뉴의 내용과 서로 다른 기준일을 보존한다."""
import unittest
from pathlib import Path
from tools import notify_report_update as mail


class SiteReportTests(unittest.TestCase):
    def test_news_has_topic_count_and_headline_without_script(self):
        page='<h2>AI 뉴스</h2><div>2026-10-04 20:28 KST 기준 · 최근 36시간 헤드라인 239건</div><script>나쁜 본문</script><table><tr><td>1</td><td><div>엔비디아</div><div>매체 21곳 · 기사 24건</div><ul><li>새 제품 발표 · 매체명</li></ul></td></tr></table>'
        result=mail.build_site_report('ai_news',page)
        self.assertIn('2026-10-04',result['as_of'])
        self.assertIn('엔비디아',' '.join(result['lines']))
        self.assertIn('매체 21곳',' '.join(result['lines']))
        self.assertIn('새 제품 발표',' '.join(result['lines']))
        self.assertNotIn('나쁜 본문',str(result))

    def test_all_public_categories_are_present_and_missing_is_explicit(self):
        def read(path):raise FileNotFoundError(path)
        reports=mail.read_site_reports(read)
        self.assertEqual({r['key'] for r in reports},{'metals','china','macro','ai_news','robot_news','trends','interest'})
        self.assertTrue(all('미확인' in r['as_of'] for r in reports))
        self.assertTrue(all('확인하지 못' in ' '.join(r['lines']) for r in reports))

    def test_published_menus_have_real_content_with_bounds(self):
        root=Path(__file__).resolve().parents[1]
        reports=mail.read_site_reports(lambda p:(root/p).read_text())
        for r in reports:
            with self.subTest(menu=r['key']):
                self.assertNotIn('미확인',r['as_of'])
                self.assertTrue(1<=len(r['lines'])<=5)
                self.assertTrue(all(len(x)<=700 for x in r['lines']))
                self.assertFalse(any('확인하지 못' in x for x in r['lines']))

    def test_summary_preserves_prediction_limits_and_selection_bias(self):
        metals='<div>생성 2026-10-04 20:00 KST</div><h3>1. 금 · 단기 예측</h3><div>기준 봉 2026-10-02 · 종가 $100</div><div>방향 판정: 보류. 과거 빈도와 구분되지 않습니다.</div><h3>2. 은 · 단기 예측</h3><div>기준 봉 2026-10-02 · 종가 $10</div><div>방향 판정: 보류. 예측으로 쓰지 않습니다.</div>'
        report=mail.build_site_report('metals',metals)
        self.assertIn('금: 방향 판정: 보류',' '.join(report['lines']))
        self.assertIn('은: 방향 판정: 보류',' '.join(report['lines']))
        china='<div>2026-10-04 20:00 KST</div><div>정책 종목이 지수를 이긴 비율 80% 초과수익 중앙값 +100%</div><p>숫자만 보면 높지만 종목을 뒤에서 골랐기 때문에 부풀려져 있습니다.</p>'
        report=mail.build_site_report('china',china)
        self.assertIn('뒤에서',' '.join(report['lines']))
