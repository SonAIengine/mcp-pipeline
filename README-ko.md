<div align="center">

# mcp-pipeline

**MCP 서버를 위한 Stateful Pipeline 프레임워크**

타입 안전 상태 관리. 선언적 tool 체이닝. 적은 tool, 적은 토큰.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

[English](README.md) · 한국어

</div>

---

## 문제

MCP 서버에는 토큰 문제가 있습니다. LLM이 tool을 호출할 때마다, **모든 tool description이 시스템 프롬프트에 포함**됩니다. tool이 많을수록 = 토큰 많이 = 비용 증가 = 정확도 하락.

Claude에 MCP 서버 몇 개를 연결하면 실제로 이런 일이 벌어집니다:

| 구성 | tool description | 200k context 대비 |
|------|------------------:|-------------------:|
| GitHub MCP 단독 (93 tools) | ~55,000 토큰 | 27.5% |
| MCP 서버 7개 평균 | ~67,300 토큰 | 33.7% |
| 서버 5개 × tool 30개씩 | ~60,000 토큰 | 30.0% |

**대화가 시작되기도 전에 context window의 1/3이 사라집니다.** 게다가 매 라운드트립마다 이걸 다시 보냅니다.

그리고 tool 간 상태 공유도 안 됩니다:

```python
# FastMCP — 각 tool이 독립적
@server.tool()
async def search_products(query: str) -> dict:
    results = await db.search(query)
    return {"products": results}  # 500 토큰 분량의 상품 데이터

@server.tool()
async def buy_product(product_id: str) -> dict:
    # 이 product_id가 어디서 왔나? LLM이 중계해야 함.
    # 즉, 검색 결과 500 토큰이 LLM context에 남아있다가
    # 이 호출 시 다시 전송됨.
    ...
```

LLM이 **데이터 택배 기사**가 됩니다. 검색 결과(500 토큰)를 받아서 context에 저장했다가, 다음 tool에 관련 데이터를 전달. **LLM을 거칠 필요 없는 데이터까지 전부 LLM을 경유합니다.**

## 해결

두 가지 아이디어:

1. **tool 수 줄이기** — 작은 tool 5개를 내부적으로 여러 작업을 하는 파이프라인 tool 1개로 합치기
2. **서버 측 상태** — tool 호출 사이의 데이터를 서버가 캐시해서 LLM이 중계하지 않게

```python
from mcp_pipeline import PipelineMCP, State
from typing import Any

# 1. 타입이 있는 상태 정의
class ShopState(State):
    search_results: dict[str, Any] = {}
    cart: list[dict] = []

server = PipelineMCP("shop", state=ShopState)

# 2. 이 tool은 결과를 상태에 저장
@server.tool(stores="search_results")
async def search(query: str, state: ShopState) -> dict:
    """상품 검색. 결과는 서버 측에 캐시됨."""
    products = await db.search(query)
    # state.search_results에 반환값이 자동 저장됨
    return {
        "products": [
            {"id": p.id, "name": p.name, "price": p.price}
            for p in products[:5]  # 상위 5개만 LLM에 반환 (압축)
        ]
    }

# 3. 이 tool은 이전 결과가 필요
@server.tool(requires="search_results")
async def buy(product_id: str, state: ShopState) -> dict:
    """검색 결과에서 상품 구매. search가 먼저 실행되어야 함."""
    product = state.search_results[product_id]  # 서버 캐시에서 조회
    order = await db.create_order(product)
    return {"order_id": order.id, "status": "confirmed"}
```

**바뀐 점:**
- LLM이 500 토큰의 상품 데이터를 저장하지 않음. 서버가 저장.
- `buy`가 서버 상태에서 검색 결과에 직접 접근.
- `search` 없이 `buy`를 호출하면 crash 대신 안내 메시지 반환.

## Before & After

### Before: 기존 MCP (14 tools, 9 라운드트립)

```
Round 1: LLM → list_platforms() → LLM         # "어떤 플랫폼이 있지?"
Round 2: LLM → get_trending("reddit") → LLM   # "트렌딩은?"
Round 3: LLM → search("MCP server") → LLM     # "관련 글 검색"
Round 4: LLM → analyze_post(id) → LLM         # "이 글 괜찮나?"
Round 5: LLM → get_post(id) → LLM             # "전체 내용 가져와"
Round 6: LLM → get_comments(id) → LLM         # "댓글도"
Round 7: LLM → check_rate_limit() → LLM       # "아직 호출 가능?"
Round 8: LLM → preview(content) → LLM         # "미리보기"
Round 9: LLM → write_comment(id, text) → LLM  # "실제 게시"

매 라운드: 14개 tool description (~3,000 토큰) + 전체 대화 이력 재전송
총 tool description 오버헤드: 14 × 9 = ~27,000 토큰
```

