"""
EnergyPlus single-zone runner — real simulation with an analytical fallback.

This module is the **domain code** for the InteractiveTask: ``main.py``
handles the envelope contract (read input → call ``simulate`` → write
output) and never changes; ``simulate`` does the work.

## Two execution modes

The single-zone scenario can run two ways, selected by
``PURELMS_EPLUS_MODE`` (default ``auto``):

- **``energyplus``** — the real thing. Builds a single-zone IDF from the
  learner's parameters (``build_idf``), runs the EnergyPlus binary
  against the bundled EPW for the climate zone, and mines
  ``eplusout.sql`` for annual heating/cooling energy + peak heating
  load. This is what the production container does (the Dockerfile bakes
  in the binary + weather files).
- **``analytical``** — a pure-Python steady-state heat-balance model
  (no binary, no I/O). The dev/CI fallback so contributors can iterate
  without the ~500 MB EnergyPlus dependency, and so the local echo-demo
  + the fast unit suite keep working. Qualitatively correct (lower U →
  less loss, colder climate → more heating) but NOT a design tool.

``auto`` picks ``energyplus`` when the binary is on ``PATH``, else
``analytical``. Both modes return the *same* dict shape — keys match the
manifest's ``outputs[].name`` exactly — so the LMS can't tell which ran
except via the ``notes`` string, which says so honestly.

## What's verified vs. what needs a real build

The fixture-testable pieces — ``build_idf`` (template substitution),
``extract_metrics`` (SQL mining against a synthetic EnergyPlus-schema
SQLite DB), ``parse_err_file``, and the analytical model — have unit
tests. The end-to-end ``energyplus`` path (does the IDF run, do the
numbers come out sane) requires the real binary + weather files and is
validated by ``just build energyplus_single_zone`` + a container run,
not by this repo's fast suite. See ``README.md`` §Verification status.

The SQL-extraction queries are translated from Validibot's
production-proven EnergyPlus validator backend (BSD-licensed reference,
re-implemented as PureLMS code per the project's adopt-by-copy model).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import string
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from climate import CLIMATE_ZONES
from climate import ClimateZone
from climate import get_climate_zone

logger = logging.getLogger(__name__)

# A progress reporter: ``on_progress(pct, step)`` where ``pct`` is
# 0-100 and ``step`` is a short human label. ``simulate`` calls it at
# phase boundaries; ``main.py`` wires it to the worker's progress
# callback so async deployments drive a determinate progress bar.
#
# Planned direction: progress is expected to eventually carry not just
# a percent + label but renderable partial VALUES (interim outputs the
# frontend can show mid-run). When that lands in the shared
# ``ProgressCallback`` body, this signature grows a third (optional)
# argument; today it is progress-only.
ProgressFn = Callable[[int, str], None]


def _noop_progress(pct: int, step: str) -> None:
    """Default reporter — does nothing. Lets callers omit ``on_progress``."""


# The window's fixed height (m). ``build_idf`` derives the window LENGTH
# from the learner's area (area / height) so the area parameter maps onto
# a single template field. 2.5 m keeps the max 20 m² window (length 8 m)
# inside the 10 m-wide south wall.
WINDOW_HEIGHT_M: float = 2.5

# The EnergyPlus CLI name + where the Dockerfile puts it on PATH.
_ENERGYPLUS_BIN = "energyplus"

# Unit conversions. EnergyPlus tabular energy is GJ; output-variable
# rates are W; energy variables are J.
_GJ_TO_KWH: float = 277.778
_W_TO_KW: float = 1.0 / 1000.0
_J_TO_KWH: float = 1.0 / 3_600_000.0

# Hard wall-clock budget for the subprocess. The manifest declares a
# 30 s timeout; we give the binary a little more headroom and let the
# LMS layer enforce its own.
_ENERGYPLUS_TIMEOUT_SECONDS: int = 120

# Preferred reporting-frequency order when mining an output variable —
# pick exactly one so an IDF requesting the same variable at multiple
# frequencies doesn't double-count (Validibot pattern).
_FREQUENCY_PREFERENCE = [
    "Run Period",
    "Monthly",
    "Daily",
    "Hourly",
    "Zone Timestep",
    "HVAC System Timestep",
]

# The peak-heating output variable requested in the IDF template.
_PEAK_HEATING_VARIABLE = "Zone Ideal Loads Supply Air Sensible Heating Rate"


# ---------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------


def simulate(
    parameters: dict[str, Any],
    *,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run the single-zone simulation; return the manifest's outputs dict.

    Pure-ish orchestration: validates parameters, selects the execution
    mode, and dispatches. The container entrypoint (``main.py``) handles
    envelope serialization; this is the domain seam.

    Args:
        parameters: Learner-supplied dict from
            ``SimulationInputEnvelope.parameters``. Required keys:

            - ``glazing_u_value`` (float, W/m²K)
            - ``window_area`` (float, m²)
            - ``climate_zone`` (str, one of ``CLIMATE_ZONES``)

        on_progress: Optional reporter called at phase boundaries with
            ``(pct, step)``. Omit it (the default) for a silent run —
            the CI/analytical/local paths pass nothing. ``main.py``
            wires it to the worker's progress callback so async
            deployments surface a determinate progress bar. The
            analytical path is effectively instantaneous, so it only
            emits the bookend phases.

    Returns:
        Dict whose keys match the manifest's ``outputs[].name`` exactly:
        ``annual_heating_kWh``, ``annual_cooling_kWh``,
        ``peak_heating_kW``, ``notes``.

    Raises:
        KeyError: A required parameter is missing.
        TypeError: A parameter has the wrong type.
        ValueError: A parameter is out of range / unparseable, OR the
            real EnergyPlus run failed (bad weather file, non-zero exit,
            missing SQL output). ``main.py`` maps all three onto a
            FAILED_RUNTIME envelope — the
            right semantic, since the learner's inputs are already
            L3-validated upstream, so a run failure is a platform/IDF
            problem, not the learner's fault.
    """
    emit = on_progress if on_progress is not None else _noop_progress

    # Validate up front (shared by both modes). ``get_climate_zone``
    # raises ValueError on an unknown zone; the float helpers raise
    # KeyError/ValueError.
    u_value = _require_float(parameters, "glazing_u_value")
    area = _require_float(parameters, "window_area")
    climate_code = _require_str(parameters, "climate_zone")
    zone = get_climate_zone(climate_code)
    emit(10, "Parameters validated")

    mode = _select_mode()
    logger.info("simulate: mode=%s zone=%s", mode, zone.code)
    if mode == "energyplus":
        emit(20, "Building EnergyPlus model")
        result = _simulate_energyplus(
            u_value=u_value,
            area=area,
            zone=zone,
            on_progress=emit,
        )
    else:
        emit(40, "Running analytical model")
        result = _simulate_analytical(u_value=u_value, area=area, zone=zone)
    emit(100, "Complete")
    return result


