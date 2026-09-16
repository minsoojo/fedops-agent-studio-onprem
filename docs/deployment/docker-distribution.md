# Docker distribution

## Published image

기본 Docker Hub image는 다음과 같다.

```text
gachonccl/fedops-agent-studio:latest
```

`.github/workflows/docker-publish.yml`은 `main` push와 `v*` tag에서 검증 후
`linux/amd64`, `linux/arm64` image를 하나의 manifest로 게시한다. Docker는 pull하는
장치의 architecture에 맞는 image를 자동 선택한다.

두 architecture를 하나의 x86 runner에서 QEMU로 순차 교차 빌드하지 않는다.

- AMD64: `ubuntu-24.04` native runner
- ARM64: `ubuntu-24.04-arm` native runner
- 두 build는 병렬로 실행되고 image digest만 Docker Hub에 먼저 push한다.
- 마지막 publish job이 두 digest를 multi-architecture manifest로 묶는다.
- BuildKit GitHub Actions cache는 `agent-studio-amd64`, `agent-studio-arm64` scope로
  분리해 `llama-cpp-python` wheel compile layer를 architecture별로 재사용한다.

따라서 ARM64 `llama-cpp-python`을 QEMU에서 컴파일하는 병목이 사라지고, Dockerfile의
GGUF runtime dependency가 바뀌지 않은 후속 build는 cache된 wheel layer를 사용한다.

- `latest`: main의 최신 검증 build
- `sha-<commit>`: commit 단위 재현 tag
- `vX.Y.Z`, `X.Y.Z`, `X.Y`: Git version tag에서 생성되는 release tag

GitHub repository에는 다음 값이 한 번 설정되어야 한다.

- Actions variable `DOCKERHUB_USERNAME`
- Actions variable `DOCKERHUB_IMAGE`
- Actions secret `DOCKERHUB_TOKEN`

로컬 Docker Desktop credential은 GitHub runner로 자동 전달되지 않으므로 token을 Git에
저장하지 않고 GitHub Actions secret으로 전달한다.

## OS와 architecture

FedOps Agent Studio는 Linux container다.

- Linux Docker Engine: `linux/amd64` 또는 `linux/arm64`
- macOS Docker Desktop: Linux VM에서 host CPU에 맞는 variant 실행
- Windows Docker Desktop: Linux container/WSL2에서 `linux/amd64` variant 실행

Windows container mode 자체는 지원 대상이 아니다.

## GPU boundary

로컬 실행 prototype은 Docker NVIDIA Runtime을 감지하면 `--gpus all`을 추가한다.

- Linux: NVIDIA driver와 NVIDIA Container Toolkit이 필요하다.
- Windows: Docker Desktop WSL2의 NVIDIA GPU 지원이 필요하다.
- macOS: Docker Desktop container가 Apple GPU를 직접 사용하지 못하므로 CPU mode다.

현재 portable image의 `llama-cpp-python`은 CPU build다. `--gpus all`은 GPU를 container에
노출하는 실행 경계이며, 실제 local training/LLM acceleration은 해당 Python environment와
추후 별도 NVIDIA Runtime image가 CUDA package를 포함해야 한다. GPU를 감지했다는 이유만으로
CPU package가 자동으로 CUDA package로 바뀌지는 않는다.

## Local command prototype

사용자는 다음 공식 명령으로 Agent Studio를 실행한다.

```bash
fedops run agent-studio
```

명령을 다시 실행할 때마다 Docker Hub의 `latest` manifest를 확인한다. 새 digest가 있으면
현재 OS의 Docker가 실행 가능한 architecture variant를 pull하고 기존 named container를
새 image로 교체한다. 따라서 별도의 `docker pull`, image 삭제 또는 container 삭제가
필요하지 않다. `~/fedops-workspace`는 host bind mount이므로 업데이트 후에도 계정별
프로젝트, 로컬 데이터와 LLM 파일을 유지한다. `fedops-agent-studio-uv` Docker named volume은
uv package/Python cache를 유지해 특히 Windows Docker Desktop에서 다음 환경 동기화의
다운로드·압축 해제 반복을 줄인다. Task별 가상환경 자체는 기존처럼 Workspace에 남는다.

