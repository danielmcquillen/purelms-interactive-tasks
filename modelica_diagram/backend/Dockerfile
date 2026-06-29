# modelica_diagram backend — Python container with a pre-compiled FMU.
#
# Multi-stage: builder installs deps into /pkgs; runtime copies deps + app.
# Dependency resolution mirrors echo — the justfile's _stage-shared-wheel
# recipe stages purelms-shared (>=0.5.0, with diagram.v1) + purelms-itask-
# runtime wheels into _vendor/; fmpy + numpy + jsonschema come from PyPI.

FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY __metadata__.py ./
COPY *.py ./
COPY _vendor/ /tmp/_vendor/
# runtime[cloud] pulls purelms-shared from _vendor + google-cloud-storage /
# google-auth from PyPI; the simulation deps come from PyPI.
RUN pip install --no-cache-dir --target /pkgs \
    --find-links /tmp/_vendor \
    "purelms-itask-runtime[cloud]" \
    "fmpy>=0.3,<1" "numpy>=1.26" "jsonschema>=4.0,<5"

FROM python:3.13-slim AS runtime
WORKDIR /app
# Modelica Buildings Library FMUs link libgomp (OpenMP) at runtime; install it
# once the FMU's exact shared-lib needs are confirmed (`ldd model/*.so`).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# Run as non-root (trust-boundary model).
RUN useradd --create-home --shell /bin/bash app
USER app
COPY --from=builder /pkgs /pkgs
COPY --from=builder /build /app
# Scenario data (palette/expected-graph + the future model.fmu) ships with the
# code so the topology check + the FMU run read the same pinned files.
COPY scenarios/ /app/scenarios/
ENV PYTHONPATH=/pkgs
# Convention paths the LMS DockerComposeExecutionBackend mounts:
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output
ENTRYPOINT ["python", "/app/main.py"]
