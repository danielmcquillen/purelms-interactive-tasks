# purelms-backends recipes
#
# Each backend has a Docker image build + a TypeScript bundle build.
# Recipes operate per-slug (e.g. `just build echo`) or across all
# slugs (`just build-all`).

default:
    @just --list

# ---------------------------------------------------------------------
# Container builds
# ---------------------------------------------------------------------

# Stage a local purelms-shared wheel into <slug>/backend/_vendor/
# so the container build can install it without PyPI access. This
# is the dev-only path; once purelms-shared is published (planned
# Phase 5) the wheel staging goes away and the Dockerfile installs
# from PyPI directly.
#
# Path is the sibling repo per workspace convention. Override with
# PURELMS_SHARED_PATH if checked out elsewhere.
_stage-shared-wheel slug shared_path=env_var_or_default("PURELMS_SHARED_PATH", "../purelms-shared"):
    @test -d "{{shared_path}}" || (echo "purelms-shared not found at {{shared_path}}" && exit 1)
    @rm -rf "{{slug}}/backend/_vendor"
    @mkdir -p "{{slug}}/backend/_vendor"
    cd "{{shared_path}}" && uv build --wheel --out-dir "{{justfile_directory()}}/{{slug}}/backend/_vendor"

# Build the container image for one backend.
# Usage: just build echo
build slug: (_stage-shared-wheel slug)
    docker build -t purelms-backends-{{slug}}:dev {{slug}}/backend

# Build all backends' container images.
build-all:
    just build echo

# Push one backend's image to the configured registry.
# Requires DOCKER_IMAGE_REGISTRY env var, e.g.:
#   DOCKER_IMAGE_REGISTRY=us-central1-docker.pkg.dev/<proj>/purelms
push slug registry=env_var_or_default("DOCKER_IMAGE_REGISTRY", ""):
    @test -n "{{registry}}" || (echo "DOCKER_IMAGE_REGISTRY not set" && exit 1)
    docker tag purelms-backends-{{slug}}:dev {{registry}}/purelms-backends-{{slug}}:latest
    docker push {{registry}}/purelms-backends-{{slug}}:latest

# ---------------------------------------------------------------------
# Frontend bundle builds
# ---------------------------------------------------------------------

# Build the TS bundle for one backend.
# Output: <slug>/frontend/dist/<slug>.js
# Operators copy this to PureLMS's static dir; convention:
#   purelms/static/backends/<slug>/<slug>.js
frontend-build slug:
    cd {{slug}}/frontend && pnpm run build

# Build all backend frontends.
frontend-build-all:
    just frontend-build echo

# ---------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------

# Run tests for one backend (Python + TypeScript).
test slug:
    cd {{slug}}/backend && uv run pytest
    cd {{slug}}/frontend && pnpm test

# Run tests across all backends + the workspace lint.
test-all:
    uv run pytest
    just test echo

# Lint + format the whole workspace.
lint:
    uv run ruff check .
    uv run ruff format --check .
