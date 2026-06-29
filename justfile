# purelms-interactive-tasks recipes
#
# Each InteractiveTask has a Docker image build + an ES module bundle
# build. Recipes operate per-slug (e.g. `just build echo`) or across
# all slugs (`just build-all`).
#
# Naming convention:
#   - Slug stays snake_case at the directory level (e.g. echo,
#     energyplus_single_zone)
#   - Docker image name derives a hyphenated alias at the boundary
#     (purelms-itask-<slug-with-hyphens>:<version>)
#   - The s/_/-/g conversion is done once, here in the justfile.

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
    # Also stage the in-repo shared backend-runtime wheel (local-dir vs
    # GCS envelope I/O + progress/complete worker callbacks). The
    # Dockerfile installs ``purelms-itask-runtime[cloud]`` from _vendor/,
    # which pulls the purelms-shared wheel staged above + google-cloud-
    # storage / google-auth from PyPI.
    cd "{{justfile_directory()}}/_shared_backends/purelms_itask_runtime" && uv build --wheel --out-dir "{{justfile_directory()}}/{{slug}}/backend/_vendor"

# Build the container image for one InteractiveTask.
# Image name derives from slug by s/_/-/g.
# Usage: just build echo
# Usage: just build energyplus_single_zone
build slug: (_stage-shared-wheel slug)
    docker build -t purelms-itask-$(echo "{{slug}}" | tr '_' '-'):dev {{slug}}/backend

# Build all InteractiveTasks' container images.
build-all:
    just build echo
    just build energyplus_single_zone
    just build modelica_diagram

# Push one InteractiveTask's image to the configured registry.
# Requires DOCKER_IMAGE_REGISTRY env var, e.g.:
#   DOCKER_IMAGE_REGISTRY=us-central1-docker.pkg.dev/<proj>/purelms
push slug registry=env_var_or_default("DOCKER_IMAGE_REGISTRY", ""):
    @test -n "{{registry}}" || (echo "DOCKER_IMAGE_REGISTRY not set" && exit 1)
    docker tag purelms-itask-$(echo "{{slug}}" | tr '_' '-'):dev {{registry}}/purelms-itask-$(echo "{{slug}}" | tr '_' '-'):latest
    docker push {{registry}}/purelms-itask-$(echo "{{slug}}" | tr '_' '-'):latest

# ---------------------------------------------------------------------
# Frontend bundle builds
# ---------------------------------------------------------------------

# Build the ES module bundle for one InteractiveTask.
# Output: <slug>/frontend/dist/<slug>.js
# Operators copy this to PureLMS's static dir via the LMS's
# `manage.py install_interactive_task` (or `collect_backend_bundles`
# for ad-hoc staging). Convention:
#   purelms/static/backends/<slug>/<slug>.js
frontend-build slug:
    cd {{slug}}/frontend && npm run build

# Build all InteractiveTask frontends.
frontend-build-all:
    just frontend-build echo
    just frontend-build energyplus_single_zone
    just frontend-build modelica_diagram

# ---------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------

# Run tests for one InteractiveTask (Python + TypeScript).
test slug:
    cd {{slug}}/backend && uv run pytest
    cd {{slug}}/frontend && npm test

# Run tests across all InteractiveTasks + the workspace lint.
test-all:
    uv run pytest
    just test echo
    just test energyplus_single_zone
    just test modelica_diagram

# Lint + format the whole workspace.
lint:
    uv run ruff check .
    uv run ruff format --check .
