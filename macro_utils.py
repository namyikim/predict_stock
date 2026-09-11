"""자료원 헬퍼의 겉모습(facade). 실제 구현은 data_sources/ 패키지에 있다.

2026-09에 1,100줄로 불어난 파일을 자료원별로 나눴다: _common(요청·정규화·보관본), kosis, ecos,
oecd, exports(관세청·일평균), flows(수급), dart(공시). 기존 `import macro_utils as mu` 와
`from macro_utils import ...` 는 그대로 동작한다.

노트북에는 tools/sync_notebook_helpers.py 가 data_sources/ 모듈들을 이어 붙여 넣는다(Colab에서
단독으로 돌아야 하므로 패키지 import 대신 본문을 넣는다).

테스트에서 함수를 바꿔칠 때는 이 facade가 아니라 정의된 모듈(data_sources.kosis 등)을 대상으로
해야 한다 — 함수는 자기 모듈의 전역을 본다.
"""
from data_sources import *  # noqa: F401,F403
from data_sources import _common, kosis, ecos, oecd, exports, flows, dart, us_calendar, dram_spot  # noqa: F401

# 테스트가 mu.urlopen / mu.time 을 참조하는 경우를 위해 공용 모듈의 것을 그대로 내보낸다.
from data_sources._common import urlopen, time, random  # noqa: F401,E402
