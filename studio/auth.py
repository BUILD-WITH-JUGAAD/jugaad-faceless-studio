"""Accounts, sessions, and per-user API keys for JUGAAD studio."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request

STUDIO = Path(__file__).resolve().parent
DATA = STUDIO / "data"
DB_PATH = DATA / "jugaad.db"
SECRET_PATH = DATA / "secret"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.Lock()
_ROUNDS = 200_000

KEY_FIELDS = ("pexels", "pollinations", "epidemic")


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def using_postgres() -> bool:
    return bool(database_url())


def cookie_secure() -> bool:
    flag = (os.getenv("JUGAAD_COOKIE_SECURE") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return bool(os.getenv("RENDER"))


def generate_enabled() -> bool:
    on = (os.getenv("JUGAAD_ENABLE_GENERATE") or "").strip().lower()
    if on in ("1", "true", "yes"):
        return True
    off = (os.getenv("JUGAAD_DISABLE_GENERATE") or "").strip().lower()
    if off in ("1", "true", "yes"):
        return False
    if os.getenv("RENDER"):
        return False
    return True


def _ensure_secret() -> str:
    env = (os.getenv("JUGAAD_SECRET") or os.getenv("SECRET_KEY") or "").strip()
    if env:
        return env
    DATA.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    value = secrets.token_hex(32)
    SECRET_PATH.write_text(value)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return value


SECRET = _ensure_secret()


def _key_material() -> bytes:
    return hashlib.sha256(("jugaad-keys:" + SECRET).encode("utf-8")).digest()


def encrypt_value(text: str) -> str:
    raw = (text or "").encode("utf-8")
    if not raw:
        return ""
    key = _key_material()
    token = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    mac = hmac.new(key, token, hashlib.sha256).digest()
    return (mac + token).hex()


def decrypt_value(blob: str) -> str:
    if not blob:
        return ""
    try:
        data = bytes.fromhex(blob)
    except ValueError:
        return ""
    if len(data) < 33:
        return ""
    key = _key_material()
    mac, token = data[:32], data[32:]
    if not hmac.compare_digest(mac, hmac.new(key, token, hashlib.sha256).digest()):
        return ""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(token)).decode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _ROUNDS
    ).hex()
    return "pbkdf2$sha256${0}${1}${2}".format(_ROUNDS, salt, digest)


def verify_password(password: str, stored: str) -> bool:
    parts = (stored or "").split("$")
    if len(parts) != 5 or parts[0] != "pbkdf2":
        return False
    rounds = int(parts[2])
    salt = parts[3]
    expected = parts[4]
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
    ).hex()
    return hmac.compare_digest(digest, expected)


def _pg_dsn(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Neon (and most hosted Postgres) require TLS.
    if "sslmode=" not in url.lower():
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _open():
    url = database_url()
    if url:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        return psycopg2.connect(_pg_dsn(url), cursor_factory=RealDictCursor)
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _db(write: bool = False):
    conn = _open()
    try:
        yield conn
        if write:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _execute(conn, sql: str, params=()):
    if using_postgres():
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur
    return conn.execute(sql, params)


def init_db() -> None:
    with _lock:
        with _db(write=True) as conn:
            if using_postgres():
                _execute(
                    conn,
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created TEXT NOT NULL
                    )
                    """,
                )
                _execute(
                    conn,
                    """
                    CREATE TABLE IF NOT EXISTS user_keys (
                        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        pexels TEXT NOT NULL DEFAULT '',
                        pollinations TEXT NOT NULL DEFAULT '',
                        epidemic TEXT NOT NULL DEFAULT ''
                    )
                    """,
                )
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_keys (
                    user_id INTEGER PRIMARY KEY,
                    pexels TEXT NOT NULL DEFAULT '',
                    pollinations TEXT NOT NULL DEFAULT '',
                    epidemic TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )


init_db()


def _row_user(row) -> dict:
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "created": row["created"],
    }


