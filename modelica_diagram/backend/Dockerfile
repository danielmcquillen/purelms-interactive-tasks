# modelica_diagram backend — Python container with a pre-compiled FMU.
#
# Multi-stage: builder installs deps into /pkgs; runtime copies deps + app.
# Dependency resolution mirrors echo — the justfile's _stage-shared-wheel
# recipe stages purelms-shared (with diagram.v1) + purelms-itask-runtime
# wheels into _vendor/; release builds fetch the exact shared-contract version
# from PyPI. fmpy + numpy + jsonschema come from PyPI.
#
# The committed FMU contains linux/amd64 native code. Cloud Run uses amd64;
# Docker Desktop emulates this same image on Apple Silicon. The normal build
# recipe sets `--platform linux/amd64`; the uname guards make a direct native
# arm64 build fail instead of producing an image that cannot load the FMU.

FROM python:3.13-slim AS builder
RUN test "$(uname -m)" = x86_64
WORKDIR /build
COPY pyproject.toml ./
COPY __metadata__.py ./
COPY *.py ./
COPY _vendor/ /tmp/_vendor/
ARG PURELMS_SHARED_VERSION="0.5.0"
# runtime[cloud] uses the vendored wheels plus uv.lock-constrained external
# packages. google-auth[requests] provides the callback OIDC transport.
RUN pip install --no-cache-dir --target /pkgs \
    --find-links /tmp/_vendor \
    --constraint /tmp/_vendor/constraints.txt \
    "purelms-shared==${PURELMS_SHARED_VERSION}" \
    "purelms-itask-runtime[cloud]" \
    "fmpy>=0.3,<1" "numpy>=1.26" "jsonschema>=4.0,<5"
RUN PYTHONPATH=/pkgs python -c \
    "from google.auth.transport.requests import Request"

FROM python:3.13-slim AS runtime
RUN test "$(uname -m)" = x86_64

ARG PURELMS_IMAGE_VERSION="dev"
ARG PURELMS_IMAGE_REVISION="unknown"
ARG PURELMS_IMAGE_SOURCE="https://github.com/danielmcquillen/purelms-interactive-tasks"
ARG PURELMS_BACKEND_SLUG="modelica_diagram"
ARG PURELMS_TASK_VERSION="0.2.9"
ARG PURELMS_SHARED_VERSION="0.5.0"

LABEL org.opencontainers.image.title="PureLMS Modelica Diagram InteractiveTask backend" \
      org.opencontainers.image.version="${PURELMS_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${PURELMS_IMAGE_REVISION}" \
      org.opencontainers.image.source="${PURELMS_IMAGE_SOURCE}" \
      io.purelms.interactive-task.slug="${PURELMS_BACKEND_SLUG}" \
      io.purelms.interactive-task.version="${PURELMS_TASK_VERSION}" \
      io.purelms.shared-contract.version="${PURELMS_SHARED_VERSION}"

WORKDIR /app
# Modelica Buildings Library FMUs link libgomp (OpenMP) at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# Run as non-root (trust-boundary model).
RUN useradd --create-home --shell /bin/bash app
USER app
COPY --from=builder /pkgs /pkgs
COPY --from=builder /build /app
# Scenario data (palette/expected-graph + model.fmu) ships with the
# code so the topology check + the FMU run read the same pinned files.
COPY scenarios/ /app/scenarios/
ENV PYTHONPATH=/pkgs
# Convention paths the LMS DockerComposeExecutionBackend mounts:
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output
ENTRYPOINT ["python", "/app/main.py"]
