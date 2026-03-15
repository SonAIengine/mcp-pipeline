"""mcp-pipeline 핵심 기능 테스트."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_pipeline import PipelineMCP, State

# ── State 테스트 ──


class SampleState(State):
    results: dict[str, Any] = {}
    history: list[dict] = []
    counter: int = 0


def test_state_field_names():
    s = SampleState()
    assert s._get_field_names() == ["results", "history", "counter"]


def test_state_mutable_isolation():
    """인스턴스 간 mutable 기본값이 공유되지 않아야 함."""
    a = SampleState()
    b = SampleState()
    a.results["key"] = "value"
    assert b.results == {}


def test_state_is_populated():
    s = SampleState()
    assert not s._is_populated("results")
    assert not s._is_populated("history")
    s.results["a"] = 1
    assert s._is_populated("results")


def test_state_field_status():
    s = SampleState()
    s.results = {"a": 1, "b": 2}
    status = s._get_field_status()
    assert status["results"]["populated"] is True
    assert status["results"]["count"] == 2
    assert status["history"]["populated"] is False
    assert status["history"]["count"] == 0


# ── PipelineMCP 테스트 ──


class TestState(State):
    opportunities: dict[str, Any] = {}
    contexts: dict[str, Any] = {}


def test_server_creates_state_from_class():
    server = PipelineMCP("test", state=TestState)
    assert server.state is not None
    assert isinstance(server.state, TestState)


def test_server_accepts_state_instance():
    s = TestState()
    server = PipelineMCP("test", state=s)
    assert server.state is s


def test_server_no_state():
    server = PipelineMCP("test")
    assert server.state is None


# ── Tool 데코레이터 테스트 ──


@pytest.fixture
def server():
    return PipelineMCP("test-server", state=TestState)


def test_tool_registers_meta(server: PipelineMCP):
    @server.tool(stores="opportunities")
    async def scout(topic: str) -> dict:
        return {"id": "opp_1", "topic": topic}

    assert "scout" in server._tool_meta
    assert server._tool_meta["scout"]["stores"] == ["opportunities"]


def test_tool_no_parens(server: PipelineMCP):
    @server.tool
    async def simple(query: str) -> str:
        return query

    assert "simple" in server._tool_meta


# ── stores/requires 통합 테스트 ──


@pytest.mark.asyncio
async def test_stores_saves_to_state():
    server = PipelineMCP("test", state=TestState)

    @server.tool(stores="opportunities")
    async def scout(topic: str, state: TestState) -> dict:
        return {"opp_1": {"topic": topic, "score": 0.9}}

    # FastMCP 내부 tool 목록에서 직접 호출
    tools = server.mcp._tool_manager._tools
    result = await tools["scout"].fn(topic="MCP")
    assert server.state.opportunities == {"opp_1": {"topic": "MCP", "score": 0.9}}
    assert result == {"opp_1": {"topic": "MCP", "score": 0.9}}


@pytest.mark.asyncio
async def test_stores_can_separate_state_and_response():
    server = PipelineMCP("test", state=TestState)

    @server.tool(
        stores="opportunities",
        store_value=lambda result: result[0],
        return_value=lambda result: result[1],
    )
    async def scout(topic: str, state: TestState) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {"opp_1": {"topic": topic, "score": 0.9}},
            {"opportunities": [{"id": "opp_1", "topic": topic}]},
        )

    tools = server.mcp._tool_manager._tools
    result = await tools["scout"].fn(topic="MCP")

    assert server.state.opportunities == {"opp_1": {"topic": "MCP", "score": 0.9}}
    assert result == {"opportunities": [{"id": "opp_1", "topic": "MCP"}]}


@pytest.mark.asyncio
async def test_requires_blocks_when_empty():
    server = PipelineMCP("test", state=TestState)

    @server.tool(requires="opportunities")
    async def draft(opp_id: str, state: TestState) -> dict:
        return {"context": state.opportunities[opp_id]}

    tools = server.mcp._tool_manager._tools
    result = await tools["draft"].fn(opp_id="opp_1")
    assert "error" in result
    assert "opportunities" in result["missing"]


@pytest.mark.asyncio
async def test_requires_passes_when_populated():
    server = PipelineMCP("test", state=TestState)
    server.state.opportunities = {"opp_1": {"topic": "MCP"}}

    @server.tool(requires="opportunities")
    async def draft(opp_id: str, state: TestState) -> dict:
        return {"context": state.opportunities[opp_id]}

    tools = server.mcp._tool_manager._tools
    result = await tools["draft"].fn(opp_id="opp_1")
    assert result == {"context": {"topic": "MCP"}}


@pytest.mark.asyncio
async def test_full_pipeline_flow():
    """scout → draft → strike 전체 흐름."""
    server = PipelineMCP("test", state=TestState)

    @server.tool(stores="opportunities")
    async def scout(topic: str, state: TestState) -> dict:
        return {"opp_1": {"topic": topic}}

    @server.tool(stores="contexts", requires="opportunities")
    async def draft(opp_id: str, state: TestState) -> dict:
        opp = state.opportunities[opp_id]
        return {opp_id: {"tone": "technical", "topic": opp["topic"]}}

    tools = server.mcp._tool_manager._tools

    # scout
    await tools["scout"].fn(topic="MCP server")
    assert "opp_1" in server.state.opportunities

    # draft (requires opportunities — 이제 populated)
    await tools["draft"].fn(opp_id="opp_1")
    assert "opp_1" in server.state.contexts


@pytest.mark.asyncio
async def test_status_tool():
    server = PipelineMCP("test", state=TestState)

    @server.tool(stores="opportunities")
    async def scout(topic: str) -> dict:
        return {}

    @server.tool(requires="opportunities")
    async def draft(opp_id: str) -> dict:
        return {}

    tools = server.mcp._tool_manager._tools
    result = await tools["_status"].fn()

    assert "state" in result
    assert "tools" in result
    # scout는 requires 없으니 available
    assert "scout" in result["tools"]["available"]
    # draft는 opportunities 필요 → blocked
    blocked_names = [b["tool"] for b in result["tools"]["blocked"]]
    assert "draft" in blocked_names
