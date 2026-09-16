# FedOps Agent Studio

FedOps Agent Studio는 FedOps 계정과 Federated Task Registry를 로컬 Workspace,
FedOps 연합학습, Agent 개발 환경에 연결하는 애플리케이션입니다. Guest mode는 제공하지
않으며 FedOps 계정으로 로그인한 후 사용합니다.

## 구성

```text
apps/studio-web        React frontend
services/studio-api    인증, FedOps 연동, 제품 API와 Socket.IO transport
services/studio-runtime  Workspace, Python environment, Task/process, PTY
contracts              외부 API와 내부 구성 요소 사이의 안정 계약
```

호출 방향은 항상 아래와 같습니다.

```text
studio-web -> studio-api -> studio-runtime
                      \-> authenticated FedOps Web
```

Frontend는 Runtime을 직접 호출하지 않습니다. Studio API는 브라우저 요청을 인증하고
검증한 뒤 필요한 로컬 작업만 Runtime에 전달합니다.

Git 저장소에 유지하는 개발·계약 문서와 실제 Docker image에 포함되는 실행 파일의 구분은
[Repository file policy](docs/contributing/repository-files.md)를 확인합니다. `AGENTS.md`,
`docs`, `contracts`와 test는 협업과 회귀 검증을 위해 Git에는 유지하지만 production
image에는 포함하지 않습니다.

## 계정별 로컬 Workspace

한 장치에서 여러 FedOps 계정을 사용해도 로컬 파일과 실행 상태는 계정별로 분리됩니다.
로그인한 계정의 FedOps 사용자 ID로부터 장치 전용 opaque `accountKey`를 만들며, 원본 ID나
이메일을 폴더명에 사용하지 않습니다.

```text
~/fedops-workspace/
├── .fedops-studio/                 장치 account key와 migration 기록
└── accounts/
    └── account-<opaque-key>/
        ├── .local-data/
        │   └── federated-tasks/<project>/dataset/  Task별 로컬 전용 dataset
        └── projects/               현재 계정의 Federated Task
```

Workspace `Task Test`가 Task별 dataset 폴더를 자동 생성하고 `Open Data Folder`로 호스트
파일 관리자에서 엽니다. Local Train과 Participation Readiness는 같은 container-visible
경로를 자동 사용하며, raw dataset은 프로젝트 source나 Registry Release에 포함되지 않습니다.

Workspace 파일, uv environment, run, process, Terminal과 브라우저 UI 상태가 같은 account
namespace를 사용합니다. uv download cache, 애플리케이션 image와 baseline 원본은 장치에서
공유할 수 있습니다. 기존 `~/fedops-workspace` 바로 아래의 프로젝트는 자동 귀속되지 않고
로그인 후 Workspace의 `Legacy Workspace projects found` 안내에서 확인한 뒤 명시적으로
현재 계정으로 가져옵니다.

실제 설정한 Workspace 안의 `.fedops-studio/device-account-key`는 계정 경로를 다시 찾는
장치 key이므로 Workspace와
함께 백업해야 합니다. 기존 `accounts/`가 있는데 이 key가 없으면 Studio는 새 key를 만들어
기존 경로를 고립시키지 않고, 백업에서 복구하라는 오류를 반환합니다.

Frontend `src`는 네 역할만 사용합니다.

- `app`: 화면 shell, session gate, 전역 상태
- `features`: Home, Workspace, Registry, Federated Learning, Agent Builder, Agents
- `api`: 기능별 Studio API 호출과 공통 HTTP 처리
- `ui`: 여러 화면에서 사용하는 시각적 primitive

Settings에서는 v1 JSON 파일을 사용자 테마로 가져올 수 있다. 가져온 값은 현재 FedOps
계정의 browser localStorage에만 저장되고 UI, Monaco Editor, xterm Terminal에 함께
적용된다. 현재 적용된 테마는 수정 가능한 v1 baseline JSON으로 다운로드할 수 있다.
특정 외부 테마는 제품 기본값으로 포함하지 않는다. 파일 형식은
[Custom theme JSON](docs/contributing/custom-themes.md)을 확인한다.