장치별 업데이트 순서는 다음과 같다.

1. Agent Studio `main`에 변경사항 push
2. GitHub Actions의 verify, AMD64/ARM64 native build, publish 완료 확인
3. 각 테스트 장치에서 `fedops run agent-studio` 재실행
4. 출력된 image digest와 `http://localhost:24368`의 변경사항 확인

이미 실행 중이어도 같은 명령을 재실행하면 해당 장치의 container만 짧게 교체된다.
개발 Mac의 `:8443` source development Web과 `fedops-agent-studio-dev-api` container에는
영향을 주지 않는다. cached image를 의도적으로 고정하는 진단 상황에서만 `--no-pull`을
사용한다.

저장소의 prototype은 공식 FedOps 명령을 수정하기 전 startup logic을 빠르게 검증할 때
사용한다.

```bash
python3 scripts/fedops_agent_studio_runner.py --dry-run
python3 scripts/fedops_agent_studio_runner.py
```

Prototype은 ASCII banner 출력 후 다음 순서로 동작한다.

1. Host OS와 architecture 확인
2. Docker CLI와 Engine 확인, 가능하면 Docker Desktop 시작
3. 최신 multi-architecture image pull
4. 기존 Agent Studio container 교체 및 이전 image 정리
5. `~/fedops-workspace`와 영속 uv cache volume 생성·mount
6. NVIDIA Runtime 자동 감지 또는 CPU mode 선택
7. host folder/hardware bridge 시작
8. Studio와 Agent API port를 모든 host interface에 publish
9. health 확인 후 browser 열기

동일한 production 실행 logic은 FedOps library의 `fedops run agent-studio` subcommand에
포함되어 있다. Prototype 변경은 검증 후에만 FedOps library와 동기화한다.

## Open Folder 연결 복구

폴더 열기는 Docker 호스트에서 실행되는 별도 프로그램이 담당한다. Docker 이미지가
최신이어도 호스트 `fedops` 패키지는 별도로 업데이트해야 한다.
아래 복구 명령은 FedOps 패키지 1.1.30.18 이상이 설치되어 있어야 사용할 수 있다.

```bash
python -m pip install -U fedops
fedops run agent-studio --repair-host
```

`--repair-host`는 실행 중인 컨테이너의 Workspace·인증 파일·실제 할당 포트를 확인해
호스트 프로그램을 복구하고 컨테이너에서 연결을 검사한다. 컨테이너나 학습을 재시작하지
않는다. 재부팅 후 Docker가 컨테이너만 자동 시작한 경우에도 사용할 수 있다.
기존 컨테이너에 호스트 연결 설정이 없으면 `fedops run agent-studio`로 다시 생성한다.

호스트 프로그램은 macOS/Linux에서 새 세션, Windows에서 detached process로 실행하며
터미널 입력을 상속하지 않는다. 하드웨어 조회는 백그라운드로 처리해 폴더 열기의 시작을
지연시키지 않는다. 로컬 호스트 통신은 시스템/HTTP 프록시를 우회한다.

오류는 인증 실패, 주소 조회 실패, 연결 거부, 시간 초과로 구분한다. 시간 초과가 지속되면
Docker → 호스트 연결의 방화벽/VPN 정책을 확인한다. 원격 브라우저에서도 폴더가 열리는
위치는 Docker가 실행 중인 컴퓨터다. 호스트 로그: `~/.fedops-agent-studio/runtime/host.log`.
Linux의 폴더 창 열기는 그래픽 데스크톱과 `xdg-open`이 있는 호스트를 전제로 한다.
