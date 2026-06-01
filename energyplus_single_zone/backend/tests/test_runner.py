"""
Tests for :mod:`runner` — both execution modes.

What's covered here (no EnergyPlus binary required):

- **Analytical fallback** — qualitative relationships (lower U → less
  loss, colder → more heating) + a pinned baseline number. These run
  with ``PURELMS_EPLUS_MODE=analytical`` forced (autouse fixture) so the
  result is deterministic regardless of whether a dev happens to have
  the binary installed.
- **``build_idf``** — template substitution: the right values land, the
  window length is derived from the area, and no placeholder survives.
- **``extract_metrics``** — the SQL-mining queries, exercised against a
  synthetic SQLite DB that mimics the EnergyPlus 25.x schema
  (``TabularDataWithStrings`` End Uses + ``ReportData``). This is the
  Validibot pattern: verify the queries without the 500 MB binary.
- **``parse_err_file``** — severity tagging + multi-line continuation.
- **``_select_mode``** — the auto/forced mode dispatch.

What's NOT covered here: the actual EnergyPlus run (does the IDF run,
are the numbers physically right). That needs the real binary + weather
files and is validated by ``just build`` + a container run. See
``README.md`` §Verification status.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Make the backend directory importable as a flat package (``from runner
# import ...`` / ``from climate import ...``) without a build step.
sys.path.insert(0, str(Path(__file__).parent.parent))

import runner
from climate import CLIMATE_ZONES
from runner import CLIMATE_DATA
from runner import build_idf
from runner import extract_metrics
from runner import parse_err_file
from runner import simulate

_GJ_TO_KWH = 277.778


@pytest.fixture(autouse=True)
def _force_analytical(monkeypatch):
    """Force the binary-free analytical mode for ``simulate()`` tests.

    Without this, a dev who happens to have ``energyplus`` on PATH would
    drive ``simulate`` down the real path (which needs weather files) and
    the pinned-number assertions would break. Tests that exercise mode
    selection itself override this explicitly.
    """
    monkeypatch.setenv("PURELMS_EPLUS_MODE", "analytical")


# ---------------------------------------------------------------------
# Analytical fallback — happy path + qualitative relationships
# ---------------------------------------------------------------------


def test_simulate_returns_all_manifest_outputs():
    """Output keys MUST match interactive_task.yaml's outputs[].name exactly."""
    result = simulate(
        {"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "5A"},
    )
    assert set(result.keys()) == {
        "annual_heating_kWh",
        "annual_cooling_kWh",
        "peak_heating_kW",
        "notes",
    }
    assert isinstance(result["annual_heating_kWh"], float)
    assert isinstance(result["annual_cooling_kWh"], float)
    assert isinstance(result["peak_heating_kW"], float)
    assert isinstance(result["notes"], str)


def test_simulate_baseline_5a_double_pane_window():
    """Pin the analytical baseline: U=2.5, A=5, 5A (HDD=5500):
    heating = 2.5 × 5 × 5500 × 0.024 = 1650 kWh/year."""
    result = simulate(
        {"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "5A"},
    )
    assert result["annual_heating_kWh"] == pytest.approx(1650.0, rel=0.01)


def test_lower_u_value_means_less_heat_loss():
    high_u = simulate(
        {"glazing_u_value": 5.0, "window_area": 5.0, "climate_zone": "5A"}
    )
    low_u = simulate({"glazing_u_value": 1.0, "window_area": 5.0, "climate_zone": "5A"})
    assert low_u["annual_heating_kWh"] < high_u["annual_heating_kWh"]
    assert low_u["peak_heating_kW"] < high_u["peak_heating_kW"]


def test_larger_window_means_more_heat_loss():
    small = simulate({"glazing_u_value": 2.5, "window_area": 2.0, "climate_zone": "5A"})
    large = simulate(
        {"glazing_u_value": 2.5, "window_area": 10.0, "climate_zone": "5A"}
    )
    ratio = large["annual_heating_kWh"] / small["annual_heating_kWh"]
    assert ratio == pytest.approx(5.0, rel=0.01)


def test_colder_climate_means_more_heating_energy():
    p = {"glazing_u_value": 2.5, "window_area": 5.0}
    cz4a = simulate({**p, "climate_zone": "4A"})
    cz5a = simulate({**p, "climate_zone": "5A"})
    cz6a = simulate({**p, "climate_zone": "6A"})
    assert (
        cz4a["annual_heating_kWh"]
        < cz5a["annual_heating_kWh"]
        < cz6a["annual_heating_kWh"]
    )
    assert cz4a["peak_heating_kW"] < cz5a["peak_heating_kW"] < cz6a["peak_heating_kW"]


# ---------------------------------------------------------------------
# Progress emission — the on_progress reporter
# ---------------------------------------------------------------------


def test_simulate_emits_monotonic_progress_ending_at_100():
    """``simulate`` calls ``on_progress(pct, step)`` at phase
    boundaries. The contract the worker relies on: pct is
    non-decreasing, bounded 0-100, and the final emission is exactly
    100 so a determinate bar lands full."""
    events: list[tuple[int, str]] = []
    simulate(
        {"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "5A"},
        on_progress=lambda pct, step: events.append((pct, step)),
    )

    assert events, "expected at least one progress emission"
    pcts = [pct for pct, _ in events]
    assert pcts == sorted(pcts), f"pct not monotonic: {pcts}"
    assert all(0 <= pct <= 100 for pct in pcts)
    assert pcts[-1] == 100
    # Steps are human-readable labels, not empty.
    assert all(step for _, step in events)


def test_simulate_without_reporter_runs_silently():
    """Omitting ``on_progress`` uses the null-object reporter — the
    domain code never branches on whether a reporter exists, and the
    outputs are identical to the reported run."""
    result = simulate(
        {"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "5A"},
    )
    assert result["annual_heating_kWh"] == pytest.approx(1650.0, rel=0.01)


# ---------------------------------------------------------------------
# Climate data invariants
# ---------------------------------------------------------------------


def test_climate_zones_cover_all_manifest_choices():
    assert set(CLIMATE_ZONES.keys()) == {"4A", "5A", "6A"}
    assert set(CLIMATE_DATA.keys()) == {"4A", "5A", "6A"}


def test_each_zone_declares_an_epw_filename():
    """The real path needs a bundled EPW per zone."""
    for zone in CLIMATE_ZONES.values():
        assert zone.epw_filename.endswith(".epw")


# ---------------------------------------------------------------------
# Error paths — validation (shared by both modes)
# ---------------------------------------------------------------------


def test_missing_parameter_raises_keyerror():
    with pytest.raises(KeyError, match="glazing_u_value"):
        simulate({"window_area": 5.0, "climate_zone": "5A"})


def test_unknown_climate_zone_raises_valueerror():
    with pytest.raises(ValueError, match="climate_zone"):
        simulate({"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "Z99"})


def test_non_string_climate_zone_raises_typeerror():
    with pytest.raises(TypeError, match="climate_zone"):
        simulate({"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": 5})


# ---------------------------------------------------------------------
# build_idf — template substitution
# ---------------------------------------------------------------------


def test_build_idf_substitutes_u_value_and_window_length():
    """U lands as-is; window length = area / WINDOW_HEIGHT (2.5)."""
    idf = build_idf({"glazing_u_value": 2.5, "window_area": 5.0})
    # U-value substituted onto the SimpleGlazingSystem object.
    assert "WindowMaterial:SimpleGlazingSystem" in idf
    assert "2.5000" in idf  # U-Factor
    # window length = 5.0 / 2.5 = 2.0
    assert "2.0000" in idf
    # No placeholder survives — a leftover ``$`` means a missed field.
    assert "$" not in idf


def test_build_idf_window_length_scales_with_area():
    idf = build_idf({"glazing_u_value": 1.0, "window_area": 20.0})
    # length = 20.0 / 2.5 = 8.0 (fits the 10 m south wall)
    assert "8.0000" in idf
    assert "$" not in idf


# ---------------------------------------------------------------------
# extract_metrics — SQL mining against a synthetic EnergyPlus DB
# ---------------------------------------------------------------------


def _make_eplus_sql(tmp_path: Path) -> Path:
    """Build a minimal eplusout.sql mimicking the EnergyPlus 25.x schema.

    - TabularDataWithStrings: End Uses Heating/Cooling rows in GJ (plus a
      Water row in m3 that must be excluded from the sums).
    - ReportData(Dictionary): the peak-heating-rate output variable, two
      Run Period samples in W so MAX is exercised.
    """
    sql_path = tmp_path / "eplusout.sql"
    conn = sqlite3.connect(sql_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE TabularDataWithStrings "
        "(ReportName TEXT, TableName TEXT, RowName TEXT, ColumnName TEXT, "
        "Value TEXT, Units TEXT)",
    )
    rows = [
        # Heating: 0.10 + 0.08 = 0.18 GJ ≈ 50 kWh (+ a Water row to exclude)
        (
            "AnnualBuildingUtilityPerformanceSummary",
            "End Uses",
            "Heating",
            "Electricity",
            "0.10",
            "GJ",
        ),
        (
            "AnnualBuildingUtilityPerformanceSummary",
            "End Uses",
            "Heating",
            "District Heating Water",
            "0.08",
            "GJ",
        ),
        (
            "AnnualBuildingUtilityPerformanceSummary",
            "End Uses",
            "Heating",
            "Water",
            "0.00",
            "m3",
        ),
        # Cooling: 0.15 GJ ≈ 41.67 kWh
        (
            "AnnualBuildingUtilityPerformanceSummary",
            "End Uses",
            "Cooling",
            "Electricity",
            "0.15",
            "GJ",
        ),
    ]
    cur.executemany(
        "INSERT INTO TabularDataWithStrings VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute(
        "CREATE TABLE ReportDataDictionary "
        "(ReportDataDictionaryIndex INTEGER PRIMARY KEY, IsMeter INTEGER, "
        "Type TEXT, IndexGroup TEXT, TimestepType TEXT, KeyValue TEXT, "
        "Name TEXT, ReportingFrequency TEXT, ScheduleName TEXT, Units TEXT)",
    )
    cur.execute(
        "CREATE TABLE ReportData "
        "(ReportDataIndex INTEGER PRIMARY KEY, TimeIndex INTEGER, "
        "ReportDataDictionaryIndex INTEGER, Value REAL)",
    )
    cur.execute(
        "INSERT INTO ReportDataDictionary VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            1,
            0,
            "Avg",
            "Zone",
            "Zone Timestep",
            "EducationalZone Ideal Loads",
            runner._PEAK_HEATING_VARIABLE,
            "Run Period",
            "",
            "W",
        ),
    )
    cur.execute("INSERT INTO ReportData VALUES (?,?,?,?)", (1, 1, 1, 3000.0))
    cur.execute("INSERT INTO ReportData VALUES (?,?,?,?)", (2, 2, 1, 5000.0))
    conn.commit()
    conn.close()
    return sql_path


def test_extract_metrics_reads_end_uses_and_peak(tmp_path):
    """Heating = 0.18 GJ → 50 kWh, Cooling = 0.15 GJ → 41.67 kWh,
    peak = MAX(3000, 5000) W → 5.0 kW."""
    sql_path = _make_eplus_sql(tmp_path)
    heating_kwh, cooling_kwh, peak_kw = extract_metrics(sql_path)
    assert heating_kwh == pytest.approx(0.18 * _GJ_TO_KWH, rel=1e-3)
    assert cooling_kwh == pytest.approx(0.15 * _GJ_TO_KWH, rel=1e-3)
    assert peak_kw == pytest.approx(5.0, rel=1e-6)


def test_extract_metrics_peak_none_when_variable_absent(tmp_path):
    """No ReportData for the heating-rate variable → peak is None (caller
    falls back to the analytical estimate)."""
    sql_path = tmp_path / "eplusout.sql"
    conn = sqlite3.connect(sql_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE TabularDataWithStrings "
        "(ReportName TEXT, TableName TEXT, RowName TEXT, ColumnName TEXT, "
        "Value TEXT, Units TEXT)",
    )
    cur.execute(
        "INSERT INTO TabularDataWithStrings VALUES (?,?,?,?,?,?)",
        (
            "AnnualBuildingUtilityPerformanceSummary",
            "End Uses",
            "Heating",
            "District Heating Water",
            "0.18",
            "GJ",
        ),
    )
    conn.commit()
    conn.close()
    _heating, _cooling, peak_kw = extract_metrics(sql_path)
    assert peak_kw is None


# ---------------------------------------------------------------------
# parse_err_file
# ---------------------------------------------------------------------


def test_parse_err_file_tags_severities(tmp_path):
    err = tmp_path / "eplusout.err"
    err.write_text(
        "Program Version,EnergyPlus 25.2\n"
        "   ** Warning ** Surface is not in the correct orientation\n"
        "   **   ~~~   ** continuation of the warning\n"
        "   ** Severe  ** GetVertices: distance check failed\n"
        "   **  Fatal  ** Errors found during processing\n"
        "************* Summary of Errors\n",
    )
    messages = parse_err_file(err)
    severities = [m["severity"] for m in messages]
    assert "warning" in severities
    assert severities.count("error") == 2  # Severe + Fatal both map to error
    # Multi-line continuation appended to the warning.
    warning = next(m for m in messages if m["severity"] == "warning")
    assert "continuation" in warning["text"]


def test_parse_err_file_missing_returns_empty(tmp_path):
    assert parse_err_file(tmp_path / "does_not_exist.err") == []


# ---------------------------------------------------------------------
# _select_mode
# ---------------------------------------------------------------------


def test_select_mode_forced_analytical(monkeypatch):
    monkeypatch.setenv("PURELMS_EPLUS_MODE", "analytical")
    assert runner._select_mode() == "analytical"


def test_select_mode_forced_energyplus(monkeypatch):
    monkeypatch.setenv("PURELMS_EPLUS_MODE", "energyplus")
    assert runner._select_mode() == "energyplus"


def test_select_mode_auto_uses_binary_presence(monkeypatch):
    monkeypatch.setenv("PURELMS_EPLUS_MODE", "auto")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    assert runner._select_mode() == "analytical"
    monkeypatch.setattr(
        runner.shutil, "which", lambda _name: "/opt/energyplus/energyplus"
    )
    assert runner._select_mode() == "energyplus"
