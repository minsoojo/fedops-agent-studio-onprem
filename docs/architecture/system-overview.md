# FedOps Agent Studio system overview

## 세 구성 요소

```text
React browser UI
      |
      | REST / Socket.IO
      v
Studio API control plane ------> FedOps Web / S3
      |
      | validated Python call
      v
Studio Python Runtime
      |
      +--> Workspace / uv environments / FedOps Task / Agent builds / PTY / processes / logs
      +--> account-local Hugging Face or Federated Task LLM
```

### Frontend

`apps/studio-web`는 화면과 사용자 상호작용을 담당합니다. Browser에서 실행되며
Python 위에서 실행되지 않습니다. Theme, 선택 tab처럼 화면 안에서 끝나는 상태는
Frontend가 관리하고, 저장·조회·실행이 필요한 작업은 Studio API를 호출합니다.

### Studio API

`services/studio-api`는 control plane입니다.

- FedOps 로그인과 local HttpOnly session
- FedOps 사용자 ID를 장치 전용 `accountKey`와 `AccountContext`로 변환
- FedOps Web/Registry/S3 연결
- 요청 권한과 입력 검증
- `/api/v1` REST 계약
- Socket.IO 인증과 Runtime terminal 연결

### Python Runtime

`services/studio-runtime`은 execution plane입니다.

- local Workspace project와 file
- owner별 uv Python environment와 `uv.lock` 동기화
- allow-list 기반 subprocess와 run state
- FedOps Federated Task 생성과 계약 검증
- 편집한 Python 파일의 선택된 uv 환경 실행
- interactive PTY
- account-local Agent draft와 Workspace source/model identity build snapshot
- Workspace Tool AI locked-uv 실행과 host-stored Base LLM inference

Runtime은 브라우저 session과 FedOps credential을 알지 않습니다.

## 계정 경계

Studio API는 모든 인증 HTTP 요청과 Socket.IO 연결에서 `AccountContext`를 만듭니다.

```text
FedOps login profile (_id)
  -> device-keyed HMAC accountKey
  -> /workspace/accounts/<accountKey>/projects
  -> Runtime namespace (run / process / terminal)
```

Runtime에는 FedOps ID나 credential을 전달하지 않고 account별 Workspace root와 opaque
namespace만 전달합니다. `localProjectId`가 같아도 `(accountKey, localProjectId)`가 다르면
서로 다른 run과 Terminal로 취급합니다. Frontend 전체 제품 shell도 `accountKey`를 React
key로 사용하므로 계정 전환 시 열린 편집 buffer와 선택 Task가 초기화됩니다.

## 소스 의존 규칙

```text
studio_web imports: app, features, api, ui
studio_api imports: features, integrations, config, session, studio_runtime
studio_runtime imports: Python standard library only
```

현재 로컬 배포는 React build, Studio API와 Runtime Python package를 하나의 이미지에
담습니다. 이는 배포 단위를 단순하게 하기 위한 것이며 소스 책임을 합치는 의미가
아닙니다. Workspace는 host volume으로 분리합니다.

## 데이터 원본

| 데이터 | 원본 |
| --- | --- |
| 사용자·권한 | FedOps Web |
| Federated Task·참여 승인 | FedOps Web |
| Global Model·Files & versions | FedOps Web/S3 |
| 프로젝트 코드·환경 메타데이터·`uv.lock` | 계정별 Studio Runtime Workspace |
| local run·process·log·Terminal | 계정별 Runtime namespace |
| 브라우저 theme·interface size·pane·Terminal 선택 | 계정별 Browser localStorage key |
| 기본 Baseline release | FedOps Web이 선택하고 S3에 불변 보관 |
| 검증 완료 Baseline·uv download cache | 장치 공용 Runtime cache |
| CPU·GPU·Memory hardware snapshot | token 보호 Host Bridge, 실패 시 Studio Runtime fallback |
| Agent draft·build·serving token hash·request metadata | 계정별 `.local-data/agents` |
| Task owner/participant raw dataset | 계정별 `.local-data/federated-tasks/<project>/dataset` |
| Base LLM weight/runtime | 계정별 `models/huggingface` 또는 Federated Task Workspace `model_release` |

## 식별자

- `taskId`: MongoDB의 안정 Federated Task ID
- `accountKey`: FedOps 사용자 ID에서 만든 장치 전용 opaque local namespace
- `runtimeKey`: FL server deployment와 접속 식별자
- `localProjectId`: local Workspace project 식별자
- `environmentId`: Task/Agent build/Agent serve가 선택하는 Python 환경 식별자
- `runId`: 하나의 Runtime 실행 식별자
- `agentId`: build/publish된 Agent 식별자
- `releaseId`: Registry에 Published된 immutable Federated Task source 식별자
- `modelVersionId`: Release와 결합된 exact Global Model 식별자
- `buildRevision`: 한 Agent의 immutable local build 순번
- `requestId`: Agent test/serving 요청 식별자

표시 이름을 위 식별자 대신 사용하지 않습니다.
