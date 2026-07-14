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

# Backend slugs published as release images. Keep in sync with `backends.toml`
# and the `matrix.slug` list in .github/workflows/release.yml. Contract tests
# enforce the three-way match. `echo` is the permanent demo/smoke backend.
slugs := "echo energyplus_single_zone modelica_diagram"
target_platform := "linux/amd64"
git_sha := `git rev-parse --short HEAD 2>/dev/null || echo "unknown"`
repo_version := `sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1`

default:
    @just --list

# Print the canonical backend slugs used by build-all / release / deploy-all.
list:
    @printf '%s\n' {{ slugs }}

[private]
_check-slug slug:
    #!/usr/bin/env bash
    set -euo pipefail
    case " {{ slugs }} " in
        *" {{ slug }} "*) ;;
        *)
            echo "✗ Unknown backend slug: {{ slug }}"
            echo "  Available: {{ slugs }}"
            exit 1
            ;;
    esac

# ---------------------------------------------------------------------
# Container builds
# ---------------------------------------------------------------------

# Stage a local purelms-shared wheel into <slug>/backend/_vendor/
# so development container builds exercise the sibling checkout. Release CI
# stages only the in-repo runtime wheel and resolves purelms-shared from PyPI,
# proving that published consumers do not depend on the workspace layout.
#
# Path is the sibling repo per workspace convention. Override with
# PURELMS_SHARED_PATH if checked out elsewhere.
_stage-shared-wheel slug shared_path=env_var_or_default("PURELMS_SHARED_PATH", "../purelms-shared"): (_check-slug slug)
    @test -d "{{ shared_path }}" || (echo "purelms-shared not found at {{ shared_path }}" && exit 1)
    @rm -rf "{{ slug }}/backend/_vendor"
    @mkdir -p "{{ slug }}/backend/_vendor"
    cd "{{ shared_path }}" && uv build --wheel --out-dir "{{ justfile_directory() }}/{{ slug }}/backend/_vendor"
    # Also stage the in-repo shared backend-runtime wheel (local-dir vs
    # GCS envelope I/O + progress/complete worker callbacks). The
    # Dockerfile installs ``purelms-itask-runtime[cloud]`` from _vendor/,
    # which pulls the purelms-shared wheel staged above + google-cloud-
    # storage / google-auth from PyPI.
    cd "{{ justfile_directory() }}/_shared_backends/purelms_itask_runtime" && uv build --wheel --out-dir "{{ justfile_directory() }}/{{ slug }}/backend/_vendor"

# Build the container image for one InteractiveTask.
# Image name derives from slug by s/_/-/g.
# Usage: just build echo
# Usage: just build energyplus_single_zone
build slug: (_stage-shared-wheel slug) _validate-release-assets
    docker build \
        --platform {{ target_platform }} \
        --build-arg PURELMS_IMAGE_VERSION="{{ repo_version }}" \
        --build-arg PURELMS_IMAGE_REVISION="{{ git_sha }}" \
        --build-arg PURELMS_BACKEND_SLUG="{{ slug }}" \
        -t purelms-itask-$(echo "{{ slug }}" | tr '_' '-'):dev \
        {{ slug }}/backend

# Build all InteractiveTasks' container images.
build-all:
    #!/usr/bin/env bash
    set -euo pipefail
    for slug in {{ slugs }}; do just build "${slug}"; done

# Build linux/amd64 and push an immutable release tag to Artifact Registry.
# The signed GitHub release workflow remains the normal production publisher;
# this explicit local path is useful for development and recovery. It never
# pushes ``latest`` and never deploys the moving tag it was given: ``deploy``
# resolves the tag to a digest before updating a Cloud Run Job.
#
# Requires PURELMS_IMAGE_BASE (normally sourced from PureLMS's tracked .just
# template), e.g. us-central1-docker.pkg.dev/<project>/purelms.
# Usage: just publish echo              # defaults to v<project version>
# Usage: just publish echo v0.2.1
publish slug image_tag="": (_stage-shared-wheel slug) _validate-release-assets
    #!/usr/bin/env bash
    set -euo pipefail

    REGISTRY="${PURELMS_IMAGE_BASE:-${DOCKER_IMAGE_REGISTRY:-}}"
    if [ -z "${REGISTRY}" ]; then
        echo "✗ PURELMS_IMAGE_BASE is not set."
        echo "  Source the PureLMS GCP .just file, then retry."
        exit 1
    fi

    VERSION=$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)
    TAG="{{ image_tag }}"
    TAG="${TAG:-v${VERSION}}"
    if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "✗ Image tag must be vX.Y.Z; got: ${TAG}"
        exit 1
    fi

    IMAGE_SLUG=$(printf '%s' "{{ slug }}" | tr '_' '-')
    IMAGE="${REGISTRY}/purelms-itask-${IMAGE_SLUG}"
    echo "Building and publishing ${IMAGE}:${TAG} (linux/amd64)..."
    docker buildx build \
        --platform {{ target_platform }} \
        --push \
        --build-arg PURELMS_IMAGE_VERSION="${TAG}" \
        --build-arg PURELMS_IMAGE_REVISION="{{ git_sha }}" \
        --build-arg PURELMS_BACKEND_SLUG="{{ slug }}" \
        --tag "${IMAGE}:${TAG}" \
        "{{ slug }}/backend"
    echo "✓ Published ${IMAGE}:${TAG}"

