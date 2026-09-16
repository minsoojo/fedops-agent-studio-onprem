# Feature contracts

## Frontend

```text
src/app       전체 shell, session, navigation
src/features  메뉴별 제품 화면
src/api       Studio API transport와 응답 type
src/ui        실제 공용 시각 요소
```

`features`는 다른 Feature 화면 파일을 import하지 않습니다. 공통 Task 선택은 안정 ID로
`app`에 전달합니다. 제품 데이터 fixture는 두지 않습니다.

## Studio API

```text
studio_api/main.py       ASGI 조립과 router 등록
studio_api/config.py     환경 설정
studio_api/session.py    local session
studio_api/features      제품 endpoint
studio_api/integrations  FedOps Web/S3 같은 외부 연결
```

Router는 권한, request/response와 HTTP error를 담당합니다. 긴 mapping이나 실행 코드는
같은 feature의 작은 모듈 또는 Runtime으로 보냅니다.

## Python Runtime

```text
studio_runtime/workspace.py  project discovery와 file access
studio_runtime/environments.py  uv 환경 registry, 선택, sync와 실행 경계
studio_runtime/jobs.py       process와 run state
studio_runtime/federated_task.py  FedOps Task 생성과 검증
studio_runtime/execution.py  Workspace Python 파일 실행
studio_runtime/terminal.py   PTY
```

Runtime의 공개 함수는 transport와 독립적이어야 하며 JSON으로 표현 가능한 결과를
반환합니다. Runtime은 FastAPI와 FedOps Web을 import하지 않습니다.
