# -*- coding: utf-8 -*-
"""작은 causal TCN 회귀 후보 — S03.

계획: guides/weekly-sequence-s03-implementation.md

torch 는 선택 의존성이다(requirements.txt 에 주석 `# torch==2.9.1`). 없으면 이 모듈은 명확한 ImportError 를
내고, 러너는 S03 단위를 skipped 로 기록한다.

회귀 모델이다. 5거래일 수익률 값을 예측하며 확률·구간을 만들지 않는다.

인과성: Conv1d 에 왼쪽만 채우는 causal padding 을 써서 시점 t 의 출력이 t 이후 입력을 보지 않는다.
결정성: seed 를 torch·numpy 에 걸고 CPU 결정적 알고리즘을 켠다. 같은 seed·설정·자료면 같은 결과.
스케일러: 채널별 표준화를 학습 구간만으로 적합한다. 타깃은 학습 구간 표준편차로 나눠 학습하고 예측 때
되곱한다(수익률 크기가 작아 그대로 두면 학습이 느리다).
재개: checkpoint_path 가 있으면 epoch 마다 상태를 원자적으로 저장하고, resume=True 면 거기서 잇는다.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    import torch
    from torch import nn
except ImportError as exc:  # pragma: no cover - 환경에 따라
    raise ImportError(
        "weekly_sequence_tcn 은 torch 가 필요합니다(선택 의존성). requirements.txt 의 주석 `# torch==2.9.1` 을 "
        "참고해 설치하세요. 없으면 S03 단위는 건너뜁니다.") from exc


@dataclass(frozen=True)
class TcnConfig:
    channels: int = 5
    lookback: int = 60
    blocks: int = 2
    filters: int = 16
    kernel: int = 3
    dropout: float = 0.1
    lr: float = 1e-3
    epochs: int = 30
    batch: int = 64
    patience: int = 5


@dataclass
class FitResult:
    model: object
    history: list
    best_epoch: int
    scaler: dict                     # {"mean": (C,), "std": (C,), "y_std": float}
    config: TcnConfig
    seed: int
    notes: dict = field(default_factory=dict)


class CausalConv1d(nn.Module):
    """왼쪽만 (kernel-1)*dilation 만큼 채우는 Conv1d. 출력 길이 = 입력 길이, 미래를 보지 않는다."""

    def __init__(self, cin, cout, kernel, dilation):
        super().__init__()
        self.pad = (kernel - 1) * dilation
        self.conv = nn.Conv1d(cin, cout, kernel, dilation=dilation)

    def forward(self, x):                                   # x: (B, C, T)
        return self.conv(nn.functional.pad(x, (self.pad, 0)))


class TcnBlock(nn.Module):
    def __init__(self, cin, cout, kernel, dilation, dropout):
        super().__init__()
        self.conv1 = CausalConv1d(cin, cout, kernel, dilation)
        self.conv2 = CausalConv1d(cout, cout, kernel, dilation)
        self.drop = nn.Dropout(dropout)
        self.skip = nn.Conv1d(cin, cout, 1) if cin != cout else nn.Identity()

    def forward(self, x):
        h = self.drop(torch.relu(self.conv1(x)))
        h = self.drop(torch.relu(self.conv2(h)))
        return torch.relu(h + self.skip(x))


class Tcn(nn.Module):
    """(B, T, C) → 시점별 출력 (B, T). 예측은 마지막 시점 값이다."""

    def __init__(self, config):
        super().__init__()
        layers, cin = [], config.channels
        for k in range(config.blocks):
            layers.append(TcnBlock(cin, config.filters, config.kernel, 2 ** k, config.dropout))
            cin = config.filters
        self.blocks = nn.Sequential(*layers)
        self.head = nn.Conv1d(cin, 1, 1)

    def forward(self, x):                                   # x: (B, T, C)
        h = self.blocks(x.transpose(1, 2))                  # (B, F, T)
        return self.head(h).squeeze(1)                      # (B, T)


def build_model(config):
    return Tcn(config)


def count_parameters(model):
    return int(sum(p.numel() for p in model.parameters()))


def forward_sequence(model, x):
    """(B, T, C) tensor → (B, T) 시점별 출력. 인과성 테스트용."""
    return model(x)


def _seed_everything(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)                                # 스레드 수가 다르면 합산 순서가 달라진다


def _fit_scaler(X_train, y_train):
    mean = X_train.reshape(-1, X_train.shape[-1]).mean(axis=0)
    std = X_train.reshape(-1, X_train.shape[-1]).std(axis=0)
    std = np.where(std > 0, std, 1.0)
    y_std = float(np.std(y_train)) or 1.0
    return {"mean": mean.astype(np.float64), "std": std.astype(np.float64), "y_std": y_std}


def _transform(X, scaler):
    return ((np.asarray(X, dtype=np.float64) - scaler["mean"]) / scaler["std"]).astype(np.float32)


def _save_checkpoint(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, tmp)
    tmp.replace(path)


def fit_tcn(X_train, y_train, X_valid, y_valid, config, seed, checkpoint_path=None, resume=False,
            fail_after_epoch=None):
    """학습. valid MAE 로 early stopping, best 상태 보존. checkpoint_path 로 epoch 단위 저장·재개."""
    _seed_everything(seed)
    scaler = _fit_scaler(np.asarray(X_train), np.asarray(y_train))
    Xt = torch.from_numpy(_transform(X_train, scaler))
    yt = torch.from_numpy((np.asarray(y_train, dtype=np.float64) / scaler["y_std"]).astype(np.float32))
    Xv = torch.from_numpy(_transform(X_valid, scaler))
    yv = np.asarray(y_valid, dtype=np.float64)

    model = build_model(config)
    optimiser = torch.optim.Adam(model.parameters(), lr=config.lr)
    generator = torch.Generator().manual_seed(seed)
    history, best_valid, best_state, best_epoch, start_epoch, bad = [], float("inf"), None, 0, 0, 0

    if resume and checkpoint_path and Path(checkpoint_path).is_file():
        ck = torch.load(checkpoint_path, weights_only=False)
        if ck.get("seed") != seed or ck.get("config") != config.__dict__:
            raise ValueError("체크포인트의 seed 또는 설정이 다릅니다 — 다른 실행의 체크포인트로 재개할 수 없습니다.")
        model.load_state_dict(ck["model_state"])
        optimiser.load_state_dict(ck["optimizer_state"])
        generator.set_state(ck["generator_state"])
        torch.set_rng_state(ck["torch_rng_state"])
        history, best_valid, best_state = ck["history"], ck["best_valid"], ck["best_state"]
        best_epoch, start_epoch, bad = ck["best_epoch"], ck["epoch"], ck["bad"]

    n = len(Xt)
    for epoch in range(start_epoch + 1, config.epochs + 1):
        model.train()
        order = torch.randperm(n, generator=generator)
        total = 0.0
        for start in range(0, n, config.batch):
            idx = order[start:start + config.batch]
            optimiser.zero_grad()
            out = model(Xt[idx])[:, -1]
            loss = torch.mean(torch.abs(out - yt[idx]))
            loss.backward()
            optimiser.step()
            total += float(loss) * len(idx)
        model.eval()
        with torch.no_grad():
            pv = model(Xv)[:, -1].numpy() * scaler["y_std"]
        valid_mae = float(np.mean(np.abs(yv - pv)))
        history.append({"epoch": epoch, "train_loss": total / n, "valid_mae": valid_mae})
        if valid_mae < best_valid - 1e-12:
            best_valid, best_epoch, bad = valid_mae, epoch, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if checkpoint_path:
            _save_checkpoint(checkpoint_path, {
                "seed": seed, "config": config.__dict__, "epoch": epoch, "bad": bad,
                "model_state": model.state_dict(), "optimizer_state": optimiser.state_dict(),
                "generator_state": generator.get_state(), "torch_rng_state": torch.get_rng_state(),
                "history": history, "best_valid": best_valid, "best_state": best_state, "best_epoch": best_epoch,
            })
        if fail_after_epoch is not None and epoch >= fail_after_epoch:
            raise KeyboardInterrupt(f"fail_after_epoch={fail_after_epoch}")
        if bad >= config.patience:
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return FitResult(model=model, history=history, best_epoch=best_epoch, scaler=scaler, config=config,
                     seed=seed, notes={"target_scaled_by_train_std": True, "parameters": count_parameters(model)})


def predict_tcn(fit, X):
    fit.model.eval()
    with torch.no_grad():
        out = fit.model(torch.from_numpy(_transform(X, fit.scaler)))[:, -1].numpy()
    return (out * fit.scaler["y_std"]).astype(np.float64)
