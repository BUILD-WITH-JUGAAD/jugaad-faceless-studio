"""Accounts, sessions, and per-user API keys for JUGAAD studio."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from fastapi import HTTPException, Request

STUDIO = Path(__file__).resolve().parent
DATA = STUDIO / "data"
DB_PATH = DATA / "jugaad.db"
SECRET_PATH = DATA / "secret"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.Lock()
_ROUNDS = 200_000

KEY_FIELDS = (
    "pexels",
    "pollinations",
    "epidemic",
    "openai",
    "google_client_id",
    "google_client_secret",
    "youtube",
)


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

# Paid / quota keys — never inherit process .env on a multi-user host.
ACCOUNT_KEY_ENV = (
    "PEXELS_API_KEY",
    "POLLINATIONS_API_KEY",
    "EPIDEMIC_API_KEY",
    "OPENAI_API_KEY",
)


def isolate_account_keys() -> bool:
    """Hosted Render: each account must bring its own keys."""
    return bool(os.getenv("RENDER"))


def register_open() -> bool:
    flag = (os.getenv("JUGAAD_DISABLE_REGISTER") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return False
    return True


def invite_secret() -> str:
    return (os.getenv("JUGAAD_INVITE") or "").strip()


def invite_required() -> bool:
    return bool(invite_secret())


def assert_can_register(invite: str = "") -> None:
    if not register_open():
        raise HTTPException(403, "New accounts are closed on this host.")
    needed = invite_secret()
    if not needed:
        return
    given = (invite or "").strip()
    if not given or len(given) != len(needed) or not hmac.compare_digest(needed, given):
        raise HTTPException(403, "That invite code is wrong.")


def _fernet() -> Fernet:
    raw = hashlib.sha256(("jugaad-fernet:" + SECRET).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _key_material() -> bytes:
    return hashlib.sha256(("jugaad-keys:" + SECRET).encode("utf-8")).digest()


def encrypt_value(text: str) -> str:
    raw = (text or "").encode("utf-8")
    if not raw:
        return ""
    return "fernet:" + _fernet().encrypt(raw).decode("ascii")


def _decrypt_xor(blob: str) -> str:
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
    try:
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(token)).decode("utf-8")
    except UnicodeDecodeError:
        return ""


def decrypt_value(blob: str) -> str:
    if not blob:
        return ""
    if blob.startswith("fernet:"):
        try:
            return _fernet().decrypt(blob[7:].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError):
            return ""
    return _decrypt_xor(blob)


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
                        epidemic TEXT NOT NULL DEFAULT '',
                        openai TEXT NOT NULL DEFAULT ''
                    )
                    """,
                )
                _ensure_key_columns(conn)
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
                    openai TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            _ensure_key_columns(conn)


def _ensure_key_columns(conn) -> None:
    for name in KEY_FIELDS:
        if using_postgres():
            _execute(
                conn,
                "ALTER TABLE user_keys ADD COLUMN IF NOT EXISTS {0} TEXT NOT NULL DEFAULT ''".format(name),
            )
            continue
        cols = [row[1] for row in conn.execute("PRAGMA table_info(user_keys)").fetchall()]
        if name not in cols:
            conn.execute(
                "ALTER TABLE user_keys ADD COLUMN {0} TEXT NOT NULL DEFAULT ''".format(name)
            )


init_db()


def first_user_id():
    with _db() as conn:
        row = _execute(conn, "SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
    if not row:
        return None
    return int(row["id"])


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
        try:
            out[name] = decrypt_value(row[name] or "")
        except (KeyError, IndexError):
            out[name] = ""
    return out


def keys_public(user_id: int) -> dict:
    keys = get_keys(user_id)
    public = {}
    for name in KEY_FIELDS:
        value = keys.get(name) or ""
        if name == "youtube":
            tokens = parse_youtube_tokens(value)
            channel = (tokens.get("channel") or "").strip()
            connected = bool(tokens.get("refresh_token"))
            public[name] = {
                "set": connected,
                "hint": channel or ("connected" if connected else ""),
                "channel": channel,
            }
            continue
        public[name] = {"set": bool(value), "hint": _hint(value) if value else ""}
    return public


def parse_youtube_tokens(raw: str) -> dict:
    try:
        data = json.loads(raw or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def youtube_tokens(user_id: int) -> dict:
    return parse_youtube_tokens(get_keys(user_id).get("youtube") or "")


def save_youtube_tokens(user_id: int, tokens: dict) -> dict:
    if not tokens:
        return save_keys(user_id, {"youtube": None})
    return save_keys(user_id, {"youtube": json.dumps(tokens)})


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
            cols = ", ".join(KEY_FIELDS)
            placeholders = ", ".join("?" for _ in KEY_FIELDS)
            assigned = ", ".join("{0} = excluded.{0}".format(name) for name in KEY_FIELDS)
            values = [user_id] + [encrypt_value(current[name]) for name in KEY_FIELDS]
            _execute(
                conn,
                "INSERT INTO user_keys (user_id, {0}) VALUES (?, {1}) "
                "ON CONFLICT (user_id) DO UPDATE SET {2}".format(cols, placeholders, assigned),
                tuple(values),
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
        ("OPENAI_API_KEY", "openai"),
        ("GOOGLE_CLIENT_ID", "google_client_id"),
        ("GOOGLE_CLIENT_SECRET", "google_client_secret"),
    )
    for env_name, field in mapping:
        val = (keys.get(field) or "").strip().strip('"').strip("'")
        if val:
            out[env_name] = val
    return out
