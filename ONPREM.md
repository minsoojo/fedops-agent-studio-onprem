# fedops-agent-studio-onprem

온프레미스용 정제 소스와 설정 외부화 작업본입니다. 기존 Git 이력의 fork가 아닌 새 초기 이력입니다. 원본 라이선스와 저작자 표기를 유지합니다.

- 원본 범위·파일 해시: [SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json). 전달본은 일부 저장소의 부분 export입니다.
- 포함된 Dockerfile은 로컬 검증에 사용한 digest 기반 레시피입니다. .env·실제 자격정보는 포함하지 않습니다.
- 새 GitHub 주소를 Task Runtime이 사용하는 연결과 F/FL 검증은 아직 완료하지 않았습니다.
- 기존 upstream 자동 게시 workflow는 실행하지 않도록 이관에서 제외했습니다.
- 배포 설정: https://github.com/minsoojo/fedops-deployment
