# M01 — 중기 러너와 평가 계약 (2026-09-10)

실행: `20260910T081026Z_samsung_m01`, `20260910T081053Z_sk_hynix_m01` (full, 고정 입력 캐시에서).
코드: `tools/run_medium_horizon.py`, `forecast_utils.py`(`make_price_model`, `price_design_frame`, `price_oof_predictions`),
노트북 36셀이 같은 함수를 호출하도록 바꿈(`tools/sync_notebook_helpers.py` 로 동기화). 테스트 `tests/test_medium_horizon.py` 30개.

## 구현·검증

1. **함수 추출과 수치 동등성.** 노트북 안에 있던 설계 행렬·OOF 계산을 forecast_utils 로 옮기고 노트북이 그것을 부른다.
   개편 전 노트북으로 만든 M00 통계(raw/zero/shrunk MAE, slope, band_q, 포함률)와 개편 후 노트북·러너의 값이 네 조합
   모두 1e-9 이내로 같다(`summary_*.json` `notebook_equivalence`, M00 `full2` 로그). 합성 자료 동등성 테스트도 있다.
2. **CLI.** `--task --target --horizon --mode --storage --results --resume`. 미등록 작업(M02~)은 거부한다.
3. **고정 입력 로더.** 노트북을 한 번 캐시로 실행해 `runs/medium_horizon/<target>/inputs_full_<data_hash>.pkl` 에 저장
   (samsung `d8a92744da7a2ec2b7d0`, sk_hynix `8798bdb0703e0325a836`). 이후 실행은 노트북을 돌리지 않는다(M00 재계산 로그:
   두 종목 4단위가 캐시에서 같은 값으로 재현됨, 종목당 8분 → 1분). 스냅샷이 바뀌면 data_hash 가 달라져 캐시를 쓰지 않는다.
4. **재개·격리.** 같은 (task, target, mode, data_hash, config_hash) 실행을 이어가고 완료 단위를 건너뛴다. 완료 표시가 있어도
   요약·행·OOF 파일이 없거나 OOF 해시가 다르면 다시 계산한다(테스트: OOF 훼손 → 재계산, 노트북 재실행 없음). 단위별 행을
   파일로 남겨 재개 실행의 metrics.csv 가 단일 실행과 같다(테스트로 확인). 실패 실행은 manifest `status=failed` 로 남고
   공식 원장(`forecast_history/`)은 읽기만 한다(테스트: 실패 전후 원장 해시 동일).
5. **누수 테스트.** 미래 가격 변경 → 과거 특징·라벨 불변. 평가 구간 정답 변경 → 보정 기울기·구간 폭·발행 판정 불변.
   만기는 달력 일수가 아니라 봉 위치로 세어 휴장일을 건너뛴다. 5·20일 폴드·보정 구간·내부 블록 모두 날짜 기준 purge 통과.

## 평가 계약 (실측)

| 항목 | 값 |
| --- | --- |
| 개발 외부 폴드 | 2021-01-01 부터 6개월, 잠금 전까지. 자료 끝에서 60행 미만으로 잘린 폴드는 제외(dev_10, 31행) → **9개** |
| 잠금 | 라벨이 있는 마지막 12개월: 5일 2025-09-04~2026-09-03(226행), 20일 2025-08-13~2026-08-12(226행). 지평마다 며칠 다르다(마지막 행이 h-1 봉 앞) |
| 학습 | 라벨 만기 종가 < 시험 시작일(날짜 purge). 최소 500행 — 모든 폴드 충족(첫 폴드 889~893행) |
| 내부 선택 | 각 외부 폴드 앞 6개월 × 3블록, 각 블록도 purge. 9개 폴드 모두 3블록 사용 가능 |
| 잠금 결과의 의미 | M00 평가 구간(2024-08~)과 겹치므로 잠금 점수는 독립 검증이 아니다. 최종 확인은 M07 사전 예측 |

## 한계·다음 단계

- 로더는 노트북 네임스페이스를 pickle 로 저장한다(pandas 버전이 바뀌면 다시 만들어야 한다; data_hash 만 검사한다).
- 선택 구간 CI 는 아직 원장에 기록되지 않는다(M00 미해결 1). M02 이후 결과 파일(`comparisons.csv`)에는 남긴다.
- 다음: M02 특징군 비교. 이 계약(`evaluation_folds`)으로 현행 전체 특징 vs 시세만 vs 그룹 A/B/C 를 같은 폴드에서 비교한다.

## 재현 명령

```powershell
$env:PREDICT_STOCK_PUBLISH = 'false'
python -m unittest discover -s tests -p test_medium_horizon.py -v
python tools/run_medium_horizon.py --task M01 --target samsung --mode full --resume
python tools/run_medium_horizon.py --task M01 --target sk_hynix --mode full --resume
```
