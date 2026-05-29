# EnergyPlus single-zone backend — multi-stage build (real EnergyPlus).
#
# EnergyPlus™ is a trademark of the U.S. Department of Energy, distributed
# under a BSD-3-Clause license by NREL. The license ships inside the
# extracted /opt/energyplus tree. PureLMS is not affiliated with DOE/NREL.
# See: https://github.com/NREL/EnergyPlus/blob/develop/LICENSE.txt
#
# Build context is the backend directory (this folder). The shared wheel
# is staged into _vendor/ by `just _stage-shared-wheel energyplus_single_zone`.
#
# Translated from Validibot's EnergyPlus validator Dockerfile (the
# adopt-by-copy reference); re-implemented for the educational single-zone
# task, which ALSO bundles weather files (the validator downloaded them
# per-run from object storage; we bake one representative EPW per climate
# zone into the image since the learner only picks a zone, not a file).

# =============================================================================
# Stage 1: fetch EnergyPlus + the bundled EPW weather files
# =============================================================================
FROM ubuntu:22.04 AS eplus-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

# EnergyPlus 25.2.0 (pin matches the Validibot reference). Update the URL
# for a different version: https://github.com/NREL/EnergyPlus/releases
RUN wget -q https://github.com/NREL/EnergyPlus/releases/download/v25.2.0/EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu22.04-x86_64.tar.gz \
    && tar -xzf EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu22.04-x86_64.tar.gz \
    && mv EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu22.04-x86_64 /opt/energyplus \
    && rm EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu22.04-x86_64.tar.gz

# Bundled TMY weather files — one representative city per ASHRAE zone the
# manifest exposes (4A / 5A / 6A). Saved under the names climate.py's
# ClimateZone.epw_filename expects. The runner references the local name
# only, so re-sourcing weather is a Dockerfile-only change.
#
# ⚠️ Verify these URLs resolve at build time. The DOE weather set moves
# occasionally; if a 404 occurs, source the equivalent TMY3 EPW from
# https://energyplus.net/weather or https://climate.onebuilding.org and
# update the URL. The filenames on the right are what the runner expects.
RUN mkdir -p /opt/weather \
    && wget -q -O /opt/weather/4A_new_york.epw \
       "https://energyplus-weather.s3.amazonaws.com/north_and_central_america_wmo_region_4/USA/NY/USA_NY_New.York-J.F.Kennedy.Intl.AP.744860_TMY3/USA_NY_New.York-J.F.Kennedy.Intl.AP.744860_TMY3.epw" \
    && wget -q -O /opt/weather/5A_chicago.epw \
       "https://energyplus-weather.s3.amazonaws.com/north_and_central_america_wmo_region_4/USA/IL/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw" \
    && wget -q -O /opt/weather/6A_minneapolis.epw \
       "https://energyplus-weather.s3.amazonaws.com/north_and_central_america_wmo_region_4/USA/MN/USA_MN_Minneapolis-St.Paul.Intl.AP.726580_TMY3/USA_MN_Minneapolis-St.Paul.Intl.AP.726580_TMY3.epw"

# =============================================================================
# Stage 2: runtime container
# =============================================================================
FROM python:3.13-slim

# EnergyPlus runtime libs: libgomp1 (OpenMP), libx11-6 + libxext6 (the
# binary links X11 even headless). sudo is intentionally absent.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libx11-6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# EnergyPlus binary + bundled weather, from the builder stage.
COPY --from=eplus-builder /opt/energyplus /opt/energyplus
COPY --from=eplus-builder /opt/weather /opt/weather
ENV PATH="/opt/energyplus:${PATH}"

# Python deps: install purelms-shared from the vendored wheel (staged by
# the justfile). No scientific deps — the runner shells out to the binary
# and parses eplusout.sql with the stdlib sqlite3.
WORKDIR /home/purelms
COPY pyproject.toml ./
COPY _vendor /vendor/
RUN pip install --no-cache-dir --find-links /vendor purelms-shared

# Non-root runtime user (uid 1000, matches the run-scoped-isolation policy).
RUN useradd --create-home --uid 1000 purelms
USER purelms

# App code. main.py does `from runner import simulate`; runner does
# `from climate import ...` and reads idf/single_zone.idf.template — so all
# four must sit together in the working directory.
COPY --chown=purelms:purelms main.py runner.py climate.py ./
COPY --chown=purelms:purelms idf ./idf

# Standard PureLMS backend contract.
ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output
# Force the real EnergyPlus path in the image (fail loud if the binary is
# somehow missing rather than silently falling back to the analytical
# model). Weather lives where the builder stage put it.
ENV PURELMS_EPLUS_MODE=energyplus
ENV PURELMS_EPLUS_WEATHER_DIR=/opt/weather

ENTRYPOINT ["python", "main.py"]
