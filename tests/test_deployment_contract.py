"""Contract tests for backend publishing and Cloud Run Job deployment."""

from __future__ import annotations

import json
import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
JUSTFILE = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
RELEASE_WORKFLOW = (REPO_ROOT / ".github/workflows/release.yml").read_text(
    encoding="utf-8"
)
RELEASING = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8")


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Return the numeric release tuple from a version or caret constraint."""
    return tuple(int(part) for part in raw.lstrip("^~<>= ").split(".")[:3])


def _recipe(name: str, next_name: str) -> str:
    """Return one recipe block delimited by the next top-level recipe."""
    start = JUSTFILE.index(f"\n{name} ") + 1
    end = JUSTFILE.index(f"\n{next_name} ", start)
    return JUSTFILE[start:end]


def test_release_slug_list_matches_workflow_matrix() -> None:
    """deploy-all and release CI must publish the same backend set."""
    slugs_match = re.search(r'^slugs := "([^"]+)"$', JUSTFILE, re.MULTILINE)
    matrix_match = re.search(r"slug: \[([^]]+)]", RELEASE_WORKFLOW)

    assert slugs_match is not None
    assert matrix_match is not None
    just_slugs = slugs_match.group(1).split()
    workflow_slugs = [item.strip() for item in matrix_match.group(1).split(",")]
    assert just_slugs == workflow_slugs


def test_release_attestation_is_published_without_org_only_storage_record() -> None:
    """The user-owned repository publishes provenance without org metadata."""
    assert "push-to-registry: true" in RELEASE_WORKFLOW
    assert "create-storage-record: false" in RELEASE_WORKFLOW
    assert "org.opencontainers.image.source=https://github.com/" in RELEASE_WORKFLOW
    assert "--bundle-from-oci" in RELEASING


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

    assert "--platform linux/amd64" in recipe
    assert "--push" in recipe
    assert ":latest" not in recipe


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
