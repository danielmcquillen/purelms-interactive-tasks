#!/usr/bin/env python3
"""Require a task-version bump whenever released task bytes can change."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import tomllib

if __package__:
    from scripts.backend_inventory import manifest_version
    from scripts.backend_inventory import released_backends
else:
    from backend_inventory import manifest_version
    from backend_inventory import released_backends

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_RUNTIME_PREFIX = "_shared_backends/purelms_itask_runtime/"
SHARED_FRONTEND_PREFIX = "_shared_frontend/"
GLOBAL_RELEASE_INPUTS = {
    ".github/workflows/release.yml",
    "backends.toml",
    "pyproject.toml",
    "uv.lock",
}


def semver_key(version: str) -> tuple[int, int, int]:
    """Return the sortable key for an already-validated release SemVer."""
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def _git(*args: str) -> str:
    """Run one read-only Git query and return stripped stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_text(ref: str, path: str) -> str:
    """Read one UTF-8 repository file from ``ref``."""
    return _git("show", f"{ref}:{path}")


def release_inputs_by_slug(
    changed_paths: set[str],
    slugs: set[str],
) -> dict[str, set[str]]:
    """Map released tasks to changed code/config inputs that require a bump."""
    affected = {slug: set() for slug in slugs}
    shared_changes = {
        path
        for path in changed_paths
        if path.startswith((SHARED_RUNTIME_PREFIX, SHARED_FRONTEND_PREFIX))
        and _is_release_input(path)
    }
    global_changes = changed_paths & GLOBAL_RELEASE_INPUTS
    for slug in slugs:
        affected[slug].update(shared_changes)
        affected[slug].update(global_changes)
        prefix = f"{slug}/"
        affected[slug].update(
            path
            for path in changed_paths
            if path.startswith(prefix) and _is_release_input(path)
        )
    return affected


def _is_release_input(path: str) -> bool:
    """Exclude tests and prose that cannot alter a container/bundle contract."""
    parts = Path(path).parts
    return "tests" not in parts and Path(path).suffix.lower() != ".md"


def validate_release_versions(
    *,
    release_ref: str,
    previous_ref: str,
) -> list[str]:
    """Return release-version contract violations between two refs."""
    errors: list[str] = []
    if release_ref.startswith("v"):
        expected = release_ref.removeprefix("v")
        project = tomllib.loads(_git_text(release_ref, "pyproject.toml"))["project"]
        if project["version"] != expected:
            errors.append(
                f"release tag {release_ref} does not match pyproject version "
                f"{project['version']}",
            )

    current_inventory = released_backends(release_ref=release_ref)
    previous_inventory = {
        backend["slug"]: backend
        for backend in released_backends(release_ref=previous_ref)
    }
    changed_paths = set(
        _git("diff", "--name-only", previous_ref, release_ref).splitlines(),
    )
    affected = release_inputs_by_slug(
        changed_paths,
        {backend["slug"] for backend in current_inventory},
    )

    for backend in current_inventory:
        slug = backend["slug"]
        previous = previous_inventory.get(slug)
        if previous is None or not affected[slug]:
            continue
        current_version = manifest_version(
            _git_text(release_ref, backend["manifest"]),
        )
        previous_version = manifest_version(
            _git_text(previous_ref, previous["manifest"]),
        )
        if semver_key(current_version) <= semver_key(previous_version):
            examples = ", ".join(sorted(affected[slug])[:3])
            errors.append(
                f"{slug} release inputs changed, so its task version must "
                f"increase beyond {previous_version}; got {current_version} "
                f"({examples})",
            )
    return errors


def _parser() -> argparse.ArgumentParser:
    """Build the release-check CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-ref", required=True)
    parser.add_argument("--previous-ref", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate one release against its preceding reachable tag."""
    args = _parser().parse_args(argv)
    try:
        previous_ref = args.previous_ref or _git(
            "describe",
            "--tags",
            "--abbrev=0",
            f"{args.release_ref}^",
        )
        errors = validate_release_versions(
            release_ref=args.release_ref,
            previous_ref=previous_ref,
        )
    except (KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Release version validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("Release version validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Release versions are valid relative to {previous_ref}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
