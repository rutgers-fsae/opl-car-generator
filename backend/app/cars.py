"""App validation and units; the supplied generator owns the binary format."""

import csv
import hashlib
import io
import math
import re
import struct
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from olveh_gen import POWER_K, Car, build_olveh, make_car, torque_curve

ROOT = Path(__file__).resolve().parents[2]
# Native unit followed by accepted alternate units and conversion to native.
UNITS = {
    "kg": {"kg": 1, "lb": 0.45359237},
    "m": {"m": 1, "mm": 0.001, "in": 0.0254},
    "m²": {"m²": 1, "m2": 1, "ft²": 0.09290304, "ft2": 0.09290304},
    "kW": {"kW": 1, "W": 0.001, "hp": 0.745699872},
    "Nm": {"Nm": 1, "N·m": 1, "lb-ft": 1.3558179483},
    "rpm": {"rpm": 1, "rad/s": 60 / (2 * math.pi)},
    "s": {"s": 1, "ms": 0.001},
    "kg/m³": {"kg/m³": 1, "kg/m3": 1, "lb/ft³": 16.018463374},
    "J/kg": {"J/kg": 1, "kJ/kg": 1000, "Wh/kg": 3600},
    "ratio": {"ratio": 1, "%": 0.01},
    "": {"": 1, "1": 1},
}
# key, label, native unit, section, kind
SPECS = [
    ("max_power_kw", "Max power", "kW", "Quick adjustments", "number"),
    ("max_torque_nm", "Max torque", "Nm", "Quick adjustments", "number"),
    ("cl", "Downforce coefficient · Cl", "", "Quick adjustments", "number"),
    ("cd", "Drag coefficient · Cd", "", "Quick adjustments", "number"),
    ("rev_limit_rpm", "Max RPM", "rpm", "Quick adjustments", "number"),
    ("name", "Car name", "", "Identity", "text"),
    ("category", "Category", "", "Identity", "text"),
    ("color", "Color · signed ARGB", "", "Identity", "integer"),
    ("mass", "Vehicle mass", "kg", "Mass & aero", "number"),
    ("frontal_area", "Frontal area", "m²", "Mass & aero", "number"),
    ("air_density", "Air density", "kg/m³", "Mass & aero", "number"),
    ("tire_radius", "Tire rolling radius", "m", "Tires & grip", "number"),
    ("mu_long", "Longitudinal friction", "", "Tires & grip", "number"),
    ("mu_lat", "Lateral friction", "", "Tires & grip", "number"),
    (
        "mass_long_friction",
        "Longitudinal reference mass",
        "kg",
        "Tires & grip",
        "number",
    ),
    ("mass_lat_friction", "Lateral reference mass", "kg", "Tires & grip", "number"),
    ("sens_long", "Longitudinal load sensitivity", "", "Tires & grip", "number"),
    ("sens_lat", "Lateral load sensitivity", "", "Tires & grip", "number"),
    ("rolling_drag", "Rolling drag", "", "Tires & grip", "number"),
    ("transmission", "Transmission", "", "Drivetrain", "text"),
    ("shift_time", "Shift time", "s", "Drivetrain", "number"),
    ("drivetrain_eff", "Drivetrain efficiency", "ratio", "Drivetrain", "number"),
    ("final_drive", "Final drive ratio", "", "Drivetrain", "number"),
    ("driven_weight_frac", "Weight on driven wheels", "ratio", "Drivetrain", "number"),
    ("gear_ratios", "Gear ratios", "", "Drivetrain", "array"),
    ("shift_rpm", "Shift RPM · blank follows max RPM", "rpm", "Drivetrain", "array"),
    (
        "energy_density",
        "Fuel / battery energy density",
        "J/kg",
        "Energy & scaling",
        "number",
    ),
    ("engine_eff", "Engine thermal efficiency", "ratio", "Energy & scaling", "number"),
    ("power_factor", "Power scaling", "ratio", "Energy & scaling", "number"),
    ("aero_factor", "Aero scaling", "ratio", "Energy & scaling", "number"),
    ("grip_factor", "Grip scaling", "ratio", "Energy & scaling", "number"),
]
FIELDS = {s[0]: s for s in SPECS}
POWER = {"max_power_kw", "max_torque_nm", "rev_limit_rpm"}


def schema():
    return [
        dict(key=k, label=label, unit=u, group=g, kind=t, units=list(UNITS[u]))
        for k, label, u, g, t in SPECS
    ]


