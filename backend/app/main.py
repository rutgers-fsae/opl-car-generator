import csv
import io
import json
import sqlite3
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from olveh_gen import build_olveh
from pydantic import BaseModel, Field

from . import auth, cars


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.initialize()
    yield


app = FastAPI(title="RFR Car Builder", lifespan=lifespan)


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "car-builder":
            return JSONResponse({"detail": "Missing request header"}, status_code=403)
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 3_000_000:
            return JSONResponse({"detail": "Request exceeds 3 MB"}, status_code=413)
        chunks.append(chunk)
    # Replay the capped body for Starlette’s downstream request.
    request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
@app.exception_handler(csv.Error)
async def invalid_value(request: Request, exc: ValueError | csv.Error):
    return JSONResponse({"detail": str(exc)}, status_code=422)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=10, max_length=128)


class PasswordChange(BaseModel):
    current: str = Field(max_length=128)
    password: str = Field(min_length=10, max_length=128)


class Build(BaseModel):
    base: dict
    overrides: dict = Field(default_factory=dict)


class CSVBuild(Build):
    text: str = Field(max_length=1_000_000)


class Preset(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data: dict


# Small per-process login throttle; production runs one backend worker.
attempts: dict[str, list[float]] = {}
attempts_lock = threading.Lock()


def throttle(request: Request):
    with attempts_lock:
        now = time.monotonic()
        key = request.client.host if request.client else "unknown"
        for address in list(attempts):
            attempts[address] = [t for t in attempts[address] if t > now - 60]
            if not attempts[address]:
                del attempts[address]
        history = attempts.setdefault(key, [])
        if len(history) >= 20:
            raise HTTPException(429, "Too many attempts; try again in a minute")
        history.append(now)


def cookie(response: Response, token: str):
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=auth.SECURE,
        samesite="strict",
        max_age=604800,
    )


