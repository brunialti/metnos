"""Explicit filesystem grants, owned by an account and scoped to one host.

Grants never come from executor arguments. The administration UI is the writer;
the authenticated file broker is the reader. Revoked records remain as audit.
No grant changes an operating-system ACL or elevates the client identity.
"""
from __future__ import annotations

import hashlib
import hmac
import ntpath
import os
import posixpath
import re
import time
import uuid
from pathlib import Path

import devices

CAPABILITY = "filesystem_broker_v1"
OPERATIONS = frozenset({"list", "read", "write", "copy", "delete"})
ERROR_CODES = frozenset({
    "ERR_FS_PATH_INVALID", "ERR_FS_DEVICE_INVALID", "ERR_FS_GRANT_INVALID", "ERR_FS_GRANT_LIMIT",
    "ERR_FS_PERMISSION_REQUIRED", "ERR_FS_RESERVED_PATH", "ERR_FS_LINK_REFUSED", "ERR_FS_CONTRACT_INVALID",
    "ERR_FS_AUTHORITY_INVALID", "ERR_FS_INVOCATION_INVALID", "ERR_FS_REQUEST_EXPIRED", "ERR_FS_TRANSFER_INVALID",
    "ERR_FS_SIZE_LIMIT", "ERR_FS_DEST_EXISTS", "ERR_FS_HASH_MISMATCH", "ERR_FS_SOURCE_CHANGED",
    "ERR_FS_BINARY_ENCODING_REQUIRED", "ERR_FS_BROKER_UNSUPPORTED", "ERR_FS_IO",
})
SCHEMA = """
CREATE TABLE IF NOT EXISTS filesystem_grants (
 id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, device_id TEXT NOT NULL,
 path TEXT NOT NULL, path_style TEXT NOT NULL, kind TEXT NOT NULL,
 recursive INTEGER NOT NULL, can_read INTEGER NOT NULL, can_write INTEGER NOT NULL,
 can_overwrite INTEGER NOT NULL, expires_at INTEGER, revoked_at INTEGER,
 created_at INTEGER NOT NULL, created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS filesystem_grants_scope
 ON filesystem_grants(owner_user_id, device_id);
CREATE TABLE IF NOT EXISTS filesystem_grant_events (
 id INTEGER PRIMARY KEY, grant_id TEXT NOT NULL, action TEXT NOT NULL,
 actor TEXT NOT NULL, at INTEGER NOT NULL
);
"""


class PermissionError(ValueError):
    """Closed error codes; rendered through the instance's i18n catalogue."""


def normalize_path(value: object, style: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise PermissionError("ERR_FS_PATH_INVALID")
    if style == "windows":
        value = value.replace("/", "\\")
        if not re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\"):
            raise PermissionError("ERR_FS_PATH_INVALID")
        parts = value[3:].rstrip("\\").split("\\") if value[3:] else []
        for part in parts:
            if (not part or part in {".", ".."} or part[-1:] in {".", " "}
                    or any(c in part for c in ':*?<>|"')
                    or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)):
                raise PermissionError("ERR_FS_PATH_INVALID")
        return value[:1].upper() + ":\\" + "\\".join(parts)
    if style != "posix" or not value.startswith("/") or value.startswith("//"):
        raise PermissionError("ERR_FS_PATH_INVALID")
    parts = value[1:].rstrip("/").split("/") if value != "/" else []
    if any(p in {"", ".", ".."} for p in parts):
        raise PermissionError("ERR_FS_PATH_INVALID")
    return "/" + "/".join(parts)


def path_style(device_id: str, *, db_path: Path | None = None) -> str:
    if device_id == "server":
        return "posix"
    dev = devices.get_device(device_id, db_path=db_path)
    if dev is None or dev.revoked_at:
        raise PermissionError("ERR_FS_DEVICE_INVALID")
    return "windows" if dev.os_family == "windows" else "posix"


def owner_for_device(device_id: str, *, db_path: Path | None = None) -> str:
    dev = devices.get_device(device_id, db_path=db_path)
    if dev is None or dev.revoked_at:
        raise PermissionError("ERR_FS_DEVICE_INVALID")
    return dev.owner_user_id


