# mcp-pipeline

Stateful Pipeline framework for MCP servers.

Type-safe state sharing between tools. Declarative dependencies. Fewer tools, fewer tokens.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## The Problem

MCP servers expose tools to LLM agents. But every tool call costs tokens:

```
System prompt loads ALL tool descriptions → every round trip
14 tools × 9 round trips = massive token waste
```

Real numbers from the ecosystem:
- GitHub MCP (93 tools): **~55,000 tokens** per call just for tool descriptions
- Average setup with 7 MCP servers: **67,300 tokens** (33.7% of context window)
- Each additional round trip re-sends the entire conversation + all tool descriptions

And tools can't share state. If `search` finds results, `execute` can't access them — the LLM has to relay everything through its context, wasting more tokens.

## The Solution

**Collapse many small tools into few pipeline tools. Let the server manage state.**

```python
from mcp_pipeline import PipelineMCP, State

class MyState(State):
    results: dict[str, Any] = {}
    history: list[dict] = []

server = PipelineMCP("my-server", state=MyState)

@server.tool(stores="results")
async def scout(query: str, state: MyState) -> dict:
    """Search and analyze — does 5 things in one call."""
    data = await _search(query)
    analyzed = _analyze(data)
    return _compress(analyzed)              # Return compressed summary to LLM

@server.tool(requires="results")
async def act(result_id: str, content: str, state: MyState) -> dict:
    """Execute action on a previously found result."""
    target = state.results[result_id]       # Retrieved from server state, not LLM context
    return await _execute(target, content)
```

**Before**: 14 tools, 9 round trips, LLM relays all data between calls.
**After**: 3 tools, 3 round trips, server caches data between calls.

## How It Works

### 1. Type-Safe State

```python
from mcp_pipeline import State

class ProjectState(State):
    search_results: dict[str, Any] = {}     # scout → stores here
    draft_contexts: dict[str, Any] = {}     # draft → stores here
    action_history: list[dict] = []         # strike → appends here
```

No more `ctx.set_state("key", value)` with string keys.
IDE autocomplete works. Type errors caught at write time.

### 2. Declarative Dependencies: `stores` and `requires`

```python
@server.tool(stores="search_results")
async def scout(query: str, state: ProjectState) -> dict:
    """Calling this tool saves results to server state."""
    ...

@server.tool(requires="search_results")
async def act(result_id: str, state: ProjectState) -> dict:
    """This tool needs scout results. Fails gracefully if missing."""
    ...
```

What happens when `requires` isn't satisfied:

```python
# LLM calls act() without calling scout() first
# → Tool doesn't execute
# → Returns: {"error": "scout를 먼저 호출하세요.", "hint": "scout(query=...)"}
# → LLM sees this and calls scout first
# → No wasted API call, no crash
```

### 3. Auto-Generated `_status` Tool

mcp-pipeline automatically registers a status tool:

```python
# LLM calls _status() to see current state
{
    "search_results": {"count": 3, "available": true},
    "draft_contexts": {"count": 0, "available": false},
    "action_history": {"count": 2},
    "available_tools": ["scout", "act"],     # Tools whose requires are met
    "blocked_tools": ["draft"]               # Tools waiting on dependencies
}
```

The LLM always knows what it can do next without guessing.

### 4. Pipeline Compression

The key insight: **move work from LLM to server**.

```
Traditional MCP (LLM does the orchestration):
LLM → search       → LLM → filter        → LLM → analyze
    → LLM → rank   → LLM → get_details   → LLM → execute

Pipeline MCP (server does the orchestration):
LLM → scout (search+filter+analyze+rank internally) → LLM → act
```

Each "pipeline tool" does internally what would otherwise require 3-5 separate tool calls. The LLM sends one request, the server runs the full pipeline.

## Full Example