def user(request: Request):
    token = request.cookies.get("session", "")
    with auth.connection() as db:
        row = db.execute(
            "SELECT users.id, username FROM users JOIN sessions ON users.id=sessions.user_id WHERE token=? AND expires>?",
            (auth.token_hash(token), time.time()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Please sign in")
    return dict(row)


User = Annotated[dict, Depends(user)]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register")
def register(data: Credentials, request: Request, response: Response):
    throttle(request)
    with auth.connection() as db:
        try:
            result = db.execute(
                "INSERT INTO users(username,password) VALUES (?,?)",
                (data.username.lower(), auth.password_hash(data.password)),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username is already taken") from None
        token = auth.new_session(db, result.lastrowid)
    cookie(response, token)
    return {"username": data.username.lower()}


@app.post("/api/auth/login")
def login(data: Credentials, request: Request, response: Response):
    throttle(request)
    with auth.connection() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=?", (data.username.lower(),)
        ).fetchone()
        stored = row["password"] if row else auth.password_hash("dummy-password")
        if not auth.check_password(data.password, stored) or not row:
            raise HTTPException(401, "Incorrect username or password")
        token = auth.new_session(db, row["id"])
    cookie(response, token)
    return {"username": row["username"]}


@app.get("/api/auth/me")
def me(account: User):
    return account


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with auth.connection() as db:
        db.execute(
            "DELETE FROM sessions WHERE token=?",
            (auth.token_hash(request.cookies.get("session", "")),),
        )
    response.delete_cookie(
        "session", secure=auth.SECURE, httponly=True, samesite="strict"
    )
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(data: PasswordChange, account: User, response: Response):
    with auth.connection() as db:
        row = db.execute(
            "SELECT password FROM users WHERE id=?", (account["id"],)
        ).fetchone()
        if not auth.check_password(data.current, row["password"]):
            raise HTTPException(403, "Current password is incorrect")
        db.execute(
            "UPDATE users SET password=? WHERE id=?",
            (auth.password_hash(data.password), account["id"]),
        )
        db.execute("DELETE FROM sessions WHERE user_id=?", (account["id"],))
        token = auth.new_session(db, account["id"])
    cookie(response, token)
    return {"ok": True}


@app.get("/api/base")
def default_base(account: User):
    return {
        "base": cars.load((cars.ROOT / "RFR27_Base_Car.OLVeh").read_bytes()),
        "fields": cars.schema(),
    }


@app.post("/api/base")
async def upload_base(file: UploadFile, account: User):
    return {"base": cars.load(await file.read(2_000_001))}


@app.post("/api/evaluate")
def evaluate(data: Build, account: User):
    return cars.evaluate(data.base, data.overrides)


@app.post("/api/csv/preview")
def preview(data: CSVBuild, account: User):
    return cars.csv_preview(data.text, data.base, data.overrides)


def download(data: bytes, name: str, media="application/octet-stream"):
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/export")
def export(data: Build, account: User):
    car = cars.resolve(data.base, data.overrides)
    return download(build_olveh(car), cars.filename(car.name))


@app.post("/api/batch")
def batch(data: CSVBuild, account: User):
    reader = csv.DictReader(io.StringIO(data.text.lstrip("\ufeff")))
    headers = reader.fieldnames or []
    if (
        not headers
        or len(set(headers)) != len(headers)
        or any(k not in cars.FIELDS for k in headers)
    ):
        raise ValueError("Batch headers must be unique editable parameter names")
    archive = io.BytesIO()
    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(["file", "parameter", "base", "value", "unit"])
    count = 0
    total_bytes = 0
    used = set()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for i, row in enumerate(reader, 1):
            if i > 200:
                raise ValueError("Batch limit is 200 cars")
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Row {i}: malformed CSV")
            try:
                edits = data.overrides | {
                    k: cars.parse(k, v) for k, v in row.items() if v.strip()
                }
                if not row.get("name", "").strip():
                    edits["name"] = f"{data.base.get('name', 'Car')}_{i:03d}"
                car = cars.resolve(data.base, edits)
                name = cars.filename(car.name)
                if name.lower() in used:
                    name = cars.filename(f"{car.name}_{i:03d}")
                if name.lower() in used:
                    raise ValueError("Duplicate export name")
                used.add(name.lower())
                vehicle_bytes = build_olveh(car)
                total_bytes += len(vehicle_bytes)
                if total_bytes > 25_000_000:
                    raise ValueError("Batch exceeds 25 MB of vehicle data")
                z.writestr(name, vehicle_bytes)
                values = cars.evaluate(data.base, edits)["car"]
                for k in cars.FIELDS:
                    if values[k] != data.base.get(k):
                        writer.writerow(
                            [
                                name,
                                k,
                                json.dumps(data.base.get(k)),
                                json.dumps(values[k]),
                                cars.FIELDS[k][2],
                            ]
                        )
            except ValueError as exc:
                raise ValueError(f"Batch row {i}: {exc}") from None
            count += 1
        if not count:
            raise ValueError("Batch contains no cars")
        z.writestr("manifest.csv", manifest.getvalue())
    return download(archive.getvalue(), "cars.zip", "application/zip")


def checked_preset(data: dict):
    if data.get("version") != 1 or not isinstance(data.get("base"), dict):
        raise ValueError("Expected a version 1 preset with a base car")
    for layer in ("csv", "manual"):
        if not isinstance(data.get(layer), dict):
            raise ValueError(f"Preset needs a {layer} override layer")
    cars.resolve(data["base"], data["csv"])
    cars.resolve(data["base"], data["csv"] | data["manual"])
    return {k: data[k] for k in ("version", "base", "csv", "manual")}


@app.post("/api/presets/validate")
def validate_preset(data: dict, account: User):
    return checked_preset(data)


@app.get("/api/presets")
def presets(account: User):
    with auth.connection() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,name FROM presets WHERE user_id=? ORDER BY id DESC",
                (account["id"],),
            )
        ]


@app.post("/api/presets")
def save_preset(data: Preset, account: User):
    value = json.dumps(checked_preset(data.data))
    with auth.connection() as db:
        result = db.execute(
            "INSERT INTO presets(user_id,name,data) VALUES (?,?,?)",
            (account["id"], data.name, value),
        )
    return {"id": result.lastrowid}


@app.get("/api/presets/{preset_id}")
def get_preset(preset_id: int, account: User):
    with auth.connection() as db:
        row = db.execute(
            "SELECT data FROM presets WHERE id=? AND user_id=?",
            (preset_id, account["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Preset not found")
    return json.loads(row["data"])


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int, account: User):
    with auth.connection() as db:
        db.execute(
            "DELETE FROM presets WHERE id=? AND user_id=?", (preset_id, account["id"])
        )
    return {"ok": True}
