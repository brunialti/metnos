"""policy.py — Capability Registry esteso e tabella autonomy x capability.

Promuove l'idea del Capability Registry v1.1 (gia' compilata in
`policy.html` v1.0) in un modulo Python che:

1. Dichiara le 13 capability canoniche con i loro attributi (critical,
   default_approval, target_kind).
2. Compila la tabella autonomy x capability: dato un livello di autonomia
   (ReadOnly/Supervised/Full) e una capability, ritorna l'esito
   ('allowed' | 'approval_required' | 'denied').
3. Registra grants persistenti per_target (es. "Roberto ha approvato
   write_files su ~/Documents/* fino a 2026-05-31"). SQLite single file.
4. Espone API per il pianificatore e per il Vaglio.

In v1.1 il modulo e' read-only dal pianificatore: i grants vengono creati
dal dispatcher di approval (cap. 5 fase 5, vedi `runtime/approval_registry.py`)
quando una pending request viene risolta come 'approved'.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import config as _C  # §7.11

DEFAULT_DB_PATH = _C.PATH_USER_STATE / "grants.db"

AutonomyLevel = Literal["ReadOnly", "Supervised", "Full"]
PolicyOutcome = Literal["allowed", "approval_required", "denied"]
TargetKind = Literal["path_glob", "host", "exact", "none"]
ApprovalMode = Literal["none", "per_target", "always"]


# --- Capability Registry v1.1 (13 voci canoniche) -------------------------

@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    critical: bool
    default_approval: ApprovalMode
    target_kind: TargetKind
    description: str


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    # File system
    "fs:read": CapabilitySpec(
        "fs:read", critical=False, default_approval="per_target",
        target_kind="path_glob",
        description="Leggere file dal filesystem locale entro path_glob dichiarati",
    ),
    "fs:write": CapabilitySpec(
        "fs:write", critical=True, default_approval="per_target",
        target_kind="path_glob",
        description="Scrivere/modificare file entro path_glob dichiarati (critico)",
    ),
    # Code/shell execution
    "code:exec": CapabilitySpec(
        "code:exec", critical=True, default_approval="always",
        target_kind="exact",
        description="Eseguire un comando shell di una whitelist (es. pkg manager)",
    ),
    # Network
    "network:http": CapabilitySpec(
        "network:http", critical=False, default_approval="per_target",
        target_kind="host",
        description="HTTP/HTTPS GET/POST verso host autorizzati",
    ),
    # LLM
    "llm:local": CapabilitySpec(
        "llm:local", critical=False, default_approval="none",
        target_kind="none",
        description="Chiamata LLM locale (Ollama/llama.cpp), costo zero",
    ),
    "llm:online": CapabilitySpec(
        "llm:online", critical=False, default_approval="per_target",
        target_kind="none",
        description="Chiamata LLM online (Anthropic/OpenAI/...), costo > 0",
    ),
    # Mail
    "mail:read": CapabilitySpec(
        "mail:read", critical=False, default_approval="per_target",
        target_kind="exact",
        description="Lettura messaggi IMAP da una mailbox autorizzata",
    ),
    "mail:send": CapabilitySpec(
        "mail:send", critical=True, default_approval="always",
        target_kind="exact",
        description="Invio SMTP a destinatari (irreversibile, alta posta in gioco)",
    ),
    # Channel
    "channel:in": CapabilitySpec(
        "channel:in", critical=False, default_approval="none",
        target_kind="exact",
        description="Ricezione messaggi da un canale (Telegram, CLI, voice)",
    ),
    "channel:out": CapabilitySpec(
        "channel:out", critical=False, default_approval="per_target",
        target_kind="exact",
        description="Invio messaggi a un canale specifico",
    ),
    # Time
    "time:read": CapabilitySpec(
        "time:read", critical=False, default_approval="none",
        target_kind="none",
        description="Lettura ora corrente e fusi orari",
    ),
    # Parse
    "parse:local": CapabilitySpec(
        "parse:local", critical=False, default_approval="none",
        target_kind="none",
        description="Parsing locale di formati noti (PDF, HTML, JSON, CSV)",
    ),
    # Calendar
    "calendar:read": CapabilitySpec(
        "calendar:read", critical=False, default_approval="per_target",
        target_kind="exact",
        description="Lettura eventi da un calendario autorizzato",
    ),
}


# --- Tabella autonomy x capability ----------------------------------------
#
# Esito per ogni combinazione (livello, capability):
#   'allowed'             : il pianificatore esegue senza chiedere
#   'approval_required'   : serve una conferma esplicita (per_target o always)
#   'denied'              : non si puo' eseguire (livello insufficiente)
#
# Convenzione:
#   - ReadOnly:  solo capability di sola lettura senza effetti laterali
#                visibili al mondo esterno. Mai write, mai send, mai exec.
#   - Supervised: tutto cio' che ReadOnly puo', piu' write/send/exec sotto
#                 approval per_target (un grant per ogni nuovo target).
#   - Full:      tutto, ma critical e default_approval='always' restano
#                always (es. mail:send richiede sempre conferma esplicita
#                per ogni destinatario, anche per Roberto stesso).

_TABLE: dict[tuple[AutonomyLevel, str], PolicyOutcome] = {}


def _init_table() -> None:
    """Costruisce la tabella secondo le regole canoniche."""
    for cap_name, spec in CAPABILITY_REGISTRY.items():
        # ReadOnly: solo capability con default_approval='none' e non critical
        if spec.critical or spec.default_approval != "none":
            # Eccezione: fs:read e mail:read sono "lettura" anche se per_target;
            # in ReadOnly servono ma chiedono approval per ogni nuovo target.
            if cap_name in ("fs:read", "mail:read", "calendar:read",
                            "channel:in", "time:read", "parse:local"):
                _TABLE[("ReadOnly", cap_name)] = "approval_required" if spec.default_approval != "none" else "allowed"
            else:
                _TABLE[("ReadOnly", cap_name)] = "denied"
        else:
            _TABLE[("ReadOnly", cap_name)] = "allowed"

        # Supervised: come ReadOnly + tutte le altre con approval_required
        if spec.default_approval == "none":
            _TABLE[("Supervised", cap_name)] = "allowed"
        else:
            _TABLE[("Supervised", cap_name)] = "approval_required"

        # Full: come Supervised, ma le critical+always restano always
        if spec.default_approval == "always":
            _TABLE[("Full", cap_name)] = "approval_required"
        else:
            _TABLE[("Full", cap_name)] = "allowed"


_init_table()


def is_allowed(
    autonomy_level: AutonomyLevel, capability: str,
) -> PolicyOutcome:
    """Esito policy senza considerare grants per_target persistenti.

    Per il check completo che include grants, usa `effective_outcome`.
    """
    return _TABLE.get((autonomy_level, capability), "denied")


# --- Grants persistenti per_target ----------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    capability      TEXT NOT NULL,
    target          TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    expires_at      TEXT,
    granted_by      TEXT,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_grants_lookup
    ON grants (channel, sender_id, capability, target);
CREATE INDEX IF NOT EXISTS idx_grants_active
    ON grants (revoked_at, expires_at);
"""


