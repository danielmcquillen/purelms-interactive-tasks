"""Contract tests for backend publishing and Cloud Run Job deployment."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_release_assets import validate_release_assets  # noqa: E402

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


def test_release_slug_list_matches_workflow_matrix() -> None:
    """Inventory, deploy-all, and release CI must publish the same set."""
    slugs_match = re.search(r'^slugs := "([^"]+)"$', JUSTFILE, re.MULTILINE)
    matrix_match = re.search(r"slug: \[([^]]+)]", RELEASE_WORKFLOW)

    assert slugs_match is not None
    assert matrix_match is not None
    just_slugs = slugs_match.group(1).split()
    workflow_slugs = [item.strip() for item in matrix_match.group(1).split(",")]
    inventory_slugs = [backend["slug"] for backend in BACKENDS if backend["release"]]
    assert inventory_slugs == just_slugs == workflow_slugs


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
        for label in (
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
            "org.opencontainers.image.source",
            "io.purelms.interactive-task.slug",
            "io.purelms.interactive-task.version",
        ):
            assert label in dockerfile


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
    for slug in ("echo", "energyplus_single_zone", "modelica_diagram"):
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


def test_publish_uses_cloud_run_architecture_and_no_latest_tag() -> None:
    """The local recovery publisher cannot push an Apple-Silicon image/latest."""
    recipe = _recipe("publish", "push")

    assert "--platform {{ target_platform }}" in recipe
    assert 'target_platform := "linux/amd64"' in JUSTFILE
    assert "--push" in recipe
    assert ":latest" not in recipe


def test_local_build_uses_cloud_run_architecture() -> None:
    """Apple-Silicon development runs the production architecture via emulation."""
    recipe = _recipe("build", "build-all")

    assert "--platform {{ target_platform }}" in recipe
    assert 'target_platform := "linux/amd64"' in JUSTFILE


def test_native_binary_images_reject_accidental_arm64_builds() -> None:
    """Direct Docker builds cannot create mixed-architecture native images."""
    for slug in ("energyplus_single_zone", "modelica_diagram"):
        dockerfile = (REPO_ROOT / slug / "backend/Dockerfile").read_text(
            encoding="utf-8",
        )
        assert 'test "$(uname -m)" = x86_64' in dockerfile


def test_release_assets_match_manifests_and_cloud_run_architecture() -> None:
    """Pinned assets are intact and embedded FMU binaries are x86-64."""
    assert validate_release_assets() == []


def test_release_workflow_validates_assets_before_creating_release() -> None:
    """A bad native asset must fail before GitHub creates the release record."""
    validation = RELEASE_WORKFLOW.index("python3 scripts/validate_release_assets.py")
    release = RELEASE_WORKFLOW.index("gh release create")

    assert validation < release


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

    assert 'JOB_NAME="purelms-itask-${IMAGE_SLUG}${SUFFIX}"' in recipe
    assert 'BACKEND_SA_NAME="purelms-sim-{{ stage }}"' in recipe
    assert 'MAIN_SA="purelms-cloudrun-{{ stage }}@' in recipe
    assert "roles/storage.objectUser" in recipe
    assert "roles/run.invoker" in recipe
    assert "roles/run.jobsExecutorWithOverrides" in recipe
    assert "roles/run.viewer" in recipe
    assert "TASK_OIDC_ALLOWED_SERVICE_ACCOUNTS=" in recipe


def test_deploy_retries_new_service_account_propagation() -> None:
    """First deployment tolerates IAM's documented eventual consistency."""
    recipe = _recipe("deploy", "deploy-all")

    assert "retry_gcloud()" in recipe
    assert "max_attempts=7" in recipe
    assert "delay=$((delay * 2))" in recipe
    assert 'if [ "${delay}" -gt 30 ]; then delay=30; fi' in recipe
    assert "retry_gcloud gcloud storage buckets add-iam-policy-binding" in recipe
    assert "retry_gcloud gcloud run jobs deploy" in recipe
