#!/usr/bin/env python3
"""Validate immutable task assets before a container build or release."""

from __future__ import annotations

import hashlib
import re
import struct
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_PATTERN = re.compile(
    r'^\s*- path:\s*["\']?([^"\'\n]+?)["\']?\s*\n'
    r'^\s+sha256:\s*["\']?([0-9a-f]{64})["\']?\s*$',
    re.MULTILINE,
)
ELF_MAGIC = b"\x7fELF"
ELF_MACHINE_X86_64 = 62
ELF_HEADER_MIN_BYTES = 20


def _sha256(path: Path) -> str:
    """Return a file's lowercase SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _elf_machine(data: bytes) -> int | None:
    """Return an ELF blob's e_machine value, or None for a non-ELF blob."""
    if len(data) < ELF_HEADER_MIN_BYTES or data[:4] != ELF_MAGIC:
        return None
    byte_order = {1: "<", 2: ">"}.get(data[5])
    if byte_order is None:
        raise ValueError("ELF file has an invalid byte-order marker")
    return struct.unpack_from(f"{byte_order}H", data, 18)[0]


def _validate_fmu(path: Path) -> list[str]:
    """Return architecture errors for one FMI archive."""
    errors: list[str] = []
    elf_members: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if not member.startswith("binaries/linux64/") or member.endswith("/"):
                    continue
                machine = _elf_machine(archive.read(member))
                if machine is None:
                    continue
                elf_members.append(member)
                if machine != ELF_MACHINE_X86_64:
                    errors.append(
                        f"{path.relative_to(REPO_ROOT)}:{member} uses ELF "
                        f"machine {machine}; Cloud Run requires x86-64 "
                        f"({ELF_MACHINE_X86_64})",
                    )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return [f"{path.relative_to(REPO_ROOT)} is not a valid FMU: {exc}"]

    if not elf_members:
        errors.append(
            f"{path.relative_to(REPO_ROOT)} has no ELF binaries under "
            "binaries/linux64/",
        )
    return errors


def validate_release_assets() -> list[str]:
    """Return all manifest-integrity and native-architecture errors."""
    errors: list[str] = []
    asset_count = 0
    for manifest in sorted(REPO_ROOT.glob("*/interactive_task.yaml")):
        task_root = manifest.parent
        matches = ASSET_PATTERN.findall(manifest.read_text(encoding="utf-8"))
        for relative_path, expected_digest in matches:
            asset_count += 1
            asset = task_root / relative_path.strip()
            if not asset.is_file():
                errors.append(f"{manifest.name}: missing asset {relative_path}")
                continue
            actual_digest = _sha256(asset)
            if actual_digest != expected_digest:
                errors.append(
                    f"{asset.relative_to(REPO_ROOT)} SHA-256 is {actual_digest}; "
                    f"manifest declares {expected_digest}",
                )
            if asset.suffix.lower() == ".fmu":
                errors.extend(_validate_fmu(asset))

    if asset_count == 0:
        errors.append("no manifest assets were discovered")
    return errors


def main() -> int:
    """Print a concise validation result and return a shell-friendly status."""
    errors = validate_release_assets()
    if errors:
        print("Release asset validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Release assets: hashes valid; embedded Linux binaries are x86-64.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