# Backward-compatible spelling; push now means the immutable publish path.
push slug image_tag="": (publish slug image_tag)

# ---------------------------------------------------------------------
# Cloud Run Jobs deployment
# ---------------------------------------------------------------------

# Deploy one released backend as a digest-pinned, stage-specific Cloud Run Job.
#
# This recipe creates a dedicated least-privilege runtime service account per
# stage, grants it object access only on the private simulation bucket and
# run.invoker on the worker, then grants the Django runtime SA permission to
# execute this Job with per-run env overrides and read execution metadata.
#
# The tag is only a human-friendly selector. It is resolved through Artifact
# Registry and the Job is deployed with IMAGE@sha256:DIGEST.
#
# Usage: just deploy energyplus_single_zone prod          # current vX.Y.Z
# Usage: just deploy energyplus_single_zone staging v0.2.1
deploy slug stage image_tag="": (_check-slug slug)
    #!/usr/bin/env bash
    set -euo pipefail

    # Service-account creation is eventually consistent across Google Cloud
    # services. IAM can see a new account before Storage or Cloud Run can.
    # Retry cross-service operations with capped exponential backoff.
    retry_gcloud() {
        local attempt=1
        local max_attempts=7
        local delay=2
        while true; do
            if "$@"; then
                return 0
            fi
            if [ "${attempt}" -ge "${max_attempts}" ]; then
                echo "✗ gcloud command still failed after ${max_attempts} attempts."
                return 1
            fi
            echo "  Google Cloud IAM has not converged; retrying in ${delay}s " \
                 "(attempt $((attempt + 1))/${max_attempts})..."
            sleep "${delay}"
            attempt=$((attempt + 1))
            delay=$((delay * 2))
            if [ "${delay}" -gt 30 ]; then delay=30; fi
        done
    }

    if [[ ! "{{ stage }}" =~ ^(dev|staging|prod)$ ]]; then
        echo "✗ Stage must be dev, staging, or prod; got: {{ stage }}"
        exit 1
    fi

    for var in GCP_PROJECT_ID GCP_REGION PURELMS_IMAGE_BASE \
        PURELMS_SIMULATION_BUCKET_BASE PURELMS_WORKER_SERVICE_BASE; do
        if [ -z "${!var:-}" ]; then
            echo "✗ ${var} is not set."
            echo "  Source the PureLMS GCP .just file, then retry."
            exit 1
        fi
    done

    VERSION=$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -1)
    TAG="{{ image_tag }}"
    TAG="${TAG:-v${VERSION}}"
    if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "✗ Image tag must be vX.Y.Z; got: ${TAG}"
        exit 1
    fi

    SUFFIX=$(if [ "{{ stage }}" = "prod" ]; then printf ''; else printf '%s' '-{{ stage }}'; fi)
    IMAGE_SLUG=$(printf '%s' "{{ slug }}" | tr '_' '-')
    IMAGE="${PURELMS_IMAGE_BASE}/purelms-itask-${IMAGE_SLUG}"
    JOB_NAME="purelms-itask-${IMAGE_SLUG}${SUFFIX}"
    MAIN_SA="purelms-cloudrun-{{ stage }}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
    BACKEND_SA_NAME="purelms-sim-{{ stage }}"
    BACKEND_SA="${BACKEND_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
    WORKER_SERVICE="${PURELMS_WORKER_SERVICE_BASE}${SUFFIX}"
    SIMULATION_BUCKET="${PURELMS_SIMULATION_BUCKET_BASE}${SUFFIX}"

    echo "Resolving ${IMAGE}:${TAG} to an immutable digest..."
    DIGEST=$(gcloud artifacts docker images describe "${IMAGE}:${TAG}" \
        --project="${GCP_PROJECT_ID}" \
        --format='value(image_summary.digest)')
    if [[ ! "${DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        echo "✗ Artifact Registry did not return a valid digest for ${IMAGE}:${TAG}."
        echo "  Publish the release image first, then retry."
        exit 1
    fi
    PINNED_IMAGE="${IMAGE}@${DIGEST}"

    if ! gcloud iam service-accounts describe "${BACKEND_SA}" \
        --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
        echo "Creating dedicated backend runtime identity ${BACKEND_SA_NAME}..."
        gcloud iam service-accounts create "${BACKEND_SA_NAME}" \
            --display-name="PureLMS {{ stage }} simulation backends" \
            --project="${GCP_PROJECT_ID}"
    fi

    echo "Granting ${BACKEND_SA_NAME} access to gs://${SIMULATION_BUCKET}..."
    retry_gcloud gcloud storage buckets add-iam-policy-binding "gs://${SIMULATION_BUCKET}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/storage.objectUser" \
        --project="${GCP_PROJECT_ID}" \
        --quiet >/dev/null

    echo "Deploying ${JOB_NAME} with ${PINNED_IMAGE}..."
    retry_gcloud gcloud run jobs deploy "${JOB_NAME}" \
        --image="${PINNED_IMAGE}" \
        --region="${GCP_REGION}" \
        --project="${GCP_PROJECT_ID}" \
        --service-account="${BACKEND_SA}" \
        --tasks=1 \
        --parallelism=1 \
        --cpu=1 \
        --memory=2Gi \
        --max-retries=0 \
        --task-timeout=1800s \
        --labels="managed-by=purelms,backend=${IMAGE_SLUG},stage={{ stage }}" \
        --quiet

    echo "Granting ${MAIN_SA} permission to execute and inspect ${JOB_NAME}..."
    for role in roles/run.jobsExecutorWithOverrides roles/run.viewer; do
        gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
            --region="${GCP_REGION}" \
            --project="${GCP_PROJECT_ID}" \
            --member="serviceAccount:${MAIN_SA}" \
            --role="${role}" \
            --quiet >/dev/null
    done

    echo "Granting ${BACKEND_SA} callback access to ${WORKER_SERVICE}..."
    gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
        --region="${GCP_REGION}" \
        --project="${GCP_PROJECT_ID}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null

    echo "✓ ${JOB_NAME} deployed at ${PINNED_IMAGE}"
    echo ""
    echo "Django callback allowlist required for this stage:"
    echo "  TASK_OIDC_ALLOWED_SERVICE_ACCOUNTS=${MAIN_SA},${BACKEND_SA}"
    echo "Apply that line to the stage's .django file, then run:"
    echo "  just gcp deploy-config {{ stage }}"

# Deploy every released backend image at the same repository release tag.
deploy-all stage image_tag="":
    #!/usr/bin/env bash
    set -euo pipefail
    for slug in {{ slugs }}; do
        just deploy "${slug}" "{{ stage }}" "{{ image_tag }}"
    done

# Describe a deployed Job without executing it.
status slug stage="prod": (_check-slug slug)
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! "{{ stage }}" =~ ^(dev|staging|prod)$ ]]; then
        echo "✗ Stage must be dev, staging, or prod; got: {{ stage }}"
        exit 1
    fi
    : "${GCP_PROJECT_ID:?Source the PureLMS GCP .just file first}"
    : "${GCP_REGION:?Source the PureLMS GCP .just file first}"
    SUFFIX=$(if [ "{{ stage }}" = "prod" ]; then printf ''; else printf '%s' '-{{ stage }}'; fi)
    IMAGE_SLUG=$(printf '%s' "{{ slug }}" | tr '_' '-')
    gcloud run jobs describe "purelms-itask-${IMAGE_SLUG}${SUFFIX}" \
        --region="${GCP_REGION}" \
        --project="${GCP_PROJECT_ID}"

