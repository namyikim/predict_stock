# S03 작은 causal TCN 회귀 후보 실행 계획

**Goal:** S02 러너의 시퀀스·폴드 위에 작은 causal TCN 회귀 후보를 얹는다. seed 42·43·44를 모두 학습·보고하고, CPU quick 학습·체크포인트 저장·중단 재개를 테스트로 고정한다.
**Architecture:** 모델은 `weekly_sequence_tcn.py`(torch 선택 의존성, 없으면 ImportError를 명확한 메시지로), 러너는 `tools/run_weekly_sequence.py`에 `--task S03`을 추가한다. S02 단위(snapshot·sequences·folds·baselines)를 그대로 재사용한다. 운영 코드는 건드리지 않는다.
**Tech Stack:** Python, NumPy, pandas, PyTorch(CPU), unittest. GPU를 요구하지 않는다.
**Spec:** [주간 모델 설계](weekly-sequence-model-plan.md), [S02](weekly-sequence-s02-implementation.md)

## 승인·진행 상태

- [x] S02 완료(main 8cc8c3d5).
- [x] S03 상세 실행 계획 작성(이 문서, 2026-09-22).
- [x] 상세 실행 계획 검토 — 사용자 '계속 진행' 지시(2026-09-22).
- [x] S03 코드 구현·검증 — TCN 테스트 10개, 러너 테스트 +4개.
- [x] S03 완료 기록 및 main 반영 확인 — ea4cd345, 원격에서 파일 셋 재확인(2026-09-22).

이 문서는 실행 계획이다. 아래 모듈·단위·테스트는 2026-09-22 구현됐다(진행 기록은 설계 문서 참고).

## Global Constraints

- torch는 선택 의존성이다(requirements.txt에 주석으로만 있음). 없으면 S03 단위는 `skipped: torch missing`으로 기록하고 S02 산출물은 그대로 낸다. 테스트는 torch가 없으면 skipTest.
- TCN은 **회귀**다. 5일 수익률 값을 예측하며 확률·구간을 만들지 않는다.
- seed 42·43·44를 **모두** 학습하고 모두 보고한다. 최고 seed만 고르지 않는다. 표에는 seed별 값과 평균을 함께 둔다.
- 설정 선택·early stopping은 **내부 검증**(각 폴드 학습 구간의 마지막 6개월, purge 적용)으로만 한다. 시험 폴드를 보고 고르지 않는다.
- 스케일러(채널별 표준화)는 폴드 학습 구간만으로 적합한다(S02 Ridge와 같은 원칙). 시험 구간 값을 바꿔도 학습 통계 불변(테스트).
- 학습 라벨 만기와 시험 정보 마감의 겹침 제거는 S02 폴드가 이미 보장한다. 내부 검증 블록도 같은 purge를 쓴다.
- CPU quick 모드는 몇 분 안에 끝나야 한다: 채널 5, 블록 2, 필터 16, 커널 3, 팽창 1·2, epoch ≤ 30. full 모드 설정은 별도 상수로 두되 이 단계에서 돌리지 않는다.
- 모델 가중치·원시 입력은 Git에 올리지 않는다. 체크포인트는 `<storage>/<target>/weekly_sequence/S03/<run_id>/`에 두고 manifest에 경로·해시만 남긴다. `.gitignore`에 `runs/`가 있는지 확인한다.
- 이 단계는 **합성 데이터로 코드 동작만** 증명한다. 실제 성능·채택 판단은 S04다.

## Review Focus

1. 인과성: TCN이 미래 시점을 보지 않는가(causal padding — 왼쪽만 채움). 입력 마지막 시점 이후 값을 바꿔도 출력 불변(테스트).
2. 내부 검증 purge: early stopping 블록의 라벨 만기가 그 블록 시작 전인가.
3. 재개: epoch 중간 중단 → 같은 seed·설정으로 재개 시 체크포인트에서 이어지고 최종 결과가 처음부터 돌린 것과 같은가(결정성).
4. seed 보고: metrics.csv에 seed 열이 있고 세 seed가 모두 있는가.
5. torch 부재: 러너와 테스트가 조용히 실패하지 않고 명시적으로 건너뛰는가.

## Task 1: 모델 모듈 (weekly_sequence_tcn.py)

Interfaces:
- `TcnConfig(channels=5, lookback=60, blocks=2, filters=16, kernel=3, dropout=0.1, lr=1e-3, epochs=30, batch=64, patience=5)`
- `build_model(config) -> torch.nn.Module` — Conv1d 블록(causal padding, dilation 1·2·4…, residual, ReLU), 마지막 시점의 표현 → Linear(1). 파라미터 수를 `count_parameters()`로 돌려준다(작아야 한다: 수천~수만).
- `fit_tcn(X_train, y_train, X_valid, y_valid, config, seed, checkpoint_path=None, resume=False, fail_after_epoch=None) -> FitResult(model, history, best_epoch, scaler)`
  - 채널별 표준화는 X_train만으로. 타깃은 그대로(수익률 스케일이 작아 학습이 느리면 y를 학습 구간 표준편차로 나누고 예측 시 되곱한다 — 그 사실을 FitResult에 기록).
  - early stopping: valid MAE 기준 patience. best 상태를 보존.
  - 결정성: `torch.manual_seed`, `numpy` seed, `torch.use_deterministic_algorithms(True)`(CPU), 데이터 순서는 seed 고정 셔플.
  - checkpoint_path가 있으면 epoch마다 `{model_state, optimizer_state, epoch, best_state, best_valid, history, rng_state}`를 원자적으로 저장. resume=True면 거기서 이어간다.
  - `fail_after_epoch`은 테스트용 중단 흉내(S02의 WEEKLY_SEQ_FAIL_AFTER와 같은 역할).