def _select_mode() -> str:
    """Choose ``"energyplus"`` or ``"analytical"``.

    ``PURELMS_EPLUS_MODE``: ``auto`` (default) → real if the binary is on
    PATH else analytical; ``energyplus`` → force real (the run raises a
    clear error if the binary is missing); ``analytical`` → force the
    fallback (handy for fast local smoke even inside the full image).
    """
    requested = os.environ.get("PURELMS_EPLUS_MODE", "auto").strip().lower()
    if requested == "analytical":
        return "analytical"
    if requested == "energyplus":
        return "energyplus"
    return "energyplus" if shutil.which(_ENERGYPLUS_BIN) else "analytical"


# ---------------------------------------------------------------------
# Real EnergyPlus path
# ---------------------------------------------------------------------


def _simulate_energyplus(
    *,
    u_value: float,
    area: float,
    zone: ClimateZone,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Build an IDF, run EnergyPlus against the zone's EPW, mine the SQL."""
    emit = on_progress if on_progress is not None else _noop_progress
    if shutil.which(_ENERGYPLUS_BIN) is None:
        msg = (
            "EnergyPlus binary not found on PATH but PURELMS_EPLUS_MODE "
            "requires it. The production image bakes the binary in; for "
            "local dev unset PURELMS_EPLUS_MODE (auto falls back to the "
            "analytical model)."
        )
        raise ValueError(msg)

    weather_dir = _weather_dir()
    epw_path = weather_dir / zone.epw_filename
    if not epw_path.is_file():
        msg = (
            f"weather file missing for zone {zone.code}: {epw_path}. "
            "The Dockerfile is responsible for downloading the bundled "
            "EPW files into the weather dir."
        )
        raise ValueError(msg)

    work_dir = Path(tempfile.mkdtemp(prefix="eplus_", dir=_work_root()))
    idf_text = build_idf({"glazing_u_value": u_value, "window_area": area})
    idf_path = work_dir / "in.idf"
    idf_path.write_text(idf_text)

    emit(40, "Running EnergyPlus")
    returncode, _stdout, stderr = _run_energyplus(idf_path, epw_path, work_dir)

    err_path = work_dir / "eplusout.err"
    sql_path = work_dir / "eplusout.sql"
    if returncode != 0 or not sql_path.is_file():
        tail = _read_err_tail(err_path) or stderr[-2000:] or "(no .err output)"
        msg = (
            f"EnergyPlus exited {returncode} without usable SQL output for "
            f"zone {zone.code}. .err tail:\n{tail}"
        )
        raise ValueError(msg)

    emit(85, "Extracting results")
    heating_kwh, cooling_kwh, peak_kw = extract_metrics(sql_path)

    # Peak fallback: if the sizing/output-variable peak wasn't found in
    # the SQL (e.g. the variable wasn't reported), fall back to the
    # steady-state design-load estimate so the learner still sees a
    # number. Tagged in ``notes`` so it's not silently misleading.
    peak_estimated = peak_kw is None
    if peak_estimated:
        peak_kw = u_value * area * zone.design_heating_dt_k * _W_TO_KW

    notes = (
        f"EnergyPlus {area:.1f} m² window, U = {u_value:.2f} W/m²K, "
        f"{zone.label}: ~{heating_kwh:.0f} kWh/yr heating, "
        f"~{cooling_kwh:.0f} kWh/yr cooling, peak heating "
        f"{peak_kw:.2f} kW" + (" (peak estimated)" if peak_estimated else "") + "."
    )

    return {
        "annual_heating_kWh": round(heating_kwh, 1),
        "annual_cooling_kWh": round(cooling_kwh, 1),
        "peak_heating_kW": round(peak_kw, 3),
        "notes": notes,
    }


def build_idf(parameters: dict[str, Any]) -> str:
    """Substitute the learner's parameters into the IDF template.

    Pure function (no I/O beyond reading the bundled template once).
    Maps ``glazing_u_value`` straight onto the SimpleGlazingSystem
    U-Factor field and derives the window LENGTH from the area
    (``area / WINDOW_HEIGHT_M``) so the area parameter lands on a single
    template field.

    Args:
        parameters: Must carry ``glazing_u_value`` (float) and
            ``window_area`` (float). Values are assumed already
            validated (numeric, in range) by the caller.

    Returns:
        The substituted IDF text, ready to write to ``in.idf``.
    """
    u_value = float(parameters["glazing_u_value"])
    area = float(parameters["window_area"])
    window_length = area / WINDOW_HEIGHT_M

    template = string.Template(_load_template())
    return template.substitute(
        GLAZING_U_VALUE=f"{u_value:.4f}",
        WINDOW_LENGTH=f"{window_length:.4f}",
        WINDOW_HEIGHT=f"{WINDOW_HEIGHT_M:.4f}",
    )


def _run_energyplus(
    idf_path: Path,
    epw_path: Path,
    work_dir: Path,
) -> tuple[int, str, str]:
    """Execute ``energyplus --weather <epw> --output-directory <dir> <idf>``.

    Returns ``(returncode, stdout, stderr)``. Does not raise on a
    non-zero exit — the caller inspects the returncode + SQL output.
    """
    cmd = [
        _ENERGYPLUS_BIN,
        "--output-directory",
        str(work_dir),
        "--weather",
        str(epw_path),
        str(idf_path),
    ]
    logger.info("executing: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        check=False,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=_ENERGYPLUS_TIMEOUT_SECONDS,
    )
    logger.info("energyplus returncode=%d", result.returncode)
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------
# SQL metric extraction (translated from Validibot's proven queries)
# ---------------------------------------------------------------------


def extract_metrics(sql_path: Path) -> tuple[float, float, float | None]:
    """Pull annual heating + cooling energy + peak heating from the SQL DB.

    Args:
        sql_path: Path to ``eplusout.sql``.

    Returns:
        ``(annual_heating_kWh, annual_cooling_kWh, peak_heating_kW)``.
        Heating/cooling come from the AnnualBuildingUtilityPerformance-
        Summary "End Uses" table (GJ → kWh, summing all fuel columns so
        IdealLoads district heating/cooling is captured). Peak comes from
        the MAX of the supply-air sensible-heating-rate output variable
        (W → kW); ``None`` if that variable wasn't reported (caller
        falls back to a steady-state estimate).
    """
    with sqlite3.connect(sql_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        heating_gj = _sum_end_use_row(cursor, "Heating")
        cooling_gj = _sum_end_use_row(cursor, "Cooling")
        peak_w = _fetch_output_variable_max(cursor, _PEAK_HEATING_VARIABLE)

    heating_kwh = heating_gj * _GJ_TO_KWH if heating_gj >= 0 else 0.0
    cooling_kwh = cooling_gj * _GJ_TO_KWH if cooling_gj >= 0 else 0.0
    peak_kw = peak_w * _W_TO_KW if peak_w is not None else None
    return heating_kwh, cooling_kwh, peak_kw


def _sum_end_use_row(cursor: sqlite3.Cursor, row_name: str) -> float:
    """Sum all GJ fuel columns for one End Uses row.

    The End Uses table has a column per fuel (Electricity, Natural Gas,
    District Heating Water, District Cooling, ...). For a category total
    we sum every energy column (``Units = 'GJ'``), excluding Water (m3).
    Returns -1.0 when the row/table is absent.
    """
    result = cursor.execute(
        """
        SELECT SUM(CAST(Value AS REAL)) AS total_gj
        FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
          AND TableName = 'End Uses'
          AND RowName = ?
          AND Units = 'GJ'
        """,
        (row_name,),
    ).fetchone()
    if not result or result["total_gj"] is None:
        return -1.0
    return float(result["total_gj"])


def _fetch_output_variable_max(
    cursor: sqlite3.Cursor,
    variable_name: str,
) -> float | None:
    """Return the MAX of an output variable across the run (native units).

    EnergyPlus stores output-variable data in two tables:
    ``ReportDataDictionary`` (name + key + frequency → index) and
    ``ReportData`` (timestep values keyed by that index). We pick one
    reporting frequency (preferring the coarsest available) and take the
    max value across all keys — for a heating-RATE variable that's the
    peak load. Returns ``None`` if the variable wasn't reported.
    """
    table_check = cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='ReportDataDictionary'",
    ).fetchone()
    if not table_check:
        return None

    freq_rows = cursor.execute(
        """
        SELECT DISTINCT ReportingFrequency
        FROM ReportDataDictionary
        WHERE Name = ? AND IsMeter = 0
        """,
        (variable_name,),
    ).fetchall()
    if not freq_rows:
        return None

    available = {r[0] for r in freq_rows}
    chosen = next(
        (f for f in _FREQUENCY_PREFERENCE if f in available),
        next(iter(available)),
    )

    result = cursor.execute(
        """
        SELECT MAX(rd.Value)
        FROM ReportData rd
        JOIN ReportDataDictionary rdd
          ON rd.ReportDataDictionaryIndex = rdd.ReportDataDictionaryIndex
        WHERE rdd.Name = ?
          AND rdd.ReportingFrequency = ?
          AND rdd.IsMeter = 0
        """,
        (variable_name, chosen),
    ).fetchone()
    if result is None or result[0] is None:
        return None
    return float(result[0])


def _read_err_tail(err_path: Path | None, max_lines: int = 80) -> str | None:
    """Read the tail of ``eplusout.err`` for failure diagnostics."""
    if err_path is None or not err_path.is_file():
        return None
    try:
        lines = err_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("could not read err file: %s", exc)
        return None
    return "\n".join(lines[-max_lines:])


def parse_err_file(err_path: Path | None) -> list[dict[str, str]]:
    """Parse ``eplusout.err`` into severity-tagged messages.

    EnergyPlus ``.err`` lines look like ``** Warning ** ...`` /
    ``** Severe  ** ...`` / ``**  Fatal  ** ...``; multi-line messages
    continue on following indented lines. Returns a deduped list of
    ``{severity, text, code}`` dicts (severity ∈ ``warning`` / ``error``).
    Translated from Validibot's proven ``.err`` parser.
    """
    if err_path is None or not err_path.is_file():
        return []
    try:
        content = err_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("could not read err file for parsing: %s", exc)
        return []

    markers = (
        (
            re.compile(r"\s*\*\*\s*Fatal\s*\*\*\s*(.*)", re.IGNORECASE),
            "error",
            "ENERGYPLUS_FATAL",
        ),
        (
            re.compile(r"\s*\*\*\s*Severe\s*\*\*\s*(.*)", re.IGNORECASE),
            "error",
            "ENERGYPLUS_SEVERE",
        ),
        (
            re.compile(r"\s*\*\*\s*Warning\s*\*\*\s*(.*)", re.IGNORECASE),
            "warning",
            "ENERGYPLUS_WARNING",
        ),
    )

    messages: list[dict[str, str]] = []
    seen: set[str] = set()
    current: dict[str, str] | None = None

    def _flush() -> None:
        nonlocal current
        if current and current["text"] not in seen:
            seen.add(current["text"])
            messages.append(current)
        current = None

    for line in content.split("\n"):
        if line.strip().startswith("*************") or "Summary of Errors" in line:
            _flush()
            continue
        matched = False
        for pattern, severity, code in markers:
            m = pattern.match(line)
            if m:
                _flush()
                current = {
                    "severity": severity,
                    "text": m.group(1).strip(),
                    "code": code,
                }
                matched = True
                break
        if (
            not matched
            and current
            and line.strip()
            and not line.strip().startswith("~")
        ):
            current["text"] += " " + line.strip()

    _flush()
    return messages


# ---------------------------------------------------------------------
# Analytical fallback (no binary required)
# ---------------------------------------------------------------------

# Energy conversion for the analytical model: 24 hours/day ÷ 1000 W/kW.
_KWH_PER_W_DAY: float = 24.0 / 1000.0


def _simulate_analytical(
    *,
    u_value: float,
    area: float,
    zone: ClimateZone,
) -> dict[str, Any]:
    """Steady-state heat-balance model — the binary-free dev fallback.

        E_kWh = U(W/m²K) × A(m²) × DegreeDays(K·days) × 24h/day / 1000
        P_kW  = U × A × design_ΔT(K) / 1000

    Captures the first-order effects (lower U → less loss, larger A →
    more loss, colder climate → more heating) so the learner sees
    qualitatively-correct trade-offs. NOT a design tool — no solar
    gains, infiltration, or thermal mass.
    """
    annual_heating = u_value * area * zone.heating_degree_days_k * _KWH_PER_W_DAY
    annual_cooling = u_value * area * zone.cooling_degree_days_k * _KWH_PER_W_DAY
    peak_heating = u_value * area * zone.design_heating_dt_k * _W_TO_KW

    notes = (
        f"Analytical estimate (no EnergyPlus binary): {area:.1f} m² window, "
        f"U = {u_value:.2f} W/m²K, {zone.label}: ~{annual_heating:.0f} kWh/yr "
        f"heating, peak load {peak_heating:.2f} kW at design conditions."
    )

    return {
        "annual_heating_kWh": round(annual_heating, 1),
        "annual_cooling_kWh": round(annual_cooling, 1),
        "peak_heating_kW": round(peak_heating, 3),
        "notes": notes,
    }


# ---------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------


def _weather_dir() -> Path:
    """Directory holding the bundled EPW files (overridable for tests)."""
    return Path(os.environ.get("PURELMS_EPLUS_WEATHER_DIR", "/opt/weather"))


def _work_root() -> Path:
    """Writable root for per-run EnergyPlus working dirs."""
    root = Path(os.environ.get("PURELMS_EPLUS_WORK_DIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_template() -> str:
    """Read the bundled IDF template that sits beside this module."""
    return (Path(__file__).parent / "idf" / "single_zone.idf.template").read_text()


# ---------------------------------------------------------------------
# Parameter validation helpers (shared by both modes)
# ---------------------------------------------------------------------


def _require_float(parameters: dict[str, Any], key: str) -> float:
    """Raise KeyError if missing, ValueError if not numeric."""
    if key not in parameters:
        msg = f"missing required parameter: {key!r}"
        raise KeyError(msg)
    value = parameters[key]
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        msg = (
            f"parameter {key!r}: must be numeric; "
            f"got {value!r} ({type(value).__name__})"
        )
        raise ValueError(msg) from exc


def _require_str(parameters: dict[str, Any], key: str) -> str:
    """Raise KeyError if missing, TypeError if not a string.

    ``TypeError`` (not ``ValueError``) is idiomatic for a failed
    ``isinstance`` — the parameter was the wrong TYPE, not out of range.
    ``main.py`` catches both when emitting FAILED_RUNTIME.
    """
    if key not in parameters:
        msg = f"missing required parameter: {key!r}"
        raise KeyError(msg)
    value = parameters[key]
    if not isinstance(value, str):
        msg = (
            f"parameter {key!r}: must be a string; "
            f"got {value!r} ({type(value).__name__})"
        )
        raise TypeError(msg)
    return value


# Backwards-compatibility: a few callers / tests referenced the old
# ``CLIMATE_DATA`` dict. Re-expose the zone data in the legacy shape so
# nothing that imported it breaks. New code should use ``climate``.
CLIMATE_DATA: dict[str, dict[str, float | str]] = {
    code: {
        "label": z.label,
        "heating_degree_days_k": z.heating_degree_days_k,
        "cooling_degree_days_k": z.cooling_degree_days_k,
        "design_heating_dt_k": z.design_heating_dt_k,
    }
    for code, z in CLIMATE_ZONES.items()
}
