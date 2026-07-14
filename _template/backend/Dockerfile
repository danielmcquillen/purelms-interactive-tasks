# Multi-stage build skeleton.
# Replace TODO markers with your slug name + domain code.

FROM python:3.13-slim AS builder

WORKDIR /build

# Copy the pyproject + vendored wheels (purelms-shared +
# purelms-itask-runtime, staged by `just _stage-shared-wheel <slug>`) and
# install. Installing ``purelms-itask-runtime[cloud]`` pulls the runtime
# + purelms-shared from /vendor and google-cloud-storage / google-auth
# (GCS I/O + OIDC for the worker callbacks) from PyPI — so the same image
# satisfies the contract on the local DockerCompose AND Cloud Run paths.
COPY pyproject.toml ./
COPY _vendor /vendor/
RUN pip install --no-cache-dir --find-links /vendor "purelms-itask-runtime[cloud]"
RUN pip install --no-cache-dir --find-links /vendor .

# ---------------------------------------------------------------------

FROM python:3.13-slim

ARG PURELMS_IMAGE_VERSION="dev"
ARG PURELMS_IMAGE_REVISION="unknown"
ARG PURELMS_IMAGE_SOURCE="https://github.com/danielmcquillen/purelms-interactive-tasks"
ARG PURELMS_BACKEND_SLUG="replace_me"
ARG PURELMS_TASK_VERSION="0.1.0"

LABEL org.opencontainers.image.title="PureLMS InteractiveTask backend" \
      org.opencontainers.image.version="${PURELMS_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${PURELMS_IMAGE_REVISION}" \
      org.opencontainers.image.source="${PURELMS_IMAGE_SOURCE}" \
      io.purelms.interactive-task.slug="${PURELMS_BACKEND_SLUG}" \
      io.purelms.interactive-task.version="${PURELMS_TASK_VERSION}"

# Non-root user for the runtime container.
RUN useradd --create-home --uid 1000 purelms
USER purelms
WORKDIR /home/purelms

# Copy the installed site-packages from the builder stage.
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Copy the container entrypoint.
COPY --chown=purelms:purelms main.py ./

# Per the contract (the runtime helper handles both modes):
#   Local: read $PURELMS_INPUT_DIR/input.json, write $PURELMS_OUTPUT_DIR/output.json
#   Async: read $PURELMS_INPUT_URI (gs://), write $PURELMS_OUTPUT_URI (gs://)
#          + POST the /complete callback.
#   Exit 0 on success; non-zero on failure (or undeliverable completion).
# These local-dir defaults are overridden by the worker on the GCS path.
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output

ENTRYPOINT ["python", "main.py"]
