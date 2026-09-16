ARG UV_VERSION=0.11.32

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

FROM node:20.19-bookworm-slim AS web-build
WORKDIR /build
COPY package.json package-lock.json ./
COPY apps/studio-web/package.json ./apps/studio-web/package.json
RUN npm ci
COPY apps/studio-web/ ./apps/studio-web/
RUN npm run build --workspace @fedops/studio-web

FROM python:3.12-slim AS gguf-runtime-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_OPENMP=ON"
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels llama-cpp-python==0.3.34

FROM python:3.12-slim
WORKDIR /opt/fedops-agent-studio
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_TERMINAL_PROMPT=0 \
    PYTHONPATH=/opt/fedops-agent-studio/services/studio-api/src:/opt/fedops-agent-studio/services/studio-runtime/src \
    WORKSPACE_DIR=/workspace \
    STUDIO_BASELINE_CACHE_DIR=/workspace/.fedops-studio/baselines \
    STUDIO_RUN_HISTORY_DIR=/workspace/.fedops-studio/runtime-history \
    STUDIO_HOST=0.0.0.0 \
    STUDIO_PORT=24368 \
    UV_CACHE_DIR=/var/cache/fedops-uv/cache \
    UV_PYTHON_INSTALL_DIR=/var/cache/fedops-uv/python \
    UV_LINK_MODE=copy
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv-bin /uv /uvx /usr/local/bin/
COPY --from=gguf-runtime-build /wheels /tmp/gguf-wheels
COPY services/studio-api/requirements.txt ./studio-api-requirements.txt
COPY services/studio-runtime/requirements.txt ./studio-runtime-requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir /tmp/gguf-wheels/llama_cpp_python-*.whl \
    && python -m pip install --no-cache-dir \
      -r studio-api-requirements.txt \
      -r studio-runtime-requirements.txt \
    && rm -rf /tmp/gguf-wheels
COPY services/studio-api/ ./services/studio-api/
COPY services/studio-runtime/ ./services/studio-runtime/
COPY --from=web-build /build/apps/studio-web/dist ./apps/studio-web/dist
RUN mkdir -p /workspace /var/cache/fedops-uv
VOLUME ["/workspace"]
EXPOSE 24368 24400-24499
CMD ["python", "-m", "studio_api.server"]
