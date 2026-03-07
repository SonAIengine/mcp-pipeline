"""타입 안전 State 베이스 클래스."""

from __future__ import annotations

import copy
from typing import Any


class State:
    """파이프라인 상태 베이스 클래스.

    서브클래싱하여 필드를 정의한다:

        class MyState(State):
            results: dict[str, Any] = {}
            history: list[dict] = []

    mutable 기본값은 인스턴스 생성 시 자동 복사된다.
    """

    def __init__(self) -> None:
        for name in self._get_field_names():
            default = getattr(self.__class__, name, None)
            if isinstance(default, (dict, list, set)):
                setattr(self, name, copy.copy(default))

    def _get_field_names(self) -> list[str]:
        """사용자 정의 필드명 목록 반환 (MRO 순서)."""
        names: list[str] = []
        for cls in reversed(type(self).__mro__):
            if cls is object:
                continue
            for name in getattr(cls, "__annotations__", {}):
                if not name.startswith("_") and name not in names:
                    names.append(name)
        return names

    def _get_field_status(self) -> dict[str, dict[str, Any]]:
        """각 필드의 상태 (populated 여부, 요소 수) 반환."""
        result: dict[str, dict[str, Any]] = {}
        for name in self._get_field_names():
            value = getattr(self, name)
            populated = bool(value) if value is not None else False
            info: dict[str, Any] = {"populated": populated}
            if isinstance(value, (dict, list)):
                info["count"] = len(value)
            result[name] = info
        return result

    def _is_populated(self, field_name: str) -> bool:
        """특정 필드에 데이터가 있는지 확인."""
        value = getattr(self, field_name, None)
        if value is None:
            return False
        if isinstance(value, (dict, list, set)):
            return len(value) > 0
        return True