@dataclass
class Grant:
    id: int
    channel: str
    sender_id: str
    capability: str
    target: str
    granted_at: str
    expires_at: str | None = None
    granted_by: str | None = None
    revoked_at: str | None = None


from timefmt import now_iso_z as _now_iso


def _open_db(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path or os.environ.get("METNOS_GRANTS_DB") or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record_grant(
    *,
    channel: str,
    sender_id: str,
    capability: str,
    target: str,
    expires_at: str | None = None,
    granted_by: str | None = None,
    db_path: Path | None = None,
) -> Grant:
    """Registra una concessione per_target. Ritorna Grant con id assegnato."""
    if capability not in CAPABILITY_REGISTRY:
        raise ValueError(f"capability sconosciuta: {capability}")
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO grants
                   (channel, sender_id, capability, target, granted_at, expires_at, granted_by)
                   VALUES (?,?,?,?,?,?,?)""",
                (channel, sender_id, capability, target, now, expires_at, granted_by),
            )
            grant_id = cur.lastrowid
        row = conn.execute("SELECT * FROM grants WHERE id = ?", (grant_id,)).fetchone()
        return Grant(**dict(row))
    finally:
        conn.close()


def has_grant(
    *,
    channel: str,
    sender_id: str,
    capability: str,
    target: str,
    db_path: Path | None = None,
) -> bool:
    """Esiste un grant attivo per (channel, sender, capability, target)?"""
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            """SELECT 1 FROM grants
               WHERE channel=? AND sender_id=? AND capability=? AND target=?
                 AND revoked_at IS NULL
                 AND (expires_at IS NULL OR expires_at > ?)
               LIMIT 1""",
            (channel, sender_id, capability, target, now),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_grants(
    *,
    channel: str | None = None,
    sender_id: str | None = None,
    include_revoked: bool = False,
    db_path: Path | None = None,
) -> list[Grant]:
    where = []
    params: list = []
    if channel:
        where.append("channel = ?"); params.append(channel)
    if sender_id:
        where.append("sender_id = ?"); params.append(sender_id)
    if not include_revoked:
        where.append("revoked_at IS NULL")
    sql = "SELECT * FROM grants"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY granted_at DESC"
    conn = _open_db(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [Grant(**dict(r)) for r in rows]
    finally:
        conn.close()


def revoke_grant(grant_id: int, *, db_path: Path | None = None) -> bool:
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, grant_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


# --- API combinata --------------------------------------------------------

def effective_outcome(
    autonomy_level: AutonomyLevel,
    capability: str,
    *,
    channel: str | None = None,
    sender_id: str | None = None,
    target: str | None = None,
    db_path: Path | None = None,
) -> PolicyOutcome:
    """Esito completo: tabella + grants per_target.

    Se la tabella dice 'allowed', ritorna 'allowed' (i grants non occorrono).
    Se la tabella dice 'denied', ritorna 'denied' (i grants non possono
    elevare un livello sotto: per quello serve un upgrade del pairing).
    Se la tabella dice 'approval_required' E (channel, sender_id, target)
    sono forniti E un grant attivo esiste, ritorna 'allowed'. Altrimenti
    'approval_required'.
    """
    base = is_allowed(autonomy_level, capability)
    if base != "approval_required":
        return base
    if channel and sender_id and target:
        if has_grant(channel=channel, sender_id=sender_id,
                     capability=capability, target=target, db_path=db_path):
            return "allowed"
    return "approval_required"


# --- CLI ------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Metnos policy registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("registry", help="Stampa il Capability Registry")
    sub.add_parser("table", help="Stampa la tabella autonomy x capability")
    p_check = sub.add_parser("check", help="Esito policy per (level, capability)")
    p_check.add_argument("level", choices=["ReadOnly", "Supervised", "Full"])
    p_check.add_argument("capability")
    p_check.add_argument("--channel", default=None)
    p_check.add_argument("--sender", default=None)
    p_check.add_argument("--target", default=None)
    p_grants = sub.add_parser("grants", help="Lista grants attivi")
    p_grants.add_argument("--channel", default=None)
    p_grants.add_argument("--sender", default=None)
    p_grants.add_argument("--all", action="store_true")
    p_revoke = sub.add_parser("revoke", help="Revoca un grant")
    p_revoke.add_argument("grant_id", type=int)
    args = ap.parse_args(argv)

    if args.cmd == "registry":
        for spec in CAPABILITY_REGISTRY.values():
            print(json.dumps({
                "name": spec.name, "critical": spec.critical,
                "default_approval": spec.default_approval,
                "target_kind": spec.target_kind,
                "description": spec.description,
            }, ensure_ascii=False))
        return 0
    if args.cmd == "table":
        for level in ("ReadOnly", "Supervised", "Full"):
            row = {"level": level}
            for cap in CAPABILITY_REGISTRY:
                row[cap] = _TABLE.get((level, cap), "denied")
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.cmd == "check":
        out = effective_outcome(
            args.level, args.capability,
            channel=args.channel, sender_id=args.sender, target=args.target,
        )
        print(out)
        return 0
    if args.cmd == "grants":
        for g in list_grants(channel=args.channel, sender_id=args.sender,
                              include_revoked=args.all):
            print(json.dumps({
                "id": g.id, "channel": g.channel, "sender": g.sender_id,
                "capability": g.capability, "target": g.target,
                "granted_at": g.granted_at, "expires_at": g.expires_at,
                "revoked_at": g.revoked_at,
            }, ensure_ascii=False))
        return 0
    if args.cmd == "revoke":
        ok = revoke_grant(args.grant_id)
        print("revoked" if ok else "no-op")
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
