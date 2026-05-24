"""
Tests for :mod:`runner` — the stubbed thermal model.

Most tests are **qualitative** (lower U → less heat loss; colder
climate → more heating) rather than exact-number, so the suite
stays robust against small model tweaks (rounding precision,
constant updates, etc.). A few exact-number tests pin the
formula's first-order behavior so a refactor that silently breaks
the math gets caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the backend directory importable as a flat package so the
# test can do ``from runner import simulate`` without a build step.
sys.path.insert(0, str(Path(__file__).parent.parent))

from runner import CLIMATE_DATA
from runner import simulate

# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_simulate_returns_all_manifest_outputs():
    """Output keys MUST match interactive_task.yaml's outputs[].name exactly.

    The LMS uses the manifest to look up display hints + units; an
    extra or missing output key gets dropped from the UI.
    """
    result = simulate(
        {
            "glazing_u_value": 2.5,
            "window_area": 5.0,
            "climate_zone": "5A",
        },
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
    """Pin the baseline math for a typical double-pane window.

    U=2.5 W/m²K, A=5m², climate 5A (HDD=5500 K·days):
      heating = 2.5 × 5 × 5500 × 0.024 = 1650 kWh/year
    """
    result = simulate(
        {"glazing_u_value": 2.5, "window_area": 5.0, "climate_zone": "5A"},
    )
    assert result["annual_heating_kWh"] == pytest.approx(1650.0, rel=0.01)


# ---------------------------------------------------------------------
# Qualitative relationships — these stay correct under small tweaks
# ---------------------------------------------------------------------


def test_lower_u_value_means_less_heat_loss():
    """A better-insulated window loses less heat. The whole point."""
    high_u = simulate(
        {"glazing_u_value": 5.0, "window_area": 5.0, "climate_zone": "5A"}
    )
    low_u = simulate({"glazing_u_value": 1.0, "window_area": 5.0, "climate_zone": "5A"})
    assert low_u["annual_heating_kWh"] < high_u["annual_heating_kWh"]
    assert low_u["peak_heating_kW"] < high_u["peak_heating_kW"]


def test_larger_window_means_more_heat_loss():
    """Doubling the area should roughly double the heat loss."""
    small = simulate({"glazing_u_value": 2.5, "window_area": 2.0, "climate_zone": "5A"})
    large = simulate(
        {"glazing_u_value": 2.5, "window_area": 10.0, "climate_zone": "5A"}
    )
    ratio = large["annual_heating_kWh"] / small["annual_heating_kWh"]
    assert ratio == pytest.approx(5.0, rel=0.01)


def test_colder_climate_means_more_heating_energy():
    """The HDD ordering 6A > 5A > 4A must propagate."""
    p = {"glazing_u_value": 2.5, "window_area": 5.0}
    cz4a = simulate({**p, "climate_zone": "4A"})
    cz5a = simulate({**p, "climate_zone": "5A"})
    cz6a = simulate({**p, "climate_zone": "6A"})
    assert (
        cz4a["annual_heating_kWh"]
        < cz5a["annual_heating_kWh"]
        < cz6a["annual_heating_kWh"]
    )
    # And peak load follows design ΔT:
    assert cz4a["peak_heating_kW"] < cz5a["peak_heating_kW"] < cz6a["peak_heating_kW"]


def test_warmer_climate_means_more_cooling_energy():
    """The CDD ordering 4A > 5A > 6A is the cooling counterpart."""
    p = {"glazing_u_value": 2.5, "window_area": 5.0}
    cz4a = simulate({**p, "climate_zone": "4A"})
    cz5a = simulate({**p, "climate_zone": "5A"})
    cz6a = simulate({**p, "climate_zone": "6A"})
    assert (
        cz6a["annual_cooling_kWh"]
        < cz5a["annual_cooling_kWh"]
        < cz4a["annual_cooling_kWh"]
    )


def test_notes_mention_inputs_and_outputs():
    """The notes string is what the learner reads as a summary —
    pin that it includes the inputs they chose."""
    result = simulate(
        {"glazing_u_value": 3.0, "window_area": 8.0, "climate_zone": "4A"},
    )
    notes = result["notes"]
    assert "8.0" in notes  # area
    assert "3.0" in notes  # U-value
    assert "Mixed-humid" in notes or "4A" in notes.upper()


# ---------------------------------------------------------------------
# Climate-data invariants
# ---------------------------------------------------------------------


def test_climate_data_covers_all_manifest_choices():
    """The manifest declares 4A / 5A / 6A — runner must have all three."""
    assert set(CLIMATE_DATA.keys()) == {"4A", "5A", "6A"}


def test_climate_data_has_required_fields():
    """Each climate-zone dict carries the four fields the formula uses."""
    required = {
        "label",
        "heating_degree_days_k",
        "cooling_degree_days_k",
        "design_heating_dt_k",
    }
    for zone, data in CLIMATE_DATA.items():
        missing = required - set(data.keys())
        assert not missing, f"{zone}: missing fields {missing}"


# ---------------------------------------------------------------------
# Error paths — validation
# ---------------------------------------------------------------------


def test_missing_parameter_raises_keyerror():
    with pytest.raises(KeyError, match="glazing_u_value"):
        simulate({"window_area": 5.0, "climate_zone": "5A"})


def test_unknown_climate_zone_raises_valueerror():
    with pytest.raises(ValueError, match="climate_zone"):
        simulate(
            {
                "glazing_u_value": 2.5,
                "window_area": 5.0,
                "climate_zone": "Z99",
            },
        )


def test_non_numeric_u_value_raises_valueerror():
    with pytest.raises(ValueError, match="glazing_u_value"):
        simulate(
            {
                "glazing_u_value": "not_a_number",
                "window_area": 5.0,
                "climate_zone": "5A",
            },
        )


def test_non_string_climate_zone_raises_typeerror():
    """Passing an int where a string is expected should fail loudly.

    Raises TypeError specifically (failed isinstance), which is
    Python-idiomatic for "wrong type." ``main.py`` catches both
    TypeError and ValueError when surfacing FAILED_RUNTIME.
    """
    with pytest.raises(TypeError, match="climate_zone"):
        simulate(
            {
                "glazing_u_value": 2.5,
                "window_area": 5.0,
                "climate_zone": 5,  # int, not str
            },
        )