def _open(db_path=None):
    conn = devices._open_db(db_path)
    conn.executescript(SCHEMA)
    return conn


def _validate_scope(owner: str, device_id: str, db_path=None) -> str:
    if not owner or (device_id != "server" and owner_for_device(device_id, db_path=db_path) != owner):
        raise PermissionError("ERR_FS_DEVICE_INVALID")
    return path_style(device_id, db_path=db_path)


def create_grant(*, owner: str, device_id: str, path: str, kind: str,
                 read: bool, write: bool, recursive: bool = False,
                 overwrite: bool = False, expires_at: int | None = None,
                 actor: str, db_path=None, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    style = _validate_scope(owner, device_id, db_path)
    path = normalize_path(path, style)
    flags = (read, write, recursive, overwrite)
    if (kind not in {"file", "folder"} or any(type(v) is not bool for v in flags)
            or not (read or write) or (recursive and kind != "folder")
            or (overwrite and not write) or not actor
            or (expires_at is not None and (type(expires_at) is not int or expires_at <= now))):
        raise PermissionError("ERR_FS_GRANT_INVALID")
    grant_id = uuid.uuid4().hex
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT count(*) FROM filesystem_grants WHERE owner_user_id=? "
                        "AND device_id=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                        (owner, device_id, now)).fetchone()[0] >= 128:
            raise PermissionError("ERR_FS_GRANT_LIMIT")
        conn.execute("INSERT INTO filesystem_grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (grant_id, owner, device_id, path, style, kind, int(recursive),
                      int(read), int(write), int(overwrite), expires_at, None, now, actor))
        conn.execute("INSERT INTO filesystem_grant_events(grant_id,action,actor,at) VALUES (?,?,?,?)",
                     (grant_id, "grant", actor, now))
        conn.commit()
    finally:
        conn.close()
    return grant_id


def list_grants(owner: str, device_id: str, *, db_path=None, active_only=False,
                now: int | None = None, conn=None) -> list[dict]:
    if conn is None:
        _validate_scope(owner, device_id, db_path)
    now = int(time.time()) if now is None else now
    own_connection = conn is None
    conn = _open(db_path) if own_connection else conn
    try:
        rows = conn.execute("SELECT * FROM filesystem_grants WHERE owner_user_id=? AND device_id=? "
                            "ORDER BY created_at DESC, id", (owner, device_id)).fetchall()
    finally:
        if own_connection:
            conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["state"] = "revoked" if item["revoked_at"] is not None else (
            "expired" if item["expires_at"] is not None and item["expires_at"] <= now else "active")
        if not active_only or item["state"] == "active":
            result.append(item)
    return result


def revoke_grant(grant_id: str, *, owner: str, device_id: str, actor: str,
                 db_path=None, now: int | None = None) -> bool:
    _validate_scope(owner, device_id, db_path)
    now = int(time.time()) if now is None else now
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("UPDATE filesystem_grants SET revoked_at=? WHERE id=? "
                               "AND owner_user_id=? AND device_id=? AND revoked_at IS NULL",
                               (now, grant_id, owner, device_id)).rowcount
        if changed:
            conn.execute("INSERT INTO filesystem_grant_events(grant_id,action,actor,at) VALUES (?,?,?,?)",
                         (grant_id, "revoke", actor, now))
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def permits(grants: list[dict], path: str, action: str, *, directory=False,
            recursive=False, overwrite=False, now: int | None = None) -> bool:
    if action not in {"read", "write"}:
        return False
    now = int(time.time()) if now is None else now
    for grant in grants:
        if (grant.get("revoked_at") is not None
                or (grant.get("expires_at") is not None and grant["expires_at"] <= now)
                or not grant.get("can_" + action) or (overwrite and not grant.get("can_overwrite"))):
            continue
        style = grant["path_style"]
        try:
            target, root = normalize_path(path, style), normalize_path(grant["path"], style)
        except PermissionError:
            continue
        sep, mod = ("\\", ntpath) if style == "windows" else ("/", posixpath)
        if style == "windows":
            # Fold ASCII only: Unicode casefold can collapse distinct Win32
            # names (e.g. ß/ss), granting a different file by mistake.
            table = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
            target, root = target.translate(table), root.translate(table)
        exact = target == root
        descendant = target.startswith(root.rstrip(sep) + sep)
        if grant["kind"] == "file":
            if exact and not directory and not recursive:
                return True
        elif grant["kind"] == "folder" and (exact or descendant):
            if recursive and not grant["recursive"]:
                continue
            if exact or grant["recursive"] or (not directory and mod.dirname(target) == root):
                return True
    return False


