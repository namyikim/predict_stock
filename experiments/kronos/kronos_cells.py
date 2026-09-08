# -*- coding: utf-8 -*-
"""Kronos-small 파운데이션 모델 실험 — 노트북에서 걷어낸 셀들의 보존본.

2026-09에 노트북에서 제거했다. zero-shot 성능이 '항상 보합' 기준선보다 나빴고(log loss 1.87 vs
1.10), 무거운 의존성(einops·huggingface_hub·safetensors·torch)의 유일한 원인이었으며, 항상 꺼져
있었다. 다시 시도하려면 이 파일의 셀을 노트북 7절 자리에 되돌리고 requirements의 선택 항목을 켠다.
아래는 제거 당시의 셀 내용 그대로다(마크다운은 주석으로).
"""

# ## 7. 금융 시계열 파운데이션 모델 Kronos-small (기본 꺼짐)
# 
# Kronos는 OHLCV를 금융시장 전용 토큰으로 변환한 뒤 다음 K-line을 생성합니다. 삼성전자에 별도 학습하지 않은 **zero-shot** 성능을 확인합니다.
# 
# **기본값이 `RUN_KRONOS = False`인 이유**: 60일 평가에서 log loss 1.87 / Brier 1.07로 "항상 보합" 기준선(1.10 / 0.67)보다 나빴고, 이미 앙상블에서도 제외되어 있었습니다. 반면 이 섹션 하나 때문에 git clone + HuggingFace 가중치라는 무거운 의존성이 붙습니다.
# 
# 켜서 실행하려면 `RUN_KRONOS = True`로 두되, 다음을 유의하세요.
# 
# - 8샘플 Monte Carlo 빈도를 확률로 쓰므로 log loss는 표본 잡음에 지배됩니다.
# - 60일 평가로는 예측력의 유무를 판정할 수 없습니다(무작위 예측의 60일 balanced accuracy 표준편차가 약 0.065). 최소 250일 이상을 권장합니다.

# %% ---------------------------------------------------------------------------

kronos_predictor = None
kronos_predictions = pd.DataFrame()

if RUN_KRONOS:
    if not TORCH_AVAILABLE:
        print("torch가 없어 Kronos를 건너뜁니다.")
        RUN_KRONOS = False
    else:
        try:
            import subprocess
            subprocess.run(
                "pip -q install einops==0.8.1 huggingface_hub==0.33.1 tqdm==4.67.1 safetensors==0.6.2",
                shell=True, check=False,
            )
            if not Path("/content/Kronos").exists():
                subprocess.run(
                    "git clone --depth 1 https://github.com/shiyu-coder/Kronos.git /content/Kronos",
                    shell=True, check=False,
                )
            if "/content/Kronos" not in sys.path:
                sys.path.append("/content/Kronos")
            from model import Kronos, KronosTokenizer, KronosPredictor

            kronos_device = "cuda:0" if torch.cuda.is_available() else "cpu"
            tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
            kronos_model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
            kronos_predictor = KronosPredictor(kronos_model, tokenizer, device=kronos_device, max_context=512)
            print("Kronos loaded on", kronos_device)
        except Exception as exc:  # ImportError만으로는 부족(HF 레이트리밋/레포 변경 등)
            print("⚠️ Kronos 로드 실패, 건너뜁니다:", exc)
            RUN_KRONOS = False
else:
    print("RUN_KRONOS=False: Kronos 섹션을 건너뜁니다. "
          "(zero-shot 성능이 '항상 보합' 기준선보다 나빴고, 무거운 의존성의 유일한 원인입니다.)")

# %% ---------------------------------------------------------------------------

def kronos_probability_for_date(target_date, mc_samples=KRONOS_MC_SAMPLES):
    target_date = pd.Timestamp(target_date).normalize()
    hist = sam.loc[sam.index < target_date].tail(KRONOS_LOOKBACK).copy()
    if len(hist) < 100:
        raise ValueError("Kronos context is too short")

    band_value = float(model_df.loc[target_date, "band"]) if target_date in model_df.index else live_band

    x_df = hist[["open", "high", "low", "close", "volume"]].copy()
    x_df["volume"] = x_df["volume"].fillna(0).clip(lower=0)
    x_timestamp = pd.Series(hist.index)
    y_timestamp = pd.Series([target_date])
    previous_close = float(hist["close"].iloc[-1])
    if TARGET_MODE == "open_to_close":
        if target_date in sam.index:
            reference_price = float(sam.loc[target_date, "open"])
        else:
            reference_price = float(LIVE_OPEN_PRICE)
    else:
        reference_price = previous_close

    # 전역 RNG를 오염시키지 않도록 상태를 저장했다가 복원한다.
    py_state, np_state = random.getstate(), np.random.get_state()
    torch_state = torch.get_rng_state()
    cuda_state = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
    try:
        sampled_returns = []
        for sample_no in range(mc_samples):
            sample_seed = SEED + sample_no
            random.seed(sample_seed)
            np.random.seed(sample_seed)
            torch.manual_seed(sample_seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(sample_seed)
            pred_df = kronos_predictor.predict(
                df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
                pred_len=1, T=0.8, top_p=0.9, sample_count=1,
            )
            sampled_returns.append(float(pred_df["close"].iloc[0]) / reference_price - 1)
    finally:
        random.setstate(py_state)
        np.random.set_state(np_state)
        torch.set_rng_state(torch_state)
        if cuda_state is not None:
            torch.cuda.set_rng_state_all(cuda_state)

    sampled_returns = np.asarray(sampled_returns)
    counts = np.array([
        np.sum(sampled_returns < -band_value),
        np.sum((sampled_returns >= -band_value) & (sampled_returns <= band_value)),
        np.sum(sampled_returns > band_value),
    ], dtype=float)
    probs = (counts + 0.5) / (counts.sum() + 1.5)   # Jeffreys smoothing
    return probs, sampled_returns


if RUN_KRONOS and kronos_predictor is not None:
    if KRONOS_EVAL_DAYS < 250:
        print(f"⚠️ KRONOS_EVAL_DAYS={KRONOS_EVAL_DAYS}일로는 예측력의 유무를 판정할 수 없습니다"
              f" (무작위 예측의 60일 balanced accuracy 표준편차가 약 0.065). 참고용으로만 보세요.")
    all_test_dates = pd.DatetimeIndex(sorted(predictions["date"].unique()))
    kronos_dates = all_test_dates.intersection(sam.index)[-KRONOS_EVAL_DAYS:]
    rows = []
    for target_date in tqdm(kronos_dates, desc="Kronos zero-shot backtest"):
        try:
            probs_k, sampled_ret = kronos_probability_for_date(target_date)
            rows.append(prediction_frame(
                "Kronos-small", [target_date], [int(model_df.loc[target_date, "target"])],
                probs_k.reshape(1, -1), fold_id="zero-shot",
                extra={"pred_return_mean": [float(sampled_ret.mean())],
                       "pred_return_std": [float(sampled_ret.std(ddof=0))]},
            ))
        except Exception as exc:
            print(f"⚠️ {target_date.date()} Kronos 실패: {exc}")
    PREDICTION_PARTS["kronos"] = pd.concat(rows, ignore_index=True) if rows else None
else:
    PREDICTION_PARTS["kronos"] = None

predictions = assemble_predictions()
if PREDICTION_PARTS.get("kronos") is not None:
    display(summarize_predictions(PREDICTION_PARTS["kronos"], with_ci=False).style.format("{:.4f}", na_rep="—"))
