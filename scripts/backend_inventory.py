#!/usr/bin/env python3
"""Read the authoritative backend inventory and build deployment catalogs.

``backends.toml`` is the only released-backend membership declaration. This
helper gives Just, GitHub Actions, and PureLMS deployment orchestration one
small, dependency-free interface to that declaration. Catalog generation reads
both the inventory and manifests from the requested Git ref, never from a
possibly newer working tree.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "backends.toml"
CATALOG_SCHEMA_VERSION = 1


def _git_text(release_ref: str, relative_path: str) -> str:
    """Return one file exactly as committed at ``release_ref``."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{release_ref}:{relative_path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def load_inventory(*, release_ref: str | None = None) -> dict[str, Any]:
    """Load and minimally validate the current or tagged backend inventory."""
    raw = (
        _git_text(release_ref, "backends.toml")
        if release_ref
        else INVENTORY_PATH.read_text(encoding="utf-8")
    )
    inventory = tomllib.loads(raw)
    if inventory.get("schema_version") != 1:
        msg = "backends.toml schema_version must be 1"
        raise ValueError(msg)
    backends = inventory.get("backend")
    if not isinstance(backends, list) or not backends:
        msg = "backends.toml must declare at least one [[backend]]"
        raise ValueError(msg)
    slugs = [backend.get("slug") for backend in backends]
    if any(not isinstance(slug, str) or not slug for slug in slugs):
        msg = "every backend inventory entry must have a non-empty slug"
        raise ValueError(msg)
    if len(slugs) != len(set(slugs)):
        msg = "backend inventory contains duplicate slugs"
        raise ValueError(msg)
    return inventory


def released_backends(
    *,
    release_ref: str | None = None,
    selected_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return released inventory records in their declared order."""
    records = [
        backend
        for backend in load_inventory(release_ref=release_ref)["backend"]
        if backend.get("release") is True
    ]
    if not selected_slugs:
        return records

    if len(selected_slugs) != len(set(selected_slugs)):
        msg = "a backend slug may be selected only once"
        raise ValueError(msg)
    selected = set(selected_slugs)
    known = {backend["slug"] for backend in records}
    unknown = sorted(selected - known)
    if unknown:
        msg = f"unknown or unreleased backend slug(s): {', '.join(unknown)}"
        raise ValueError(msg)
    return [backend for backend in records if backend["slug"] in selected]


def parse_image_assignments(values: list[str]) -> dict[str, str]:
    """Parse repeated ``slug=immutable-image-uri`` CLI assignments."""
    images: dict[str, str] = {}
    for value in values:
        slug, separator, image_uri = value.partition("=")
        if not separator or not slug or not image_uri:
            msg = f"--image must use slug=image-uri syntax; got {value!r}"
            raise ValueError(msg)
        if slug in images:
            msg = f"duplicate --image assignment for {slug!r}"
            raise ValueError(msg)
        images[slug] = image_uri
    return images


def build_registration_catalog(
    *,
    release_ref: str,
    image_by_slug: dict[str, str],
    selected_slugs: list[str] | None = None,
) -> dict[str, Any]:
    """Build the exact manifest/image catalog consumed by Django sync."""
    backends = released_backends(
        release_ref=release_ref,
        selected_slugs=selected_slugs,
    )
    expected = {backend["slug"] for backend in backends}
    supplied = set(image_by_slug)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        details = []
        if missing:
            details.append(f"missing images: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected images: {', '.join(extra)}")
        msg = (
            "catalog image assignments do not match inventory ("
            + "; ".join(
                details,
            )
            + ")"
        )
        raise ValueError(msg)

    entries = []
    for backend in backends:
        slug = backend["slug"]
        manifest_path = backend.get("manifest")
        if not isinstance(manifest_path, str) or not manifest_path:
            msg = f"backend {slug!r} has no manifest path"
            raise ValueError(msg)
        entries.append(
            {
                "slug": slug,
                "manifest_yaml": _git_text(release_ref, manifest_path),
                "image_uri": image_by_slug[slug],
            },
        )

    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "release_ref": release_ref,
        "backends": entries,
    }


def _parser() -> argparse.ArgumentParser:
    """Build the inventory helper's command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Print released slugs.")
    list_parser.add_argument("--release-ref")
    list_parser.add_argument("--slug", action="append", default=[])
    list_parser.add_argument(
        "--format",
        choices=("lines", "words"),
        default="lines",
    )

    subparsers.add_parser(
        "matrix",
        help="Print the GitHub Actions JSON matrix from current inventory.",
    )

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Build a tagged manifest/image registration catalog.",
    )
    catalog_parser.add_argument("--release-ref", required=True)
    catalog_parser.add_argument("--slug", action="append", default=[])
    catalog_parser.add_argument("--image", action="append", default=[])
    catalog_parser.add_argument("--base64", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one inventory query and write its machine-readable result."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "list":
            records = released_backends(
                release_ref=args.release_ref,
                selected_slugs=args.slug,
            )
            slugs = [record["slug"] for record in records]
            separator = "\n" if args.format == "lines" else " "
            sys.stdout.write(separator.join(slugs))
            sys.stdout.write("\n")
        elif args.command == "matrix":
            slugs = [backend["slug"] for backend in released_backends()]
            sys.stdout.write(json.dumps({"slug": slugs}, separators=(",", ":")))
            sys.stdout.write("\n")
        else:
            catalog = build_registration_catalog(
                release_ref=args.release_ref,
                image_by_slug=parse_image_assignments(args.image),
                selected_slugs=args.slug,
            )
            raw = json.dumps(catalog, separators=(",", ":")).encode("utf-8")
            if args.base64:
                sys.stdout.write(base64.b64encode(raw).decode("ascii"))
            else:
                sys.stdout.write(raw.decode("utf-8"))
            sys.stdout.write("\n")
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
