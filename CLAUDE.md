# mcp-pipeline

Stateful Pipeline MCP 프레임워크. FastMCP 위에 타입 안전 상태 관리 + 선언적 tool 체이닝 제공.

## 구조

```
mcp_pipeline/
├── __init__.py      # Public API (PipelineMCP, State)
├── server.py        # PipelineMCP 클래스 (FastMCP 래핑)
├── state.py         # State 베이스 클래스 + 필드 인트로스펙션
├── decorators.py    # stores/requires 데코레이터 로직
└── status.py        # _status tool 자동 생성
```

## 핵심 패턴

### PipelineMCP
- FastMCP를 래핑 (컴포지션: `self._mcp = FastMCP(...)`)
- FastMCP import: `from mcp.server.fastmcp import FastMCP` (not `from fastmcp`)
- `@server.tool(stores="field", requires="field")` 데코레이터 확장
- State 인스턴스를 모듈 레벨 싱글턴으로 관리
- _status tool 자동 등록

### State 클래스
- dataclass 스타일 (기본값 필수)
- 사용자가 서브클래싱하여 필드 정의
- 필드명 = stores/requires에서 참조하는 키
- 직렬화 불필요 (프로세스 내 메모리)

### stores/requires 동작
- `stores="results"` → tool 실행 후 반환값을 state.results에 자동 저장
- `requires="results"` → state.results가 비어있으면 실행 안 함, 가이드 메시지 반환
- 여러 필드: `stores=["results", "contexts"]`, `requires=["results"]`

### _status tool
- PipelineMCP가 자동 등록
- 각 state 필드의 존재 여부, 카운트 반환
- available_tools / blocked_tools 목록 제공

## 코드 스타일
- Python 3.10+, async
- 타입 힌트 필수
- mcp[cli] (FastMCP) 기반
- 최소 의존성 (mcp[cli]만)

## 빌드
```bash
pip install -e ".[dev]"
pytest
mypy mcp_pipeline/
ruff check mcp_pipeline/
```
