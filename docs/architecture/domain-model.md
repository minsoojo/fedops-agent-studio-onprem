# Shared domain model

## 서로 다른 핵심 개체

| 개체 | 안정 ID | 의미 |
|---|---|---|
| Local account context | `accountKey` | 한 장치에서 FedOps 사용자별 Workspace와 Runtime 경계 |
| Federated Task | `taskId` | FedOps Web/MongoDB의 연합학습 정의 |
| Runtime target | `runtimeKey` | FL protocol에서 사용하는 Task ID; K8s runtime 조회 키 |
| Global Model | `globalModelId` + `modelVersion` | Task에서 생성된 사용 가능 모델 version |
| Local Project | `localProjectId` | 하나의 로컬 Workspace와 venv |
| Participation | `participationId` | 사용자와 Task의 참여/승인 관계 |
| Local Run | `runId` | install, validate, local train, participate 실행 |
| Agent | `agentId` + `agentVersion` | 모델/tool reference를 묶은 Agent manifest |

표시 이름은 모두 수정 가능하다. 따라서 URL, 파일 binding, API mutation, runtime
접속에 표시 이름을 ID처럼 사용하면 안 된다.

`accountKey`는 FedOps Web의 불변 사용자 `_id`를 장치에 저장한 random key로 HMAC하여
생성한다. 원본 `_id`, username, handle은 로컬 경로에 사용하지 않는다. 계정별 경로는
`/workspace/accounts/<accountKey>/projects`이며 `accountKey`는 인증 session에서만 결정하고
클라이언트가 API/Socket payload로 지정하지 않는다.

## 관계

```text
FederatedTask 1 --- N GlobalModelVersion
FederatedTask 1 --- N Participation
FederatedTask 1 --- 0..N LocalProject
LocalProject  1 --- N LocalRun
LocalAccount  1 --- N LocalProject
Agent         1 --- N GlobalModelReference
GlobalModelReference N --- 1 FederatedTask
```

한 사용자/장치에서는 같은 `taskId`의 기본 local project를 하나만 생성한다.
명시적 fork는 새 `localProjectId`와 새 owner Task draft를 만들며 원본 Task의
`sourceTaskId`를 기록한다.

## 상태 소유권

- Registry: access, approval policy, participation status, published versions
- Workspace: file dirty state, data mapping, local config, validation, assets
- Federated Learning: client process, server connection, round stage, upload state
- Agent Builder: draft graph, model/tool bindings, validation result
- Agents: built package, serving process, endpoint, request/runtime telemetry

화면 전환 시 상태를 복사하지 않고 ID를 전달한 뒤 공용 store에서 projection을
다시 구한다.

## Federated Learning 연결 계약

연합학습 클라이언트는 `runtimeKey`에 담긴 FL Task ID로 Server Manager에 연결
정보를 조회한다. 클라이언트나 Workspace는 gateway IP/port를 고정값으로 보관하거나
직접 설정하지 않는다. Server Manager가 매 조회 시 Task ID를 현재 Kubernetes
Service와 Istio VirtualService 경로로 해석하고, 그 시점의 endpoint를 반환한다.

따라서 endpoint가 재할당되어도 Task ID는 유지되며, 동일 Task의 metadata port와
Istio route port는 항상 같은 원자적 할당 결과여야 한다.

## Join 상태 전이

```text
not-joined
  -> pending-approval -> approved -> workspace-imported -> ready -> active
  -> joined-immediately -----------^
```

승인 취소/탈퇴는 remote participation을 변경한다. 로컬 Workspace 삭제는 별도
사용자 동작이며 자동으로 수행하지 않는다.

## 로컬 binding schema

`.fedops-task.json`은 다음 최소 값만 보관한다.

```json
{
  "schemaVersion": 1,
  "taskId": "507f1f77bcf86cd799439011",
  "runtimeKey": "mnist-runtime-v2",
  "displayName": "MNIST Federated Task"
}
```

FedOps Web의 전체 YAML, access token, S3 credential은 binding에 저장하지 않는다.