## 로컬 개발

요구 버전은 Node.js 20.19+, Python 3.11~3.12, uv입니다. 배포 이미지는 고정된 uv와
Baseline의 commit-pinned FedOps dependency를 설치하는 데 필요한 Git/CA certificate를
포함합니다.

```bash
npm install
python3.12 -m venv .venv
.venv/bin/pip install \
  -r services/studio-api/requirements.txt \
  -r services/studio-runtime/requirements.txt
```

Federated Task를 처음 열면 `Default` 환경 메타데이터가 자동 생성됩니다. Workspace의
`Python Environments`에서 Python 버전을 선택해 환경을 추가하고 `Sync environment`를
실행하면 uv가 프로젝트의 `uv.lock`과 선택된 환경을 동기화합니다. Task Test와 Code
Run은 선택된 환경에서 `uv run --locked --no-sync`로 실행됩니다.
동기화 중에는 uv 출력에서 확인한 단계를 기반으로 진행률과 현재 단계를 표시하며,
`Stop sync`로 실행을 안전하게 중단할 수 있습니다. 중단된 환경은 삭제되지 않고
`Outdated` 상태로 남으므로 의존성을 확인한 뒤 다시 동기화하면 됩니다.
Baseline 0.10.0부터 사용자는 `requirements.txt`에 `library==version`만 작성합니다.
`Python Environments`의 Task Dependencies 편집기도 같은 파일을 사용하며 저장 시 모든
환경을 업데이트 필요 상태로 바꿉니다. `pyproject.toml`은 FedOps Task 계약,
`uv.lock`은 uv가 생성한 전체 의존성 잠금 파일이므로 직접 수정하지 않습니다.
Workspace Code의 수정 권한은 `.fedops-studio/baseline.json`에 보존된 Baseline manifest의
`editable` 값을 단일 기준으로 사용합니다. `README.md`, `requirements.txt`, `conf/`,
`local_training/`, `tool_ai/`의 Owner 파일은 수정할 수 있고, `federated_learning/`,
`runtime/`, `task_readiness/`와 FedOps entrypoint/package/lock 파일은 열람만 할 수 있습니다.
Explorer와 Editor가 잠금 상태를 표시하며 Studio API도 같은 정책으로 저장·정렬·삭제를
차단합니다. 사용자가 추가하는 helper 파일은 Owner 영역 안에서만 생성할 수 있습니다.
Task별 가상환경은 host-mounted Workspace에 보존합니다. 재사용 가능한 uv package/Python
cache는 `fedops-agent-studio-uv` Docker named volume에 분리하여 Windows Docker Desktop에서도
매번 다시 다운로드·압축 해제하는 비용을 줄입니다. cache와 Workspace가 다른 filesystem이므로
link mode는 명시적으로 `copy`를 사용합니다. 새 Task는 로그인된 Studio API가 FedOps Web backend에 직접 포함된
Federated Task Baseline 0.10.0의 manifest와 opaque artifact를 인증 API로 받은 뒤 경로·크기·
SHA-256을 검증하여 생성합니다. 검증된 원본은 Workspace의 장치 공용 cache에 두고 계정별
프로젝트로 원자적으로 복사합니다. 검증 manifest는 사용자 파일로 노출하지 않고 프로젝트의
`.fedops-studio/baseline.json` 내부 메타데이터로 보관합니다. Web과 Studio는 이 과정에서 `FedOps-SiloBaseline` Git
저장소를 clone/pull하지 않고, Studio는 Registry API/MinIO/S3 credential을 보유하지 않습니다.

Workspace는 세 가지 시작 흐름을 같은 project model로 처리합니다.

- Web에서 생성한 Draft를 선택해 개발하는 `Develop a FedOps Draft`
- 기존 로컬 프로젝트를 계정 Workspace로 가져와 계속 개발하는 local-first 흐름
- 로컬 프로젝트를 Web Draft에 연결해 정식 Federated Task로 전환하는 흐름

