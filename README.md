<div align="center">

# mcp-pipeline

**Stateful Pipeline framework for MCP servers.**

Type-safe state. Declarative tool chaining. Fewer tools, fewer tokens.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

English · [한국어](README-ko.md)

</div>

---

## The Problem

MCP servers have a token problem. Every time an LLM calls a tool, the **entire list of tool descriptions** is sent in the system prompt. More tools = more tokens = more cost = worse accuracy.

Here's what actually happens when you connect a few MCP servers to Claude:

| Setup | Tool descriptions | % of 200k context |
|-------|------------------:|-------------------:|
| GitHub MCP alone (93 tools) | ~55,000 tokens | 27.5% |
| 7 MCP servers average | ~67,300 tokens | 33.7% |
| 5 servers × 30 tools each | ~60,000 tokens | 30.0% |

**A third of your context window — gone before the conversation even starts.** And it gets worse: every round trip re-sends all of this.

But the token waste doesn't stop at tool descriptions. Tools also can't share state:

```python
# FastMCP — each tool is isolated
@server.tool()
async def search_products(query: str) -> dict:
    results = await db.search(query)
    return {"products": results}  # 500 tokens of product data

@server.tool()
async def buy_product(product_id: str) -> dict:
    # Where is this product_id from? The LLM has to relay it.
    # That means the 500 tokens of search results live in the LLM context
    # and get re-sent on this call.
    ...
```

The LLM becomes a data relay. It receives search results (500 tokens), stores them in its context, then passes the relevant bits to the next tool. **Every piece of data flows through the LLM — even when it doesn't need to.**

## The Solution

Two ideas:

1. **Fewer tools** — collapse 5 small tools into 1 pipeline tool that does multiple things internally
2. **Server-side state** — let the server cache data between tool calls so the LLM doesn't have to relay it

```python
from mcp_pipeline import PipelineMCP, State
from typing import Any

# 1. Define typed state
class ShopState(State):
    search_results: dict[str, Any] = {}
    cart: list[dict] = []

server = PipelineMCP("shop", state=ShopState)

# 2. This tool stores its results in state
@server.tool(stores="search_results")
async def search(query: str, state: ShopState) -> dict:
    """Search products. Results are cached server-side."""
    products = await db.search(query)
    # state.search_results is auto-populated with the return value
    return {
        "products": [
            {"id": p.id, "name": p.name, "price": p.price}
            for p in products[:5]  # Only send top 5 to LLM (compressed)
        ]
    }

# 3. This tool requires previous results
@server.tool(requires="search_results")
async def buy(product_id: str, state: ShopState) -> dict:
    """Buy a product from search results. Requires search first."""
    product = state.search_results[product_id]  # From server cache, not LLM context
    order = await db.create_order(product)
    return {"order_id": order.id, "status": "confirmed"}
```

**What changed:**
- The LLM doesn't store 500 tokens of product data. The server does.
- `buy` accesses search results directly from server state.
- If someone calls `buy` without `search`, they get a helpful error instead of a crash.

## Before & After

### Before: Traditional MCP (14 tools, 9 round trips)

```
Round 1: LLM → list_platforms() → LLM         # "which platforms exist?"
Round 2: LLM → get_trending("reddit") → LLM   # "what's trending?"
Round 3: LLM → search("MCP server") → LLM     # "find relevant posts"
Round 4: LLM → analyze_post(id) → LLM         # "is this post good?"
Round 5: LLM → get_post(id) → LLM             # "get full content"
Round 6: LLM → get_comments(id) → LLM         # "get the comments"
Round 7: LLM → check_rate_limit() → LLM       # "can I still post?"
Round 8: LLM → preview(content) → LLM         # "dry run"
Round 9: LLM → write_comment(id, text) → LLM  # "actually post"

Each round: resends 14 tool descriptions (~3,000 tokens) + full conversation history
Total tool description overhead: 14 × 9 = ~27,000 tokens
```

### After: Pipeline MCP (3 tools, 3 round trips)

