"""노트북의 구조·문법 검증.

여기서는 "코드가 파싱되는가", "설정 스위치가 존재하는가", "누수 방지 장치가
코드에 남아 있는가" 정도만 확인한다. 실제 동작(누수가 실제로 없는지, 폴드가
겹치지 않는지 등)은 test_pipeline_behavior.py에서 합성 데이터로 검증한다.

문자열 매칭만으로는 누수를 잡을 수 없다는 점을 기억할 것:
`.shift(1)` 한 줄을 지워도 이 파일의 테스트는 전부 통과한다.
"""

import ast
import json
import unittest
from pathlib import Path

NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "samsung_direction_model_colab.ipynb"


def strip_magics(source):
    return "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith(("%", "!"))
    )


class NotebookStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        cls.code_cells = [
            "".join(cell.get("source", []))
            for cell in cls.notebook["cells"]
            if cell.get("cell_type") == "code"
        ]
        cls.source = "\n".join(cls.code_cells)

    def test_every_code_cell_is_valid_python(self):
        for index, cell in enumerate(self.code_cells):
            with self.subTest(cell=index):
                ast.parse(strip_magics(cell))

    def test_notebook_stays_small_enough_to_review(self):
        """Colab에서 저장하면 실행 출력이 함께 커밋된다. 그 자체는 문제가 아니지만,
        base64 이미지가 쌓이면 저장소가 비대해지고 diff를 읽을 수 없게 된다.
        출력을 금지하는 대신 크기 상한만 둔다(출력을 비우려면 `nbstripout`)."""
        size_mb = NOTEBOOK_PATH.stat().st_size / 1024 / 1024
        self.assertLess(size_mb, 3.0, f"노트북이 {size_mb:.1f}MB입니다. 출력을 정리하세요.")

    def test_no_single_output_is_enormous(self):
        """한 셀이 저장소를 혼자 부풀리는 것(대개 base64 이미지)을 막는다.

        나머지 종목을 이어서 돌리는 구동 셀만은 예외다. 그 셀 하나에 파이프라인
        전체의 출력이 남은 종목 수만큼 담기므로(한 종목당 약 600KB) 상한을 따로 둔다.
        저장소 전체가 부푸는 것은 노트북 크기 상한이 계속 잡아준다."""
        for index, cell in enumerate(self.notebook["cells"]):
            if cell.get("cell_type") != "code":
                continue
            source = "".join(cell.get("source", []))
            budget = 400
            if "_remaining = list(RUN_TARGETS[1:])" in source:
                budget = 2000
            payload = sum(len(json.dumps(o)) for o in cell.get("outputs", []))
            with self.subTest(cell=index):
                self.assertLess(payload / 1024, budget,
                                f"셀 {index}의 출력이 {payload/1024:.0f}KB입니다(상한 {budget}KB).")

    def test_report_warns_when_it_is_old(self):
        """자동 발행이 실패하면 Pages에는 옛 보고서가 남는다. 보는 쪽에서 알 수 있어야 한다."""
        self.assertIn('id="stale-note"', self.source)
        self.assertIn("prediction_date.date().isoformat()", self.source)

    def test_configuration_switches_exist(self):
        for setting in (
            "TARGET_MODE", "BAND_MODE", "VOL_BAND_MULT", "LIVE_OPEN_PRICE",
            "ENSEMBLE_MODELS", "RUN_TRANSFORMER", "RUN_KRONOS",
            "COST_BP", "BOOTSTRAP_B", "USE_DATA_CACHE",
        ):
            with self.subTest(setting=setting):
                self.assertIn(f"{setting} = ", self.source)

    def test_kronos_is_opt_in(self):
        """Kronos는 기준선보다 나쁘고 무거운 의존성의 유일한 원인이므로 기본 꺼짐."""
        self.assertIn("RUN_KRONOS = False", self.source)

    def test_kospi200_is_not_used(self):
        """^KS200은 KOSPI와 중복이면서 Yahoo 공백이 학습 구간을 잘라낸 원인이었다.
        (주석에서 이유를 설명하는 것은 허용하고, 실제로 쓰이는지만 본다.)"""
        self.assertNotIn('"kospi200"', self.source)
        self.assertNotIn('"^KS200"', self.source)
        self.assertNotIn("kospi200_ret", self.source)

    def test_stacking_and_performance_weighting_are_gone(self):
        """단순 평균보다 나빴던 두 앙상블 장치가 되살아나지 않았는지 확인한다."""
        self.assertNotIn("stack_meta_full", self.source)
        self.assertNotIn("STACK_BASE_MODELS", self.source)
        self.assertNotIn("np.exp(-2.0", self.source)

    def test_gap_session_decomposition_is_reported(self):
        """이 노트북의 핵심 한계(예측력의 출처가 갭)를 매 실행마다 드러내야 한다."""
        for token in ("decomposition_row", "auc_gap", "auc_session", "session_bp_net"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_confidence_intervals_are_reported(self):
        for token in ("block_bootstrap_ci", "paired_delta_ci", "bal_acc_lo", "log_loss_hi"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_open_price_forecast_is_separate_and_scored_on_the_open(self):
        # 갭(시가) 예측은 종가 예측과 다른 kind로 원장에 남고, 시가 변동성은 d-1까지만 쓴다.
        self.assertIn('"kind": "open", "horizon_days": 1', self.source)
        self.assertIn("sam_gap.rolling(20).std().shift(1)", self.source)
        self.assertIn('"predicted_open"', self.source)
        self.assertIn("gap_sign_auc", self.source)

    def test_report_labels_open_and_close_predictions_explicitly(self):
        self.assertIn("시초가예측", self.source)
        self.assertIn("종가예측", self.source)

    def test_report_reviews_prospective_ledger(self):
        # 보고서는 원장의 사전 예측만 채점한 결과를 보여주고, 축소 전 원시 예측을 원장에 남긴다.
        self.assertIn("review_ledger(daily, sam", self.source)
        self.assertIn("ledger_section_html(ledger_review", self.source)
        self.assertIn("LEDGER_SECTION_START", self.source)   # 오후 갱신 도구가 바꿔 끼울 경계
        self.assertIn('"raw_predicted_return": stats["raw_point"]', self.source)
        self.assertIn('"raw_predicted_return": open_forecast_stats["raw_point"]', self.source)

    def test_macro_fallback_is_pulled_independently_of_ledger_sync(self):
        # 보관본 내려받기는 '읽기'라서 발행 여부·토큰과 무관해야 하고, 원장 조회 실패에
        # 휩쓸리면 안 된다(2026-09-09: KOSIS가 막힌 날 실행이 통째로 죽었다).
        source = self.source
        pull = source.index("월별 지표 보관본:")
        sync = source.index("if SYNC_LEDGER_TO_GITHUB:\n    _token = github_token()")
        self.assertLess(pull, sync, "보관본 내려받기가 원장 동기화 블록보다 앞에 있어야 한다")
        self.assertIn("raw.githubusercontent.com", source)      # 토큰 없이 받는다
        self.assertIn('fallback_dir=STORAGE_ROOT / "macro_fallback"', source)

    def test_macro_cache_upload_is_independent_of_ledger_publishing(self):
        # 한국 정부 API가 해외 IP(Actions)에서 막히므로, 한국에서 돌린 실행이 보관본을 갱신해야 한다.
        # 그 갱신은 원장 발행과 무관해야 Colab에서 그냥 전체 실행만 해도 보관본이 채워진다.
        source = self.source
        cache_block = source.index("SYNC_MACRO_CACHE = ")
        ledger_block = source.index("if SYNC_LEDGER_TO_GITHUB:\n    try:")
        self.assertLess(cache_block, ledger_block)
        # 이번 실행에서 직접 받은 자료만 올린다(보관본을 보관본으로 덮어쓰지 않는다).
        self.assertIn('macro_info.get("fresh")', source)
        self.assertIn('nsi_info.get("source") in ("ECOS_API", "user_csv")', source)
        self.assertIn('flow_info.get("fresh")', source)
        # 원장 파일은 이 블록에서 올리지 않는다.
        block = source[cache_block:ledger_block]
        self.assertNotIn("GITHUB_LEDGER_DIR", block)

    def test_after_close_run_only_scores(self):
        # 장 마감 후 실행은 워크포워드를 다시 돌리지 않고 채점·절 교체만 한다.
        # 예측을 기록하면 정보가 적은 오후 예측이 아침 예측을 밀어낸다(원장은 최초 사전 예측만 집계).
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/afternoon-report.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "10 7 * * 1-5"', workflow)        # 16:10 KST, 미완성 봉 기준(15:40) 이후
        self.assertIn("build_afternoon_update.py", workflow)
        self.assertNotIn("run_notebook.py", workflow)          # 무거운 재계산을 하지 않는다
        # 노트북에도 예측을 기록하지 않는 스위치가 남아 있다(수동 실행용).
        self.assertIn("PREDICT_STOCK_RECORD_FORECAST", self.source)
        self.assertIn("if RECORD_FORECAST:\n    all_log = append_forecasts", self.source)

    def test_bootstrap_size_can_be_reduced_for_automation(self):
        self.assertIn('os.environ.get("PREDICT_STOCK_BOOTSTRAP_B")', self.source)

    def test_price_forecast_is_gated_on_a_baseline_test(self):
        for token in ("beats_baseline", "oof_slope", "band_coverage", "mae_diff_lo"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_run_identity_is_exported(self):
        for token in ('"run_id"', '"data_snapshot_hash"', '"versions"', "forecast_log.csv"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_final_summary_report_exists(self):
        """마지막 셀이 결과를 한 화면으로 정리해 주어야 한다."""
        for token in ("def build_summary()", "종합 보고서", "auc_session", "자동 판정"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)
        # 판정은 사람이 아니라 코드가 내려야 한다.
        self.assertIn("session_tradeable", self.source)

    def test_staleness_guard_exists(self):
        """보조 시계열의 공백이 최근 학습 구간을 조용히 잘라내지 못하게 한다."""
        self.assertIn("staleness_days", self.source)
        self.assertIn("assert staleness_days", self.source)


if __name__ == "__main__":
    unittest.main()
