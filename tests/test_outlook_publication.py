"""최종 병합 원장과 파생 화면을 같은 커밋에서 검증한다(2026-10-02)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import github_pages as gp
import outlook_ledger as ol
import refresh_longterm_tab as rt
import build_metals_report as mr
from test_publish_batch import FakeRepo


class PublicationTests(unittest.TestCase):
    def test_deferred_unchanged_tab_does_not_create_a_commit(self):
        content = '<h3>장기 전망</h3><div id="longterm-summary">그대로</div>'
        original = '<section class="rtab-panel" id="longterm">' + content + '</section>'
        repo = FakeRepo({"docs/samsung/index.html": original})
        head = repo.head
        with patch.object(gp, "_repo_api", repo.api), patch.object(
                gp, "fetch_with_sha", return_value=(original, gp.blob_sha(original))):
            with gp.batch("변경 없음", "test"):
                rt.publish_tab("samsung", lambda: content, "", "test")
        self.assertEqual(repo.head, head)


    def test_retries_rebuild_fragment_and_tab_from_final_ledger(self):
        rows = [{"record_id": str(i), "series": "cli_kor", "kind": "value", "unit": "index",
                 "label": "선행지수", "point": 100., "info_as_of": "2026-09", "target_period": f"2027-0{i}"}
                for i in (1, 2, 3)]
        ours, _ = ol.record(ol.read_ledger_text(""), rows[:1])
        remote, _ = ol.record(ours.copy(), rows[1:2])
        final, _ = ol.record(remote.copy(), rows[2:])
        final, _ = ol.score(final, {"cli_kor": lambda row: 102.})
        path = "forecast_history/samsung/outlook_log.csv"
        original = '<section class="rtab-panel" id="longterm"><div id="longterm-summary">old</div></section>'
        repo = FakeRepo({path: ol.to_csv(ours), "docs/samsung/index.html": original})
        state = ol.PendingLedger(ours)
        with patch.object(gp, "_repo_api", repo.api), patch("time.sleep"), patch.object(
                gp, "fetch_with_sha", return_value=(original, gp.blob_sha(original))):
            with gp.batch("원장·화면 동시 발행", "test"):
                gp.publish(path, ol.to_csv(ours), "test", "원장", expected_sha=gp.blob_sha(ol.to_csv(ours)), merge=state.merge)
                gp.publish("docs/samsung/outlook.html", lambda: ol.render(state.frame), "test", "채점")
                rt.publish_tab("samsung", lambda: '<div id="longterm-summary"></div>' + ol.render(state.frame), "", "test")
                metal = {"outlook": ours, "outlook_state": state}
                gp.publish("docs/metals/index.html", lambda: mr.render_longterm_asset("gold", metal), "test", "금속")
                repo.push_other({path: ol.to_csv(remote)})
                repo.before_ref_update = lambda: repo.push_other({path: ol.to_csv(final),
                    "docs/samsung/index.html": original + '<aside>다른 실행</aside>'})
        files = repo.files()
        merged = ol.read_ledger_text(files[path])
        self.assertEqual(len(merged), 3)
        self.assertEqual((merged.status == "scored").sum(), 3)
        expected = ol.render(merged)
        self.assertEqual(files["docs/samsung/outlook.html"], expected)
        self.assertIn(expected, rt.without_stamps(files["docs/samsung/index.html"]))
        self.assertIn("다른 실행", files["docs/samsung/index.html"])
        self.assertIn(ol.render(merged, heading="h4", title="지난 장기 전망은 맞았나",
                                footnote="금 장기 전망 탭에 숫자로 나온 전망을"), files["docs/metals/index.html"])
        pd.testing.assert_frame_equal(ours, ol.read_ledger_text(ol.to_csv(ours)), check_dtype=False)