# ---------------------------------------------------------------------
# Frontend bundle builds
# ---------------------------------------------------------------------

# Build the ES module bundle for one InteractiveTask.
# Output: <slug>/frontend/dist/<slug>.js
# Operators copy this to PureLMS's static dir via the LMS's
# `manage.py install_interactive_task` (or `collect_backend_bundles`
# for ad-hoc staging). Convention:
# purelms/static/backends/<slug>/<slug>.js
frontend-build slug:
    cd {{ slug }}/frontend && npm run build

# Build all InteractiveTask frontends.
frontend-build-all:
    #!/usr/bin/env bash
    set -euo pipefail
    for slug in {{ slugs }}; do just frontend-build "${slug}"; done

# ---------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------

[private]
_validate-release-assets:
    python3 scripts/validate_release_assets.py

# Build and run one real backend container as Cloud Run's linux/amd64 target (emulated on Apple Silicon).
smoke slug: (build slug)
    python3 scripts/smoke_backend.py "{{ slug }}"

# Exercise the real domain runtime in every backend container.
smoke-all:
    #!/usr/bin/env bash
    set -euo pipefail
    for slug in {{ slugs }}; do just smoke "${slug}"; done

# Run tests for one InteractiveTask (Python + TypeScript).
test slug:
    cd {{ slug }}/backend && uv run pytest
    cd {{ slug }}/frontend && npm test