- `predict_tcn(fit, X) -> ndarray`

- [x] torch 없을 때 `import weekly_sequence_tcn`이 명확한 ImportError를 낸다(메시지에 requirements 주석 언급).
- [x] 실패 테스트: 인과성 — 입력 마지막 시점 이후를 바꿔도 예측 불변(lookback을 늘린 입력의 앞부분만 쓰는 방식으로 검증).
- [x] 결정성: 같은 seed·설정·자료로 두 번 학습하면 예측이 같다(atol 1e-6).
- [x] 세 seed는 서로 다른 예측을 낸다(같으면 seed가 안 걸린 것).
- [x] 스케일러 학습 구간 전용: 시험 입력을 극단으로 바꿔도 학습 구간 예측 불변.
- [x] 체크포인트 재개: epoch 3에서 중단 → resume → 처음부터 돌린 것과 history·예측이 같다.
- [x] 파라미터 수가 상한(예: 50,000) 이하.
- [x] quick 설정으로 합성 700일 학습이 CPU에서 60초 안에 끝난다.

## Task 2: 러너 단위 (tools/run_weekly_sequence.py --task S03)

- 단위: S02의 7개 + `tcn:seed42`, `tcn:seed43`, `tcn:seed44`, `tcn:summary`.
- 각 seed 단위: 폴드마다 내부 검증 블록(학습 구간 마지막 6개월, purge)으로 early stopping, 시험 폴드 예측을 모아 `predictions_tcn_seed<k>.csv`(prediction_date, target_date, y, pred)로 저장. 체크포인트는 storage 쪽.
- `tcn:summary`: seed별·평균 MAE/RMSE/방향 적중률을 metrics.csv에 추가(model=`tcn_seed42` … `tcn_mean`), comparisons.csv에 `tcn_mean_vs_persistence`, `tcn_mean_vs_ohlcv_ridge`(월 블록 CI). decision.md에 "seed 3개 모두 보고, 최고 seed 선택 없음, 채택 판단 없음(S04)" 명시.
- torch 없으면 네 단위를 `skipped: torch missing`으로 기록하고 종료 코드 0.
- 완료 단위의 산출물(predictions csv)이 없으면 다시 계산(S02와 같은 규칙).

- [x] 실패 테스트: `--task S03` quick 실행이 세 seed의 predictions csv와 metrics의 `tcn_*` 행 4개를 만든다.
- [x] `tcn:seed43` 뒤에서 중단 → 재개 시 seed42·43 건너뛰고 44부터.
- [x] 예측 csv를 지우면 그 seed만 다시 계산.
- [x] torch 부재를 흉내 내(환경변수 `WEEKLY_SEQ_NO_TORCH=1`) 실행하면 skipped 기록과 종료 코드 0.
- [x] manifest에 체크포인트 경로와 파일 해시가 있고, 체크포인트가 results 디렉터리에는 없다.
- [x] `.gitignore`가 `runs/`를 무시한다(없으면 추가).

## Task 3: 검증·인수인계·커밋

- [x] python -m unittest tests.test_weekly_sequence_tcn -v (torch 있는 환경)
- [x] python -m unittest tests.test_weekly_sequence_runner -v
- [x] PREDICT_STOCK_SKIP_SMOKE=1 python -m unittest discover -s tests — 전체가 5분 제한을 넘겨 모듈을 셋으로 나눠 실행, 모두 OK. (모듈 지정 실행 시 test_news_sentiment 의 test_pipeline_behavior import 오류는 경로 문제로 discover 에서는 통과 — 이 변경과 무관)
- [x] git diff --check, 운영 파일 무변경, `runs/` 산출물이 스테이징되지 않음.
- [x] guides/weekly-sequence-model-plan.md 의 S03 상태·진행 기록·다음(S04) 갱신.
- [x] 커밋: feat: add causal TCN candidate with seeded, resumable CPU training
- [x] main 반영 뒤 원격 재확인 — origin/main ea4cd345.

## 다음 단계

S04는 **실제 스냅샷**으로 full 비교를 돌리는 단계다. 현재가 유지·운영 Ridge(고정 입력 pkl 연결)·OHLCV Ridge·TCN(seed 3개)을 공통 날짜에서 비교하고, 잠금 12개월은 그때 한 번만 연다. S03만 완료한 상태에서 성능 개선을 말하지 않는다.
