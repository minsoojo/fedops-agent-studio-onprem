# Repository file policy

Git 저장소, Docker build context와 실행 container의 파일 범위를 구분한다.

## 제품 실행과 이미지 빌드에 필요한 파일

| 경로 | 역할 | Docker image 포함 |
| --- | --- | --- |
| `apps/studio-web` | React browser UI | build 결과만 포함 |
| `services/studio-api` | 인증, FedOps 연동, HTTP/Socket.IO | 포함 |
| `services/studio-runtime` | Workspace, uv, process와 local model Runtime | 포함 |
| `package.json`, `package-lock.json` | 재현 가능한 Frontend build | build stage에서 사용 |
| `services/*/requirements.txt` | Python Runtime dependency | build stage에서 사용 |

Dockerfile은 위 경로를 명시적으로 `COPY`한다. Git의 문서, test, contributor 설정은
production image에 들어가지 않는다.

## Git에는 유지하지만 실행 이미지에는 넣지 않는 파일

| 경로 | 유지 이유 |
| --- | --- |
| `AGENTS.md` | Codex와 동료 개발자가 Frontend → API → Runtime 경계와 검증 규칙을 동일하게 적용 |
| `contracts` | REST, Realtime과 Runtime boundary의 versioned source of truth |
| `docs` | 기능 담당자, 호출 흐름, FedOps Web 연동과 사용자 설정을 설명 |
| `tests` | OS나 담당자가 달라도 동일한 동작을 회귀 검증 |
| `.github` | Pull Request와 multi-architecture Docker Hub 배포 자동화 |
| `.editorconfig`, `.nvmrc` | editor format과 Node.js build version 통일 |
| `Makefile` | 로컬 설치·검증 명령 통일 |

이 파일들은 제품 실행 용량을 늘리지 않으며, 삭제하면 코드 이해와 변경 검증이 어려워져
추후 리팩토링 위험이 커진다.

## Git과 Docker에서 제외하는 로컬 상태

- `.runtime/`: host bridge token, pid, log
- `.venv/`, `node_modules/`, `.ruff_cache/`: 장치별 dependency/cache
- `apps/studio-web/dist/`: 다시 만들 수 있는 build output
- `~/fedops-workspace`: 계정별 project, dataset, uv environment, LLM과 Agent state
- `.DS_Store`, IDE 설정, socket, log와 임시 파일

민감 token, 사용자 dataset, 모델과 로컬 Workspace는 Git에 추가하지 않는다.