```python
from mcp_pipeline import PipelineMCP, State
from typing import Any

class SocialState(State):
    opportunities: dict[str, Any] = {}
    contexts: dict[str, Any] = {}
    history: list[dict] = []

server = PipelineMCP("social-agent", state=SocialState)

@server.tool(stores="opportunities")
async def scout(topic: str, platforms: list[str] | None = None) -> dict:
    """Scan platforms for relevant discussions. Returns top opportunities."""
    # Internally: trending + search + analyze + score + filter
    results = await _multi_platform_search(topic, platforms)
    scored = _score_relevance(results, topic)
    return {
        "opportunities": [
            {"id": opp.id, "platform": opp.platform,
             "title": opp.title, "relevance": opp.score,
             "reason": opp.reason}
            for opp in scored[:5]
        ],
        "summary": f"Found {len(scored)} opportunities, showing top 5",
    }

@server.tool(stores="contexts", requires="opportunities")
async def draft(opportunity_id: str, state: SocialState) -> dict:
    """Gather context for a specific opportunity."""
    opp = state.opportunities[opportunity_id]
    # Internally: get_post + get_comments + analyze_sentiment
    context = await _gather_context(opp)
    return {
        "post_title": context.title,
        "post_summary": context.summary,
        "top_comments": context.top_comments[:5],
        "tone": context.tone,
        "suggested_approach": context.approach,
    }

@server.tool(requires="contexts")
async def strike(
    opportunity_id: str, action: str, content: str, state: SocialState,
) -> dict:
    """Execute: post a comment, reply, or new post."""
    ctx = state.contexts[opportunity_id]
    result = await _write_to_platform(ctx, action, content)
    state.history.append({"opp": opportunity_id, "action": action, "url": result.url})
    return {"url": result.url, "status": "posted"}
```

3 tool calls. 3 round trips. Full workflow.

## Comparison

### vs Raw FastMCP

| | FastMCP | mcp-pipeline |
|---|---|---|
| State management | `ctx.set_state("key", val)` string keys | Typed `State` class with IDE support |
| Dependencies | None — tools are independent | `stores`/`requires` — declarative |
| Missing dependency | Silent bug or crash | Graceful error + hint message |
| Status introspection | Manual implementation | Auto-generated `_status` tool |
| Pipeline pattern | DIY — write orchestration in each tool | Built-in `stores`/`requires` chaining |

### vs mcp-workflow (TypeScript)

| | mcp-workflow | mcp-pipeline |
|---|---|---|
| Language | TypeScript | **Python** |
| Approach | Step-based workflows with branching | **Declarative state dependencies** |
| State | `WorkflowSessionManager` (Map) | **Typed State class** |
| Integration | Own workflow engine | **Extends FastMCP** |

### vs mcp-agent

| | mcp-agent | mcp-pipeline |
|---|---|---|
| Focus | Agent orchestration (client-side) | **MCP server design (server-side)** |
| State | Conversation history in LLM wrapper | **Server-side typed state** |
| Goal | Coordinate multiple agents | **Reduce tools & tokens per server** |

## Install

```bash
pip install mcp-pipeline
```

## Architecture

```
mcp_pipeline/
├── __init__.py          # PipelineMCP, State exports
├── server.py            # PipelineMCP class (wraps FastMCP)
├── state.py             # State base class + serialization
├── decorators.py        # stores/requires decorator logic
└── status.py            # Auto-generated _status tool
```

```
┌─────────────────────────────────────────────┐
│  LLM Agent                                  │
│  Sees: 3-4 tools (not 14)                   │
│  Does: judgment + content generation         │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  PipelineMCP Server                         │
│                                             │
│  ┌─────────┐  ┌─────────┐  ┌────────────┐  │
│  │  Tool A │─▶│  State  │◀─│   Tool B   │  │
│  │ stores= │  │ (typed) │  │ requires=  │  │
│  └─────────┘  └─────────┘  └────────────┘  │
│                                             │
│  Does: data fetching, analysis, filtering,  │
│        caching, pipeline orchestration       │
└─────────────────────────────────────────────┘
```

## Development

```bash
git clone https://github.com/SonAIengine/mcp-pipeline.git
cd mcp-pipeline
pip install -e ".[dev]"

pytest
mypy mcp_pipeline/
ruff check mcp_pipeline/
```

## License

[MIT](LICENSE)