`Open Web Draft`에서는 이름을 다시 입력하지 않습니다. Web의 `displayName`을 Studio에서도
Federated Task 이름으로 사용하고 `@owner/slug`를 Registry ID로 표시하며, 로컬 Workspace 폴더는
slug에서 자동 생성합니다. 같은 `taskId`가 이미 연결되어 있으면 중복 프로젝트를 만들지 않고 기존
Workspace를 엽니다. `Start Local Project`에서만 `Local Project Name`을 입력하며 이 값은 Web Task
identity가 아니라 로컬 폴더 식별자입니다. 신규 폴더에는 `fedops-` 접두어를 강제로 붙이지 않고,
기존 `fedops-*` 프로젝트는 환경·터미널·경로 호환성을 위해 이름을 그대로 유지합니다.

연결 정보는 project의 `.fedops-studio/task-binding.json`에 저장되며 source code와 Release
bundle에는 포함되지 않습니다. `Local Training`은 사용자 데이터로 local path를 검증하고,
`Release Readiness`는 Owner의 source/model 조합이 Registry에 게시 가능한지 확인합니다.
Candidate 제출 시 Studio는 initial model과 deterministic ZIP을 FedOps Web에만 전송합니다.
Web에서 Owner가 Publish한 뒤 다른 사용자는 Registry의 `Open in Workspace`로 정확한 Release
snapshot과 model을 받아 `Participation Readiness`를 실행할 수 있습니다. 사용자 raw dataset은
계정별 로컬 Workspace에만 남습니다.

Workspace의 `Project Setup`은 Web Draft 식별 정보와 구현 파일의 책임을 함께 보여줍니다.
`conf/config.yaml`에는 Primary Model, 데이터 입출력 계약, 로컬 학습 기본값과 Release가
지원하는 집계 전략만 저장합니다. 실제 실행의 라운드 수, 라운드당 클라이언트 수와 선택
전략은 FedOps Web Server Management의 Campaign으로 별도 관리하며, 같은 Release를 다시
만들지 않고 실행마다 안전하게 선택할 수 있습니다.

Federated Learning은 Registry discovery나 다른 참여자 통계 화면이 아니라 현재 계정의 로컬
Client 실행·모니터링 화면입니다. Published Release를 연 Workspace가 Participation Readiness를
통과하면 Web의 승인 상태, 정확한 Release/Global Model, 집계 endpoint를 participation manifest로
받습니다. Start Client는 선택된 uv environment에서 고정 `federated_task.main participate`
entrypoint만 실행하며 임의 command를 받지 않습니다. 구조화된 JSONL event와 bounded process log로
내 Client의 Round, Local Training, Evaluation, Model Update upload 상태만 표시합니다. 다른 참여자의
identity나 로컬 metric은 Agent Studio에 전달하거나 표시하지 않습니다.

Agent Builder는 Launcher 전역 상태나 fixture를 사용하지 않습니다. Agent draft는 계정별
`.local-data/agents/store.json`에 atomic 저장합니다. Tool AI는 계정별 Workspace에 연
Federated Task의 `federated_task` 코드와 `model_release`를 직접 사용하며 Agent 전용 archive로
중복 복사하지 않습니다. Build는 `localProjectId`, source fingerprint와 model SHA-256을
고정합니다. 이후 Workspace가 바뀌면 기존 build 실행을 거부하고 다시 검증·build하도록 합니다.
Test & Build의 Draft Playground는 현재 편집값을 먼저 저장합니다. `Check Draft & Tool AI`는
Draft identity, Tool AI smoke inference와 Base LLM 준비 여부만 검사하고 LLM 답변을 생성하지
않습니다. `Generate Response`는 선택적인 Tool AI 결과를 Harness에 연결한 뒤 실제 Base LLM으로
대화 답변을 생성합니다. 따라서 테스트를 위해 임시 Build를 만들지 않습니다.

```text
agentId + buildRevision
  ├── Hugging Face repo@revision 또는 LLM Federated Task
  ├── localProjectId + source fingerprint + model SHA-256 (Tool AI 0개 이상)
  └── Agent Harness instructions/routing/context/memory/safety
```

