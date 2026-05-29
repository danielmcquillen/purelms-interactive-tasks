"""
Climate-zone configuration for the EnergyPlus single-zone InteractiveTask.

This is the **configurable data layer** of the educational backend: the
mapping from the learner's ``climate_zone`` enum choice to (a) the
bundled EPW weather file the real EnergyPlus run uses, and (b) the
approximate degree-day / design-ΔT figures the analytical fallback uses
when no EnergyPlus binary is present.

Adding a fourth climate zone is a data change here + a manifest enum
addition + bundling one more EPW in the Dockerfile — no runner logic
changes. That's the "configurable educational backend" property the
framework wants (ADR-0014): the *scenario* is data; the *machinery*
(IDF templating, subprocess, SQL extraction) is generic.

## EPW weather files

The real EnergyPlus path needs an EPW (EnergyPlus Weather) file. We
bundle one representative TMY (Typical Meteorological Year) file per
zone into the container image at ``$PURELMS_EPLUS_WEATHER_DIR`` (default
``/opt/weather``). The Dockerfile downloads them at build time from the
public DOE weather set, mirroring how it downloads the EnergyPlus
binary itself. The ``epw_filename`` below is the name the Dockerfile
saves each file as — the runner only ever references the local name, so
swapping the upstream source is a Dockerfile-only change.

## Degree-day data (analytical fallback only)

The HDD / CDD / design-ΔT figures are approximate ASHRAE 90.1 values
for a representative city in each zone, rounded for pedagogical
legibility. They drive ONLY the analytical fallback model
(:func:`runner._simulate_analytical`). The real EnergyPlus path ignores
them entirely — it reads hourly weather from the EPW.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClimateZone:
    """One ASHRAE climate zone's scenario data.

    Attributes:
        code: The manifest enum value (e.g. ``"5A"``). Matches
            ``interactive_task.yaml`` ``parameters[climate_zone].choices``.
        label: Human-readable label with a representative city.
        epw_filename: The local filename of the bundled EPW weather file
            (saved by the Dockerfile into the weather dir). The real
            EnergyPlus run passes this to ``energyplus --weather``.
        heating_degree_days_k: Annual heating degree-days (K·days, base
            18 °C). Analytical fallback only.
        cooling_degree_days_k: Annual cooling degree-days (K·days).
            Analytical fallback only.
        design_heating_dt_k: Design-day heating temperature differential
            (K). Drives the analytical peak-load estimate (and the
            real-path peak fallback when the SQL sizing report is absent).
    """

    code: str
    label: str
    epw_filename: str
    heating_degree_days_k: float
    cooling_degree_days_k: float
    design_heating_dt_k: float


# The v1 scenario set: three humid-subset ASHRAE zones spanning a
# meaningful heating-load gradient (mixed → cool → cold) so a learner
# exploring glazing choices sees the climate effect clearly.
CLIMATE_ZONES: dict[str, ClimateZone] = {
    "4A": ClimateZone(
        code="4A",
        label="4A Mixed-humid (e.g. New York City)",
        epw_filename="4A_new_york.epw",
        heating_degree_days_k=4000.0,
        cooling_degree_days_k=1000.0,
        design_heating_dt_k=25.0,
    ),
    "5A": ClimateZone(
        code="5A",
        label="5A Cool-humid (e.g. Chicago)",
        epw_filename="5A_chicago.epw",
        heating_degree_days_k=5500.0,
        cooling_degree_days_k=800.0,
        design_heating_dt_k=32.0,
    ),
    "6A": ClimateZone(
        code="6A",
        label="6A Cold-humid (e.g. Minneapolis)",
        epw_filename="6A_minneapolis.epw",
        heating_degree_days_k=7500.0,
        cooling_degree_days_k=500.0,
        design_heating_dt_k=38.0,
    ),
}


def get_climate_zone(code: str) -> ClimateZone:
    """Resolve a climate-zone code to its :class:`ClimateZone` data.

    Raises:
        ValueError: ``code`` is not one of the configured zones. The
            message lists the allowed codes so the FAILED_RUNTIME
            envelope is actionable.
    """
    zone = CLIMATE_ZONES.get(code)
    if zone is None:
        allowed = ", ".join(sorted(CLIMATE_ZONES))
        msg = f"unknown climate_zone {code!r}; allowed: {allowed}"
        raise ValueError(msg)
    return zone
