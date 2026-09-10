# P00 기준선 — quick 동작 확인

**결론: 채택/미채택 판단 없음.** quick은 파이프라인이 두 종목 모두 끝까지 도는지 확인하는
용도다. 3폴드·부트스트랩 400이라 성능 근거로 쓰지 않는다(계획 4절).

## 확인한 것

- 두 종목 모두 46개 셀을 오류 없이 실행하고 산출물을 남겼다.
- 고정 스냅샷을 새로 만들었다. 기존 `runs/real_validation/data_cache`는 자산 키가 옛 이름
  (`samsung`, `samsung_gdr`)이고 `usdjpy`가 없어 현재 코드와 맞지 않아 쓸 수 없었다.
- 평가일 276일(3폴드), 학습 행 samsung 2,753 / sk_hynix 2,752, 백테스트 범위 2015-04-01~2026-09-09.

## quick 지표 (참고용, 채택 근거 아님)

| 종목 | 모델 | accuracy | balanced_accuracy | log_loss |
| --- | --- | ---: | ---: | ---: |
| samsung | Always flat | 0.2681 | 0.3333 | 1.0993 |
| samsung | Previous ensemble | 0.4529 | 0.4488 | 1.0326 |
| samsung | **No macro ensemble** (대표) | 0.4674 | 0.4585 | 1.0253 |
| sk_hynix | Always flat | 0.2935 | 0.3333 | 1.0977 |
| sk_hynix | Previous ensemble | 0.4746 | 0.4538 | 0.9954 |
| sk_hynix | **No macro ensemble** (대표) | 0.4964 | 0.4600 | 0.9848 |

전체 모델 지표는 `metrics.csv`에 있다.

## 다음

같은 고정 스냅샷으로 full(12폴드·부트스트랩 2000) 평가를 실행한다. 그 결과가 P00의
정식 기준선이며 별도 run_id로 저장한다.
