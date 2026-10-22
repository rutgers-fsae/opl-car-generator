#!/usr/bin/env python3
"""
Generate OptimumLap .OLVeh vehicle files.

Format (reverse-engineered from RFR27_Base_Car.OLVeh, .NET BinaryWriter layout):
  0x00, str version, str modified, str "1/1/0001 12:00:00 AM", str guid, str name,
  str argb-color, str category, 0x00, str transmission, str shift_time, 3x str "100",
  7bit count(28), 0x00, 28 x [key,key,display,unit,flag,kind (strs) + float64],
  7bit count(4),  0x00,  4 x [key,key,display,unit,flag,kind (strs) + int32 n + n*float64],
  20-byte SHA-1 of everything above.
  str = 7-bit-encoded length + UTF-8.  All values are stored in SI (rad/s, m, kg, ...).

Usage:
  python olveh_gen.py -o out/ --name Test1 --set mass=290 tire_radius=0.2
  python olveh_gen.py -o out/ --template RFR27_Base_Car.OLVeh --sweep mass=270,280,290
  python olveh_gen.py --selftest RFR27_Base_Car.OLVeh
As a module:  from olveh_gen import Car, write_olveh;  write_olveh(Car(mass=290), "x.OLVeh")
"""
import argparse
import hashlib
import math
import struct
import sys
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

VERSION = "1.0.5"
RPM = 2 * math.pi / 60           # rpm -> rad/s
EPOCH_STR = "1/1/0001 12:00:00 AM"
POWER_K = 9549                   # torque[Nm] = power[kW] * POWER_K / rpm

# (key, display name, unit type, flag, kind) in file order. kind "4" = input, "5" = computed output.
SCALARS = [
    ("masslongfriction",             "Mass Longitudinal Friction",                    "Mass",          "True",  "4"),
    ("mass",                         "Vehicle Mass",                                  "Mass",          "True",  "4"),
    ("dragcoefficient",              "Drag Coefficient",                              "Unitless",      "True",  "4"),
    ("liftcoefficient",              "Downforce Coefficient",                         "Unitless",      "True",  "4"),
    ("aeroefficiency",               "Aero Efficiency",                               "Unitless",      "True",  "4"),
    ("frontarea",                    "Frontal Area",                                  "Area",          "True",  "4"),
    ("driveefficiency",              "Drivetrain Efficiency",                         "Ratio",         "True",  "4"),
    ("tireradius",                   "Tire Rolling Radius",                           "Length",        "True",  "4"),
    ("airdensity",                   "Air Density",                                   "Density",       "True",  "4"),
    ("revlimit",                     "Rev Limit",                                     "SpeedAngular",  "True",  "4"),
    ("longfriction",                 "Longitudinal Friction",                         "Unitless",      "True",  "4"),
    ("latfriction",                  "Lateral Friction",                              "Unitless",      "True",  "4"),
    ("finaldriveratio",              "Final Drive Ratio",                             "Unitless",      "True",  "4"),
    ("weightdrivenwheel",            "Percent Weight on Driven Wheel",                "Ratio",         "False", "4"),
    ("fuelenergydensity",            "Fuel Energy Density",                           "EnergyDensity", "True",  "4"),
    ("engineefficiency",             "Engine Thermal Efficiency",                     "Ratio",         "True",  "4"),
    ("tirerollingdrag",              "Tire Rolling Drag",                             "Unitless",      "True",  "4"),
    ("powerfactor",                  "Power Scaling Factor",                          "Ratio",         "True",  "4"),
    ("aerofactor",                   "Aero Scaling Factor",                           "Ratio",         "True",  "4"),
    ("gripfactor",                   "Grip Scaling Factor",                           "Ratio",         "True",  "4"),
    ("masslatfriction",              "Mass Lateral Friction",                         "Mass",          "True",  "4"),
    ("sensitivitylatfriction",       "Load Sensitivity Lateral Friction",             "Unitless",      "True",  "4"),
    ("sensitivitylongfriction",      "Load Sensitivity Longitudinal Friction",        "Unitless",      "True",  "4"),
    ("topSpeed",                     "Top Speed",                                     "SpeedLinear",   "False", "5"),
    ("accelerationtimetospeed",      "Acceleration Time To Speed [27.778 km/h]",      "Time",          "False", "5"),
    ("accelerationtimefordistance",  "Acceleration Time For Distance [100 m]",        "Time",          "False", "5"),
    ("decelerationtimeforspeed",     "Deceleration Time For Speed [27.778 km/h]",     "Time",          "False", "5"),
    ("decelerationdistanceforspeed", "Deceleration Distance For Speed [27.778 km/h]", "Length",        "False", "5"),
]
OUTPUT_KEYS = [s[0] for s in SCALARS if s[4] == "5"]

