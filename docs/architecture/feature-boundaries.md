# Feature boundaries

화면 메뉴와 팀 작업 경계는 동일하게 유지합니다.

| Feature | 주요 책임 | 안정 식별자 |
| --- | --- | --- |
| Home | 최근 Task와 Workspace 진입점 | 참조만 사용 |
| Workspace | 코드, file, 환경 선택, Task test, code run | `localProjectId`, `environmentId`, `runId` |
| Registry | Federated Task 탐색, model/file, 참여 요청 | `taskId`, handle/slug |
| Federated Learning | 승인된 Task 참여와 client 상태 | `taskId`, `runtimeKey`, `runId` |
| Agent Builder | 선택형 Base LLM, local Federated Task Tool AI, Harness 작성·검증·build | `agentId`, `localProjectId`, `buildRevision` |
| Agents | local Tool smoke, 선택한 Base LLM 실행, token 보호 API serve, request 상태 | `agentId`, `buildRevision`, `requestId` |

공용 shell, 인증, 계약과 배포는 제품 Feature가 아닙니다. `app`, Studio API root와
`contracts`가 담당합니다. 각 담당자는 다른 Feature 화면 내부 타입을 import하지 않고
안정 ID와 API 계약으로 연결합니다.

Backend feature 이름은 Frontend feature 이름과 맞춥니다. Runtime 구현은 화면별로
복제하지 않고 Workspace, job, Federated Task, code execution, terminal capability를 제공합니다.
`python-environments`는 Workspace 화면에 먼저 연결된 공유 capability이며 소유자 타입
(`federated-task`, `agent-build`, `agent-serve`)으로 각 제품 Feature의 환경을 분리합니다.
