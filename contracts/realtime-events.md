# Realtime event contract

Socket.IO는 terminal byte stream과 장시간 run event에만 사용한다. 목록/설정 CRUD는
REST를 사용한다. 새 event는 모두 `runId`와 event schema version을 포함해야 한다.

## Workspace terminal event

- input: `terminal_list`, `terminal_create`, `terminal_context`, `terminal_restart`, `terminal_close`,
  `terminal_input`, `terminal_resize`, `terminal_clear`
- output: `terminal_output`

`terminal_create`는 `localProjectId`와 선택된 `environmentId`를 받는다. 서버가 실제
project와 uv environment 경로 및 ready 상태를 검증한 뒤 독립된 terminal ID와 PTY를
생성한다. `terminal_context`는 그 terminal ID의 room으로 socket을 전환한다. 브라우저가
`cd`, activate script 또는 임의 environment 경로를 만들지 않는다. Project당 최대 8개
terminal을 허용한다. Session 응답의 `title`은 `Terminal N`, `shellName`은 실제 runtime
shell(`bash`)이다. Session은 선택 environment가 ready가 아니어도 `environmentId`,
`environmentLabel`, 정확한 `environmentStatus`를 반환한다. 새 번호는 현재 활성 session의
가장 큰 번호 다음으로 정한다. 따라서 마지막 번호를 닫으면 그 번호를 재사용하지만 낮은
번호만 닫으면 중간 번호를 채우지 않는다. 모든 terminal을 닫으면 다음 생성 번호는 1부터
다시 시작한다.
`terminal_output.replay=true`는 재연결 시 Runtime의 최근 scrollback snapshot이므로
client는 기존 emulator buffer를 교체한다.

`terminal_restart`는 `localProjectId`, 교체할 `terminalId`, 현재 선택한 `environmentId`를
받는다. 서버는 같은 account와 project 소유권 및 environment를 다시 검증하고 기존 PTY를
종료한 뒤 같은 tab title의 새 terminal ID와 PTY를 반환한다. 실행 중 shell의 environment를
조용히 변경하지 않으며 Frontend가 사용자의 명시적인 restart 동작에서만 호출한다.

브라우저는 로그인 session cookie로 Socket.IO에 연결한다. Terminal은 사용자가 직접
입력하는 interactive shell에만 사용한다. Install, Validation, Local Run처럼 제품이
제공하는 작업은 REST action endpoint를 사용하며 브라우저가 shell command를 만들지
않는다.

서버는 Socket session의 `accountKey`와 Terminal 소유 `accountKey`가 일치할 때만 목록,
context 전환, 입력, resize, clear와 close를 허용한다. Project Terminal 목록과 이름 순번의
내부 key는 `(accountKey, localProjectId)`이며 다른 계정에서 같은 `localProjectId`를
사용해도 별개의 Terminal namespace로 취급한다. `accountKey`는 realtime payload에
노출하거나 브라우저 입력값으로 받지 않고 인증 session에서만 결정한다.

## Managed run event

```json
{
  "event": "run.output",
  "schemaVersion": 1,
  "runId": "run_01...",
  "sequence": 42,
  "stream": "stdout",
  "text": "Epoch 2/3..."
}
```

```json
{
  "event": "run.status",
  "schemaVersion": 1,
  "runId": "run_01...",
  "status": "succeeded",
  "exitCode": 0,
  "occurredAt": "2026-08-03T12:00:00Z"
}
```

Managed run은 현재 REST polling으로 상태와 출력을 조회한다. 실시간 stream을 추가할
때 위 event를 사용하고 재연결 시 client가 마지막 `sequence`를 보내 누락 event를
재조회할 수 있어야 한다. Interactive terminal을 Agent/FL 실행 상태로 사용하지 않는다.
