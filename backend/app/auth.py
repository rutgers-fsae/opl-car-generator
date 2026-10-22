import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB = Path(os.getenv("DATABASE_PATH", "backend/storage/app.sqlite3"))
SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
          expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS presets (
          id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL, data TEXT NOT NULL);
        """)


def password_hash(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864
    ).hex()
    return f"{salt}:{digest}"


def check_password(password: str, stored: str):
    return hmac.compare_digest(password_hash(password, stored.split(":")[0]), stored)


def token_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db, user_id: int):
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    db.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        (token_hash(token), user_id, time.time() + 604800),
    )
    return token
