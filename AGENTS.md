# FedOps Agent Studio contributor rules

## Stable boundaries

- `apps/studio-web`는 브라우저 UI만 소유한다.
- `services/studio-api`는 인증, 권한, FedOps 연동과 transport만 소유한다.
- `services/studio-runtime`은 로컬 파일, Python environment와 process만 소유한다.
- 호출 방향은 `studio-web -> studio-api -> studio-runtime`이다. 역방향 import와
  Frontend의 Runtime 직접 호출을 금지한다.
- API shape를 바꿀 때 `contracts/studio-api.v1.yaml`을 먼저 수정한다.

## Frontend

- 전체 shell과 전역 session은 `src/app`에 둔다.
- 제품 화면은 `src/features/<feature>`에 둔다.
- `fetch`는 `src/api/http.ts`에서만 사용하고 기능 호출은 `src/api/<feature>.ts`로
  노출한다.
- 여러 화면이 실제로 공유하는 시각 요소만 `src/ui`에 둔다.
- 빈 계층, fixture 제품 데이터, 단순 re-export용 `index.ts`를 만들지 않는다.

## Python

- Backend endpoint는 `studio_api/features/<feature>/router.py`에서 시작한다.
- 외부 시스템 호출만 `studio_api/integrations`에 둔다.
- Workspace file/process/PTY 구현은 `studio_runtime`에 둔다. Runtime은 FastAPI,
  Socket.IO session 또는 FedOps credential을 import하지 않는다.
- 파일이 실제로 두 책임을 가지기 전에는 service/repository 하위 계층을 만들지 않는다.

## Identity and verification

- `taskId`, `runtimeKey`, `localProjectId`, `environmentId`, `agentId`, `runId`를 서로 대체하지 않는다.
- UI 변경은 typecheck와 production build, Python 변경은 unit test와 compile 검사를
  통과시킨다.
