# Echo backend — minimal Python container.
#
# Multi-stage build keeps the final image small (~80MB):
#   - builder: install deps with pip into a target dir
#   - runtime: copy the deps + the app code, no build tooling
#
# Dependency resolution for ``purelms-shared``:
#
# - **Dev (today):** the ``just build`` recipe stages a local wheel
#   of ``purelms-shared`` into ``_vendor/`` (sibling-repo path); we
#   install from that wheel.
# - **Production (Phase 5+):** ``purelms-shared`` will be published
#   to PyPI; the ``_vendor/`` directory will be empty / absent and
#   the install line will switch to ``pip install
#   purelms-shared>=0.2.0``. Until then, the local wheel is the
#   only way to reach a working image.

FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY __metadata__.py ./
COPY main.py ./
# _vendor/ contains a locally-built purelms_shared-*.whl (see
# justfile's _stage-shared-wheel recipe). The wildcard tolerates
# version bumps without Dockerfile changes.
COPY _vendor/ /tmp/_vendor/
RUN pip install --no-cache-dir --target /pkgs /tmp/_vendor/purelms_shared-*.whl

FROM python:3.13-slim AS runtime
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
