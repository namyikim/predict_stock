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
        for index, cell in enumerate(self.notebook["cells"]):
            if cell.get("cell_type") != "code":
                continue
            payload = sum(len(json.dumps(o)) for o in cell.get("outputs", []))
            with self.subTest(cell=index):
                self.assertLess(payload / 1024, 400, f"셀 {index}의 출력이 {payload/1024:.0f}KB입니다.")

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

    def test_price_forecast_is_gated_on_a_baseline_test(self):
        for token in ("beats_baseline", "oof_slope", "band_coverage", "mae_diff_lo"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_run_identity_is_exported(self):
        for token in ('"run_id"', '"data_snapshot_hash"', '"versions"', "forecast_log.csv"):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_staleness_guard_exists(self):
        """보조 시계열의 공백이 최근 학습 구간을 조용히 잘라내지 못하게 한다."""
        self.assertIn("staleness_days", self.source)
        self.assertIn("assert staleness_days", self.source)


if __name__ == "__main__":
    unittest.main()