def get_user(user_id: int):
    with _db() as conn:
        row = _execute(conn, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_user(row) if row else None


def find_user_by_email(email: str):
    with _db() as conn:
        return _execute(
            conn,
            "SELECT * FROM users WHERE email = ?",
            ((email or "").strip().lower(),),
        ).fetchone()


def register_user(email: str, password: str, name: str) -> dict:
    email = (email or "").strip().lower()
    name = (name or "").strip() or email.split("@")[0]
    password = password or ""
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Use a valid email address")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if len(name) > 48:
        name = name[:48]
    with _lock:
        with _db(write=True) as conn:
            if _execute(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                raise HTTPException(409, "That email already has an account")
            created = datetime.now().isoformat(timespec="seconds")
            hashed = hash_password(password)
            if using_postgres():
                row = _execute(
                    conn,
                    "INSERT INTO users (email, name, password_hash, created) VALUES (?, ?, ?, ?) RETURNING id",
                    (email, name, hashed, created),
                ).fetchone()
                user_id = int(row["id"])
            else:
                cur = _execute(
                    conn,
                    "INSERT INTO users (email, name, password_hash, created) VALUES (?, ?, ?, ?)",
                    (email, name, hashed, created),
                )
                user_id = int(cur.lastrowid)
            _execute(conn, "INSERT INTO user_keys (user_id) VALUES (?)", (user_id,))
        return get_user(user_id)


def authenticate(email: str, password: str) -> dict:
    row = find_user_by_email(email)
    if not row or not verify_password(password or "", row["password_hash"]):
        raise HTTPException(401, "Email or password is wrong")
    return _row_user(row)


def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        return get_user(int(user_id))
    except (TypeError, ValueError):
        return None


def login_session(request: Request, user: dict) -> None:
    request.session.clear()
    request.session["user_id"] = user["id"]
    request.session["email"] = user["email"]


def logout_session(request: Request) -> None:
    request.session.clear()


def _hint(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 6:
        return "saved" if text else ""
    return "ending " + text[-4:]


def get_keys(user_id: int) -> dict:
    with _db() as conn:
        row = _execute(
            conn, "SELECT * FROM user_keys WHERE user_id = ?", (user_id,)
        ).fetchone()
    out = {name: "" for name in KEY_FIELDS}
    if not row:
        return out
    for name in KEY_FIELDS:
        out[name] = decrypt_value(row[name] or "")
    return out


def keys_public(user_id: int) -> dict:
    keys = get_keys(user_id)
    public = {}
    for name in KEY_FIELDS:
        value = keys.get(name) or ""
        public[name] = {"set": bool(value), "hint": _hint(value) if value else ""}
    return public


def save_keys(user_id: int, updates: dict) -> dict:
    current = get_keys(user_id)
    for name in KEY_FIELDS:
        if name not in updates:
            continue
        raw = updates[name]
        if raw is None:
            current[name] = ""
            continue
        text = str(raw).strip().strip('"').strip("'")
        if text:
            current[name] = text
    with _lock:
        with _db(write=True) as conn:
            _execute(
                conn,
                """
                INSERT INTO user_keys (user_id, pexels, pollinations, epidemic)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id) DO UPDATE SET
                    pexels = excluded.pexels,
                    pollinations = excluded.pollinations,
                    epidemic = excluded.epidemic
                """,
                (
                    user_id,
                    encrypt_value(current["pexels"]),
                    encrypt_value(current["pollinations"]),
                    encrypt_value(current["epidemic"]),
                ),
            )
    return keys_public(user_id)


def apply_user_keys(user_id: int) -> dict:
    """Env overrides for this account. Omit blank fields so .env still applies."""
    keys = get_keys(user_id)
    out = {}
    mapping = (
        ("PEXELS_API_KEY", "pexels"),
        ("POLLINATIONS_API_KEY", "pollinations"),
        ("EPIDEMIC_API_KEY", "epidemic"),
    )
    for env_name, field in mapping:
        val = (keys.get(field) or "").strip().strip('"').strip("'")
        if val:
            out[env_name] = val
    return out
