"""多用户账户存储（SQLite）。

设计要点：

1. 密码使用 PBKDF2-HMAC-SHA256 + 每用户独立盐，数据库不保存明文。
2. 用户名唯一且做格式校验，注册默认开放，可用
   ``DQUANT_ALLOW_REGISTRATION=0`` 关闭。
3. 数据库默认 ``data/users.db``，可用 ``DQUANT_USERS_DB`` 覆盖。
4. 交易所密钥不存这里：由 ``security.credentials.CredentialStore``
   按用户分别加密落盘。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_DB_LOCK = threading.Lock()
_USERNAME_RE = re.compile(r"^[\w.\-]{3,32}$", re.UNICODE)
_PBKDF2_ITERATIONS = 210_000


class UserStoreError(RuntimeError):
    """用户存储或校验失败。"""


def users_db_path() -> Path:
    raw = (os.getenv("DQUANT_USERS_DB") or "data/users.db").strip() or "data/users.db"
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def registration_allowed() -> bool:
    value = os.getenv("DQUANT_ALLOW_REGISTRATION", "1").strip() or "1"
    return value.lower() not in {"0", "false"}


def _connect() -> sqlite3.Connection:
    path = users_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_ts_ms INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    return conn


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def validate_username(username: str) -> str:
    value = (username or "").strip()
    if not _USERNAME_RE.match(value):
        raise UserStoreError("用户名需为 3–32 位字母、数字、下划线、点或短横线")
    return value


def create_user(username: str, password: str, role: str = "user") -> dict[str, Any]:
    name = validate_username(username)
    if len(password) < 8:
        raise UserStoreError("密码至少 8 位")
    if len(password) > 128:
        raise UserStoreError("密码过长")
    digest, salt = hash_password(password)
    now = int(time.time() * 1000)
    with _DB_LOCK:
        conn = _connect()
        try:
            try:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, salt, role, created_ts_ms)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, digest, salt, role, now),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise UserStoreError("该用户名已被注册") from exc
        finally:
            conn.close()
    return {"id": int(cur.lastrowid), "username": name, "role": role, "created_ts_ms": now}


def get_user(username: str) -> dict[str, Any] | None:
    try:
        name = validate_username(username)
    except UserStoreError:
        return None
    with _DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id, username, role, created_ts_ms FROM users"
                " WHERE username = ? AND is_active = 1",
                (name,),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "created_ts_ms": int(row["created_ts_ms"]),
    }


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    try:
        name = validate_username(username)
    except UserStoreError:
        return None
    with _DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id, username, password_hash, salt, role, created_ts_ms FROM users"
                " WHERE username = ? AND is_active = 1",
                (name,),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    digest, _ = hash_password(password, str(row["salt"]))
    if not secrets.compare_digest(digest, str(row["password_hash"])):
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "created_ts_ms": int(row["created_ts_ms"]),
    }


def user_count() -> int:
    with _DB_LOCK:
        conn = _connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