### After: Pipeline MCP (3 tools, 3 라운드트립)

```
Round 1: LLM → scout("MCP server", ["reddit"]) → LLM
         서버 내부: trending + search + analyze + rank + filter
         반환: 상위 3개 기회 (압축, ~200 토큰)

Round 2: LLM → draft("opp_1") → LLM
         서버 내부: get_post + get_comments + analyze_tone
         scout 결과는 서버 상태에서 참조 (캐시)
         반환: 맥락 요약 (~300 토큰)
         LLM이 이걸 보고 자연스러운 콘텐츠 생성

Round 3: LLM → strike("opp_1", "comment", "내용...") → LLM
         서버 내부: write_comment + record_history
         draft 맥락은 서버 상태에서 참조 (캐시)
         반환: {"url": "...", "status": "posted"}

매 라운드: 3개 tool description (~500 토큰) + 대화 이력
총 tool description 오버헤드: 3 × 3 = ~4,500 토큰
```

**토큰 절감: tool description만으로 ~83% 감소.** 중간 데이터 중계 토큰까지 합하면 더 큰 절감.

## 작동 방식

### 1. 타입 안전 State

```python
from mcp_pipeline import State

class MyState(State):
    search_results: dict[str, Any] = {}     # Tool A가 여기에 저장
    processed_data: dict[str, Any] = {}     # Tool B가 여기에 저장
    action_history: list[dict] = []         # Tool C가 여기에 추가
```

**FastMCP 내장 상태와 비교:**

```python
# FastMCP — 문자열 키, Any 타입, 알려진 버그 (#2098)
await ctx.set_state("search_results", data)
results = await ctx.get_state("search_results")  # Any 반환, 자동완성 불가

# mcp-pipeline — 타입 필드, IDE 자동완성, 컴파일 타임 체크
state.search_results = data                        # 타입 검증됨
results = state.search_results                     # IDE가 타입을 앎
```

### 2. `stores`와 `requires` — 선언적 의존성

```python
@server.tool(stores="search_results")
async def scout(query: str, state: MyState) -> dict:
    """이 tool의 반환값이 state.search_results에 자동 저장됨."""
    ...

@server.tool(requires="search_results")
async def act(result_id: str, state: MyState) -> dict:
    """scout가 먼저 실행되어야 함."""
    ...
```

`requires`가 충족되지 않으면:

```python
# LLM이 scout() 없이 act()를 호출
# crash나 이상한 결과 대신:
{
    "error": "필수 상태 'search_results'가 비어있습니다.",
    "hint": "먼저 scout(query=...)를 호출하세요.",
    "required_by": "act"
}
# LLM이 이걸 보고 scout를 먼저 호출.
# 낭비되는 API 호출 없음. 스택 트레이스 없음. 가이드만 제공.
```

### 3. 자동 생성 `_status` Tool

모든 `PipelineMCP` 서버에 `_status` tool이 자동 등록됩니다:

```python
# LLM이 _status() 호출
{
    "state": {
        "search_results": {"populated": true, "count": 5},
        "processed_data": {"populated": false},
        "action_history": {"populated": true, "count": 2}
    },
    "tools": {
        "available": ["scout", "act"],       # requires 충족된 tool
        "blocked": ["process"]               # 의존성 대기 중인 tool
    }
}
```

LLM이 항상 알 수 있음: 어떤 데이터가 있는지, 어떤 tool을 실행할 수 있는지, 뭐가 막혀있는지.

### 4. 파이프라인 압축

핵심 설계 원칙: **오케스트레이션을 LLM에서 서버로 이동.**

```
기존: LLM이 오케스트레이션
┌─────┐      ┌─────┐      ┌─────┐      ┌─────┐
│ LLM │─────▶│Tool1│─────▶│ LLM │─────▶│Tool2│───▶ ...
│     │◀─────│     │◀─────│중계  │◀─────│     │◀──
└─────┘      └─────┘      └─────┘      └─────┘
  LLM이 tool 사이에서 데이터 운반 (비쌈)

파이프라인: 서버가 오케스트레이션
┌─────┐      ┌──────────────────────────────┐
│ LLM │─────▶│ PipelineTool                 │
│     │◀─────│  내부: Tool1 → Tool2         │
└─────┘      │  상태는 서버 측 캐시          │
             └──────────────────────────────┘
  한 번 호출, 서버가 나머지 처리 (저렴)
```

