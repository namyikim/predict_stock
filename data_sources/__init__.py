"""자료원 패키지. 노트북에는 tools/sync_notebook_helpers.py 가 이 모듈들을 이어 붙여 넣는다.

여기서 밑줄로 시작하는 이름(_kosis_request 등)까지 내보내는 이유는 테스트가 그것들을 바꿔치기해
재시도·대체 경로를 검사하기 때문이다. 바꿔칠 때는 이름이 정의된 모듈(data_sources.kosis 등)을
대상으로 해야 한다 — 함수는 자기 모듈의 전역을 본다.
"""
from data_sources import _common, kosis, ecos, oecd, exports, flows, dart  # noqa: F401

__all__ = []
for _module in (_common, kosis, ecos, oecd, exports, flows, dart):
    for _name in dir(_module):
        if _name.startswith("__"):
            continue
        globals()[_name] = getattr(_module, _name)
        __all__.append(_name)
