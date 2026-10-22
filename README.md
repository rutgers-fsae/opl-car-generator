# RFR · OptimumLap Car Builder

A React/TypeScript editor and FastAPI API for `.OLVeh` files. The supplied `olveh_gen.py` owns the vehicle format, checksum, and torque-curve generation. The bundled **RFR27_Base_Car.OLVeh** supplies the initial values; upload another `.OLVeh` to replace the base.

## Run locally

With Docker:

```sh
docker compose up --build
```

Open http://localhost:5173 and create an account. Development cookies work over HTTP. Accounts and saved presets live in the `car-data` Docker volume.

Or install Node.js 22.12+ and uv, then run from the repository root:

```sh
uv sync --locked --project backend
npm --prefix frontend ci
uv run --project backend python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1
# In another terminal:
npm --prefix frontend run dev
```

The Python virtual environment is `backend/.venv`. Local account data lives under `backend/storage` (ignored by Git).

## Deploy on the VM

```sh
docker compose -f docker-compose.prod.yml up --build -d
```

Point the existing HTTPS reverse proxy on the other machine at `http://DOCKER_VM_PRIVATE_IP:8081`. Serve the app at the hostname root, not a subpath. The frontend's Nginx serves the SPA and proxies `/api/` to the backend on the private Compose network. The backend has no public port in production. Production sessions require HTTPS (`Secure`, `HttpOnly`, `SameSite=Strict` cookies).

The frontend binds to all IPv4 interfaces on port 8081 by default, avoiding DAQ's port 8080. Set `APP_BIND_ADDRESS` to the Docker VM's private interface IP to restrict the binding, and `APP_PORT` to change the port. Restrict inbound access to that port to the reverse proxy machine. Keep TLS termination at the outer proxy. Login/registration are limited to 20 attempts per minute per directly connected address in one backend process; configure the outer proxy's per-client rate limits for a larger deployment.

Only accounts, hashed sessions, and user-owned presets are persisted. Uploads are processed temporarily and removed; generated files download directly. Back up the `car-data` volume. To create a consistent SQLite backup while the service is running:

```sh
docker compose -f docker-compose.prod.yml exec backend python -c "import sqlite3; s=sqlite3.connect('/data/app.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d); d.close(); s.close()"
```

Copy `/data/backup.sqlite3` out of the backend container to your backup location. `docker compose down` preserves data; `down -v` deletes the volume.

No email recovery is included. An administrator with VM access can reset a user's password interactively; this also revokes their sessions:

```sh
docker compose -f docker-compose.prod.yml exec backend uv run --project backend --no-sync python -m app.admin USERNAME
```

## Editor behavior

- Values layer as **base → CSV → manual**. Reset a manual field to its CSV/base value; reset a CSV field to base. Reset all clears both override layers. Loading another base clears overrides.
- Peak power and torque are inferred from the base curve. Its original points are preserved until max power, max torque, or max RPM is edited. A one-point/short curve is extended flat to the rev limit **for the preview only**.
- Power generation uses the supplied script's constant-torque/constant-power approximation. The UI shows actual peak power and warns if the requested power cannot be reached before the rev limit.
- Positive Cl means downforce. Overall ratio is each gearbox ratio × final drive. Top speed is the kinematic estimate from rev limit, tire radius, and the tallest gear, not a drag/power-limited prediction.
- Zero mass, friction above 3, and kinematic speed below 40 km/h are warnings. Invalid numbers, invalid gearing, or invalid efficiency fractions block export. Stored OptimumLap simulation results are cleared on export for recalculation.
- Each export gets a fresh GUID and timestamp. The vehicle name becomes the sanitized download filename.

## CSV and units

Single-car CSV headers are exactly `parameter,value,unit`. Review the before/after table before applying. CSV imports update the existing CSV layer; manual changes retain priority. Unknown names, duplicate parameters, bad units, and bad values are listed as errors. See [examples/parameters.csv](examples/parameters.csv).

Batch CSV headers are editable parameter names, with **one car per row**. Each row overrides the current editor configuration; blank cells retain current values. Units can be included in cells, such as `440 lb`. Arrays use comma-separated values inside a quoted CSV cell, e.g. `"2.8, 1.9, 1.3"`. The optional `name` column supplies names; otherwise the base name plus a row number is used. The ZIP includes a manifest of differences from the selected base. Any invalid row blocks the whole batch; maximum 200 cars and 25 MB of uncompressed vehicle data. See [examples/batch.csv](examples/batch.csv).

Use the field names returned by `/api/base` or shown in the CSV preview, including `mass`, `cl`, `cd`, `max_power_kw`, `max_torque_nm`, `rev_limit_rpm`, `gear_ratios`, `shift_rpm`, `drivetrain_eff`, `tire_radius`, and `energy_density`.

| Native unit | Accepted alternatives |
| --- | --- |
| kg | lb |
| m | mm, in |
| m² | m2, ft², ft2 |
| kW | W, hp (mechanical) |
| Nm | N·m, lb-ft |
| rpm | rad/s |
| s | ms |
| kg/m³ | kg/m3, lb/ft³ |
| J/kg | kJ/kg, Wh/kg |
| ratio (0–1) | % |

Manual numeric fields also accept a unit suffix. Blank CSV units use the field's native unit. Lists accept their native unit or a shared CSV unit. A blank manual `shift_rpm` follows the rev limit. Computed values and file metadata are managed by the generator.

Presets retain the base car and both override layers. Save private presets to your account, or download/import versioned JSON files. Templates and generated files are not otherwise retained on the server.

## API and checks

All car/preset endpoints require a session. Mutating endpoints require `X-Requested-With: car-builder`; use same-origin requests through the frontend proxy.

- `/api/auth/{register,login,logout,password,me}`: accounts and sessions.
- `GET/POST /api/base`: default template/schema or uploaded base file.
- `POST /api/evaluate`, `/api/export`: JSON `{base, overrides}` to evaluate or download.
- `POST /api/csv/preview`, `/api/batch`: the same payload plus CSV `text`.
- `/api/presets`: private preset listing, creation, loading, deletion; `/api/presets/validate` validates imported JSON.
- `GET /health`: process health.

```sh
npm run check
npm test
```

Checks run TypeScript/build, ESLint, Ruff, and ty. API tests cover units, validation, binary round-trips, batch manifests, authentication, and preset isolation. The original generator is imported as supplied rather than reformatted or retyped.

Final acceptance requires opening an exported `.OLVeh` in OptimumLap and comparing its values; the automated round-trip checks cannot replace that application check.
