"""PipelineMCP — FastMCP 래핑, 상태 주입 + stores/requires 체이닝."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from .decorators import normalize, wrap_tool
from .state import State
from .status import make_status_fn


class PipelineMCP:
    """Stateful Pipeline MCP 서버.

    FastMCP를 래핑하여 타입 안전 상태 관리와
    선언적 tool 체이닝(stores/requires)을 제공한다.

    Usage:
        class MyState(State):
            results: dict[str, Any] = {}

        server = PipelineMCP("my-server", state=MyState)

        @server.tool(stores="results")
        async def search(query: str, state: MyState) -> dict:
            ...
    """

    def __init__(
        self,
        name: str,
        state: State | type | None = None,
        **kwargs: Any,
    ) -> None:
        self._mcp = FastMCP(name=name, **kwargs)
        self._tool_meta: dict[str, dict[str, Any]] = {}

        # State 인스턴스 생성
        if isinstance(state, type) and issubclass(state, State):
            self._state: State | None = state()
        elif isinstance(state, State):
            self._state = state
        else:
            self._state = None

        # _status tool 자동 등록
        if self._state is not None:
            self._register_status_tool()

    def _register_status_tool(self) -> None:
        status_fn = make_status_fn(self._state, self._tool_meta)  # type: ignore[arg-type]
        self._mcp.tool(name="_status")(status_fn)

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        stores: str | list[str] | None = None,
        requires: str | list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """tool 데코레이터. stores/requires로 상태 의존성 선언.

        @server.tool
        async def simple(query: str) -> dict: ...

        @server.tool(stores="results", requires="config")
        async def pipeline(query: str, state: MyState) -> dict: ...
        """
        stores_list = normalize(stores)
        requires_list = normalize(requires)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = kwargs.get("name", func.__name__)
            self._tool_meta[tool_name] = {
                "stores": stores_list,
                "requires": requires_list,
            }

            # 상태 주입이 필요한지 판단
            needs_wrap = False
            if self._state is not None:
                if stores_list or requires_list:
                    needs_wrap = True
                else:
                    sig = inspect.signature(func)
                    if "state" in sig.parameters:
                        needs_wrap = True

            wrapped = wrap_tool(func, self._state, stores_list, requires_list) if needs_wrap else func  # type: ignore[arg-type]
            self._mcp.tool(**kwargs)(wrapped)
            return func

        # @server.tool (괄호 없이) 지원
        if fn is not None:
            return decorator(fn)
        return decorator

    def run(self, **kwargs: Any) -> None:
        """MCP 서버 실행."""
        self._mcp.run(**kwargs)

    @property
    def state(self) -> State | None:
        """현재 State 인스턴스."""
        return self._state

    @property
    def mcp(self) -> FastMCP:
        """내부 FastMCP 인스턴스 (고급 사용)."""
        return self._mcp
