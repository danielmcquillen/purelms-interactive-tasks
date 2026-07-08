model HydronicLoop
  "Single-room hydronic heating loop for the PureLMS modelica_diagram task.

   A boiler heats water that a pump circulates through a radiator in a closed
   loop; the radiator warms a single room through its heat ports; the room
   loses heat to a fixed outdoor temperature; an on/off thermostat cycles the
   boiler. Four author-facing knobs, all genuinely tunable in the FMU:

     QBoi_kW      boiler power [kW]   -> warm-up speed + adequacy (an undersized
                  boiler never reaches the setpoint), NOT total energy.
     UA_WperK     heat loss [W/K]     -> heating ENERGY (the envelope dominates);
                  lower = better insulated.
     TOut_degC    outdoor temp [degC] -> heating load: a colder day needs more
                  energy and a bigger boiler to keep up.
     TRooSet_degC target temp [degC]  -> comfort/energy trade-off.

   Outputs: TRoo_degC (final room temp), EHea_kWh (heating energy),
            tReach_min (minutes to first reach the setpoint; = run length if the
            boiler is too small to ever get there).

   IMPORTANT: the fluid components are sized for a FIXED design power
   (QBoiMax_kW). If QBoi_kW sized a component, OpenModelica would constant-fold
   it and the slider would do nothing (it did, in v1). Instead QBoi_kW scales
   the firing rate, which keeps it a true runtime parameter. Based on
   Buildings.Examples.Tutorial.Boiler (Buildings 13.0.0).
"
  extends Modelica.Icons.Example;

  package MediumW = Buildings.Media.Water "Loop fluid (water)";

  // ---- Author-facing knobs (each genuinely tunable) ----
  parameter Real QBoi_kW = 10 "Boiler power [kW]";
  parameter Real TRooSet_degC = 21 "Target room temperature [degC]";
  parameter Real UA_WperK = 150
    "Building heat-loss coefficient [W/K] (lower = better insulated)";

  // ---- Fixed design sizing (largest the slider allows) so QBoi_kW stays tunable ----
  parameter Real QBoiMax_kW = 20 "Design power the components are sized for [kW]";
  parameter Modelica.Units.SI.Power QDes_flow = QBoiMax_kW*1000 "Design power [W]";
  parameter Modelica.Units.SI.MassFlowRate mWat_flow_nominal=QDes_flow/(4200*20)
    "Loop water flow [kg/s]";
  parameter Real firingScale = QBoi_kW/QBoiMax_kW
    "Firing fraction = chosen power / design power";

  parameter Modelica.Units.SI.Temperature TRooSet=TRooSet_degC + 273.15 "Setpoint [K]";
  parameter Modelica.Units.SI.HeatCapacity CRoo = 1e6 "Room thermal mass [J/K]";
  parameter Real TOut_degC = 0 "Outdoor temperature [degC]";
  parameter Modelica.Units.SI.Temperature TOut_K = TOut_degC + 273.15 "Outdoor temp [K]";
  parameter Modelica.Units.SI.Temperature TRoo_start = 288.15 "Room start (15 degC)";

  // ---- Water loop (sized for the FIXED design power) ----
  Buildings.Fluid.Boilers.BoilerPolynomial boi(
    redeclare package Medium = MediumW,
    m_flow_nominal=mWat_flow_nominal,
    Q_flow_nominal=QDes_flow,
    fue=Buildings.Fluid.Data.Fuels.NaturalGasLowerHeatingValue(),
    dp_nominal=3000,
    T_nominal=353.15) "Boiler";
  Buildings.Fluid.Movers.FlowControlled_m_flow pum(
    redeclare package Medium = MediumW,
    m_flow_nominal=mWat_flow_nominal,
    inputType=Buildings.Fluid.Types.InputType.Constant,
    nominalValuesDefineDefaultPressureCurve=true) "Circulation pump (constant flow)";
  Buildings.Fluid.HeatExchangers.Radiators.RadiatorEN442_2 rad(
    redeclare package Medium = MediumW,
    m_flow_nominal=mWat_flow_nominal,
    Q_flow_nominal=QDes_flow,
    T_a_nominal=353.15,
    T_b_nominal=333.15,
    TAir_nominal=293.15,
    dp_nominal=3000) "Radiator";
  Buildings.Fluid.Sources.Boundary_pT exp(
    redeclare package Medium = MediumW,
    nPorts=1) "Expansion / reference-pressure boundary";

  // ---- Room ----
  Modelica.Thermal.HeatTransfer.Components.HeatCapacitor rooCap(
    C=CRoo, T(start=TRoo_start, fixed=true)) "Room air + furnishings";
  Modelica.Thermal.HeatTransfer.Components.ThermalConductor rooLos(G=UA_WperK)
    "Room -> outdoor heat loss (the insulation knob)";
  Modelica.Thermal.HeatTransfer.Sources.FixedTemperature out(T=TOut_K) "Outdoor";
  Modelica.Thermal.HeatTransfer.Sensors.TemperatureSensor TRooSen "Room temp sensor";

  // ---- On/off thermostat -> firing rate (scaled to the chosen boiler power) ----
  Modelica.Blocks.Sources.Constant setp(k=TRooSet) "Setpoint [K]";
  Modelica.Blocks.Logical.OnOffController thermostat(bandwidth=1) "Heating call";
  Modelica.Blocks.Math.BooleanToReal call "0/1 call";
  Modelica.Blocks.Math.Gain fire(k=firingScale) "Firing rate at the chosen power";
  Modelica.Blocks.Math.Gain heaPow(k=QDes_flow) "Heat power = firing x design power [W]";
  Modelica.Blocks.Continuous.Integrator EHea(y_start=0) "Cumulative heat energy [J]";

  // ---- Outputs ----
  output Real TRoo_degC = TRooSen.T - 273.15 "Room temperature [degC]";
  output Real EHea_kWh = EHea.y/3.6e6 "Heating energy [kWh]";

  // Minutes for the room to first reach the setpoint (run length if it never does).
  discrete Real tReachS(start=10800, fixed=true) "First time at setpoint [s]";
  Boolean reached(start=false, fixed=true);
  output Real tReach_min = tReachS/60 "Minutes to reach setpoint";

equation
  // ---- closed water loop ----
  connect(boi.port_b, pum.port_a);
  connect(pum.port_b, rad.port_a);
  connect(rad.port_b, boi.port_a);
  connect(exp.ports[1], boi.port_a);
  // ---- radiator heat -> room ----
  connect(rad.heatPortCon, rooCap.port);
  connect(rad.heatPortRad, rooCap.port);
  // ---- room loss -> outdoors ----
  connect(rooCap.port, rooLos.port_a);
  connect(rooLos.port_b, out.port);
  connect(rooCap.port, TRooSen.port);
  // ---- thermostat -> scaled firing -> boiler ----
  connect(TRooSen.T, thermostat.u);
  connect(setp.y, thermostat.reference);
  connect(thermostat.y, call.u);
  connect(call.y, fire.u);
  connect(fire.y, boi.y);
  // ---- energy = firing x design power, integrated ----
  connect(fire.y, heaPow.u);
  connect(heaPow.y, EHea.u);
  // ---- time to reach the setpoint (first up-crossing only) ----
  when TRooSen.T >= TRooSet then
    reached = true;
    tReachS = if pre(reached) then pre(tReachS) else time;
  end when;

  annotation (experiment(StopTime=10800, Tolerance=1e-6));
end HydronicLoop;
