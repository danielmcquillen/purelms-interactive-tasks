"""
Stubbed steady-state thermal model for the EnergyPlus single-zone
InteractiveTask.

This module is the **swap-in seam** for the real EnergyPlus
implementation that lands in Slice 4+. v1 ships a pure-Python
analytical model so contributors can iterate without a real
EnergyPlus binary; v2 will replace :func:`simulate` with a
``subprocess.run`` against ``EnergyPlus`` plus IDF/EPW handling
via ``eppy`` / ``opyplus``. The function signature + return
shape stay the same — only the implementation changes.

## The physics (v1 stubbed model)

Steady-state heat balance through a single window assembly:

    Q_loss = U × A × ΔT × duration

For annual energy in kWh from heating degree days (HDD, K·days):

    E_kWh = U(W/m²K) × A(m²) × HDD(K·days) × 24(hours/day) / 1000(W/kW)
          = U × A × HDD × 0.024

For peak heating load in kW (at the climate zone's design ΔT):

    P_kW = U × A × ΔT(K) / 1000

The model captures the dominant first-order effects (lower U →
less heat loss, larger A → more heat loss, colder climate →
more heating) so a learner exploring glazing choices sees
qualitatively-correct trade-offs. It is **NOT** suitable as a
real design tool — solar gains, infiltration, thermal mass, and
the rest of the EnergyPlus modelling stack are deliberately
absent.

## Climate zone data

The HDD / CDD / design-day ΔT tables below are approximate values
taken from ASHRAE 90.1 Climatic Data tables for representative
cities in each zone. They're rounded for pedagogical legibility,
not for engineering accuracy. The real-EnergyPlus version will
read these from EPW (EnergyPlus Weather) files attached as
``ResourceFile`` per-run.
"""

from __future__ import annotations

from typing import Any

# ASHRAE climate-zone approximate annual heating/cooling-degree-days
# (base 18°C / 65°F) plus design-day heating ΔT. Pedagogical values;
# the real EnergyPlus version reads EPW weather files for hourly
# accuracy.
CLIMATE_DATA: dict[str, dict[str, float | str]] = {
    "4A": {
        "label": "Mixed-humid (e.g. New York City)",
        "heating_degree_days_k": 4000.0,  # K·days
        "cooling_degree_days_k": 1000.0,  # K·days
        "design_heating_dt_k": 25.0,  # K (indoor 20°C - outdoor -5°C)
    },
    "5A": {
        "label": "Cool-humid (e.g. Chicago)",
        "heating_degree_days_k": 5500.0,
        "cooling_degree_days_k": 800.0,
        "design_heating_dt_k": 32.0,
    },
    "6A": {
        "label": "Cold-humid (e.g. Minneapolis)",
        "heating_degree_days_k": 7500.0,
        "cooling_degree_days_k": 500.0,
        "design_heating_dt_k": 38.0,
    },
}

# Energy conversion: 24 hours/day ÷ 1000 W/kW = 0.024 (kWh per
# W·day). Pre-compute so the formula reads cleanly.
_KWH_PER_W_DAY: float = 24.0 / 1000.0


def simulate(parameters: dict[str, Any]) -> dict[str, Any]:
    """Run the v1 stubbed single-zone thermal model.

    Pure function — no I/O, no side effects, no envelope handling.
    The container entrypoint (``main.py``) handles envelope
    serialization; ``runner.simulate`` is the domain code.

    Args:
        parameters: The learner-supplied parameter dict from
            ``SimulationInputEnvelope.parameters``. Required keys:

            - ``glazing_u_value`` (float, W/m²K)
            - ``window_area`` (float, m²)
            - ``climate_zone`` (str, one of "4A" / "5A" / "6A")

    Returns:
        A dict whose keys match the manifest's ``outputs[].name``
        exactly: ``annual_heating_kWh``, ``annual_cooling_kWh``,
        ``peak_heating_kW``, ``notes``. Numeric values are rounded
        to a small number of decimal places to match the precision
        the LMS UI renders.

    Raises:
        KeyError: A required parameter is missing.
        TypeError: A parameter has the wrong type (e.g. an int where
            a string is required).
        ValueError: A parameter value is out of range or unparseable
            (e.g. unknown ``climate_zone``, non-numeric ``glazing_u_value``).
    """
    u_value = _require_float(parameters, "glazing_u_value")
    area = _require_float(parameters, "window_area")
    climate_zone = _require_str(parameters, "climate_zone")

    if climate_zone not in CLIMATE_DATA:
        allowed = ", ".join(sorted(CLIMATE_DATA))
        msg = f"unknown climate_zone {climate_zone!r}; allowed: {allowed}"
        raise ValueError(msg)

    cz = CLIMATE_DATA[climate_zone]
    hdd_k = float(cz["heating_degree_days_k"])
    cdd_k = float(cz["cooling_degree_days_k"])
    design_dt_k = float(cz["design_heating_dt_k"])
    label = str(cz["label"])

    # Steady-state thermal balance — see module docstring.
    annual_heating = u_value * area * hdd_k * _KWH_PER_W_DAY
    annual_cooling = u_value * area * cdd_k * _KWH_PER_W_DAY
    peak_heating = u_value * area * design_dt_k / 1000.0  # kW

    notes = (
        f"Single-zone heat loss through a {area:.1f} m² window with "
        f"U = {u_value:.2f} W/m²K in {label}: "
        f"~{annual_heating:.0f} kWh/year heating, peak load "
        f"{peak_heating:.2f} kW at design conditions."
    )

    return {
        # Rounded to match the precision the LMS UI renders.
        "annual_heating_kWh": round(annual_heating, 1),
        "annual_cooling_kWh": round(annual_cooling, 1),
        "peak_heating_kW": round(peak_heating, 3),
        "notes": notes,
    }


# ---------------------------------------------------------------------
# Internal helpers
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
    """Raise ``KeyError`` if missing, ``TypeError`` if not a string.

    ``TypeError`` (rather than ``ValueError``) is the Python-idiomatic
    raise for a failed ``isinstance`` check — the parameter was the
    wrong TYPE, not an out-of-range value. ``main.py`` catches both
    when it falls back to the FAILED_RUNTIME envelope.
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
