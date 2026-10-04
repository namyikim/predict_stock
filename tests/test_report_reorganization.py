import unittest
import report_html as rh


class ReorganizationTests(unittest.TestCase):
    def test_move_scorecard_and_scores_keep_prediction(self):
        page=('<div><section id="easy-summary"><h3>한눈에 보는 쉬운 요약</h3>예상 시초가'
              '<!--SCORECARD_START--><div>지금까지 성적 53%</div><!--SCORECARD_END--></section>'
              '<h3>2026-09-23 예측 vs 실제</h3>실제 비교'
              '<h3>직전 거래일 장 회고 — 2026-09-23</h3>지난 뉴스'
              '<h3>1. 다음 거래일 방향</h3>예측 방향'
              '<h3>3. 이 예측을 어떻게 읽어야 하는가</h3>AUC 검증'
              '<h3>이 모델의 예측 성적</h3><h4>자동 판정</h4>과거 검증</div>')
        out=rh.tabify_sections(page)
        from report_html import panels, refresh_layout
        ps=panels(out)
        self.assertIn('예상 시초가',ps[0]['inner'])
        self.assertNotIn('지금까지 성적',ps[0]['inner'])
        self.assertNotIn('실제 비교',ps[0]['inner'])
        score=next(p['inner'] for p in ps if '실제 비교' in p['inner'])
        self.assertLess(score.index('지금까지 성적'),score.index('실제 비교'))
        # 장 회고는 '오늘의 장 예측' 탭에 둔다(2026-09-28 요청). 예측 성적 탭에는 없다.
        self.assertIn('지난 뉴스',ps[0]['inner'])
        self.assertNotIn('지난 뉴스',score)
        self.assertIn('1. 실제 발행 후 누적 성적',score)
        self.assertEqual(out.count('SCORECARD_START'),1)
        self.assertFalse(rh.tab_structure_problems(out))
        self.assertEqual(refresh_layout(out),out)

    def test_each_panel_numbering_restarts(self):
        from report_html import number_headings
        text='<h3 id="x">7. 가</h3><h4>8. 나</h4><h3>1-1. 다</h3><h4>라</h4>'
        out=number_headings(text)
        self.assertEqual(out,'<h3 id="x">1. 가</h3><h4>1.1 나</h4><h3>2. 다</h3><h4>2.1 라</h4>')
        self.assertEqual(number_headings(out),out)