def _actors(owner):
    actors = [owner] if owner else []
    if owner:
        import users
        user = users.get_user(owner)
        if user and user.get("role") == "host" and owner != "host":
            actors.append("host")
    return actors


def server_excluded(owner, path, action):
    from workspace_policy import get_excludes
    from invocation_scope import _resolved, _within
    return any(_within(_resolved(path), hint) for actor in _actors(owner)
               for hint in get_excludes(actor, "fs:" + action))


def server_default_permits(owner, manifest, args, path, action):
    """Evaluate existing scopes without widening their glob/file semantics.

    Runtime arguments request paths; they cannot turn into default grants.
    Account workspace scopes and static signed scopes remain independent of
    the registry. Exclusions take precedence over either source of access.
    """
    if action not in {"read", "write"} or server_excluded(owner, path, action):
        return False
    from capabilities import effective_capabilities
    from invocation_scope import _resolved, _within
    from workspace_policy import get_scope
    hints = [h for actor in _actors(owner) for h in get_scope(actor, "fs:" + action)]
    caps = effective_capabilities(manifest.get("capabilities", []), manifest.get("args", {}), args or {})
    for cap in caps:
        if cap.get("name") == "fs:" + action:
            hints.extend(h for h in cap.get("hint", []) or []
                         if isinstance(h, str) and not h.startswith("arg:"))
    return any(_within(_resolved(path), hint) for hint in hints)


def require(owner: str, device_id: str, path: str, action: str, *, db_path=None,
            conn=None, manifest=None, args=None, **kwargs):
    if device_id == "server":
        if server_excluded(owner, path, action):
            raise PermissionError("ERR_FS_PERMISSION_REQUIRED")
        if server_default_permits(owner, manifest or {}, args or {}, path, action):
            return
    grants = list_grants(owner, device_id, db_path=db_path, active_only=True, conn=conn)
    if not permits(grants, path, action, **kwargs):
        raise PermissionError("ERR_FS_PERMISSION_REQUIRED")


def contract(manifest: dict, args: dict | None = None) -> str | None:
    value = manifest.get("filesystem")
    if value is None or value == {}:
        return None
    if (not isinstance(value, dict) or set(value) - {"operation", "protocol", "when"}
            or value.get("protocol") != CAPABILITY or value.get("operation") not in OPERATIONS):
        raise PermissionError("ERR_FS_CONTRACT_INVALID")
    caps = {v.get("name") for v in manifest.get("capabilities", []) if isinstance(v, dict)}
    needed = "fs:write" if value["operation"] in {"write", "copy", "delete"} else "fs:read"
    if needed not in caps:
        raise PermissionError("ERR_FS_CONTRACT_INVALID")
    if value["operation"] == "copy" and "fs:read" not in caps:
        raise PermissionError("ERR_FS_CONTRACT_INVALID")
    when = value.get("when")
    if when is not None and (not isinstance(when, dict) or set(when) != {"arg", "values"}
                             or not isinstance(when["arg"], str) or not isinstance(when["values"], list)
                             or not when["values"]):
        raise PermissionError("ERR_FS_CONTRACT_INVALID")
    if args is not None:
        if args.get("client", "local") != "local" or args.get("repo"):
            return None
        if when and not any(type(args.get(when["arg"])) is type(v) and args.get(when["arg"]) == v for v in when["values"]):
            return None
    return value["operation"]


def form_token(admin_key: str, device_id: str) -> str:
    if not admin_key:
        return ""
    return hmac.new(admin_key.encode(), ("metnos-filesystem-grants-v1:" + device_id).encode(), hashlib.sha256).hexdigest()


def valid_form_token(token: object, admin_key: str, device_id: str) -> bool:
    expected = form_token(admin_key, device_id)
    return bool(expected and isinstance(token, str) and token.isascii() and hmac.compare_digest(token, expected))
