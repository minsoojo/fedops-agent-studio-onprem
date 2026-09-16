# Team workflow

## 작업 시작

1. 담당 Feature와 안정 ID를 적습니다.
2. API 변경이면 `contracts/studio-api.v1.yaml`을 먼저 수정합니다.
3. Frontend, API, Runtime 중 실제로 수정해야 하는 구성 요소만 선택합니다.

## 코드 위치

- 화면: `apps/studio-web/src/features/<feature>`
- 화면의 API 호출: `apps/studio-web/src/api/<feature>.ts`
- Backend 진입점: `services/studio-api/src/studio_api/features/<feature>`
- 외부 시스템: `services/studio-api/src/studio_api/integrations`
- local Python 실행: `services/studio-runtime/src/studio_runtime`

새 디렉터리는 파일이 실제로 두 책임으로 나뉘거나 독립 담당자가 생겼을 때만
만듭니다. 한 파일을 감싸는 추상 service/repository와 단순 re-export 파일은 만들지
않습니다.

## 완료 조건

- 화면에 직접 `fetch`나 shell command가 없음
- API에 UI 상태나 Runtime process 구현이 없음
- Runtime에 browser/FedOps credential 처리가 없음
- TypeScript typecheck와 build 통과
- Python unit test와 compile 통과
- 계약과 작업 문서 업데이트
