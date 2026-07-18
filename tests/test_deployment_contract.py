"""Contract tests for backend publishing and Cloud Run Job deployment."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.backend_inventory import build_registration_catalog  # noqa: E402
from scripts.backend_inventory import cloud_run_job_name  # noqa: E402
from scripts.backend_inventory import manifest_version  # noqa: E402
from scripts.validate_release_assets import validate_release_assets  # noqa: E402
from scripts.validate_release_versions import release_inputs_by_slug  # noqa: E402
from scripts.validate_release_versions import semver_key  # noqa: E402

JUSTFILE = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
RELEASE_WORKFLOW = (REPO_ROOT / ".github/workflows/release.yml").read_text(
    encoding="utf-8"
)
RELEASING = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8")
INVENTORY = tomllib.loads((REPO_ROOT / "backends.toml").read_text(encoding="utf-8"))
BACKENDS = list(INVENTORY["backend"])


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Return the numeric release tuple from a version or caret constraint."""
    return tuple(int(part) for part in raw.lstrip("^~<>= ").split(".")[:3])


def _recipe(name: str, next_name: str) -> str:
    """Return one recipe block delimited by the next top-level recipe."""
    start_match = re.search(rf"^{re.escape(name)}(?:\s|:)", JUSTFILE, re.MULTILINE)
    assert start_match is not None
    end_match = re.search(
        rf"^{re.escape(next_name)}(?:\s|:)",
        JUSTFILE[start_match.end() :],
        re.MULTILINE,
    )
    assert end_match is not None
    end = start_match.end() + end_match.start()
    return JUSTFILE[start_match.start() : end]


