# syntax=docker/dockerfile:1
ARG PYTHON_BASE=python:3.12.15-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d

FROM ${PYTHON_BASE} AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
COPY docker/pip-bootstrap.lock /build/pip-bootstrap.lock
COPY requirements/docker-cpu-py312-linux-amd64.lock /build/production.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-compile -r /build/pip-bootstrap.lock && \
    /opt/venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-compile -r /build/production.lock && \
    /opt/venv/bin/python -m pip check

FROM ${PYTHON_BASE} AS model-tools
COPY docker/package_model.py docker/bge-m3-manifest.json /build/
COPY docker/licenses/ /build/licenses/

FROM model-tools AS model-download
RUN --mount=type=cache,target=/model-download \
    python /build/package_model.py --download-cache /model-download --output /model

# Supply only the pinned snapshot directory through --build-context model_snapshot=PATH.
# This target never downloads and never includes the host model in the main context.
FROM model-tools AS model-local
RUN --mount=type=bind,from=model_snapshot,target=/snapshot,readonly \
    python /build/package_model.py --source /snapshot --output /model

FROM ${PYTHON_BASE} AS runtime-base
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEMORY_DATA_DIR=/data \
    MEMORY_MODEL_DIR=/opt/remember-me/models \
    MEMORY_STORAGE=dual \
    EMBED_PROVIDER=sentence_transformers \
    SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-m3 \
    SENTENCE_TRANSFORMERS_DEVICE=cpu \
    HOME=/data \
    TMPDIR=/data/tmp \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/cache/huggingface \
    TORCH_HOME=/data/cache/torch
RUN groupadd --gid 10001 remember-me && \
    useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home remember-me && \
    install -d -o 10001 -g 10001 /data
COPY --from=deps /opt/venv /opt/venv
WORKDIR /opt/remember-me
EXPOSE 1314
USER 10001:10001
ENTRYPOINT ["python", "/opt/remember-me/docker/entrypoint.py"]
CMD ["--host", "0.0.0.0", "--port", "1314"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "/opt/remember-me/docker/healthcheck.py"]
LABEL org.opencontainers.image.source="https://github.com/tech-kuiperbelt/remember-me" \
      org.opencontainers.image.licenses="Apache-2.0" \
      io.remember-me.model="BAAI/bge-m3" \
      io.remember-me.model.revision="5617a9f61b028005a4858fdac845db406aefb181" \
      io.remember-me.model.license="MIT"

FROM runtime-base AS runtime-local
COPY --from=model-local /model/ /opt/remember-me/models/
COPY app/ ./app/
COPY web/ ./web/
COPY LICENSE ./LICENSE
COPY docker/entrypoint.py docker/healthcheck.py ./docker/

FROM runtime-local AS validation-local
COPY bin/verify_cpu_runtime.py ./bin/verify_cpu_runtime.py
COPY requirements/docker-cpu-py312-linux-amd64.lock ./requirements/docker-cpu-py312-linux-amd64.lock

# The default target uses the official download path. Validation targets only
# add files to the runtime; no runtime stage inherits validation tools.
FROM runtime-base AS runtime-download
COPY --from=model-download /model/ /opt/remember-me/models/
COPY app/ ./app/
COPY web/ ./web/
COPY LICENSE ./LICENSE
COPY docker/entrypoint.py docker/healthcheck.py ./docker/

FROM runtime-download AS validation
COPY bin/verify_cpu_runtime.py ./bin/verify_cpu_runtime.py
COPY requirements/docker-cpu-py312-linux-amd64.lock ./requirements/docker-cpu-py312-linux-amd64.lock

FROM runtime-download AS runtime
