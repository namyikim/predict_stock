# M08 — 채택·유지·복구 결정 (2026-09-10, 관찰 연장)

M00~M07 의 결과로 종목·지평·목적별 결정을 기록한다. **운영 채택은 없다.** 관찰 연장 항목은 M07 첫 점검(5일 ≈ 2026-12, 20일 ≈ 2027-03) 뒤에
다시 기록한다. 관찰 연장은 운영 채택 완료가 아니다.

## 1. 종목·지평·목적별 결정 (계획 4절 기준)

| 조합 | 가격 중심값 | 방향 확률 | 예측 구간 | 근거 |
| --- | --- | --- | --- | --- |
| samsung/5 | **유지**(현행 Ridge 1e4·expanding·전체 특징·현행 판정) | **유지**(직접 모델 없음) | **유지**(simple) | M02·M03 동률, M04 log loss 동률, M05 어느 후보도 우위 없음 |
| samsung/20 | **유지** — 단, 현행이 유지보다 나쁘다는 증거(M00·M03·M05·잠금 0/3)가 반복됨. 발행률 0% 상태가 오히려 안전 | 유지 | 유지 | 사전 선언 장치가 고른 대안 없음. full_plus_A 는 메모만 |
| sk_hynix/5 | **유지 + 관찰**(Candidate strict gate) | 유지 | **유지 + 관찰**(Candidate HAR interval) | M05 개발 폴드 통과, 잠금에서 HAR 미통과·엄격 판정은 2/3 과 동일 결정 |
| sk_hynix/20 | **유지** | 유지 | **유지 + 관찰**(Candidate HAR interval) | 잠금에서 HAR 미통과(점수 +0.03, 포함률 0.51) |

가격·방향·구간 채택은 분리해 판단했고 어느 것도 채택하지 않았다. 잠금 비교 3개 검정의 Holm 보정은 M07 decision.md 에 있다.

## 2. 기본 설정 변경 여부와 복구값

운영 기본 설정(모델·특징·보정·판정·구간·원장 규칙)은 **바꾸지 않았다**. 이 계획에서 노트북·공통 코드에 들어간 변경과 되돌리는 방법:

| 변경 | 커밋 | 되돌리기 |
| --- | --- | --- |
| 설계 행렬·OOF 를 `forecast_utils.price_design_frame`·`price_oof_predictions`·`make_price_model` 로 추출(수치 동등) | aa1dd90 | `git revert aa1dd90` (노트북 36셀이 인라인 계산으로 돌아간다) |
| 보고서에 최근 60예측일 발행률 병기(`price_issuance_summary`) | 9ec09f3 | 노트북 40셀의 `_issuance_text` 줄 삭제 |
| 원장 후보 행 기록(`PRICE_CANDIDATES`)과 `review_ledger` 의 후보 제외 | 6ee179a | 노트북 36셀 `PRICE_CANDIDATES = {…}` 를 `{}` 로 — 기존 후보 행은 원장에 남지만 집계에서 계속 제외된다 |
| Windows 원자적 쓰기 재시도(`replace_with_retry`) | aa1dd90 | 되돌릴 이유 없음 |

관찰 후보를 채택하게 되면 바꿀 값(그때 기록): HAR 구간 → 노트북 `VOL_MODEL = "har"`(복구값 `"simple"`); 엄격 판정 → `price_forecast_variants` 의
`beats_baseline` 을 선택 구간·평가 구간 CI 상한 모두 < 0 으로(복구값: 선택 구간만).

## 3. 검증

- 노트북 단독 실행: `tools/run_notebook.py --storage runs/medium_horizon/sk_hynix --targets sk_hynix --quick --use-cache` 성공(후보 행 3개, 헤드라인
  불변, 보고서 생성). M01·M00 full 실행이 개편 후 노트북으로 노트북 통계와 1e-9 이내 동등(4조합). 자동 실행(Actions)은 같은 노트북을 쓰므로
  다음 아침 실행이 실제 환경 확인이다.
- 보고서 표현: 신호 없는 지평은 숫자를 내지 않고 "예측하지 않음"과 발행률을 표시한다(M05).
- 최초 예측 보존·재실행 중복: 기존 원장 테스트 그대로. 후보 행은 헤드라인과 같은 record_id 규칙(run_id 접두)으로 append_forecasts 를 거친다.
- 후보 장애 fallback: HAR 변형이 없으면 후보 행을 생략한다(코드 `if "har" not in variants: continue`); 헤드라인은 영향 없다.
- 회귀 테스트: `python -m unittest discover -s tests` 553개 중 환경 오류 1(test_easy_summary, Windows cp949 콘솔; CI Linux 에서는 통과) —
  코드 결함 아님. 중기 러너 테스트 62개.

## 4. 실행 시간·메모리·자동 실행 환경

- 러너(로컬, CPU): 노트북 캐시 실행 종목당 약 8분(M00·M01 최초 1회, pkl 저장). 이후 작업은 종목당 10~25초(M01~M07, Ridge/Logistic + 부트스트랩 2000).
  메모리: 패널(M06) 포함 1 GB 미만.
- Actions 아침 실행: 후보 행 3개 추가와 발행률 계산은 수 초 이내(HAR 변형은 이미 매 실행 계산되던 것). 새 의존성 없음.
- GPU·유료 API 사용 없음.

## 5. 남은 것

- M07 관찰 판정(첫 점검 후 이 문서 갱신). 판정 규칙은 계획 4절, 현황은 `python tools/run_medium_horizon.py --task M07 --target sk_hynix --mode full --resume` 의 summary.
- 계획 밖 메모: 잠금 구간(2025-09~2026-09)에서 20일 구간 포함률이 0.5 대로 무너진 것은 현행 구간 방식의 구조적 한계(급등 국면 전환)이며, 후보 HAR 도 해결하지
  못했다. 국면 전환에 반응하는 구간(예: 잔차 창을 변동성 국면으로 나누기)은 새 계획 항목으로 다뤄야 한다.