Base LLM은 Hugging Face repository/revision/model file 또는 LLM으로 만든 Federated Task 중
하나를 선택합니다. 기본 검증 선택은
`bartowski/Qwen_Qwen3.5-4B-GGUF`의 `Qwen_Qwen3.5-4B-Q4_K_M.gguf`이며, repository 전체가 아닌 선택한
약 3 GB GGUF 하나만 받습니다. GGUF 대화 추론은 container에 고정한 `llama.cpp` Python runtime을
사용합니다. Hugging Face Prepare는 모델을 container layer가 아닌
`~/fedops-workspace/accounts/<accountKey>/models/huggingface`에 받습니다. LLM Federated Task는
Workspace의 `federated_task/llm` 실행 계약과 `model_release`를 사용합니다. 모델이 없으면
`not-prepared`, 로컬 파일이 준비되면 `installed`, 실행이 성공하면 `ready`, 로딩에 실패하면
`error`로 구분하며 placeholder response는 반환하지 않습니다. 다운로드 완료 여부는 metadata뿐
아니라 Transformers safetensors shard 또는 선택한 GGUF의 원격/로컬 크기를 함께 검사합니다.
Prepare 요청은 HTTP 연결을 장시간 점유하지 않고 account/model별 background 작업을 시작합니다.
Agent Builder와 Agents는 준비 상태를 polling하여 `checking`, `downloading`, `verifying`, `ready`
단계와 실제 downloaded/total bytes 및 percent를 표시합니다. 같은 모델을 다시 요청하면 병렬 중복
다운로드를 만들지 않고 실행 중인 작업 상태를 공유합니다.

Built Agent는 Agents 화면에서 현재 build에 고정된 Workspace Tool AI를 해당 프로젝트의 locked uv
환경으로 smoke test할 수 있습니다. Agent Builder와 Agents의 대화 UI는 최근 40개 user/assistant
메시지를 다음 요청에 전달하며, Serving API도 같은 선택형 `history` 계약을 사용합니다.
Serving을 활성화하면 동적 host port 대신 Agent Studio의 기존 loopback origin 아래에 endpoint가
생성되므로 macOS, Windows, Linux의 Docker port mapping이 동일합니다.

```text
GET  http://localhost:{agentPort}/health
GET  http://localhost:{agentPort}/info
POST http://localhost:{agentPort}/chat
```

Agent마다 `24400–24499` 범위의 전용 localhost 포트를 선택합니다. 이미 다른 Agent에
할당된 포트는 사용할 수 없습니다. 기존
`/serve/v1/agents/{agentId}/*` 경로는 이전 Build 및 클라이언트 호환을 위해 유지합니다.

Serving bearer token은 생성·회전 때 한 번만 평문으로 표시하고 로컬 store에는 SHA-256 hash만
보관합니다. Request log는 time, path, status, latency만 bounded 저장하며 prompt, Tool input,
response와 token을 기록하지 않습니다.

Workspace `Code`는 로컬 bundle Monaco Editor를 사용합니다. Explorer에서 새 파일 생성,
활성 파일 삭제, Python/JSON format, 문법 하이라이트를 제공하며 Explorer·File Info·하단
Terminal 영역을 drag해 크기를 조절할 수 있습니다. 숨김 디렉터리, Runtime 환경과 symlink는
편집 API에서 보호됩니다. Explorer와 Editor tab은 저장소 내부에서 직접 작성한 generic
file/folder SVG와 확장자 모노그램을 공유하므로, 외부 icon asset이나 언어 브랜드 로고 없이
파일 형식과 directory 계층을 구분합니다.
Editor toolbar의 `− / px / +` control은 Monaco 코드 글자 크기를 10–24px 범위에서 조절하고
가운데 px 값을 누르면 기본 13px로 복원합니다. 선택값은 FedOps account별 browser
localStorage에 저장되며 전체 UI와 Terminal 글자 크기에는 영향을 주지 않습니다.