```
Round 1: LLM → scout("MCP server", ["reddit"]) → LLM
         Server internally: trending + search + analyze + rank + filter
         Returns: top 3 opportunities (compressed, ~200 tokens)

Round 2: LLM → draft("opp_1") → LLM
         Server internally: get_post + get_comments + analyze_tone
         Uses cached opportunity from scout (server state)
         Returns: context summary (~300 tokens)
         LLM generates natural content

Round 3: LLM → strike("opp_1", "comment", "content...") → LLM
         Server internally: write_comment + record_history
         Uses cached context from draft (server state)
         Returns: {"url": "...", "status": "posted"}

Each round: resends 3 tool descriptions (~500 tokens) + conversation history
Total tool description overhead: 3 × 3 = ~4,500 tokens
```

**Token savings: ~83% reduction** in tool description overhead alone. Plus the LLM doesn't relay intermediate data between tools.

## How It Works

### 1. Type-Safe State

```python
from mcp_pipeline import State

class MyState(State):
    search_results: dict[str, Any] = {}     # Tool A stores here
    processed_data: dict[str, Any] = {}     # Tool B stores here
    action_history: list[dict] = []         # Tool C appends here
```

**vs FastMCP's built-in state:**

```python
# FastMCP — string keys, no type safety, known bugs (#2098)
await ctx.set_state("search_results", data)
results = await ctx.get_state("search_results")  # Returns Any, no autocomplete

# mcp-pipeline — typed fields, IDE autocomplete, compile-time checks
state.search_results = data                        # Type-checked
results = state.search_results                     # IDE knows the type
```

### 2. `stores` and `requires` — Declarative Dependencies

```python
@server.tool(stores="search_results")
async def scout(query: str, state: MyState) -> dict:
    """This tool's return value is automatically saved to state.search_results."""
    ...

@server.tool(requires="search_results")
async def act(result_id: str, state: MyState) -> dict:
    """This tool needs scout to have run first."""
    ...
```

When `requires` isn't met:

```python
# LLM calls act() before scout()
# Instead of crashing or returning garbage:
{
    "error": "Required state 'search_results' is empty.",
    "hint": "Call scout(query=...) first to populate search results.",
    "required_by": "act"
}
# The LLM reads this and knows to call scout first.
# No wasted API call. No stack trace. Just guidance.
```

### 3. Auto-Generated `_status` Tool

Every `PipelineMCP` server automatically gets a `_status` tool:

```python
# LLM calls _status()
{
    "state": {
        "search_results": {"populated": true, "count": 5},
        "processed_data": {"populated": false},
        "action_history": {"populated": true, "count": 2}
    },
    "tools": {
        "available": ["scout", "act"],       # requires are met
        "blocked": ["process"]               # waiting on dependencies
    }
}
```

The LLM always knows: what data exists, what tools can run, what's blocking.

### 4. Pipeline Compression

The core design principle: **move orchestration from LLM to server.**

```
Traditional: LLM orchestrates
┌─────┐      ┌─────┐      ┌─────┐      ┌─────┐
│ LLM │─────▶│Tool1│─────▶│ LLM │─────▶│Tool2│───▶ ...
│     │◀─────│     │◀─────│relay│◀─────│     │◀──
└─────┘      └─────┘      └─────┘      └─────┘
  LLM shuttles data between tools (expensive)

Pipeline: Server orchestrates
┌─────┐      ┌──────────────────────────────┐
│ LLM │─────▶│ PipelineTool                 │
│     │◀─────│  internally: Tool1 → Tool2   │
└─────┘      │  state cached server-side    │
             └──────────────────────────────┘
  One call, server handles the rest (cheap)
```

## Full Example: Social Media Agent

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
    """Scan developer communities for relevant discussions.
    Internally runs: trending + search + score + filter across all platforms.
    Returns top opportunities with IDs for follow-up."""
    all_posts = []
    for platform in get_active_platforms(platforms):
        trending = await platform.get_trending()
        searched = await platform.search(topic)
        all_posts.extend(trending + searched)

    scored = score_relevance(all_posts, topic)
    top = scored[:5]

    return {
        "opportunities": [
            {
                "id": f"opp_{i}",
                "platform": opp.platform,
                "title": opp.title[:80],
                "relevance": round(opp.score, 2),
                "comments": opp.comment_count,
                "reason": opp.reason,
            }
            for i, opp in enumerate(top)
        ],
        "total_scanned": len(all_posts),
        "summary": f"{len(top)} opportunities across {len(set(o.platform for o in top))} platforms",
    }

