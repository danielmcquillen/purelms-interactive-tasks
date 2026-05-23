"""
Unit tests for the echo backend's ``main()`` entrypoint.

Tests run without Docker — they exercise ``main()`` directly against
a temp-dir workspace. The Docker integration is covered by the
LMS-side ``test_e2e_docker.py`` (skip-by-default in CI).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from purelms_shared.envelopes import SimulationOutputEnvelope

# Make the backend importable without installing it as a package.
sys.path.insert(0, str(Path(__file__).parent.parent))
import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Two temp dirs (input + output) wired into the env vars main() reads."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("PURELMS_INPUT_DIR", str(input_dir))
    monkeypatch.setenv("PURELMS_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("PURELMS_RUN_ID", "test-run")
    return input_dir, output_dir


def _write_input(input_dir: Path, parameters: dict) -> None:
    """Write a valid SimulationInputEnvelope into input/input.json."""
    envelope = {
        "schema_version": "purelms.input.v1",
        "run_id": str(uuid4()),
        "backend_slug": "echo",
        "backend_version": "0.1.0",
        "student_id": 1,
        "course_id": 1,
        "block_id": 1,
        "parameters": parameters,
        "input_files": [],
        "resource_files": [],
        "context": {
            "callback_url_progress": "file:///dev/null",
            "callback_url_complete": "file:///dev/null",
            "callback_audience": "unused",
            "timeout_seconds": 30,
            "progress_min_interval_seconds": 2.0,
        },
    }
    (input_dir / "input.json").write_text(json.dumps(envelope))


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_main_writes_success_output_envelope(workspace):
    """Echo reads input, writes a SUCCESS output envelope."""
    input_dir, output_dir = workspace
    _write_input(input_dir, {"wall_r": 13, "window_u": 0.4})

    exit_code = main.main()

    assert exit_code == 0
    output_path = output_dir / "output.json"
    assert output_path.exists()

    output = json.loads(output_path.read_text())
    assert output["schema_version"] == "purelms.output.v1"
    assert output["status"] == "success"
    assert output["outputs"]["echoed_parameters"] == {"wall_r": 13, "window_u": 0.4}
    assert output["outputs"]["echoed_backend_slug"] == "echo"
    # runtime_seconds is positive (echo runs in milliseconds, but
    # never negative or NaN).
    assert output["runtime_seconds"] >= 0


def test_main_echoes_empty_parameters(workspace):
    """Empty parameters dict — still produces a valid envelope."""
    input_dir, output_dir = workspace
    _write_input(input_dir, {})

    assert main.main() == 0
    output = json.loads((output_dir / "output.json").read_text())
    assert output["outputs"]["echoed_parameters"] == {}


# ---------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------


def test_main_returns_nonzero_when_input_missing(workspace):
    """No input/input.json → exit 1, no output written."""
    _input_dir, output_dir = workspace
    # Don't write the input envelope.

    assert main.main() == 1
    assert not (output_dir / "output.json").exists()


def test_main_returns_nonzero_when_input_malformed(workspace):
    """Input is JSON but not a valid SimulationInputEnvelope → exit 1."""
    input_dir, output_dir = workspace
    (input_dir / "input.json").write_text('{"not_an_envelope": true}')

    assert main.main() == 1
    assert not (output_dir / "output.json").exists()


def test_main_returns_nonzero_when_input_not_json(workspace):
    """Input isn't even JSON → exit 1."""
    input_dir, output_dir = workspace
    (input_dir / "input.json").write_text("totally not json")

    assert main.main() == 1
    assert not (output_dir / "output.json").exists()


# ---------------------------------------------------------------------
# Round-trip: the LMS can parse echo's output
# ---------------------------------------------------------------------


def test_main_output_parses_back_as_simulation_output_envelope(workspace):
    """The output JSON must round-trip through
    SimulationOutputEnvelope.model_validate_json — that's the
    contract with the LMS-side DockerComposeExecutionBackend."""
    input_dir, output_dir = workspace
    _write_input(input_dir, {"x": 1})
    main.main()

    raw = (output_dir / "output.json").read_text()
    envelope = SimulationOutputEnvelope.model_validate_json(raw)
    assert envelope.status.value == "success"
    assert envelope.outputs["echoed_parameters"] == {"x": 1}
