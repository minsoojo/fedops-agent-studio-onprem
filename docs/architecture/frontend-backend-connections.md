# Frontend–backend connection map

화면에서 실제 실행 코드까지 다음 순서로 찾습니다.

```text
features/<feature>/<Feature>Screen.tsx
  -> api/<feature>.ts
  -> api/http.ts
  -> contracts/studio-api.v1.yaml
  -> studio_api/features/<feature>/router.py
  -> studio_api/integrations/* 또는 studio_runtime/*
```

| 기능 | Frontend | Studio API | Runtime/외부 연결 |
| --- | --- | --- | --- |
| 로그인 | `app/SessionGate.tsx`, `api/auth.ts` | `features/auth` | `integrations/fedops_web.py` |
| 계정 identity | `app/App.tsx`, `api/auth.ts` | `account.py`, `session.py` | FedOps login profile `_id` → device-local `accountKey` |
| Bootstrap 및 system hardware | `app/StudioProvider.tsx`, `app/StatusBar.tsx`, `api/system.ts` | `features/system` | 현재 계정 root discovery, `studio_runtime.hardware`, token 보호 host bridge |
| Registry | `features/registry`, `api/registry.ts` | `features/registry` | FedOps Web/S3 |
| Workspace IDE/file lifecycle | `features/workspace`, `ui/CodeEditor.tsx`, `api/workspace.ts` | `features/workspace/router.py` | `studio_runtime/workspace.py`, ruff |
| New Federated Task | `features/workspace`, `api/workspace.ts` | `features/workspace/router.py`, `integrations/fedops_web.py` | Web `/api/baselines/default` → signed S3 download → `studio_runtime/federated_task.py` 검증 cache/Workspace |
| Workspace Task test | `features/workspace`, `api/workspace.ts` | `features/workspace/router.py` | `studio_runtime/jobs.py`, `federated_task.py` |
| Workspace Python file run/Run Output | `features/workspace`, `api/workspace.ts` | `features/workspace/router.py`의 action/run list/get/cancel | `studio_runtime/jobs.py`, `execution.py`, process-local run history |
| Workspace folder open | `features/workspace`, `api/workspace.ts` | `features/workspace/router.py` | `studio_runtime/folder.py`, host bridge |
| Task-local data folder | `features/workspace`, `features/federated-learning`, `api/workspace.ts` | `features/workspace/router.py`의 data-binding/open-data-folder | `studio_runtime/folder.py`가 계정 `.local-data/federated-tasks/<project>/dataset`만 생성·개방 |
| Legacy Workspace import | `features/workspace`, `api/workspace.ts` | `features/workspace/router.py` | `studio_runtime/legacy_workspace.py` |
| Python environment | `features/python-environments`, `api/environments.ts` | `features/environments` | `studio_runtime/environments.py`, uv |
| Project multi uv Terminal | `features/workspace/WorkspaceTerminal.tsx` | `features/workspace/realtime.py`의 list/create/context/restart/close event | terminal별 `studio_runtime/terminal.py` PTY, uv CLI와 environment readiness 분리 |
| Agent Builder | `features/agent-builder`, `api/agents.ts` | `features/agent_builder/router.py` | account Workspace model source 검증 → atomic draft store → 공용 definition Runtime의 test/NDJSON streaming chat/LLM prepare → immutable build |
| Agents · Test/chat | `features/agents`, `api/agents.ts` | `features/agents/router.py` | build에 고정한 identity를 확인하고 공용 definition Runtime에서 Tool smoke와 NDJSON token streaming Base LLM 실행 |
| Agents · Serving API | `features/agents`, `api/agents.ts` | `features/agents/router.py`, dedicated `24400–24499` listeners, `/serve/v1/agents/*` compatibility path | `studio_runtime.agents` global port ownership, token hash/request log, host-stored Hugging Face/Federated Task LLM |

Frontend는 URL과 cookie 처리 방법을 화면에 넣지 않습니다. Runtime은 HTTP status나
browser session을 처리하지 않습니다. API router가 두 경계를 변환합니다.

새 기능은 다음 순서로 추가합니다.

1. `contracts/studio-api.v1.yaml`에 endpoint와 response를 정의합니다.
2. `studio_api/features/<feature>/router.py`에 인증된 진입점을 추가합니다.
3. 실행이 필요하면 `studio_runtime`에 transport 독립 Python 함수를 추가합니다.
4. `apps/studio-web/src/api/<feature>.ts`에 이름 있는 호출을 추가합니다.
5. Feature 화면이 그 호출을 사용합니다.
