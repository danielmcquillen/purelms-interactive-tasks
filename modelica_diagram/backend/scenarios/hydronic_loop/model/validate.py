"""Validate HydronicLoop.fmu: parameters are settable + the model responds.

Run inside the OM container: ``docker exec om-build python3 /work/validate.py``
(fmpy must be pip-installed in the container).
"""

import fmpy

FMU = "/work/HydronicLoop.fmu"
WANT = ["QBoi_kW", "TRooSet_degC", "TRoo_degC", "EHea_kWh"]

md = fmpy.read_model_description(FMU)
names = {v.name for v in md.modelVariables}
present = ", ".join(f"{w}={'yes' if w in names else 'NO'}" for w in WANT)
print("FMI", md.fmiVersion, "| variables present:", present)

print("simulate 3h, room starts 15 C:")
for q, tset in [(10, 21), (4, 20), (15, 23)]:
    try:
        r = fmpy.simulate_fmu(
            FMU,
            stop_time=10800,
            start_values={"QBoi_kW": float(q), "TRooSet_degC": float(tset)},
            output=["TRoo_degC", "EHea_kWh"],
        )
        room = float(r["TRoo_degC"][-1])
        energy = float(r["EHea_kWh"][-1])
        print(
            f"  QBoi_kW={q:2d}  TRooSet={tset}C  ->  "
            f"room {room:5.2f} C   energy {energy:6.3f} kWh",
        )
    except Exception as exc:
        print(f"  QBoi_kW={q} TRooSet={tset}: FAILED {type(exc).__name__}: {exc}")