하단 Terminal은 project-scoped xterm PTY입니다. Header는 Studio image의 uv CLI 설치 상태와
선택한 Python environment의 준비 상태를 별도 badge로 표시합니다. 준비된 환경에서 생성한
Terminal만 같은 Python과 package를 사용하며 프롬프트의 `(uv:환경명)`이 이를 나타냅니다.
동기화 전에는 `(env:환경명:sync-required)`로 표시하고 가상 환경을 가장해 활성화하지 않습니다.
환경을 동기화하거나 선택을 바꾸면 실행 중 PTY를 암묵적으로 변경하지 않고
`Restart with <환경명>`으로 명시적으로 교체합니다. Prompt는 Docker의 일시적인
`root@<container-id>` 대신 `fedops-studio:<현재 폴더>` context를 사용합니다. Tab,
방향키, Ctrl+C, copy/paste, ANSI output과 scrollback을 지원하며 browser reconnect 시
Runtime의 bounded replay buffer에서 최근 출력을 복원합니다. `+`로 프로젝트당 최대 8개의
독립 terminal을 생성하고 `Terminal N` tab으로 전환하거나 각 tab의 `×`로 해당 PTY를
종료할 수 있습니다. 새 번호는 활성 terminal의 가장 큰 번호 다음으로 정하며, 마지막 번호를
닫으면 그 번호를 다시 사용합니다. 낮은 번호만 닫고 더 높은 번호가 남아 있으면 중간 번호를
채우지 않습니다. 모든 terminal을 닫은 뒤 새로 생성하면 `Terminal 1`부터 시작합니다.

Code의 `Run Python File`은 현재 `.py` 파일을 저장한 후 선택된 ready environment에서
`uv run --locked --no-sync`로 실행하는 비대화형 관리 작업입니다. Federated Task 전체 실행이나
연합학습 참여를 시작하지 않습니다. 결과는 Terminal과 섞지 않고 `Run Output`에서 상태,
명령, environment ID, 시작 시각, 종료 코드와 함께 확인합니다. Code 화면을 다시 열어도
현재 Studio process가 보관한 프로젝트의 Python file run 기록을 다시 불러옵니다.
정적 diagnostics pipeline이 아직 없는 `Problems` placeholder는 노출하지 않습니다.

API는 `AGENT`의 전화 키패드 표기인 `24368`을 기본 포트로 사용합니다.
브라우저에서 직접 사용하는 기본 주소는 `http://localhost:24368`입니다. Docker host
publish는 기본적으로 `0.0.0.0`에 bind하므로 localhost, 같은 네트워크의 host IP, 외부
port-forwarding domain으로 접속할 수 있습니다. Agent Studio API와 Socket.IO는 모든 Host와
browser Origin을 허용하며, 로그인 cookie는 `HttpOnly`와 `SameSite=Strict`로 제한합니다.

Development/테스트 실행은 배포 이미지 검증과 별도 프로세스로 관리합니다.

| 용도 | 주소 | 실행 주체 | Container |
| --- | --- | --- | --- |
| Source development UI | `http://localhost:8443` | Vite | host process |
| Source development API/Runtime | `http://localhost:24369` | source-mounted Docker | `fedops-agent-studio-dev-api` |
| Published image validation | `http://localhost:24368` | `fedops run agent-studio` | `fedops-agent-studio` |

개발 API는 이미 로컬에 존재하는 Published image를 Python/Linux Runtime base로만 사용하고,
현재 `services/studio-api/src`와 `services/studio-runtime/src`를 read-only mount합니다. 따라서
소스 수정마다 이미지를 build/pull하지 않습니다. Docker 안에서 생성된 uv environment도 같은
Linux Runtime에서 판정하므로 macOS host에서 container용 `.venv`를 `missing`으로 오인하지
않습니다.

```bash
make dev-api
```

Web:

```bash
npm run dev:web
```

개발 Web은 `http://localhost:8443`에서 실행되고 전용 개발 API `24369`로 proxy합니다.
개발 실행은 `fedops-agent-studio` release container를 시작, 중지, 교체하지 않습니다.