def parse(key: str, value: Any, unit: str = "") -> Any:
    if key not in FIELDS:
        raise ValueError(f"Unknown parameter: {key}")
    _, _, native, _, kind = FIELDS[key]
    if kind == "text":
        if unit:
            raise ValueError(f"{key}: text fields do not accept units")
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError(f"{key}: enter 1–200 characters")
        return value.strip()
    if key == "shift_rpm" and (value is None or value == ""):
        return None
    if kind == "array":
        values = (
            value
            if isinstance(value, list)
            else re.split(r"[,;\s]+", str(value).strip())
        )
        if not 1 <= len(values) <= 20:
            raise ValueError(f"{key}: enter 1–20 values")
        return [number(key, v, unit or native) for v in values]
    if isinstance(value, str):
        match = re.fullmatch(
            r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*", value
        )
        if not match:
            raise ValueError(f"{key}: invalid number")
        value, suffix = match.groups()
        if suffix:
            if unit and suffix != unit:
                raise ValueError(f"{key}: conflicting units")
            unit = suffix
    result = number(key, value, unit or native)
    if kind == "integer":
        if not result.is_integer() or not -(2**31) <= result < 2**31:
            raise ValueError(f"{key}: expected a signed 32-bit integer")
        return int(result)
    return result


def number(key: str, value: Any, unit: str) -> float:
    native = FIELDS[key][2]
    if unit not in UNITS[native]:
        raise ValueError(
            f"{key}: bad unit '{unit}'; use {', '.join(UNITS[native]) or 'unitless'}"
        )
    if isinstance(value, bool):
        raise ValueError(f"{key}: invalid number")
    try:
        result = float(value) * UNITS[native][unit]
    except (TypeError, ValueError):
        raise ValueError(f"{key}: invalid number") from None
    if not math.isfinite(result) or abs(result) > 1e12:
        raise ValueError(
            f"{key}: value must be finite and no greater than 1e12 in magnitude"
        )
    return result


def load(raw: bytes) -> dict:
    if len(raw) < 25 or len(raw) > 2_000_000:
        raise ValueError("Vehicle file must be between 25 bytes and 2 MB")
    if hashlib.sha1(raw[:-20]).digest() != raw[-20:]:
        raise ValueError("Vehicle checksum does not match")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "base.OLVeh"
        path.write_bytes(raw)
        try:
            car = make_car(path)
            validate(car)
        except (
            struct.error,
            IndexError,
            KeyError,
            UnicodeError,
            ValueError,
            TypeError,
            OverflowError,
        ) as exc:
            raise ValueError(f"Invalid vehicle: {exc}") from None
    return asdict(car)


def sampled_curve(car: Car):
    if car.max_power_kw is not None:
        return torque_curve(car.rev_limit_rpm, car.max_power_kw, car.max_torque_nm)
    rpm, torque = list(car.torque_rpm), list(car.torque_nm)
    # OptimumLap templates can store one constant-torque point; show its flat tail.
    if rpm[-1] < car.rev_limit_rpm:
        rpm.append(car.rev_limit_rpm)
        torque.append(torque[-1])
    return rpm, torque


def peaks(car: Car) -> tuple[float, float]:
    rpm, torque = sampled_curve(car)
    return max(t * r / POWER_K for t, r in zip(torque, rpm, strict=True)), max(torque)


def validate(car: Car):
    if (
        not isinstance(car.header_extra, (list, tuple))
        or len(car.header_extra) != 3
        or any(not isinstance(v, str) or len(v) > 200 for v in car.header_extra)
    ):
        raise ValueError("Invalid vehicle header fields")
    if not isinstance(car.outputs, dict) or any(
        not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in car.outputs.values()
    ):
        raise ValueError("Invalid stored outputs")
    for key in FIELDS:
        value = getattr(car, key)
        if value is None and key not in {"shift_rpm", "max_power_kw", "max_torque_nm"}:
            raise ValueError(f"{key}: a value is required")
        if value is not None:
            parse(key, value)
    for values in (car.torque_rpm, car.torque_nm):
        if (
            not values
            or len(values) > 100_000
            or not all(math.isfinite(v) for v in values)
        ):
            raise ValueError("Invalid torque curve")
    if (
        len(car.torque_nm) != len(car.torque_rpm)
        or any(r < 0 for r in car.torque_rpm)
        or any(t < 0 for t in car.torque_nm)
        or any(b <= a for a, b in zip(car.torque_rpm, car.torque_rpm[1:]))
    ):
        raise ValueError("Invalid torque curve points")
    positive = (
        "tire_radius",
        "final_drive",
        "frontal_area",
        "air_density",
        "energy_density",
    )
    if any(getattr(car, k) <= 0 for k in positive):
        raise ValueError(
            "Radius, drive ratio, area, density and energy density must be positive"
        )
    if car.shift_time < 0:
        raise ValueError("Shift time must be nonnegative")
    if car.mass < 0 or car.cd == 0 or not 1 < car.rev_limit_rpm <= 100_000:
        raise ValueError(
            "Mass must be nonnegative, Cd nonzero, and RPM between 1 and 100,000"
        )
    for key in ("drivetrain_eff", "driven_weight_frac", "engine_eff"):
        if not 0 <= getattr(car, key) <= 1:
            raise ValueError(f"{key}: expected a fraction from 0 to 1")
    if any(v <= 0 for v in car.gear_ratios) or (
        car.shift_rpm and any(v <= 0 for v in car.shift_rpm)
    ):
        raise ValueError("Gear ratios and shift RPM must be positive")
    overall = [g * car.final_drive for g in car.gear_ratios]
    if not all(math.isfinite(g) and g > 0 for g in overall):
        raise ValueError("Overall gear ratios must be finite and positive")
    speed = car.rev_limit_rpm * 2 * math.pi * car.tire_radius / (60 * min(overall))
    if not math.isfinite(speed) or not math.isfinite(car.cl / car.cd):
        raise ValueError("Derived speed and aero efficiency must be finite")
    build_olveh(car)


