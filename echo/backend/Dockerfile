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
# - **Release CI:** stages only ``purelms-itask-runtime``; pip downloads the
#   inventory-pinned ``purelms-shared`` version from PyPI. This keeps clean
#   checkouts independent of a sibling repository.

FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY __metadata__.py ./
COPY main.py ./
ARG PURELMS_SHARED_VERSION="0.5.0"
# _vendor/ contains locally-built wheels (see the justfile's
# _stage-shared-wheel recipe): purelms_shared-*.whl + the shared
# purelms_itask_runtime-*.whl. Install the runtime with its ``cloud``
# extra: pip resolves the runtime + purelms-shared from --find-links and uses
# the uv.lock constraints for external packages. google-auth provides callback
# OIDC; object I/O uses signed HTTPS URLs and the standard library.
COPY _vendor/ /tmp/_vendor/
RUN pip install --no-cache-dir --target /pkgs \
    --find-links /tmp/_vendor \
    --constraint /tmp/_vendor/constraints.txt \
    "purelms-shared==${PURELMS_SHARED_VERSION}" \
    "purelms-itask-runtime[cloud]"

FROM python:3.13-slim AS runtime

ARG PURELMS_IMAGE_VERSION="dev"
ARG PURELMS_IMAGE_REVISION="unknown"
ARG PURELMS_IMAGE_SOURCE="https://github.com/danielmcquillen/purelms-interactive-tasks"
ARG PURELMS_BACKEND_SLUG="echo"
ARG PURELMS_TASK_VERSION="0.1.1"
ARG PURELMS_SHARED_VERSION="0.5.0"

LABEL org.opencontainers.image.title="PureLMS Echo InteractiveTask backend" \
      org.opencontainers.image.version="${PURELMS_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${PURELMS_IMAGE_REVISION}" \
      org.opencontainers.image.source="${PURELMS_IMAGE_SOURCE}" \
      io.purelms.interactive-task.slug="${PURELMS_BACKEND_SLUG}" \
      io.purelms.interactive-task.version="${PURELMS_TASK_VERSION}" \
      io.purelms.shared-contract.version="${PURELMS_SHARED_VERSION}"

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
