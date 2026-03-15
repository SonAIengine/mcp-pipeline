"""_status tool 자동 생성."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .state import State


def make_status_fn(
    state: State,
    tool_meta: dict[str, dict[str, Any]],
) -> Callable[..., Any]:
    """_status tool 함수를 생성하여 반환."""

    async def _status() -> dict[str, Any]:
        """파이프라인 상태 조회. 각 상태 필드와 사용 가능한 tool 목록을 반환."""
        field_status = state._get_field_status()

        available: list[str] = []
        blocked: list[dict[str, Any]] = []

        for tool_name, meta in tool_meta.items():
            if tool_name == "_status":
                continue
            req = meta.get("requires", [])
            missing = [r for r in req if not state._is_populated(r)]
            if missing:
                blocked.append({"tool": tool_name, "waiting_for": missing})
            else:
                available.append(tool_name)

        return {
            "state": field_status,
            "tools": {"available": available, "blocked": blocked},
        }

    return _status
