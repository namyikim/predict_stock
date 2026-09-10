# -*- coding: utf-8 -*-
"""P15 — 후보 모델의 사전 예측 등록 계약.

  1. 노트북이 후보(Candidate expanding)를 라이브 예측에 넣어 원장에 별도 모델명으로 남긴다.
  2. 후보는 대표 모델도, 앙상블 구성원도 아니다(운영 결과를 바꾸지 않는다).
  3. 후보 등록은 config_hash에 반영돼, 후보가 있는 실행과 없는 실행이 같은 설정으로 섞이지 않는다.
  4. 후보 학습 창은 예측일 전 전체(expanding)이고 예측일 당일은 포함하지 않는다.
"""
import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _code_cells():
    nb = json.loads((ROOT / "samsung_direction_model_colab.ipynb").read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


class CandidateRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = _code_cells()
        cls.text = "\n".join(cls.cells)

    def test_candidate_is_registered_in_live_predictions(self):
        self.assertIn('live_probs["Candidate expanding"]', self.text)
        self.assertIn("CANDIDATE_MODELS", self.text)

    def test_candidate_is_not_headline_or_ensemble_member(self):
        cfg = next(c for c in self.cells if "ENSEMBLE_MODELS =" in c and "HEADLINE_MODEL =" in c)
        tree = ast.parse(cfg)
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("ENSEMBLE_MODELS", "HEADLINE_MODEL"):
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertNotIn("Candidate expanding", values["ENSEMBLE_MODELS"])
        self.assertNotEqual(values["HEADLINE_MODEL"], "Candidate expanding")

    def test_candidate_is_part_of_config_hash(self):
        cell = next(c for c in self.cells if "experiment_config = {" in c)
        self.assertIn('"candidate_models"', cell)
        # config_hash는 experiment_config 전체를 해시한다
        self.assertIn("config_hash = hashlib.sha256(json.dumps(experiment_config", cell)

    def test_candidate_window_excludes_the_prediction_day(self):
        cell = next(c for c in self.cells if 'live_probs["Candidate expanding"]' in c)
        self.assertIn("pd.DatetimeIndex(dates) < prediction_date", cell,
                      "expanding 창이 예측일 당일을 포함하면 안 된다(엄격한 <)")

    def test_candidate_uses_market_only_features_like_the_headline(self):
        cell = next(c for c in self.cells if 'live_probs["Candidate expanding"]' in c)
        block = cell[cell.index("CANDIDATE_MODELS = "):cell.index('live_probs["Candidate expanding"]')]
        self.assertIn("market_X", block)
        self.assertIn("market_feature_idx", cell)


if __name__ == "__main__":
    unittest.main()