def resolve(base: dict, overrides: dict) -> Car:
    # Only the reader's model is accepted; metadata is never accepted as an override.
    allowed = set(asdict(Car()))
    if set(base) != allowed:
        raise ValueError("Base must contain exactly the generator’s Car fields")
    try:
        car = Car(**base)
        validate(car)
        edits = {key: parse(key, value) for key, value in overrides.items()}
        if POWER.intersection(edits):
            p, t = (
                peaks(car)
                if car.max_power_kw is None
                else (car.max_power_kw, car.max_torque_nm)
            )
            edits.setdefault("max_power_kw", p)
            edits.setdefault("max_torque_nm", t)
        car = replace(car, **edits, guid=None, modified=None, outputs={})
        validate(car)
        return car
    except (TypeError, KeyError, OverflowError, AttributeError, struct.error) as exc:
        raise ValueError(f"Invalid car data: {exc}") from None


def evaluate(base: dict, overrides: dict) -> dict:
    car = resolve(base, overrides)
    rpm, torque = sampled_curve(car)
    power = [r * t / POWER_K for r, t in zip(rpm, torque, strict=True)]
    overall = [g * car.final_drive for g in car.gear_ratios]
    speed = (
        car.rev_limit_rpm * 2 * math.pi * car.tire_radius / (60 * min(overall)) * 3.6
    )
    warnings = []
    if car.mass == 0:
        warnings.append("Vehicle mass is zero.")
    if max(car.mu_long, car.mu_lat) > 3:
        warnings.append("Tire friction is above 3; check the input.")
    if speed < 40:
        warnings.append("Kinematic top speed is under 40 km/h; check gearing.")
    if (
        car.max_power_kw is not None
        and car.max_power_kw * POWER_K / car.max_torque_nm >= car.rev_limit_rpm
    ):
        warnings.append(
            "The power corner is at or above max RPM; requested peak power is not reached."
        )
    return dict(
        car=asdict(car),
        rpm=rpm,
        torque=torque,
        power=power,
        peak_power=max(power),
        peak_torque=max(torque),
        overall=overall,
        aero_efficiency=car.cl / car.cd,
        top_speed_kmh=speed,
        warnings=warnings,
    )


def csv_preview(text: str, base: dict, current: dict) -> dict:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != ["parameter", "value", "unit"]:
        raise ValueError("CSV headers must be parameter,value,unit")
    changes, errors, edits = [], [], {}
    effective = resolve(base, current)
    for i, row in enumerate(reader, 2):
        try:
            if i > 1000 or None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed CSV or more than 999 rows")
            key = row["parameter"].strip()
            if key in edits:
                raise ValueError(f"Duplicate parameter: {key}")
            value = parse(key, row["value"], row["unit"].strip())
            edits[key] = value
            before = getattr(effective, key)
            if before is None and key in ("max_power_kw", "max_torque_nm"):
                before = peaks(effective)[key == "max_torque_nm"]
            if before != value:
                changes.append(
                    dict(parameter=key, before=before, after=value, unit=FIELDS[key][2])
                )
        except ValueError as exc:
            errors.append(f"Row {i}: {exc}")
    if not edits:
        errors.append("CSV contains no parameters")
    if not errors:
        try:
            resolve(base, current | edits)
        except ValueError as exc:
            errors.append(str(exc))
    return dict(changes=changes, errors=errors, overrides=edits)


def filename(name: str) -> str:
    return (
        re.sub(r"[^a-zA-Z0-9_.-]+", "_", name).strip("._")[:100] or "Car"
    ) + ".OLVeh"
