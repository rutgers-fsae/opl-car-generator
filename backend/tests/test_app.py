import io
import json
import zipfile
from dataclasses import asdict

import pytest
from backend.app import auth, cars
from backend.app.main import app, attempts
from fastapi.testclient import TestClient
from olveh_gen import POWER_K, build_olveh, read_olveh, torque_curve


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB", tmp_path / "app.sqlite3")
    attempts.clear()
    with TestClient(app, headers={"X-Requested-With": "car-builder"}) as c:
        assert (
            c.post(
                "/api/auth/register",
                json={"username": "engineer", "password": "test-password-123"},
            ).status_code
            == 200
        )
        yield c


@pytest.fixture
def base():
    return json.loads(
        json.dumps(cars.load((cars.ROOT / "RFR27_Base_Car.OLVeh").read_bytes()))
    )


def test_base_roundtrip_and_flat_preview(client, base, tmp_path):
    assert client.get("/api/base").json()["base"] == base
    preview = client.post("/api/evaluate", json={"base": base}).json()
    assert preview["rpm"][-1] == base["rev_limit_rpm"]
    assert preview["peak_power"] == pytest.approx(220 * 5500 / POWER_K)
    assert preview["car"]["torque_rpm"] == [1]
    response = client.post("/api/export", json={"base": base})
    assert response.status_code == 200
    path = tmp_path / "test.OLVeh"
    path.write_bytes(response.content)
    assert (
        json.loads(
            json.dumps(asdict(read_olveh(path)) | {"guid": None, "modified": None})
        )
        == base
    )
    original = (cars.ROOT / "RFR27_Base_Car.OLVeh").read_bytes()
    assert (
        build_olveh(read_olveh(cars.ROOT / "RFR27_Base_Car.OLVeh", keep_outputs=True))
        == original
    )


def test_csv_preview_units_and_errors(client, base):
    response = client.post(
        "/api/csv/preview",
        json={
            "base": base,
            "text": "parameter,value,unit\nmass,440,lb\ndrivetrain_eff,95,%\n",
        },
    )
    result = response.json()
    assert result["errors"] == []
    assert result["overrides"]["mass"] == pytest.approx(199.5806428)
    assert result["overrides"]["drivetrain_eff"] == pytest.approx(0.95)
    assert base["mass"] == 298.7041834161
    result = client.post(
        "/api/csv/preview",
        json={
            "base": base,
            "text": "parameter,value,unit\nunknown,1,kg\nmass,1,mph\ncl,wat,\n",
        },
    ).json()
    assert len(result["errors"]) == 4  # three row errors and no valid parameters
    result = cars.csv_preview(
        "parameter,value,unit\nmass,200,kg\nmass,220,kg\n", base, {}
    )
    assert "Duplicate" in result["errors"][0]


def test_power_export_all_editable_values(client, base, tmp_path):
    overrides = {
        "name": "Track setup",
        "mass": "440 lb",
        "max_power_kw": "80 kW",
        "max_torque_nm": 240,
        "rev_limit_rpm": 6000,
        "gear_ratios": "2, 1",
        "shift_rpm": "5000, 6000",
        "cl": 3,
        "cd": 1.3,
        "drivetrain_eff": "95 %",
        "tire_radius": "8 in",
    }
    response = client.post("/api/export", json={"base": base, "overrides": overrides})
    assert response.status_code == 200, response.text
    path = tmp_path / "car.OLVeh"
    path.write_bytes(response.content)
    car = read_olveh(path)
    expected = cars.resolve(base, overrides)
    for key in cars.FIELDS.keys() - {"max_power_kw", "max_torque_nm"}:
        actual, value = getattr(car, key), getattr(expected, key)
        assert actual == (
            pytest.approx(value) if isinstance(value, (int, float, list)) else value
        )
    rpm, tq = torque_curve(6000, 80, 240)
    assert car.torque_rpm == pytest.approx(rpm)
    assert car.torque_nm == pytest.approx(tq)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cd": 0},
        {"mass": "NaN"},
        {"rev_limit_rpm": 1e20},
        {"drivetrain_eff": 2},
        {"gear_ratios": [0]},
        {"shift_rpm": [1, 2]},
        {"guid": "injected"},
        {"max_power_kw": -2},
    ],
)
def test_invalid_inputs_block_export(client, base, overrides):
    assert (
        client.post(
            "/api/export", json={"base": base, "overrides": overrides}
        ).status_code
        == 422
    )