# Test the shared local-directory / Cloud Run-GCS runtime contract.
test-runtime:
    cd _shared_backends/purelms_itask_runtime && uv run --extra dev pytest

# Run workspace contracts, shared runtime, and every backend/frontend suite.
test-all:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pytest
    just test-runtime
    for slug in {{ slugs }}; do just test "${slug}"; done

# Lint + format the whole workspace.
lint:
    uv run ruff check .
    uv run ruff format --check .

# ---------------------------------------------------------------------
# Release  (mirrors validibot-validator-backends' `just release`)
# ---------------------------------------------------------------------

# One-time keyless GitHub Actions → GAR setup for the signed release workflow.
#
# Creates an exact-repository Workload Identity Federation provider, a
# publisher service account with writer access only to the named GAR
# repository, and the five non-secret GitHub Actions variables consumed by
# release.yml. The WIF condition and IAM principal use GitHub's stable numeric
# repository/owner IDs rather than reusable names.
#
# Usage:
# just setup-release-gar owner/purelms-interactive-tasks project-id region repo
setup-release-gar github_repo project_id region gar_repository:
    #!/usr/bin/env bash
    set -euo pipefail

    GITHUB_REPO="{{ github_repo }}"
    PROJECT_ID="{{ project_id }}"
    REGION="{{ region }}"
    GAR_REPOSITORY="{{ gar_repository }}"
    POOL_ID="purelms-github"
    PROVIDER_ID="interactive-tasks"
    SA_NAME="purelms-backend-publisher"

    for command in gh gcloud; do
        if ! command -v "${command}" >/dev/null 2>&1; then
            echo "✗ ${command} is required."
            exit 1
        fi
    done
    if [[ ! "${GITHUB_REPO}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        echo "✗ github_repo must use owner/repo form; got: ${GITHUB_REPO}"
        exit 1
    fi
    for value_name in PROJECT_ID REGION GAR_REPOSITORY; do
        if [ -z "${!value_name}" ]; then
            echo "✗ ${value_name} must not be empty."
            exit 1
        fi
    done

    echo "Configuring keyless GAR publishing for ${GITHUB_REPO}..."
    PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" \
        --format='value(projectNumber)')
    REPO_ID=$(gh api "repos/${GITHUB_REPO}" --jq '.id')
    OWNER_ID=$(gh api "repos/${GITHUB_REPO}" --jq '.owner.id')
    if [[ ! "${PROJECT_NUMBER}" =~ ^[0-9]+$ || ! "${REPO_ID}" =~ ^[0-9]+$ || ! "${OWNER_ID}" =~ ^[0-9]+$ ]]; then
        echo "✗ Could not resolve stable project/repository identity numbers."
        exit 1
    fi

    gcloud services enable \
        iam.googleapis.com \
        iamcredentials.googleapis.com \
        sts.googleapis.com \
        artifactregistry.googleapis.com \
        --project="${PROJECT_ID}" \
        --quiet

    SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
    if ! gcloud iam service-accounts describe "${SA_EMAIL}" \
        --project="${PROJECT_ID}" >/dev/null 2>&1; then
        gcloud iam service-accounts create "${SA_NAME}" \
            --display-name="PureLMS backend image publisher" \
            --project="${PROJECT_ID}"
    fi

    if ! gcloud iam workload-identity-pools describe "${POOL_ID}" \
        --location=global \
        --project="${PROJECT_ID}" >/dev/null 2>&1; then
        gcloud iam workload-identity-pools create "${POOL_ID}" \
            --location=global \
            --display-name="PureLMS GitHub Actions" \
            --project="${PROJECT_ID}"
    fi

    ATTRIBUTE_MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id"
    ATTRIBUTE_CONDITION="assertion.repository_id == '${REPO_ID}' && assertion.repository_owner_id == '${OWNER_ID}'"
    if gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
        --workload-identity-pool="${POOL_ID}" \
        --location=global \
        --project="${PROJECT_ID}" >/dev/null 2>&1; then
        gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
            --workload-identity-pool="${POOL_ID}" \
            --location=global \
            --display-name="PureLMS backend releases" \
            --attribute-mapping="${ATTRIBUTE_MAPPING}" \
            --attribute-condition="${ATTRIBUTE_CONDITION}" \
            --issuer-uri="https://token.actions.githubusercontent.com" \
            --project="${PROJECT_ID}"
    else
        gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
            --workload-identity-pool="${POOL_ID}" \
            --location=global \
            --display-name="PureLMS backend releases" \
            --attribute-mapping="${ATTRIBUTE_MAPPING}" \
            --attribute-condition="${ATTRIBUTE_CONDITION}" \
            --issuer-uri="https://token.actions.githubusercontent.com" \
            --project="${PROJECT_ID}"
    fi

    POOL_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
    PRINCIPAL="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository_id/${REPO_ID}"
    gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
        --member="${PRINCIPAL}" \
        --role="roles/iam.workloadIdentityUser" \
        --project="${PROJECT_ID}" \
        --quiet >/dev/null

    if ! gcloud artifacts repositories describe "${GAR_REPOSITORY}" \
        --location="${REGION}" \
        --project="${PROJECT_ID}" >/dev/null 2>&1; then
        echo "✗ GAR repository ${REGION}/${GAR_REPOSITORY} does not exist in ${PROJECT_ID}."
        exit 1
    fi
    gcloud artifacts repositories add-iam-policy-binding "${GAR_REPOSITORY}" \
        --location="${REGION}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/artifactregistry.writer" \
        --project="${PROJECT_ID}" \
        --quiet >/dev/null

    PROVIDER_RESOURCE=$(gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
        --workload-identity-pool="${POOL_ID}" \
        --location=global \
        --project="${PROJECT_ID}" \
        --format='value(name)')
    gh variable set GCP_PROJECT_ID --body "${PROJECT_ID}" --repo "${GITHUB_REPO}"
    gh variable set GCP_REGION --body "${REGION}" --repo "${GITHUB_REPO}"
    gh variable set GCP_GAR_REPOSITORY --body "${GAR_REPOSITORY}" --repo "${GITHUB_REPO}"
    gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "${PROVIDER_RESOURCE}" --repo "${GITHUB_REPO}"
    gh variable set GCP_SERVICE_ACCOUNT_EMAIL --body "${SA_EMAIL}" --repo "${GITHUB_REPO}"

    echo "✓ Keyless GAR publishing is configured for repository id ${REPO_ID}."
    echo "  provider: ${PROVIDER_RESOURCE}"
    echo "  service account: ${SA_EMAIL}"
    echo "  repository role: roles/artifactregistry.writer on ${REGION}/${GAR_REPOSITORY}"
    echo "  Allow up to five minutes for new WIF/IAM configuration to propagate."

# Cut a release: verify preconditions, sign the git tag, and push it. CI
# (.github/workflows/release.yml) then builds every backend image with a
# sigstore attestation + SBOM and publishes to GHCR (and PureLMS's own GAR
# when that repo's GCP variables are set). This recipe only tags + pushes;
# the build + signing happen in CI, exactly like validibot.
#
# One-time setup: a signing key in .allowed_signers (see that file) and
# `git config --global gpg.format ssh` + a `user.signingkey`.
#
# Usage: just release 0.2.1
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail

    # Version must be X.Y.Z.
    if [[ ! "{{ VERSION }}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "✗ Version must be X.Y.Z (e.g. 0.2.0). Got: {{ VERSION }}"
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
    TAG="v{{ VERSION }}"
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
    if [[ "$TOML_VERSION" != "{{ VERSION }}" ]]; then
        echo "✗ pyproject.toml version ($TOML_VERSION) != {{ VERSION }}."
        echo "  Bump the version in pyproject.toml, commit, and push first."
        exit 1
    fi

    # Fail before creating a signed tag if an immutable manifest asset has
    # drifted or an embedded native binary cannot execute on Cloud Run.
    python3 scripts/validate_release_assets.py

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
    echo "CI will then build + push images for: {{ slugs }}"
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
