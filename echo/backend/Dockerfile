# Echo backend — minimal Python container.
#
# Multi-stage build keeps the final image small (~80MB):
#   - builder: install deps with pip into a target dir
#   - runtime: copy the deps + the app code, no build tooling
#
# Dependency resolution for ``purelms-shared``:
#
# - **Development:** the ``just build`` recipe stages a local wheel
#   of ``purelms-shared`` into ``_vendor/`` (sibling-repo path); we
#   install from that wheel.
# - **Release CI:** stages only ``purelms-itask-runtime``; pip resolves
#   its ``purelms-shared>=0.5.0`` dependency from PyPI. This keeps clean
#   checkouts independent of a sibling repository.

FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY __metadata__.py ./
COPY main.py ./
# _vendor/ contains locally-built wheels (see the justfile's
# _stage-shared-wheel recipe): purelms_shared-*.whl + the shared
# purelms_itask_runtime-*.whl. Install the runtime with its ``cloud``
# extra: pip resolves the runtime + purelms-shared from --find-links
# /tmp/_vendor and pulls google-cloud-storage / google-auth (GCS I/O +
# OIDC for the worker callbacks) from PyPI, so the same image meets the
# runtime contract on the local DockerCompose AND the Cloud Run Jobs path.
COPY _vendor/ /tmp/_vendor/
RUN pip install --no-cache-dir --target /pkgs \
    --find-links /tmp/_vendor \
    "purelms-itask-runtime[cloud]"

FROM python:3.13-slim AS runtime

ARG PURELMS_IMAGE_VERSION="dev"
ARG PURELMS_IMAGE_REVISION="unknown"
ARG PURELMS_IMAGE_SOURCE="https://github.com/danielmcquillen/purelms-interactive-tasks"
ARG PURELMS_BACKEND_SLUG="echo"
ARG PURELMS_TASK_VERSION="0.1.0"

LABEL org.opencontainers.image.title="PureLMS Echo InteractiveTask backend" \
      org.opencontainers.image.version="${PURELMS_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${PURELMS_IMAGE_REVISION}" \
      org.opencontainers.image.source="${PURELMS_IMAGE_SOURCE}" \
      io.purelms.interactive-task.slug="${PURELMS_BACKEND_SLUG}" \
      io.purelms.interactive-task.version="${PURELMS_TASK_VERSION}"

WORKDIR /app
# Run as non-root for security (per the trust-boundary model).
RUN useradd --create-home --shell /bin/bash app
USER app
COPY --from=builder /pkgs /pkgs
COPY --from=builder /build /app
ENV PYTHONPATH=/pkgs
# Convention paths the LMS DockerComposeExecutionBackend mounts:
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output
ENTRYPOINT ["python", "/app/main.py"]