## 전체 예시: 소셜 미디어 에이전트

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
    """개발자 커뮤니티에서 관련 토론을 탐색.
    내부적으로: trending + search + score + filter를 전 플랫폼에서 실행.
    후속 작업을 위한 ID가 포함된 상위 기회를 반환."""
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
        "summary": f"{len(set(o.platform for o in top))}개 플랫폼에서 {len(top)}건의 기회 발견",
    }

@server.tool(stores="contexts", requires="opportunities")
async def draft(opportunity_id: str, state: SocialState) -> dict:
    """특정 기회의 전체 맥락 수집.
    게시글, 댓글 트리, 대화 분위기를 분석하여 요약 반환.
    LLM이 이 맥락으로 자연스러운 응답을 생성."""
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
    """실행: 댓글, 답글, 또는 새 게시글 작성.
    draft에서 캐시된 맥락 사용. 이력 기록."""
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

3개 tool. 3번 왕복. 탐색부터 실행까지 전체 워크플로우 완료.

## 비교

### vs FastMCP (직접 사용)

| | FastMCP | mcp-pipeline |
|---|---|---|
| 상태 API | `ctx.set_state("key", val)` 문자열 키 | `state.field = val` 타입 있는 필드 |
| 상태 버그 | [알려진 이슈](https://github.com/jlowin/fastmcp/issues/2098) | 단순 dataclass 기반 — 예측 가능 |
| tool 의존성 | 없음 — 모든 tool 독립 | `stores`/`requires` 선언적 체이닝 |
| 의존성 미충족 시 | 조용한 실패 또는 crash | 가이드 에러: "X를 먼저 호출하세요" |
| 상태 확인 | 직접 구현해야 함 | `_status` tool 자동 생성 |
| 설계 철학 | 범용 툴킷 | 토큰 효율적 파이프라인 설계 |

### vs mcp-workflow (TypeScript)

| | [mcp-workflow](https://github.com/P0u4a/mcp-workflow) | mcp-pipeline |
|---|---|---|
| 언어 | TypeScript | **Python** |
| 접근 | 단계 기반 워크플로우 엔진 | **선언적 상태 의존성** |
| 상태 | `WorkflowSessionManager` (Map) | **타입 있는 State 클래스** |
| 복잡도 | 워크플로우 DSL, 분기, 일시정지/재개 | **최소 API: `stores`/`requires`** |

### vs mcp-agent

| | [mcp-agent](https://github.com/lastmile-ai/mcp-agent) | mcp-pipeline |
|---|---|---|
| 포커스 | 에이전트 오케스트레이션 (클라이언트 측) | **MCP 서버 설계 (서버 측)** |
| 풀려는 문제 | "여러 에이전트를 어떻게 조율하나?" | **"서버당 토큰을 어떻게 줄이나?"** |
| 상태 | LLM 래퍼 내 대화 이력 | **서버 측 타입 있는 상태** |

## 설치

```bash
pip install mcp-pipeline
```

의존성은 `mcp[cli]` (FastMCP) 하나뿐. 다른 의존성 없음.

## 아키텍처

```
mcp_pipeline/
├── __init__.py      # PipelineMCP, State exports
├── server.py        # PipelineMCP (FastMCP 래핑, 상태 주입 추가)
├── state.py         # State 베이스 클래스 (dataclass 스타일, 필드 인트로스펙션)
├── decorators.py    # stores/requires 로직 (검증, 자동 저장, 에러 가이드)
└── status.py        # _status tool 자동 생성
```

```
┌───────────────────────────────────────────────┐
│  LLM Agent                                    │
│  보이는 것: 3-4개 tool (14개가 아님)            │
│  하는 일: 판단, 콘텐츠 생성                     │
└──────────────┬────────────────────────────────┘
               │ 최소한의 라운드트립
┌──────────────▼────────────────────────────────┐
│  PipelineMCP Server                           │
│                                               │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │  Tool A  │─▶│  State   │◀─│   Tool B   │  │
│  │ stores=X │  │  (타입)  │  │ requires=X │  │
│  └──────────┘  └──────────┘  └────────────┘  │
│                                               │
│  서버가 처리: 데이터 수집, 분석, 필터링,        │
│  캐싱, 오케스트레이션                           │
└───────────────────────────────────────────────┘
```

## 개발

```bash
git clone https://github.com/SonAIengine/mcp-pipeline.git
cd mcp-pipeline
pip install -e ".[dev]"

pytest
mypy mcp_pipeline/
ruff check mcp_pipeline/
```

## 라이선스

[MIT](LICENSE)