def _manifest_value(path: Path, field: str) -> str:
    """Read a simple top-level scalar from an InteractiveTask manifest."""
    match = re.search(
        rf"^{re.escape(field)}:\s*[\"']?([^\s#\"']+)",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None, f"{path} has no top-level {field}"
    return match.group(1)


def test_release_inventory_drives_just_and_github_actions() -> None:
    """Aggregate tooling must consume membership instead of redeclaring it."""
    assert (
        "slugs := `python3 scripts/backend_inventory.py list --format words`"
        in JUSTFILE
    )
    assert "python3 scripts/backend_inventory.py matrix" in RELEASE_WORKFLOW
    assert (
        "matrix: ${{ fromJSON(needs.verify-signed-tag.outputs.backend-matrix) }}"
        in RELEASE_WORKFLOW
    )
    assert "slug: [echo," not in RELEASE_WORKFLOW


def test_release_preflight_runs_all_local_gates_before_tagging() -> None:
    """A release must prove its declared assets before the signing step."""
    assert "release-preflight VERSION:" in JUSTFILE
    preflight = JUSTFILE.split("release-preflight VERSION:", maxsplit=1)[1]
    preflight = preflight.split("# Bootstrap", maxsplit=1)[0]

    assert "validate_release_assets.py" in preflight
    assert "validate_release_versions.py --release-ref HEAD" in preflight
    assert "just lint" in preflight
    assert "just test-all" in preflight
    assert "just frontend-build-all" in preflight
    assert "just smoke-all" in preflight

    release = JUSTFILE.split("release VERSION:", maxsplit=1)[1]
    assert 'just release-preflight "{{ VERSION }}"' in release


def test_registration_catalog_uses_tagged_inventory_and_manifests() -> None:
    """Deployment registration pairs every declared manifest with one image."""
    tagged_inventory = tomllib.loads(
        subprocess.run(
            ["git", "show", "HEAD:backends.toml"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout,
    )
    released = [
        backend for backend in tagged_inventory["backend"] if backend["release"]
    ]
    images = {
        backend["slug"]: f"registry.example/{backend['image_name']}@sha256:{'a' * 64}"
        for backend in released
    }

    catalog = build_registration_catalog(
        release_ref="HEAD",
        stage="prod",
        image_by_slug=images,
    )

    assert catalog["schema_version"] == 1
    assert [entry["slug"] for entry in catalog["backends"]] == [
        backend["slug"] for backend in released
    ]
    for backend, entry in zip(released, catalog["backends"], strict=True):
        assert entry["image_uri"] == images[backend["slug"]]
        tagged_manifest = subprocess.run(
            ["git", "show", f"HEAD:{backend['manifest']}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert entry["manifest_yaml"] == tagged_manifest
        assert entry["cloud_run_job_name"] == cloud_run_job_name(
            slug=backend["slug"],
            version=manifest_version(tagged_manifest),
            stage="prod",
        )


def test_cloud_run_job_names_pin_task_version_and_compact_stage() -> None:
    """A Job identity names the exact task release and deployment stage."""
    assert (
        cloud_run_job_name(slug="echo", version="0.1.0", stage="prod")
        == "purelms-itask-echo-v0-1-0"
    )
    assert (
        cloud_run_job_name(
            slug="energyplus_single_zone",
            version="0.2.3",
            stage="staging",
        )
        == "purelms-itask-energyplus-single-zone-v0-2-3-stg"
    )


def test_long_cloud_run_job_names_are_bounded_and_collision_safe() -> None:
    """Long slugs keep readable prefixes but distinct releases never collide."""
    slug = "a_very_long_interactive_task_backend_slug_that_exceeds_the_limit"
    first = cloud_run_job_name(slug=slug, version="1.2.3", stage="staging")
    second = cloud_run_job_name(slug=slug, version="1.2.4", stage="staging")

    assert len(first) <= 49
    assert first.startswith("purelms-itask-a-very-long")
    assert first.endswith("-v1-2-3-stg")
    assert first != second


@pytest.mark.parametrize(
    ("slug", "version"),
    [("Bad Slug", "1.2.3"), ("echo", "1.2"), ("echo", "v1.2.3")],
)
def test_cloud_run_job_names_reject_non_release_identity(
    slug: str,
    version: str,
) -> None:
    """Only canonical backend slugs and release SemVer may reach Cloud Run."""
    with pytest.raises(
        ValueError,
        match=r"invalid backend slug|backend version must",
    ):
        cloud_run_job_name(slug=slug, version=version, stage="prod")


def test_release_version_gate_scopes_task_and_shared_runtime_changes() -> None:
    """Aggregate and shared inputs bump every task; tests and prose do not."""
    affected = release_inputs_by_slug(
        {
            "pyproject.toml",
            "echo/backend/main.py",
            "energyplus_single_zone/backend/tests/test_main.py",
            "modelica_diagram/README.md",
            "_shared_backends/purelms_itask_runtime/src/runtime.py",
            "_shared_frontend/run_lifecycle.ts",
        },
        {"echo", "energyplus_single_zone", "modelica_diagram"},
    )

    assert "echo/backend/main.py" in affected["echo"]
    assert all(
        "_shared_backends/purelms_itask_runtime/src/runtime.py" in paths
        for paths in affected.values()
    )
    assert all(
        "_shared_frontend/run_lifecycle.ts" in paths for paths in affected.values()
    )
    assert all("pyproject.toml" in paths for paths in affected.values())
    assert not any(
        "tests/test_main.py" in path for path in affected["energyplus_single_zone"]
    )
    assert not any("README.md" in path for path in affected["modelica_diagram"])


def test_release_version_gate_orders_task_versions_numerically() -> None:
    """Release gates reject equal or regressed versions without lexical traps."""
    assert semver_key("0.10.0") > semver_key("0.9.9")
    assert semver_key("1.0.0") > semver_key("0.99.99")


def test_backend_inventory_paths_and_contracts() -> None:
    """The authoritative inventory matches every backend's checked-in files."""
    assert INVENTORY["schema_version"] == 1
    assert len({backend["slug"] for backend in BACKENDS}) == len(BACKENDS)

    for backend in BACKENDS:
        slug = backend["slug"]
        for field in (
            "dockerfile",
            "project_file",
            "test_path",
            "metadata",
            "manifest",
        ):
            assert (REPO_ROOT / backend[field]).exists(), f"{slug}.{field} is missing"
        assert _manifest_value(REPO_ROOT / backend["manifest"], "slug") == slug
        assert backend["image_name"] == f"purelms-itask-{slug.replace('_', '-')}"
        assert backend["platforms"] == ["linux/amd64"]
        assert backend["native_payload"] in {
            "portable-python",
            "energyplus-x86_64",
            "fmu-linux64-x86_64",
        }

        project = tomllib.loads(
            (REPO_ROOT / backend["project_file"]).read_text(encoding="utf-8"),
        )
        shared = next(
            dependency
            for dependency in project["project"]["dependencies"]
            if dependency.startswith("purelms-shared")
        )
        assert shared == f"purelms-shared>={backend['shared_contract_floor']}"


def test_backend_metadata_and_image_labels_match_task_version() -> None:
    """Manifests, metadata, and OCI defaults expose one task version."""
    for backend in BACKENDS:
        version = _manifest_value(REPO_ROOT / backend["manifest"], "version")
        metadata = (REPO_ROOT / backend["metadata"]).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / backend["dockerfile"]).read_text(encoding="utf-8")

        assert f'BACKEND_VERSION = "{version}"' in metadata
        assert f'ARG PURELMS_TASK_VERSION="{version}"' in dockerfile
        assert (
            f'ARG PURELMS_SHARED_VERSION="{backend["shared_contract_floor"]}"'
            in dockerfile
        )
        assert "--constraint" in dockerfile
        for label in (
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
            "org.opencontainers.image.source",
            "io.purelms.interactive-task.slug",
            "io.purelms.interactive-task.version",
            "io.purelms.shared-contract.version",
        ):
            assert label in dockerfile

    # The release matrix intentionally carries membership only. Each tagged
    # Dockerfile pins the validated shared-contract version as its ARG default.
    assert "matrix.shared_contract_floor" not in RELEASE_WORKFLOW
    assert "uv export --frozen" in RELEASE_WORKFLOW


def test_cloud_runtime_locks_google_auth_requests_transport() -> None:
    """Release images must be able to mint callback OIDC tokens."""
    runtime_project = tomllib.loads(
        (REPO_ROOT / "_shared_backends/purelms_itask_runtime/pyproject.toml").read_text(
            encoding="utf-8"
        ),
    )
    assert runtime_project["project"]["optional-dependencies"]["cloud"] == [
        "google-auth[requests]>=2.20",
    ]

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    runtime_package = next(
        package
        for package in lock["package"]
        if package["name"] == "purelms-itask-runtime"
    )
    assert runtime_package["optional-dependencies"]["cloud"] == [
        {"name": "google-auth", "extra": ["requests"]},
    ]
    assert any(package["name"] == "requests" for package in lock["package"])

    for backend in BACKENDS:
        dockerfile = (REPO_ROOT / backend["slug"] / "backend/Dockerfile").read_text(
            encoding="utf-8",
        )
        assert "from google.auth.transport.requests import Request" in dockerfile


def test_release_jobs_checkout_the_requested_signed_tag() -> None:
    """Manual recovery builds must use tagged source in both release jobs."""
    checkout_ref = "ref: ${{ inputs.tag || github.ref }}"

    assert RELEASE_WORKFLOW.count(checkout_ref) == 2
    assert 'REVISION="$(git rev-parse HEAD)"' in RELEASE_WORKFLOW
    assert "PURELMS_IMAGE_REVISION=${{ steps.image-ref.outputs.revision }}" in (
        RELEASE_WORKFLOW
    )
    assert "PURELMS_IMAGE_REVISION=${{ github.sha }}" not in RELEASE_WORKFLOW


def test_backend_build_contexts_exclude_credentials_and_local_noise() -> None:
    """Every Docker build context excludes likely secrets and test state."""
    required = {
        ".env",
        ".env.*",
        ".envs",
        "*.pem",
        "*.key",
        "*.crt",
        "*.p12",
        "*.jsonl",
        "tests/",
        "__pycache__/",
    }
    for backend in BACKENDS:
        dockerfile = REPO_ROOT / backend["dockerfile"]
        ignored = set((dockerfile.parent / ".dockerignore").read_text().splitlines())
        assert required <= ignored


def test_release_attestation_is_published_without_org_only_storage_record() -> None:
    """The user-owned repository publishes provenance without org metadata."""
    assert "push-to-registry: true" in RELEASE_WORKFLOW
    assert "create-storage-record: false" in RELEASE_WORKFLOW
    assert "org.opencontainers.image.source=https://github.com/" in RELEASE_WORKFLOW
    assert "--bundle-from-oci" in RELEASING


def test_release_and_local_builds_stamp_image_metadata() -> None:
    """Developer and CI images expose the same useful OCI identity fields."""
    build = _recipe("build", "build-all")
    for argument in (
        "PURELMS_IMAGE_VERSION",
        "PURELMS_IMAGE_REVISION",
        "PURELMS_BACKEND_SLUG",
    ):
        assert f"--build-arg {argument}" in build
        assert argument in RELEASE_WORKFLOW
    assert "PURELMS_IMAGE_SOURCE" in RELEASE_WORKFLOW


def test_aggregate_suite_includes_shared_cloud_runtime() -> None:
    """The all-tests entry point must exercise the shared GCS/callback path."""
    recipe = _recipe("test-all", "lint")

    assert "just test-runtime" in recipe
    assert "_shared_backends/purelms_itask_runtime" in JUSTFILE
    assert "uv run --extra dev pytest" in JUSTFILE


def test_release_gar_auth_uses_non_secret_wif_variables() -> None:
    """GAR authentication is keyless and does not store a service-account key."""
    assert "vars.GCP_WORKLOAD_IDENTITY_PROVIDER" in RELEASE_WORKFLOW
    assert "vars.GCP_SERVICE_ACCOUNT_EMAIL" in RELEASE_WORKFLOW
    assert "secrets.GCP_WORKLOAD_IDENTITY_PROVIDER" not in RELEASE_WORKFLOW
    assert "credentials_json" not in RELEASE_WORKFLOW


def test_release_gar_setup_scopes_identity_and_writer_role() -> None:
    """The setup recipe trusts one numeric repo and one GAR repository."""
    assert "attribute.repository_id=assertion.repository_id" in JUSTFILE
    assert "attribute.repository_owner_id=assertion.repository_owner_id" in JUSTFILE
    assert "attribute.repository_id/${REPO_ID}" in JUSTFILE
    assert "roles/iam.workloadIdentityUser" in JUSTFILE
    assert "roles/artifactregistry.writer" in JUSTFILE
    assert "gcloud artifacts repositories add-iam-policy-binding" in JUSTFILE
    assert "gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER" in JUSTFILE


def test_python_lock_uses_patched_cryptography() -> None:
    """The backend lock cannot regress below the OpenSSL security patch."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    cryptography = next(
        package for package in lock["package"] if package["name"] == "cryptography"
    )

    assert _version_tuple(cryptography["version"]) >= (48, 0, 1)


def test_frontend_locks_use_patched_test_toolchain() -> None:
    """Shipped frontend locks keep Vite, Vitest, and Happy DOM above advisories."""
    for slug in [backend["slug"] for backend in BACKENDS if backend["release"]]:
        lock_path = REPO_ROOT / slug / "frontend/package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        packages = lock["packages"]

        vite_packages = [
            package
            for path, package in packages.items()
            if path == "node_modules/vite" or path.endswith("/node_modules/vite")
        ]
        assert vite_packages
        assert all(
            _version_tuple(package["version"]) >= (8, 0, 16)
            for package in vite_packages
        )
        assert _version_tuple(packages["node_modules/vitest"]["version"]) >= (
            3,
            2,
            6,
        )
        assert _version_tuple(packages["node_modules/happy-dom"]["version"]) >= (
            20,
            8,
            9,
        )


def test_template_starts_above_patched_test_toolchain_floors() -> None:
    """New backends cannot inherit the vulnerable template dependency floors."""
    template_path = REPO_ROOT / "_template/frontend/package.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    dependencies = template["devDependencies"]

    assert _version_tuple(dependencies["vitest"]) >= (3, 2, 6)
    assert _version_tuple(dependencies["happy-dom"]) >= (20, 8, 9)


def test_publish_uses_cloud_run_architecture_and_immutable_tags() -> None:
    """The local recovery publisher cannot push ARM/latest or overwrite a tag."""
    recipe = _recipe("publish", "push")

    assert "--platform {{ target_platform }}" in recipe
    assert 'target_platform := "linux/amd64"' in JUSTFILE
    assert "--push" in recipe
    assert ":latest" not in recipe
    assert 'gcloud artifacts docker images list "${IMAGE}"' in recipe
    assert "--include-tags" in recipe
    assert '--filter="tags:${TAG}"' in recipe
    assert "--limit=1" in recipe
    assert "Refusing to overwrite existing immutable image tag" in recipe


def test_local_build_uses_cloud_run_architecture() -> None:
    """Apple-Silicon development runs the production architecture via emulation."""
    recipe = _recipe("build", "build-all")

    assert "--platform {{ target_platform }}" in recipe
    assert 'target_platform := "linux/amd64"' in JUSTFILE


def test_native_binary_images_reject_accidental_arm64_builds() -> None:
    """Direct Docker builds cannot create mixed-architecture native images."""
    for slug in [
        backend["slug"]
        for backend in BACKENDS
        if backend["native_payload"] != "portable-python"
    ]:
        dockerfile = (REPO_ROOT / slug / "backend/Dockerfile").read_text(
            encoding="utf-8",
        )
        assert 'test "$(uname -m)" = x86_64' in dockerfile


def test_release_assets_match_manifests_and_cloud_run_architecture() -> None:
    """Pinned assets are intact and embedded FMU binaries are x86-64."""
    assert validate_release_assets() == []


def test_release_workflow_validates_assets_and_versions_before_release() -> None:
    """Bad native assets or stale task versions fail before publication."""
    asset_validation = RELEASE_WORKFLOW.index(
        "python3 scripts/validate_release_assets.py",
    )
    version_validation = RELEASE_WORKFLOW.index(
        "python3 scripts/validate_release_versions.py",
    )
    release = RELEASE_WORKFLOW.index("gh release create")

    assert asset_validation < release
    assert version_validation < release


def test_deploy_resolves_tag_and_pins_job_to_digest() -> None:
    """A release tag is only a selector; the deployed Job uses its digest."""
    recipe = _recipe("deploy", "deploy-all")

    assert "gcloud artifacts docker images describe" in recipe
    assert "image_summary.digest" in recipe
    assert 'PINNED_IMAGE="${IMAGE}@${DIGEST}"' in recipe
    assert '--image="${PINNED_IMAGE}"' in recipe
    assert ":latest" not in recipe


def test_deploy_preserves_stage_and_identity_boundaries() -> None:
    """Stages get distinct Jobs and containers run outside the Django identity."""
    recipe = _recipe("deploy", "deploy-all")
    suffix_assignment = 'SUFFIX=$(if [ "{{ stage }}" = "prod" ]'

    assert "scripts/backend_inventory.py job-name" in recipe
    assert "JOB_NAME=$(python3 scripts/backend_inventory.py job-name" in recipe
    assert suffix_assignment in recipe
    assert recipe.index(suffix_assignment) < recipe.index(
        'WORKER_SERVICE="${PURELMS_WORKER_SERVICE_BASE}${SUFFIX}"',
    )
    assert recipe.index(suffix_assignment) < recipe.index(
        'SIMULATION_BUCKET="${PURELMS_SIMULATION_BUCKET_BASE}${SUFFIX}"',
    )
    assert 'BACKEND_SA_NAME="purelms-sim-{{ stage }}"' in recipe
    assert 'MAIN_SA="purelms-cloudrun-{{ stage }}@' in recipe
    assert "roles/storage.objectUser" in recipe
    assert "roles/run.invoker" in recipe
    assert "roles/run.jobsExecutorWithOverrides" in recipe
    assert "roles/run.viewer" in recipe
    assert "SIMULATION_CALLBACK_SERVICE_ACCOUNT" in recipe
    assert "gcloud run services get-iam-policy" in recipe
    assert 'if [ "${CONFIGURED_CALLBACK_SA}" != "${BACKEND_SA}" ]' in recipe
    storage_check = recipe[
        recipe.index(
            "STORAGE_BINDINGS=$(gcloud storage buckets get-iam-policy"
        ) : recipe.index('echo "✓ ${JOB_NAME} deployed')
    ]
    assert "--filter=" not in storage_check
    assert '--flatten="bindings[].members"' in storage_check
    assert 'grep -Fqx "${BACKEND_STORAGE_BINDING}"' in storage_check
    assert "TASK_OIDC_ALLOWED_SERVICE_ACCOUNTS=" not in recipe


def test_deploy_refuses_to_repoint_an_existing_versioned_job() -> None:
    """A task-version Job cannot be silently changed to different image bytes."""
    recipe = _recipe("deploy", "deploy-all")

    assert 'if [ "${EXISTING_IMAGE}" != "${PINNED_IMAGE}" ]; then' in recipe
    assert "Refusing to rewrite immutable Job" in recipe
    assert "task-version=${TASK_VERSION_LABEL}" in recipe
    assert "release=${RELEASE_LABEL}" in recipe


def test_deploy_retries_new_service_account_propagation() -> None:
    """First deployment tolerates IAM's documented eventual consistency."""
    recipe = _recipe("deploy", "deploy-all")

    assert "retry_gcloud()" in recipe
    assert "max_attempts=7" in recipe
    assert "delay=$((delay * 2))" in recipe
    assert 'if [ "${delay}" -gt 30 ]; then delay=30; fi' in recipe
    assert "retry_gcloud gcloud run jobs deploy" in recipe
    assert "retry_gcloud gcloud run services add-iam-policy-binding" in recipe
