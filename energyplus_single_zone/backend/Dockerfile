# EnergyPlus single-zone backend — multi-stage build.
#
# v1 is stubbed (pure-Python thermal model in runner.py), so the
# image is small and dependency-light. When real EnergyPlus lands
# (Slice 4+), a third stage will install the EnergyPlus binary
# (~500 MB) and the runner will subprocess into it.

FROM python:3.13-slim AS builder

WORKDIR /build

# Copy the pyproject + vendored purelms-shared wheel (staged by
# ``just _stage-shared-wheel energyplus_single_zone``) and install
# dependencies.
COPY pyproject.toml ./
COPY _vendor /vendor/
RUN pip install --no-cache-dir --find-links /vendor purelms-shared
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------------

FROM python:3.13-slim

# Non-root user for the runtime container (uid 1000 by convention,
# matches the run-scoped-isolation policy).
RUN useradd --create-home --uid 1000 purelms
USER purelms
WORKDIR /home/purelms

# Copy the installed site-packages from the builder stage.
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Copy the container entrypoint + runner. main.py imports runner.py
# via a relative ``from runner import simulate`` so they must sit
# in the same directory.
COPY --chown=purelms:purelms main.py runner.py ./

# Standard PureLMS contract:
#   Read input envelope from $PURELMS_INPUT_DIR/input.json
#   Write output envelope to $PURELMS_OUTPUT_DIR/output.json
#   Exit 0 on success; non-zero on contract violation
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output

ENTRYPOINT ["python", "main.py"]
