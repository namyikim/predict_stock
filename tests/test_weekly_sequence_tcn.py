# -*- coding: utf-8 -*-
"""S03 — 작은 causal TCN 회귀 후보(weekly_sequence_tcn.py).

torch 가 없으면 건너뛴다(선택 의존성). 합성 데이터로 인과성·결정성·seed·스케일러·재개만 검증한다.
성능에 대해서는 아무것도 말하지 않는다.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


def synthetic(n=700, lookback=60, channels=5, seed=0, signal=0.3):
    """X: (n, lookback, 5) 잡음 + 마지막 시점 close_return 에 비례하는 y. 신호를 심어 학습이 되는지 본다."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 0.01, (n, lookback, channels)).astype(np.float32)
    y = (signal * X[:, -1, 0] + rng.normal(0, 0.002, n)).astype(np.float64)
    return X, y


class ImportTests(unittest.TestCase):
    def test_import_error_is_explicit_without_torch(self):
        source = (ROOT / "weekly_sequence_tcn.py").read_text(encoding="utf-8")
        self.assertIn("requirements.txt", source)
        self.assertIn("torch", source)


@unittest.skipUnless(HAVE_TORCH, "torch 없음(선택 의존성)")
class TcnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import weekly_sequence_tcn as tcn
        cls.tcn = tcn
        cls.config = tcn.TcnConfig(epochs=6, patience=3, filters=8, blocks=2)
        X, y = synthetic()
        cls.X, cls.y = X, y
        cls.train = slice(0, 500)
        cls.valid = slice(500, 600)
        cls.test = slice(600, 700)

    def fit(self, seed=42, X=None, y=None, **kw):
        X = self.X if X is None else X
        y = self.y if y is None else y
        return self.tcn.fit_tcn(X[self.train], y[self.train], X[self.valid], y[self.valid],
                                self.config, seed=seed, **kw)

    def test_model_is_small(self):
        model = self.tcn.build_model(self.config)
        self.assertLessEqual(self.tcn.count_parameters(model), 50_000)

    def test_causal_padding_ignores_the_future(self):
        # 입력의 마지막 시점 이후를 바꿔도(더 긴 시퀀스의 앞부분만 쓰면) 출력이 같아야 한다.
        model = self.tcn.build_model(self.config)
        model.eval()
        x = torch.randn(4, self.config.lookback + 10, self.config.channels)
        with torch.no_grad():
            full = self.tcn.forward_sequence(model, x)                     # (4, T) 시점별 출력
            short = self.tcn.forward_sequence(model, x[:, :self.config.lookback])
        torch.testing.assert_close(full[:, :self.config.lookback], short, atol=1e-6, rtol=0)

    def test_same_seed_is_deterministic(self):
        a = self.tcn.predict_tcn(self.fit(seed=42), self.X[self.test])
        b = self.tcn.predict_tcn(self.fit(seed=42), self.X[self.test])
        np.testing.assert_allclose(a, b, atol=1e-6, rtol=0)

    def test_different_seeds_differ(self):
        a = self.tcn.predict_tcn(self.fit(seed=42), self.X[self.test])
        b = self.tcn.predict_tcn(self.fit(seed=43), self.X[self.test])
        self.assertFalse(np.allclose(a, b, atol=1e-6))

    def test_scaler_uses_training_rows_only(self):
        fit = self.fit(seed=42)
        base = self.tcn.predict_tcn(fit, self.X[self.train][:50])
        extreme = self.X.copy()
        extreme[self.test] *= 100.0                                        # 시험 구간만 극단으로
        fit2 = self.fit(seed=42, X=extreme)
        again = self.tcn.predict_tcn(fit2, extreme[self.train][:50])
        np.testing.assert_allclose(base, again, atol=1e-6, rtol=0)
        np.testing.assert_allclose(fit.scaler["mean"], fit2.scaler["mean"], atol=1e-9, rtol=0)

    def test_learns_the_planted_signal_better_than_zero(self):
        fit = self.fit(seed=42)
        pred = self.tcn.predict_tcn(fit, self.X[self.test])
        y = self.y[self.test]
        self.assertLess(np.mean(np.abs(y - pred)), np.mean(np.abs(y)))     # 현재가 유지(0)보다 낫다

    def test_checkpoint_resume_matches_uninterrupted_run(self):
        path = Path(tempfile.mkdtemp()) / "ckpt.pt"
        full = self.fit(seed=42, checkpoint_path=path)
        path2 = Path(tempfile.mkdtemp()) / "ckpt.pt"
        with self.assertRaises(KeyboardInterrupt):
            self.fit(seed=42, checkpoint_path=path2, fail_after_epoch=3)
        self.assertTrue(path2.is_file())
        resumed = self.fit(seed=42, checkpoint_path=path2, resume=True)
        self.assertEqual(full.history, resumed.history)
        self.assertEqual(full.best_epoch, resumed.best_epoch)
        np.testing.assert_allclose(self.tcn.predict_tcn(full, self.X[self.test]),
                                   self.tcn.predict_tcn(resumed, self.X[self.test]), atol=1e-6, rtol=0)

    def test_early_stopping_keeps_the_best_state(self):
        fit = self.fit(seed=42)
        valid_mae = [h["valid_mae"] for h in fit.history]
        self.assertEqual(fit.best_epoch, int(np.argmin(valid_mae)) + 1)
        self.assertLessEqual(len(fit.history), self.config.epochs)

    def test_quick_training_is_fast_on_cpu(self):
        started = time.time()
        self.fit(seed=42)
        self.assertLess(time.time() - started, 60.0)


if __name__ == "__main__":
    unittest.main()
