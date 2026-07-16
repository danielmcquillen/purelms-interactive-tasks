"""
Entrypoint tests for ``main.py`` — the envelope read/write contract.

Exercise ``main()`` directly against synthetic input envelopes in
``tmp_path``. No Docker daemon required; these tests run in the
backend's own pytest run via ``just test energyplus_single_zone``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

# Make backend dir importable without a build step.
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import main
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import ExecutionContext
from purelms_shared.envelopes import SimulationInputEnvelope
from purelms_shared.envelopes import SimulationOutputEnvelope

# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _envelope(parameters: dict, *, run_id=None) -> SimulationInputEnvelope:
    """Build a minimal SimulationInputEnvelope for the sync path.

    The context fields are placeholders because this unit test uses the local
    directory path. Cloud Run Jobs uses them for progress and completion
    callbacks through the shared runtime.
    """
    return SimulationInputEnvelope(
        run_id=run_id or uuid4(),
        backend_slug="energyplus_single_zone",
        backend_version="0.1.0",
        student_id=1,
        course_id=1,
        block_id=1,
        parameters=parameters,
        context=ExecutionContext(
            callback_url_progress="file:///dev/null",
            callback_url_complete="file:///dev/null",
            callback_audience="unused-sync-backend",
            timeout_seconds=30,
        ),
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A per-test (input/, output/) workspace pair.

    Sets the standard env vars so ``main()`` reads + writes the
    right paths.
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("PURELMS_INPUT_DIR", str(input_dir))
    monkeypatch.setenv("PURELMS_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("PURELMS_RUN_ID", "test-run-id")
    # Force the binary-free analytical model so these contract tests are
    # deterministic regardless of whether the dev's machine happens to
    # have an ``energyplus`` binary on PATH (auto mode would otherwise
    # try the real path, which needs bundled weather files).
    monkeypatch.setenv("PURELMS_EPLUS_MODE", "analytical")
    return input_dir, output_dir


def _write_envelope(input_dir: Path, envelope: SimulationInputEnvelope) -> None:
    (input_dir / "input.json").write_text(envelope.model_dump_json())


def _read_output(output_dir: Path) -> dict:
    return json.loads((output_dir / "output.json").read_text())


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_main_writes_success_envelope_with_all_outputs(workspace):
    input_dir, output_dir = workspace
    _write_envelope(
        input_dir,
        _envelope(
            {
                "glazing_u_value": 2.5,
                "window_area": 5.0,
                "climate_zone": "5A",
            },
        ),
    )

    assert main() == 0

    out = _read_output(output_dir)
    assert out["status"] == OutputStatus.SUCCESS
    assert set(out["outputs"].keys()) == {
        "annual_heating_kWh",
        "annual_cooling_kWh",
        "peak_heating_kW",
        "notes",
    }
    # Baseline math (see test_runner.py for the formula).
    assert out["outputs"]["annual_heating_kWh"] == pytest.approx(1650.0, rel=0.01)


def test_main_echoes_run_id_from_input_to_output(workspace):
    input_dir, output_dir = workspace
    run_id = uuid4()
    _write_envelope(
        input_dir,
        _envelope(
            {
                "glazing_u_value": 2.5,
                "window_area": 5.0,
                "climate_zone": "5A",
            },
            run_id=run_id,
        ),
    )

    assert main() == 0

    out = _read_output(output_dir)
    assert out["run_id"] == str(run_id)


def test_main_output_parses_as_simulation_output_envelope(workspace):
    """Round-trip: the output we write MUST parse back via the v1 schema."""
    input_dir, output_dir = workspace
    _write_envelope(
        input_dir,
        _envelope(
            {
                "glazing_u_value": 1.0,
                "window_area": 3.0,
                "climate_zone": "6A",
            },
        ),
    )

    assert main() == 0

    raw = (output_dir / "output.json").read_text()
    parsed = SimulationOutputEnvelope.model_validate_json(raw)
    assert parsed.is_success()


# ---------------------------------------------------------------------
# Bad parameters → FAILED_RUNTIME envelope (not a non-zero exit)
# ---------------------------------------------------------------------


def test_main_bad_parameters_writes_failed_runtime_envelope(workspace):
    """Credits are refunded on FAILED_RUNTIME. We want the
    LMS to see a clean envelope, not a log-tail fallback."""
    input_dir, output_dir = workspace
    _write_envelope(
        input_dir,
        _envelope(
            {
                "glazing_u_value": 2.5,
                "window_area": 5.0,
                "climate_zone": "Z99",  # invalid
            },
        ),
    )

    # Exit 0 is correct — the envelope carries the failure.
    assert main() == 0

    out = _read_output(output_dir)
    assert out["status"] == OutputStatus.FAILED_RUNTIME
    assert out["outputs"] == {}
    # Error message surfaced via the messages array.
    error_messages = [m for m in out["messages"] if m["level"] == "error"]
    assert len(error_messages) == 1
    assert "Z99" in error_messages[0]["text"]


def test_main_missing_required_parameter_writes_failed_runtime_envelope(workspace):
    input_dir, output_dir = workspace
    _write_envelope(
        input_dir,
        _envelope(
            {
                # missing glazing_u_value
                "window_area": 5.0,
                "climate_zone": "5A",
            },
        ),
    )

    assert main() == 0
    out = _read_output(output_dir)
    assert out["status"] == OutputStatus.FAILED_RUNTIME
    assert any(
        "glazing_u_value" in m["text"] for m in out["messages"] if m["level"] == "error"
    )


# ---------------------------------------------------------------------
# Contract violations → non-zero exit
# ---------------------------------------------------------------------


def test_main_missing_input_file_exits_nonzero(workspace):
    """No input.json → exit 1; LMS marks the run FAILED_RUNTIME via the
    exit-code fallback path."""
    _, output_dir = workspace
    # Don't write input.json.

    assert main() == 1
    # And we should NOT have written an output envelope.
    assert not (output_dir / "output.json").exists()


def test_main_malformed_input_file_exits_nonzero(workspace):
    input_dir, output_dir = workspace
    (input_dir / "input.json").write_text("{not: valid: json")

    assert main() == 1
    assert not (output_dir / "output.json").exists()


# The progress/complete callback wiring + local-vs-signed-object I/O
# live in the shared ``purelms_itask_runtime`` package and are tested
# there (``_shared_backends/purelms_itask_runtime/tests/test_runtime.py``).
# These tests cover only this backend's contract: read input -> run the
# domain code -> write a SUCCESS / FAILED_RUNTIME envelope, on the local
# (sync) path the ``workspace`` fixture exercises.
