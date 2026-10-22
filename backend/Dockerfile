FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock /app/backend/
RUN uv sync --project backend --frozen --no-dev
COPY olveh_gen.py RFR27_Base_Car.OLVeh /app/
COPY backend/app /app/backend/app
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
USER app
ENV PYTHONPATH=/app:/app/backend DATABASE_PATH=/data/app.sqlite3 UV_CACHE_DIR=/tmp/uv-cache
EXPOSE 8000
CMD ["uv", "run", "--project", "backend", "--no-sync", "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