ARRAYS = [
    ("gearratiodata",   "Gear Ratio Data",   "Unitless",     "True", "4"),
    ("gearshiftpoints", "Gear Shift Points", "SpeedAngular", "True", "4"),
    ("torquedata",      "Torque Data",       "Torque",       "True", "4"),
    ("rpmdata",         "RPM Data",          "SpeedAngular", "True", "4"),
]


def torque_curve(max_rpm, max_power_kw, max_torque_nm, step=100):
    """Build (rpm_list, torque_Nm_list) from three numbers.

    1) (1 rpm, max torque)
    2) (floor(P*9549/Tmax) rpm, max torque)   <- corner where the power limit starts
    3) every multiple of `step` rpm above the corner, torque = P*9549/rpm
    4) (max_rpm, P*9549/max_rpm)
    If the corner is at/above max_rpm the power limit is never reached: the curve is flat
    at max torque from 1 rpm to max_rpm (actual peak power is then below max_power_kw).
    """
    if min(max_rpm, max_power_kw, max_torque_nm) <= 0:
        raise ValueError("max_rpm, max_power_kw and max_torque_nm must be > 0")
    if max_rpm <= 1:
        raise ValueError("max_rpm must be > 1")
    corner = math.floor(max_power_kw * POWER_K / max_torque_nm)
    if corner >= max_rpm:
        return [1.0, float(max_rpm)], [float(max_torque_nm)] * 2
    corner = max(corner, 2)                      # keep rpm strictly increasing after the 1 rpm point
    rpms = [1.0, float(corner)]
    r = (corner // step + 1) * step              # next multiple of step above the corner
    while r < max_rpm:
        rpms.append(float(r))
        r += step
    rpms.append(float(max_rpm))
    tq = [float(max_torque_nm)] * 2 + [max_power_kw * POWER_K / r for r in rpms[2:]]
    return rpms, tq


@dataclass
class Car:
    # identity
    name: str = "Generated_Car"
    category: str = "FSAE"
    color: int = -16777216                 # ARGB int, -16777216 = opaque black
    transmission: str = "Sequential Gearbox"
    shift_time: float = 0.02               # s
    header_extra: tuple = ("100", "100", "100")   # three header fields, meaning unknown; copied as-is
    # mass / aero
    mass: float = 298.7041834161           # kg
    cd: float = 1.539                      # drag coefficient (Cd)
    cl: float = 2.584                      # downforce coefficient (Cl, positive = downforce)
    frontal_area: float = 1.0              # m^2
    air_density: float = 1.208             # kg/m^3
    # tires / grip
    tire_radius: float = 0.2032            # m (rolling radius)
    mu_long: float = 1.675
    mu_lat: float = 1.625
    mass_long_friction: float = 0.0        # kg
    mass_lat_friction: float = 0.0         # kg
    sens_long: float = 0.0                 # load sensitivity, longitudinal
    sens_lat: float = 0.0                  # load sensitivity, lateral
    rolling_drag: float = 0.02             # rolling resistance coefficient
    # drivetrain
    drivetrain_eff: float = 0.92
    final_drive: float = 3.47
    driven_weight_frac: float = 0.5        # fraction of weight on driven wheels
    rev_limit_rpm: float = 5500.0          # rpm at the wheel-side motor shaft as used by OptimumLap
    gear_ratios: list = field(default_factory=lambda: [1.0])
    shift_rpm: list = None                 # one per gear; defaults to rev_limit_rpm for every gear
    # energy
    energy_density: float = 720000.0       # J/kg (200 Wh/kg)
    engine_eff: float = 0.9
    # scaling factors
    power_factor: float = 1.0
    aero_factor: float = 1.0
    grip_factor: float = 1.0
    # power / torque. Either give max_power_kw AND max_torque_nm (curve is generated, ending at
    # rev_limit_rpm), or give explicit torque_nm / torque_rpm point lists (used if max_* are None).
    max_power_kw: float = None
    max_torque_nm: float = None
    torque_nm: list = field(default_factory=lambda: [220.0])
    torque_rpm: list = field(default_factory=lambda: [1.0])
    # file metadata (auto if None)
    guid: str = None
    modified: str = None
    # computed outputs OptimumLap stores (topSpeed etc.); zeros unless supplied
    outputs: dict = field(default_factory=dict)


def _w7(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _s(text):
    b = text.encode("utf-8")
    return _w7(len(b)) + b


def _g(x):
    return "%.15g" % x


def _now_str():
    d = datetime.now()
    h = d.hour % 12 or 12
    return f"{d.month}/{d.day}/{d.year} {h}:{d.minute:02d}:{d.second:02d} {'AM' if d.hour < 12 else 'PM'}"


def build_olveh(car: Car) -> bytes:
    n = len(car.gear_ratios)
    shift = car.shift_rpm if car.shift_rpm is not None else [car.rev_limit_rpm] * n
    if len(shift) != n:
        raise ValueError(f"shift_rpm needs {n} entries (one per gear), got {len(shift)}")
    if (car.max_power_kw is None) != (car.max_torque_nm is None):
        raise ValueError("set both max_power_kw and max_torque_nm, or neither")
    if car.max_power_kw is not None:
        tq_rpm, tq_nm = torque_curve(car.rev_limit_rpm, car.max_power_kw, car.max_torque_nm)
    else:
        tq_rpm, tq_nm = list(car.torque_rpm), list(car.torque_nm)
    if len(tq_nm) != len(tq_rpm):
        raise ValueError("torque_nm and torque_rpm must be the same length")
    if car.cd == 0:
        raise ValueError("cd must be nonzero (aero efficiency = cl/cd)")

    vals = {
        "masslongfriction": car.mass_long_friction,
        "mass": car.mass,
        "dragcoefficient": car.cd,
        "liftcoefficient": car.cl,
        "aeroefficiency": car.cl / car.cd,
        "frontarea": car.frontal_area,
        "driveefficiency": car.drivetrain_eff,
        "tireradius": car.tire_radius,
        "airdensity": car.air_density,
        "revlimit": car.rev_limit_rpm * RPM,
        "longfriction": car.mu_long,
        "latfriction": car.mu_lat,
        "finaldriveratio": car.final_drive,
        "weightdrivenwheel": car.driven_weight_frac,
        "fuelenergydensity": car.energy_density,
        "engineefficiency": car.engine_eff,
        "tirerollingdrag": car.rolling_drag,
        "powerfactor": car.power_factor,
        "aerofactor": car.aero_factor,
        "gripfactor": car.grip_factor,
        "masslatfriction": car.mass_lat_friction,
        "sensitivitylatfriction": car.sens_lat,
        "sensitivitylongfriction": car.sens_long,
    }
    for k in OUTPUT_KEYS:
        vals[k] = car.outputs.get(k, 0.0)

    arrs = {
        "gearratiodata": list(car.gear_ratios),
        "gearshiftpoints": [r * RPM for r in shift],
        "torquedata": tq_nm,
        "rpmdata": [r * RPM for r in tq_rpm],
    }

    out = bytearray(b"\x00")
    for t in (VERSION, car.modified or _now_str(), EPOCH_STR,
              car.guid or str(uuid.uuid4()), car.name, str(car.color), car.category):
        out += _s(t)
    out += b"\x00"
    for t in (car.transmission, _g(car.shift_time), *car.header_extra):
        out += _s(t)

    out += _w7(len(SCALARS)) + b"\x00"
    for key, disp, unit, flag, kind in SCALARS:
        for t in (key, key, disp, unit, flag, kind):
            out += _s(t)
        out += struct.pack("<d", vals[key])

    out += _w7(len(ARRAYS)) + b"\x00"
    for key, disp, unit, flag, kind in ARRAYS:
        for t in (key, key, disp, unit, flag, kind):
            out += _s(t)
        out += struct.pack("<i", len(arrs[key]))
        for v in arrs[key]:
            out += struct.pack("<d", v)

    out += hashlib.sha1(out).digest()
    return bytes(out)


def write_olveh(car: Car, path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".olveh":
        path = path.with_name(path.name + ".OLVeh")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_olveh(car))
    return path


# ---------------------------------------------------------------- reader (for --template) ----
def read_olveh(path, keep_outputs=False) -> Car:
    data = Path(path).read_bytes()
    if hashlib.sha1(data[:-20]).digest() != data[-20:]:
        print("warning: SHA-1 trailer does not match", file=sys.stderr)
    pos = 0

    def u8():
        nonlocal pos
        pos += 1
        return data[pos - 1]

    def v7():
        r = s = 0
        while True:
            b = u8()
            r |= (b & 0x7F) << s
            if not b & 0x80:
                return r
            s += 7

    def st():
        nonlocal pos
        n = v7()
        pos += n
        return data[pos - n:pos].decode("utf-8")

    def f64():
        nonlocal pos
        pos += 8
        return struct.unpack("<d", data[pos - 8:pos])[0]

    def i32():
        nonlocal pos
        pos += 4
        return struct.unpack("<i", data[pos - 4:pos])[0]

    u8()
    _ver, modified, _epoch, guid, name, color, category = (st() for _ in range(7))
    u8()
    transmission, shift_time, *extra = (st() for _ in range(5))
    n = v7(); u8()
    sc = {}
    for _ in range(n):
        key = st(); st(); st(); st(); st(); st()
        sc[key] = f64()
    n = v7(); u8()
    ar = {}
    for _ in range(n):
        key = st(); st(); st(); st(); st(); st()
        ar[key] = [f64() for _ in range(i32())]

    r9 = lambda x: round(x / RPM, 9)
    return Car(
        name=name, category=category, color=int(color), transmission=transmission,
        shift_time=float(shift_time), header_extra=tuple(extra),
        mass=sc["mass"], cd=sc["dragcoefficient"], cl=sc["liftcoefficient"],
        frontal_area=sc["frontarea"], air_density=sc["airdensity"],
        tire_radius=sc["tireradius"], mu_long=sc["longfriction"], mu_lat=sc["latfriction"],
        mass_long_friction=sc["masslongfriction"], mass_lat_friction=sc["masslatfriction"],
        sens_long=sc["sensitivitylongfriction"], sens_lat=sc["sensitivitylatfriction"],
        rolling_drag=sc["tirerollingdrag"], drivetrain_eff=sc["driveefficiency"],
        final_drive=sc["finaldriveratio"], driven_weight_frac=sc["weightdrivenwheel"],
        rev_limit_rpm=r9(sc["revlimit"]), gear_ratios=ar["gearratiodata"],
        shift_rpm=None if all(r9(x) == r9(sc["revlimit"]) for x in ar["gearshiftpoints"])
                  else [r9(x) for x in ar["gearshiftpoints"]],   # None = follow rev limit
        energy_density=sc["fuelenergydensity"], engine_eff=sc["engineefficiency"],
        power_factor=sc["powerfactor"], aero_factor=sc["aerofactor"], grip_factor=sc["gripfactor"],
        torque_nm=ar["torquedata"], torque_rpm=[r9(x) for x in ar["rpmdata"]],
        guid=guid, modified=modified,
        outputs={k: sc[k] for k in OUTPUT_KEYS} if keep_outputs else {},
    )


def make_car(template=None, **overrides) -> Car:
    """One entry point for the three app modes:
         make_car()                              generic base car
         make_car("my.OLVeh")                    import an existing file
         make_car("my.OLVeh", mass=280)          change a few values
         make_car(max_power_kw=80, max_torque_nm=230, rev_limit_rpm=6000)
    New GUID / timestamp are always generated."""
    car = read_olveh(template) if template else Car()
    bad = set(overrides) - {f.name for f in fields(Car)}
    if bad:
        raise TypeError(f"unknown parameter(s): {', '.join(sorted(bad))}")
    return replace(car, guid=None, modified=None, **overrides)


# ------------------------------------------------------------------------------- CLI ----------
def _coerce(car, key, text):
    names = {f.name for f in fields(Car)}
    if key not in names:
        raise SystemExit(f"unknown parameter '{key}'. valid: {', '.join(sorted(names))}")
    cur = getattr(car, key)
    if isinstance(cur, list) or key in ("shift_rpm",):
        return [float(x) for x in text.split(",")]
    if isinstance(cur, tuple):
        return tuple(text.split(","))
    if isinstance(cur, str):
        return text
    if isinstance(cur, int) and not isinstance(cur, bool):
        return int(text)
    return float(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=".", help="output directory")
    ap.add_argument("--template", help="existing .OLVeh to start from")
    ap.add_argument("--name", help="vehicle name / file stem")
    ap.add_argument("--set", nargs="*", default=[], metavar="K=V", help="override Car fields")
    ap.add_argument("--sweep", action="append", default=[], metavar="K=V1,V2,..",
                    help="one file per value (repeatable; multiple sweeps = cartesian product)")
    ap.add_argument("--selftest", metavar="FILE", help="rebuild FILE byte-for-byte and compare")
    a = ap.parse_args()

    if a.selftest:
        raw = Path(a.selftest).read_bytes()
        rebuilt = build_olveh(read_olveh(a.selftest, keep_outputs=True))
        print("byte-identical" if rebuilt == raw else f"MISMATCH ({len(raw)} vs {len(rebuilt)} bytes)")
        return 0 if rebuilt == raw else 1

    car = read_olveh(a.template) if a.template else Car()
    car = replace(car, guid=None, modified=None)
    if a.name:
        car = replace(car, name=a.name)
    for kv in a.set:
        k, v = kv.split("=", 1)
        car = replace(car, **{k: _coerce(car, k, v)})

    combos = [(car, "")]
    for sw in a.sweep:
        k, vs = sw.split("=", 1)
        combos = [(replace(c, **{k: _coerce(c, k, v)}), tag + f"_{k}{v}")
                  for c, tag in combos for v in vs.split(",")]

    for c, tag in combos:
        c = replace(c, name=c.name + tag)
        print(write_olveh(c, Path(a.out) / c.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
