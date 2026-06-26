"""Authentication and user management — SQLite backend.

Tables:
  users    — id, username, password (werkzeug hash), role, status, session_token
  settings — key, value (string)

First registered user automatically becomes admin+active.
All subsequent registrations are viewer+pending until admin approves.

Single-session enforcement: each user can only have ONE active login.
Login generates a token stored in DB; when a second device logs in, the
token is overwritten, causing the first device to be force-logged-out on its
next request.
"""

import os
import secrets
import sqlite3
from functools import wraps

from flask import session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from robot_control.web_gui import i18n

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

def _lang():
    """Return language from Flask session, default zh."""
    try:
        return session.get("lang", "zh")
    except RuntimeError:
        return "zh"

ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE,
                password TEXT    NOT NULL,
                role     TEXT    NOT NULL DEFAULT 'viewer',
                status   TEXT    NOT NULL DEFAULT 'pending',
                session_token TEXT DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('camera_timeout_min', '5');
        """)
        # migrate existing DB — add session_token if it doesn't exist
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "session_token" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN session_token TEXT DEFAULT NULL")


# ── User CRUD ─────────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_name(username: str):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users():
    with get_db() as conn:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()


def create_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (success, error_message)."""
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        role   = "admin"  if count == 0 else "viewer"
        status = "active" if count == 0 else "pending"
        try:
            conn.execute(
                "INSERT INTO users (username, password, role, status) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), role, status),
            )
            return True, ""
        except sqlite3.IntegrityError:
            return False, i18n.t("username_exists", _lang())


def check_password(username: str, password: str):
    """Returns user Row if credentials valid and account active, else None."""
    user = get_user_by_name(username)
    if user and check_password_hash(user["password"], password):
        return user
    return None


def update_user(user_id: int, role: str = None, status: str = None) -> None:
    with get_db() as conn:
        if role is not None:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if status is not None:
            conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))


def delete_user(user_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── Session helpers ───────────────────────────────────────────────────────────

def _generate_session_token() -> str:
    return secrets.token_hex(32)


def set_session_token(user_id: int) -> str:
    """Create a new session token for user, invalidating all prior sessions."""
    token = _generate_session_token()
    with get_db() as conn:
        conn.execute("UPDATE users SET session_token = ? WHERE id = ?", (token, user_id))
    return token


def clear_session_token(user_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE users SET session_token = NULL WHERE id = ?", (user_id,))


def validate_session(user_id: int, token: str) -> bool:
    """Check whether the given session token is still the current one in DB."""
    user = get_user_by_id(user_id)
    if not user or not token:
        return False
    return user["session_token"] == token


def get_current_user():
    """Returns user Row for the logged-in session, or None.
    Also enforces single-session: if another device logged in with the same
    account, the current session's token will no longer match and is rejected.
    """
    user_id = session.get("user_id")
    token = session.get("session_token")
    if not user_id or not token:
        return None
    if not validate_session(user_id, token):
        session.clear()
        return None
    return get_user_by_id(user_id)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login", msg="relogin"))
        if user["status"] != "active":
            session.clear()
            return redirect(url_for("login", msg="pending"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or user["status"] != "active":
            return redirect(url_for("login"))
        if user["role"] != "admin":
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated
