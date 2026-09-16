# Studio API–Python Runtime boundary

Studio API는 인증, 권한, HTTP/Socket.IO transport를 소유하고 Python Runtime은 로컬
Workspace와 실행 프로세스를 소유한다. Frontend는 Runtime을 직접 호출하지 않는다.

현재 단일 로컬 배포에서는 `studio_api`가 `studio_runtime` Python package를 직접
호출한다. 이 호출 경계에서 전달하는 값은 문자열·경로·JSON 직렬화 가능한 run
record로 제한한다. Runtime은 FastAPI, Socket.IO session, FedOps Web credential을
import하거나 소유하지 않는다.

```text
studio-web -> studio-api -> studio-runtime
```

Runtime 공개 모듈:

- `studio_runtime.workspace`: project discovery와 안전한 file access
- `studio_runtime.jobs`: subprocess와 run state
- `studio_runtime.federated_task`: FedOps Task 생성과 Task 계약 검증
- `studio_runtime.execution`: 편집한 Python 파일의 uv 환경 실행
- `studio_runtime.environments`: uv 환경 registry, 선택, 동기화
- `studio_runtime.folder`: Workspace 경계 검증과 운영체제 file manager 호출
- `studio_runtime.hardware`: macOS/Windows/Linux CPU, GPU, memory 정규화와 host bridge client
- `studio_runtime.folder_bridge`: Docker에서 host file manager와 read-only hardware snapshot을 호출하는 token 보호 bridge
- `studio_runtime.terminal`: PTY lifecycle과 byte stream
- `studio_runtime.agent_builder`: 계정별 Agent draft, Workspace model source 검증과 build identity snapshot
- `studio_runtime.agents`: Built Agent serving 상태, bearer token hash, local Tool/LLM 실행과 bounded request 기록
- `studio_runtime.model_runner`: 계정별 host-mounted Hugging Face model 다운로드·상태·추론
