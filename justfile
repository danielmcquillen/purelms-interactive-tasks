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

# Backend slugs published as release images. Keep in sync with the
# `matrix.slug` list in .github/workflows/release.yml. `echo` is the
# demo/smoke backend — drop it here + in the matrix to skip publishing it.
slugs := "echo energyplus_single_zone modelica_diagram"

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

# ---------------------------------------------------------------------
# Release  (mirrors validibot-validator-backends' `just release`)
# ---------------------------------------------------------------------

# Cut a release: verify preconditions, sign the git tag, and push it. CI
# (.github/workflows/release.yml) then builds every backend image with a
# sigstore attestation + SBOM and publishes to GHCR (and PureLMS's own GAR
# when that repo's GCP variables are set). This recipe only tags + pushes;
# the build + signing happen in CI, exactly like validibot.
#
# One-time setup: a signing key in .allowed_signers (see that file) and
# `git config --global gpg.format ssh` + a `user.signingkey`.
#
# Usage: just release 0.2.0
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail

    # Version must be X.Y.Z.
    if [[ ! "{{VERSION}}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "✗ Version must be X.Y.Z (e.g. 0.2.0). Got: {{VERSION}}"
        exit 1
    fi

    # Clean working tree — image tags carry the git SHA, so a dirty tree
    # would produce an image that matches no commit.
    if [[ -n $(git status --porcelain) ]]; then
        echo "✗ Working tree has uncommitted changes. Commit or stash first."
        git status --short
        exit 1
    fi

    # Releases are cut from main only.
    BRANCH=$(git branch --show-current)
    if [[ "$BRANCH" != "main" ]]; then
        echo "✗ Not on main (currently on '$BRANCH'). git checkout main"
        exit 1
    fi

    # Tag must not already exist (local or remote).
    TAG="v{{VERSION}}"
    if git rev-parse "$TAG" >/dev/null 2>&1; then
        echo "✗ Tag $TAG already exists locally."
        exit 1
    fi
    if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
        echo "✗ Tag $TAG already exists on origin."
        exit 1
    fi

    # Local main must be in sync with origin/main.
    git fetch origin main
    if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
        echo "✗ Local main is not in sync with origin/main. Run: git pull"
        exit 1
    fi

    # pyproject.toml version must match the release.
    TOML_VERSION=$(grep '^version = ' pyproject.toml | head -1 | sed 's/version = "\(.*\)"/\1/')
    if [[ "$TOML_VERSION" != "{{VERSION}}" ]]; then
        echo "✗ pyproject.toml version ($TOML_VERSION) != {{VERSION}}."
        echo "  Bump the version in pyproject.toml, commit, and push first."
        exit 1
    fi

    # purelms-shared freshness — informational. The backends pin a floor
    # (purelms-shared>=X) and the image resolves the latest at build time,
    # so a behind floor doesn't break the build; this just flags it in case
    # you meant to raise it. Never blocks.
    SHARED_FLOOR=$(grep -E '"purelms-shared>=' pyproject.toml | head -1 | sed -E 's/.*"purelms-shared>=([^",]+)".*/\1/' || true)
    if [[ -n "${SHARED_FLOOR:-}" ]]; then
        SHARED_LATEST=$(curl -s --max-time 10 https://pypi.org/pypi/purelms-shared/json 2>/dev/null | jq -r '.info.version' 2>/dev/null || true)
        if [[ -n "${SHARED_LATEST:-}" && "$SHARED_LATEST" != "null" && "$SHARED_FLOOR" != "$SHARED_LATEST" ]]; then
            echo "ℹ purelms-shared floor is >=$SHARED_FLOOR; latest on PyPI is $SHARED_LATEST."
            echo "  The image builds against latest regardless; raise the floor if you rely on it."
        fi
    fi

    echo ""
    echo "About to sign and push tag $TAG."
    echo "CI will then build + push images for: {{slugs}}"
    echo "Press Enter to continue, Ctrl+C to abort..."
    read -r

    # Signed tag — CI verifies the signature against .allowed_signers
    # before it publishes anything.
    git tag -s "$TAG" -m "$TAG"
    git push origin "$TAG"

    echo ""
    echo "✓ Pushed $TAG"
    echo "  CI will: verify the signed tag → build each backend image →"
    echo "  push to GHCR with a sigstore attestation + SBOM →"
    echo "  (optional) mirror to PureLMS GAR when GCP_PROJECT_ID is set."
    echo "  Monitor: gh run watch"
