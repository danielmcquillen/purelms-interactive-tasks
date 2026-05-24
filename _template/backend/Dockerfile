# Multi-stage build skeleton.
# Replace TODO markers with your slug name + domain code.

FROM python:3.13-slim AS builder

WORKDIR /build

# Copy the pyproject + vendored purelms-shared wheel (staged by
# `just _stage-shared-wheel <slug>`) and install dependencies.
COPY pyproject.toml ./
COPY _vendor /vendor/
RUN pip install --no-cache-dir --find-links /vendor purelms-shared
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------------

FROM python:3.13-slim

# Non-root user for the runtime container.
RUN useradd --create-home --uid 1000 purelms
USER purelms
WORKDIR /home/purelms

# Copy the installed site-packages from the builder stage.
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Copy the container entrypoint.
COPY --chown=purelms:purelms main.py ./

# Per the contract:
#   Read input envelope from $PURELMS_INPUT_DIR/input.json
#   Write output envelope to $PURELMS_OUTPUT_DIR/output.json
#   Exit 0 on success; non-zero on failure
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output

ENTRYPOINT ["python", "main.py"]
