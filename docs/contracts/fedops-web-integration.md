# FedOps Web integration

Studio API만 FedOps Web에 인증 요청을 보냅니다. Browser에는 local HttpOnly session만
제공하고 FedOps token이나 S3 장기 credential을 노출하지 않습니다.

| 기능 | FedOps Web API | Studio 처리 |
| --- | --- | --- |
| 로그인 | `POST /api/auth/login` | cookie/token을 local memory session에 보관 |
| 계정 Task | `GET /api/tasks?username=...` | 안정 `taskId` read model로 변환 |
| Registry | `GET /api/tasks/public`, `/authorized` | handle/slug 기반 Registry model로 변환 |
| Task Hub | `GET /api/model/.../hub` | model/file/activity projection |
| 참여 요청 | `POST /api/tasks/:taskTitle/participants` | handle/slug를 조회한 뒤 server locator로 변환 |
| File/model download | download endpoint | 짧은 수명의 download URL을 그대로 전달 |
| 기본 Baseline | `GET /api/baselines/default` | manifest와 S3 URL을 받아 Runtime 검증 cache로 설치 |

Studio API adapter는 FedOps Web 응답의 차이를 흡수하고 Frontend에는
`contracts/studio-api.v1.yaml`의 안정 형식만 반환합니다.

연합학습 참여 연동 시 FedOps Web이 제공해야 할 정보:

- `taskId`, `runtimeKey`, server endpoint
- 승인된 참여자용 단기 접속 자격증명
- model/baseline file manifest와 checksum
- 입력 feature와 preprocessing 계약
- FedOps Task schema version과 검증·참여 parameter

Runtime은 위 정보를 Studio API가 검증하고 정규화한 뒤에만 전달받습니다.

## Baseline 공급 경계

`FedOps-SiloBaseline` Git 저장소는 개발·검증·release 제작에만 사용합니다. 제품 실행 중
Web과 Agent Studio는 그 저장소를 clone하거나 pull하지 않습니다.

1. release 도구가 `baseline-manifest.json`과 파일을 불변 S3 prefix에 한 번 게시합니다.
2. Web은 배포 설정의 기본 release를 선택하고 새 Task의 MongoDB 문서에 그 버전을 고정합니다.
3. 로그인된 Studio는 Web의 `/api/baselines/default`에서 manifest와 짧은 수명의 URL을 받습니다.
4. Studio Runtime은 경로, file count, size, content type, role, edit policy와 SHA-256을 검증합니다.
5. 검증된 release만 장치 공용 `.fedops-studio/baselines` cache와 계정별 Workspace에 들어갑니다.

Browser와 Runtime에는 S3 장기 credential, Git URL 또는 Git credential을 전달하지 않습니다.
