"""stores/requires 데코레이터 로직."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from .state import State

Transform = Callable[[Any], Any]


def normalize(value: str | list[str] | None) -> list[str]:
    """stores/requires 인자를 리스트로 정규화."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def wrap_tool(
    fn: Callable[..., Any],
    state: State | None,
    stores: list[str],
    requires: list[str],
    *,
    store_value: Transform | None = None,
    return_value: Transform | None = None,
) -> Callable[..., Any]:
    """tool 함수를 래핑하여 상태 주입 + stores/requires 처리."""
    sig = inspect.signature(fn)
    has_state_param = "state" in sig.parameters
    is_async = inspect.iscoroutinefunction(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # requires 검증
        missing = [r for r in requires if state is None or not state._is_populated(r)]
        if missing:
            return {
                "error": f"필수 상태가 비어있습니다: {', '.join(missing)}",
                "hint": "먼저 해당 상태를 생성하는 tool을 호출하세요.",
                "missing": missing,
            }

        # state 주입
        if has_state_param:
            if state is None:
                raise RuntimeError("State injection requested, but PipelineMCP has no state.")
            kwargs["state"] = state

        # 원본 함수 호출
        result = await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)
        stored_result = store_value(result) if store_value is not None else result
        response_result = return_value(result) if return_value is not None else result

        # 반환값을 state에 저장
        if state is not None:
            for field_name in stores:
                setattr(state, field_name, stored_result)

        return response_result

    # state 파라미터를 시그니처에서 제거 (MCP 스키마에 노출 방지)
    if has_state_param:
        params = [p for name, p in sig.parameters.items() if name != "state"]
        wrapper.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]

    return wrapper
