#!/usr/bin/env python3
"""Run one built backend image through its real local envelope contract."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SLUGS = ("echo", "energyplus_single_zone", "modelica_diagram")
TARGET_PLATFORM = "linux/amd64"


def _correct_modelica_diagram() -> dict:
    """Build the canonical correct graph from the checked-in scenario."""
    scenario_path = (
        REPO_ROOT / "modelica_diagram/backend/scenarios/hydronic_loop/scenario.json"
    )
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    return {
        "schema": "purelms.diagram.v1",
        "nodes": [
            {"id": component, "type": component}
            for component in scenario["expected"]["nodes"]
        ],
        "edges": [
            {
                "source": {
                    "node": edge["source"]["type"],
                    "port": edge["source"]["port"],
                },
                "target": {
                    "node": edge["target"]["type"],
                    "port": edge["target"]["port"],
                },
            }
            for edge in scenario["expected"]["edges"]
        ],
    }


def _parameters(slug: str) -> dict:
    """Return a representative successful parameter set for a backend."""
    if slug == "echo":
        return {"smoke_test": True, "target_platform": TARGET_PLATFORM}
    if slug == "energyplus_single_zone":
        return {
            "glazing_u_value": 2.5,
            "window_area": 5.0,
            "climate_zone": "5A",
        }
    if slug == "modelica_diagram":
        return {
            "scenario": "hydronic_loop",
            "diagram_json": json.dumps(_correct_modelica_diagram()),
            "boiler_nominal_power_kw": 10,
            "room_setpoint_c": 21,
            "heat_loss_w_per_k": 150,
            "outdoor_temp_c": 0,
        }
    raise ValueError(f"unsupported backend slug: {slug}")


def _input_envelope(slug: str) -> dict:
    """Return a local-directory SimulationInputEnvelope payload."""
    return {
        "schema_version": "purelms.input.v1",
        "run_id": str(uuid4()),
        "backend_slug": slug,
        "backend_version": "local-smoke",
        "student_id": 1,
        "course_id": 1,
        "block_id": 1,
        "parameters": _parameters(slug),
        "input_files": [],
        "resource_files": [],
        "context": {
            "callback_url_progress": "file:///dev/null",
            "callback_url_complete": "file:///dev/null",
            "callback_audience": "unused-local-smoke",
            "timeout_seconds": 300,
            "progress_min_interval_seconds": 2.0,
        },
    }


def _assert_success(slug: str, output: dict) -> None:
    """Assert backend-specific proof that real domain execution completed."""
    if output.get("status") != "success":
        raise RuntimeError(f"{slug} returned status {output.get('status')!r}")
    values = output.get("outputs", {})
    if slug == "echo" and not values.get("echoed_parameters", {}).get("smoke_test"):
        raise RuntimeError("echo did not round-trip the smoke parameters")
    if slug == "energyplus_single_zone" and not values.get("annual_heating_kWh"):
        raise RuntimeError("EnergyPlus did not produce annual heating output")
    if slug == "modelica_diagram":
        if values.get("topology_correct") is not True:
            raise RuntimeError("Modelica topology did not pass")
        required = {"room_temp_final_c", "energy_used_kwh", "series_json"}
        missing = required - values.keys()
        if missing:
            messages = [
                message.get("text", "") for message in output.get("messages", [])
            ]
            raise RuntimeError(
                f"Modelica FMU did not produce {sorted(missing)}; messages={messages}",
            )


def _assert_image_contract(slug: str, image: str) -> None:
    """Verify the loaded image architecture and operator-facing OCI labels."""
    completed = subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    records = json.loads(completed.stdout)
    if len(records) != 1:
        raise RuntimeError(
            f"expected one inspect record for {image}, got {len(records)}"
        )
    record = records[0]
    if record.get("Architecture") != "amd64" or record.get("Os") != "linux":
        raise RuntimeError(
            f"{image} is {record.get('Os')}/{record.get('Architecture')}, "
            f"expected {TARGET_PLATFORM}",
        )

    labels = record.get("Config", {}).get("Labels") or {}
    expected = {
        "io.purelms.interactive-task.slug": slug,
        "org.opencontainers.image.source": (
            "https://github.com/danielmcquillen/purelms-interactive-tasks"
        ),
    }
    for name, value in expected.items():
        if labels.get(name) != value:
            raise RuntimeError(
                f"{image} label {name!r} is {labels.get(name)!r}, expected {value!r}",
            )
    for name in (
        "io.purelms.interactive-task.version",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.version",
    ):
        if not labels.get(name):
            raise RuntimeError(f"{image} is missing required OCI label {name!r}")


def smoke_backend(slug: str, image: str) -> None:
    """Execute an image with bind-mounted input/output and validate its result."""
    _assert_image_contract(slug, image)
    with tempfile.TemporaryDirectory(prefix=f"purelms-{slug}-") as workspace:
        root = Path(workspace)
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        output_dir.chmod(0o777)
        (input_dir / "input.json").write_text(
            json.dumps(_input_envelope(slug)),
            encoding="utf-8",
        )

        command = [
            "docker",
            "run",
            "--rm",
            "--platform",
            TARGET_PLATFORM,
            "--network",
            "none",
            "--env",
            "PURELMS_INPUT_DIR=/purelms/input",
            "--env",
            "PURELMS_OUTPUT_DIR=/purelms/output",
            "--volume",
            f"{input_dir}:/purelms/input:ro",
            "--volume",
            f"{output_dir}:/purelms/output",
            image,
        ]
        subprocess.run(command, check=True)
        output_path = output_dir / "output.json"
        if not output_path.is_file():
            raise RuntimeError(f"{slug} did not write output.json")
        output = json.loads(output_path.read_text(encoding="utf-8"))
        _assert_success(slug, output)


def main() -> int:
    """Parse CLI arguments and execute one smoke test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", choices=SUPPORTED_SLUGS)
    parser.add_argument(
        "--image",
        help="image reference (defaults to purelms-itask-<slug>:dev)",
    )
    args = parser.parse_args()
    image_slug = args.slug.replace("_", "-")
    image = args.image or f"purelms-itask-{image_slug}:dev"
    smoke_backend(args.slug, image)
    print(f"{args.slug}: local {TARGET_PLATFORM} container smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
