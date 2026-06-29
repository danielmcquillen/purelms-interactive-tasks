"""Entrypoint contract tests for the modelica_diagram backend.

Run without Docker via ``main()`` against temp dirs (mirrors echo). Needs the
backend env (purelms-itask-runtime + purelms-shared + jsonschema): run via
``just test modelica_diagram``. Covers the no-FMU paths (wrong diagram, unknown
scenario); the correct-diagram + FMU path runs in-container once the FMU is
compiled (ADR-0019 Check 1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from purelms_shared.envelopes import SimulationOutputEnvelope

sys.path.insert(0, str(Path(__file__).parent.parent))
import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Two temp dirs wired into the env vars main() reads (mirrors echo)."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("PURELMS_INPUT_DIR", str(input_dir))
    monkeypatch.setenv("PURELMS_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("PURELMS_RUN_ID", "test-run")
    return input_dir, output_dir


def _write_input(input_dir: Path, parameters: dict) -> None:
    envelope = {
        "schema_version": "purelms.input.v1",
        "run_id": str(uuid4()),
        "backend_slug": "modelica_diagram",
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


def test_wrong_diagram_is_success_with_topology_false(workspace):
    """An empty diagram is a valid run graded topology_correct=false."""
    input_dir, output_dir = workspace
    _write_input(input_dir, {"scenario": "hydronic_loop", "diagram_json": ""})

    assert main.main() == 0
    out = json.loads((output_dir / "output.json").read_text())
    assert out["status"] == "success"
    assert out["outputs"]["topology_correct"] is False


def test_unknown_scenario_is_graded_failure(workspace):
    input_dir, output_dir = workspace
    _write_input(input_dir, {"scenario": "does_not_exist", "diagram_json": "{}"})

    assert main.main() == 0
    out = json.loads((output_dir / "output.json").read_text())
    assert out["status"] == "success"
    assert out["outputs"]["topology_correct"] is False
    assert any("Unknown scenario" in m["text"] for m in out["messages"])


def test_output_round_trips_as_envelope(workspace):
    """Output must parse back through SimulationOutputEnvelope (LMS contract)."""
    input_dir, output_dir = workspace
    _write_input(input_dir, {"scenario": "hydronic_loop", "diagram_json": ""})
    main.main()

    raw = (output_dir / "output.json").read_text()
    envelope = SimulationOutputEnvelope.model_validate_json(raw)
    assert envelope.status.value == "success"