def test_batch_manifest_and_atomic_failure(client, base):
    response = client.post(
        "/api/batch",
        json={
            "base": base,
            "text": "name,mass,max_power_kw\nLight,200 kg,80 kW\nPower,440 lb,90 kW\n",
        },
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert set(z.namelist()) == {"Light.OLVeh", "Power.OLVeh", "manifest.csv"}
        manifest = z.read("manifest.csv").decode()
        assert "199.5806428" in manifest and "max_power_kw" in manifest
    assert (
        client.post(
            "/api/batch", json={"base": base, "text": "mass\n200\ninvalid\n"}
        ).status_code
        == 422
    )


def test_preset_isolation_and_sessions(client, base):
    data = {"version": 1, "base": base, "csv": {"mass": 200}, "manual": {"mass": 220}}
    preset_id = client.post("/api/presets", json={"name": "Race", "data": data}).json()[
        "id"
    ]
    assert client.get(f"/api/presets/{preset_id}").json() == data
    client.post("/api/auth/logout", json={})
    assert client.get("/api/base").status_code == 401
    client.post(
        "/api/auth/register", json={"username": "other", "password": "another-pass-123"}
    )
    assert client.get(f"/api/presets/{preset_id}").status_code == 404
    assert client.get("/api/presets").json() == []
    client.delete(f"/api/presets/{preset_id}")
    client.post("/api/auth/logout", json={})
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "ENGINEER", "password": "test-password-123"},
        ).status_code
        == 200
    )
    assert len(client.get("/api/presets").json()) == 1
    old_token = client.cookies.get("session")
    assert (
        client.post(
            "/api/auth/password",
            json={"current": "test-password-123", "password": "changed-pass-123"},
        ).status_code
        == 200
    )
    with auth.connection() as db:
        assert (
            db.execute(
                "SELECT * FROM sessions WHERE token=?", (auth.token_hash(old_token),)
            ).fetchone()
            is None
        )
        stored = db.execute(
            "SELECT password FROM users WHERE username='engineer'"
        ).fetchone()[0]
        assert "changed-pass" not in stored


def test_upload_csrf_size_and_preset_validation(client, base):
    assert (
        client.post("/api/base", files={"file": ("bad.OLVeh", b"invalid")}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/base",
            files={
                "file": (
                    "base.OLVeh",
                    (cars.ROOT / "RFR27_Base_Car.OLVeh").read_bytes(),
                )
            },
        ).status_code
        == 200
    )
    with TestClient(app) as anonymous:
        assert (
            anonymous.post(
                "/api/auth/login",
                json={"username": "engineer", "password": "test-password-123"},
            ).status_code
            == 403
        )
    assert client.post("/api/export", json={"base": {}}).status_code == 422
    broken = {**base, "torque_nm": [float("inf")]}
    assert (
        client.post(
            "/api/presets/validate",
            content=json.dumps({"version": 1, "base": broken, "csv": {}, "manual": {}}),
        ).status_code
        == 422
    )
    assert (
        client.post("/api/presets/validate", content="x" * 3_000_001).status_code == 413
    )


def test_malformed_header_and_null_color_are_rejected(client, base):
    for broken in ({**base, "header_extra": ["100"]}, {**base, "color": None}):
        assert client.post("/api/export", json={"base": broken}).status_code == 422


def test_auth_throttle(client):
    for _ in range(19):
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "engineer", "password": "wrong-password"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "engineer", "password": "wrong-password"},
        ).status_code
        == 429
    )