```bash
make dev-api-stop
```

운영체제 file manager로 실제 Workspace 폴더를 열려면 host opener를 실행합니다.
macOS는 Finder, Windows는 Explorer, Linux는 `xdg-open`을 사용합니다.

```bash
make folder-opener-start
# 종료할 때
make folder-opener-stop
```

Native API 실행은 opener를 직접 호출합니다. Docker API는 Workspace 밖 경로와 임의
command를 받지 않는 token-protected host bridge를 사용합니다. 같은 bridge의 read-only
`/hardware` 응답은 macOS, Windows, Linux 정보를 공통 CPU/GPU/Memory schema로 정규화해
하단 status bar에 전달합니다. Bridge가 없으면 container가 사용할 수 있는 Runtime 자원으로
fallback하며 `Host`와 `Runtime` source를 UI에서 구분합니다.

로컬 컨테이너는 Workspace만 read-write로 mount합니다. Baseline은 별도 host 디렉토리를
mount하지 않으며, FedOps Web에서 받은 검증된 bundled Baseline cache도 같은 Workspace volume의
`.fedops-studio/baselines`에 보관합니다.

```bash
docker run --rm -p 0.0.0.0:24368:24368 \
  -p 0.0.0.0:24400-24499:24400-24499 \
  -v "$HOME/fedops-workspace:/workspace" \
  --mount type=volume,source=fedops-agent-studio-uv,target=/var/cache/fedops-uv \
  -v "$PWD/.runtime/folder-opener-token:/run/secrets/folder-opener-token:ro" \
  -e UV_CACHE_DIR=/var/cache/fedops-uv/cache \
  -e UV_PYTHON_INSTALL_DIR=/var/cache/fedops-uv/python \
  -e STUDIO_FOLDER_OPENER_URL=http://host.docker.internal:5602 \
  -e STUDIO_FOLDER_OPENER_TOKEN_FILE=/run/secrets/folder-opener-token \
  -e STUDIO_HOST_WORKSPACE_DIR="$HOME/fedops-workspace" \
  gachonccl/fedops-agent-studio:latest
```

브라우저에서 `http://localhost:24368`을 엽니다. Production image는 React build와 두
Python source component를 하나의 로컬 이미지로 배포하고 Workspace는 volume으로
분리합니다.

`main` Git push 뒤 Docker Hub에 `linux/amd64`, `linux/arm64` image를 자동 게시하는 방식과
OS/GPU 지원 범위는 [Docker distribution](docs/deployment/docker-distribution.md)을 확인합니다.
다른 테스트 장치에서는 새 image가 게시된 뒤 아래 명령을 다시 실행하면 됩니다.

```bash
fedops run agent-studio
```

기본 실행은 Docker Hub의 `latest`를 먼저 확인하고 장치 architecture에 맞는 image를 pull한
뒤 `fedops-agent-studio` container를 교체합니다. Workspace는 host의
`~/fedops-workspace`에 있으므로 image와 container가 바뀌어도 프로젝트, 로컬 데이터,
다운로드한 모델은 유지됩니다. 특정 cached image를 그대로 써야 하는 진단 상황에서만
`--no-pull`을 사용합니다.

FedOps library의 실제 실행기와 독립적으로 startup logic을 빠르게 검사할 때만 다음 local
prototype을 사용합니다.

```bash
python3 scripts/fedops_agent_studio_runner.py --dry-run
python3 scripts/fedops_agent_studio_runner.py
```

## 검증

```bash
npm run typecheck
npm run build
PYTHONPATH=services/studio-api/src:services/studio-runtime/src \
  .venv/bin/python -m unittest discover \
  -s services/studio-api/tests -p 'test_*.py'
```

구조는 [System overview](docs/architecture/system-overview.md), 코드 연결은
[Frontend–backend connection map](docs/architecture/frontend-backend-connections.md),
팀 작업 경계는 [Feature boundaries](docs/architecture/feature-boundaries.md)를
확인합니다.
