"""本机使用者。馆藏共用，浏览口味按使用者分开。"""
from __future__ import annotations

import contextvars
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_PROFILE_ID = "local-default"
PROFILE_COOKIE = "peach_profile"
PROFILE_LIMIT = 8
_PROFILE_ID = re.compile(r"local-[a-z0-9]+")
_ACTIVE = contextvars.ContextVar("peach_active_profile", default=DEFAULT_PROFILE_ID)


def bind_active(raw: str | None):
    text = str(raw or "").strip()
    return _ACTIVE.set(text if _PROFILE_ID.fullmatch(text) else DEFAULT_PROFILE_ID)


def reset_active(token) -> None:
    _ACTIVE.reset(token)


def requested_profile_id() -> str:
    return _ACTIVE.get()


def resolve_profile(connection, profile_id: str | None = None) -> str:
    """未知或未登记的身份回到默认使用者。缺表时也回到默认，旧测试库不必先建 profile。"""
    chosen = requested_profile_id() if profile_id is None else str(profile_id or "")
    if not _PROFILE_ID.fullmatch(chosen):
        return DEFAULT_PROFILE_ID
    if chosen == DEFAULT_PROFILE_ID:
        return DEFAULT_PROFILE_ID
    try:
        row = connection.execute("SELECT id FROM profile WHERE id=?", (chosen,)).fetchone()
    except sqlite3.Error:
        return DEFAULT_PROFILE_ID
    return str(row[0]) if row else DEFAULT_PROFILE_ID


def store_for(base: Path, profile_id: str) -> Path:
    """默认使用者继续用原来的 history.sqlite，其余各自一个文件。"""
    if profile_id == DEFAULT_PROFILE_ID or not _PROFILE_ID.fullmatch(profile_id):
        return base
    return base.with_name(f"history-{profile_id}.sqlite")


def manifest_for(contract, profile_id: str) -> Path:
    if profile_id == DEFAULT_PROFILE_ID:
        manifest = getattr(contract, "taste_history_manifest", None)
        if manifest is not None:
            return manifest
    return store_for(contract.taste_history_store, profile_id).with_name(f"manifest-{profile_id}.json")


def preference_cache_path(store: Path, filename: str) -> Path:
    """默认库的缓存文件名保持不变，其他使用者不共用这一份。"""
    if store.name == "history.sqlite":
        return store.parent / filename
    return store.with_name(f"{store.stem}.{filename}")


def viewer_files(contract, connection=None):
    def resolve(conn):
        profile_id = resolve_profile(conn)
        return (profile_id, store_for(contract.taste_history_store, profile_id),
                manifest_for(contract, profile_id))
    if connection is not None:
        return resolve(connection)
    with contract.read_connection() as conn:
        return resolve(conn)


def list_profiles(connection) -> dict:
    active = resolve_profile(connection)
    try:
        rows = connection.execute(
            "SELECT id, name FROM profile WHERE user_id='local' ORDER BY is_default DESC, name"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if not rows:
        rows = [(DEFAULT_PROFILE_ID, "Default")]
    return {
        "viewers": [{"id": row[0], "name": row[1], "active": row[0] == active} for row in rows],
        "limit": PROFILE_LIMIT,
    }


def create_profile(connection, name: str) -> dict:
    label = " ".join(str(name or "").split())
    if not label or len(label) > 40:
        raise ValueError("使用者名称需为 1–40 个字符")
    count = connection.execute("SELECT count(*) FROM profile WHERE user_id='local'").fetchone()[0]
    if int(count) >= PROFILE_LIMIT:
        raise ValueError("最多 8 个使用者")
    if connection.execute("SELECT 1 FROM profile WHERE user_id='local' AND name=?", (label,)).fetchone():
        raise ValueError("已经有同名使用者")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection.execute(
        "INSERT OR IGNORE INTO app_user(id,display_name,created_at) VALUES('local','Local user',?)",
        (stamp,))
    profile_id = "local-" + secrets.token_hex(8)
    connection.execute(
        "INSERT INTO profile(id,user_id,name,is_default,settings_json,created_at,updated_at) "
        "VALUES(?,?,?,0,'{}',?,?)",
        (profile_id, "local", label, stamp, stamp))
    return {"id": profile_id, "name": label, "active": True}


def q_profiles(contract, _args=None) -> dict:
    with contract.read_connection() as connection:
        return list_profiles(connection)


def w_profile_create(contract, body) -> dict:
    with contract.write_transaction() as connection:
        return create_profile(connection, (body or {}).get("name"))


def w_profile_activate(contract, body) -> dict:
    profile_id = str((body or {}).get("id") or "")
    with contract.read_connection() as connection:
        resolved = resolve_profile(connection, profile_id)
    if resolved != profile_id:
        raise ValueError("没有这个使用者")
    name = ""
    with contract.read_connection() as connection:
        try:
            row = connection.execute("SELECT name FROM profile WHERE id=?", (resolved,)).fetchone()
        except sqlite3.Error:
            row = None
        name = str(row[0]) if row else "Default"
    return {"id": resolved, "name": name, "active": True}


def read_settings(connection):
    """读取默认 profile 的设置对象。界面习惯仍跟馆藏走，不随使用者切换。"""
    row = connection.execute("SELECT settings_json FROM profile WHERE id=?", (DEFAULT_PROFILE_ID,)).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row[0] or "{}")
    except (ValueError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}