@server.tool(stores="contexts", requires="opportunities")
async def draft(opportunity_id: str, state: SocialState) -> dict:
    """Gather full context for a specific opportunity.
    Reads the post, its comment tree, and analyzes the conversation tone.
    Returns a context summary for the LLM to craft a response."""
    opp = state.opportunities[opportunity_id]
    post = await get_post(opp)
    comments = await get_comments(opp)

    return {
        "title": post.title,
        "body_summary": post.body[:500],
        "comment_count": len(comments),
        "top_comments": [c.body[:200] for c in comments[:5]],
        "tone": analyze_tone(comments),
        "suggested_approach": suggest_approach(post, comments),
    }

@server.tool(requires="contexts")
async def strike(
    opportunity_id: str,
    action: str,
    content: str,
    state: SocialState,
) -> dict:
    """Execute: write a comment, reply, or new post.
    Uses cached context from draft. Records action in history."""
    ctx = state.contexts[opportunity_id]
    result = await write_to_platform(ctx, action, content)

    state.history.append({
        "opportunity": opportunity_id,
        "action": action,
        "url": result.url,
        "timestamp": now(),
    })

    return {"url": result.url, "status": "posted"}
```

3 tools. 3 round trips. Complete workflow from discovery to action.

## Comparisons

### vs Raw FastMCP

| | FastMCP | mcp-pipeline |
|---|---|---|
| State API | `ctx.set_state("key", val)` — string keys, `Any` type | `state.field = val` — typed, IDE autocomplete |
| State bugs | [Known issues](https://github.com/jlowin/fastmcp/issues/2098) with get/set | Built on simple dataclass — predictable |
| Tool dependencies | None — all tools are independent | `stores`/`requires` — declarative chaining |
| Missing dependency | Silent failure or crash | Guided error: "call X first" |
| Status introspection | Build it yourself | Auto-generated `_status` tool |
| Design philosophy | General-purpose toolkit | Token-efficient pipeline design |

### vs mcp-workflow

| | [mcp-workflow](https://github.com/P0u4a/mcp-workflow) | mcp-pipeline |
|---|---|---|
| Language | TypeScript | **Python** |
| Approach | Step-based workflow engine | **Declarative state dependencies** |
| State | `WorkflowSessionManager` (Map) | **Typed State class** |
| Complexity | Workflow DSL, branching, pause/resume | **Minimal API: `stores`/`requires`** |
| Integration | Own workflow engine | **Wraps FastMCP** |

### vs mcp-agent

| | [mcp-agent](https://github.com/lastmile-ai/mcp-agent) | mcp-pipeline |
|---|---|---|
| Focus | Agent orchestration (client-side) | **MCP server design (server-side)** |
| Problem | "How do I coordinate multiple agents?" | **"How do I reduce tokens per server?"** |
| State | Conversation history in LLM wrapper | **Server-side typed state** |
| Output | Multi-agent workflows | **Fewer, smarter tools** |

## Install

```bash
pip install mcp-pipeline
```

Depends only on `mcp[cli]` (FastMCP). No other dependencies.

## Architecture

```
mcp_pipeline/
├── __init__.py      # PipelineMCP, State exports
├── server.py        # PipelineMCP (wraps FastMCP, adds state injection)
├── state.py         # State base class (dataclass-style, field introspection)
├── decorators.py    # stores/requires logic (validation, auto-save, error guidance)
└── status.py        # Auto-generated _status tool
```

```
┌───────────────────────────────────────────────┐
│  LLM Agent                                    │
│  Sees: 3-4 tools (not 14)                     │
│  Does: judgment, content generation            │
└──────────────┬────────────────────────────────┘
               │ minimal round trips
┌──────────────▼────────────────────────────────┐
│  PipelineMCP Server                           │
│                                               │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │  Tool A  │─▶│  State   │◀─│   Tool B   │  │
│  │ stores=X │  │  (typed) │  │ requires=X │  │
│  └──────────┘  └──────────┘  └────────────┘  │
│                                               │
│  Server handles: data fetching, analysis,     │
│  filtering, caching, orchestration            │
└───────────────────────────────────────────────┘
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
