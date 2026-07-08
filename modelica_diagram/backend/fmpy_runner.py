"""Run a scenario's pre-compiled FMU with the learner's parameters.

``fmpy.simulate_fmu`` executes native FMU code; a divergent solver can hang a
process indefinitely, so the simulation runs in a SEPARATE process with a
wall-clock timeout (terminate, then kill, on overrun) — the pattern proven in
Validibot's FMU runner. This module is only reached once the topology is
already correct.

NOT executed in-repo: running the FMU needs the compiled ``model.fmu``
(``linux64``) and ``fmpy`` present, so the smoke + golden-vector tests run
in-container on ``linux/amd64`` (Check 1). The pure-Python parameter
mapping + output summarisation here are unit-testable independently.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

# Cap the time series persisted to the output envelope; the LMS stores
# ``outputs_payload`` in Postgres and returns it on every status poll, so an
# unbounded series would bloat both.
MAX_SERIES_POINTS = 500
_KILL_GRACE_SECONDS = 5


class FmuRunError(Exception):
    """The FMU run failed: missing model, timeout, solver error, or bad binding."""


def map_start_values(parameters: dict, scenario: dict) -> dict[str, Any]:
    """Map manifest parameter names to FMU start values via ``parameter_map``.

    Only mapped, non-None parameters are forwarded; ``diagram_json`` and the
    ``scenario`` selector are not FMU inputs and are ignored.
    """
    param_map = scenario.get("parameter_map", {})
    return {
        fmu_var: parameters[manifest_name]
        for manifest_name, fmu_var in param_map.items()
        if parameters.get(manifest_name) is not None
    }


def summarize(time, recorded: dict, scenario: dict) -> dict[str, Any]:
    """Reduce raw FMU traces to the scenario's declared output values + series.

    ``recorded`` maps FMU variable name -> sequence of sampled values. Each
    declared output is summarised per its ``summarize`` mode (``final`` /
    ``integral``); the headline series is downsampled to ``MAX_SERIES_POINTS``.
    """
    out: dict[str, Any] = {}
    series_var = None
    for name, spec in scenario.get("outputs", {}).items():
        var = spec["fmu_variable"]
        values = list(recorded.get(var, []))
        if not values:
            continue
        # Coerce out of numpy scalars so the output envelope is JSON-clean.
        if spec.get("summarize") == "integral":
            out[name] = float(_trapezoid(time, values))
        else:  # "final" (default)
            out[name] = float(values[-1])
        series_var = series_var or var
    if series_var is not None:
        out["series_json"] = json.dumps(
            _downsample(time, recorded.get(series_var, [])),
        )
    return out


def run_fmu(
    fmu_path: str | Path,
    parameters: dict,
    scenario: dict,
    *,
    timeout_s: float = 60,
) -> dict[str, Any]:
    """Simulate ``fmu_path`` with the learner's parameters; return output values.

    Runs ``simulate_fmu`` in a spawned subprocess with a wall-clock budget so a
    hung solver can't take the container down. Raises :class:`FmuRunError` on
    any failure (the caller turns it into a learner message; the run still
    completes with ``topology_correct=True``).
    """
    path = Path(fmu_path)
    if not path.is_file():
        raise FmuRunError("The simulation model isn't available for this scenario yet.")

    start_values = map_start_values(parameters, scenario)
    record = [spec["fmu_variable"] for spec in scenario.get("outputs", {}).values()]

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_simulate_worker,
        args=(str(path), start_values, record, queue),
    )
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(_KILL_GRACE_SECONDS)
        if proc.is_alive():
            proc.kill()
        raise FmuRunError(f"The simulation timed out after {timeout_s:.0f}s.")

    try:
        ok, payload = queue.get_nowait()
    except Exception as exc:  # empty queue → the worker died before reporting
        raise FmuRunError("The simulation produced no result.") from exc
    if not ok:
        raise FmuRunError(payload)

    time, recorded = payload
    return summarize(time, recorded, scenario)


def _simulate_worker(fmu_path: str, start_values: dict, record: list, queue) -> None:
    """Subprocess target: run the FMU, put ``(ok, payload)`` on the queue.

    Imports ``fmpy`` HERE (not at module load) so the parent stays importable +
    unit-testable without ``fmpy`` installed.
    """
    try:
        from fmpy import simulate_fmu  # noqa: PLC0415

        result = simulate_fmu(
            fmu_path,
            start_values=start_values,
            output=record or None,
        )
        recorded = {
            name: list(result[name]) for name in record if name in result.dtype.names
        }
        queue.put((True, (list(result["time"]), recorded)))
    except Exception as exc:  # any solver / binding failure
        queue.put((False, f"The simulation failed: {exc}"))


def _trapezoid(time, values) -> float:
    """Trapezoidal integral of ``values`` over ``time`` (no numpy dependency)."""
    total = 0.0
    for i in range(1, len(values)):
        total += (time[i] - time[i - 1]) * (values[i] + values[i - 1]) / 2.0
    return total


def _downsample(time, values) -> list[list[float]]:
    """``[[t, v], ...]`` downsampled to at most ``MAX_SERIES_POINTS`` points."""
    pairs = list(zip(time, values, strict=False))
    if len(pairs) <= MAX_SERIES_POINTS:
        return [[float(t), float(v)] for t, v in pairs]
    step = len(pairs) / MAX_SERIES_POINTS
    return [
        [float(pairs[int(i * step)][0]), float(pairs[int(i * step)][1])]
        for i in range(MAX_SERIES_POINTS)
    ]
