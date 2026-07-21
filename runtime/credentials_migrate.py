"""runtime/credentials_migrate.py — migrazione one-shot dei segreti
nel store cifrato `runtime.credentials` (ADR 0089 + ADR 0131, 14/5/2026).

Sorgenti pre-migrazione (centralizzate da qui):
  - `~/.config/metnos/mail.env`              → smtp_metnos_system + smtp_metnos_secondary
  - `~/.config/account_personal/mail.env`             → smtp_account_personal (legacy example.com)
  - `~/.config/metnos/mail/<account>.env`    → smtp_<account> (dynamic accounts)
  - `~/.config/metnos/credentials.env`       → anthropic_api_key + openai_api_key
                                                + telegram_bot_token
                                                + telegram_chat_id_host
  - `~/.config/metnos/anthropic.env`         → anthropic_api_key (fallback)
  - `~/.config/metnos/openai.env`            → openai_api_key (fallback)
  - `~/.config/metnos/google_maps.env`       → google_maps_api_key

Classificazione user/system (direttiva 14/5/2026):
  - `*_api_key` (anthropic/openai/google_maps): system (billing).
  - `telegram_bot_token`: system (bot e' un servizio Metnos).
  - `telegram_chat_id_host`: utente host (100000001 = Roberto).
  - `smtp_*`: misto (system_account vs user_account; mapping caso-per-caso).

NB: il file OAuth `~/.local/share/metnos/skills/google-workspace/google_token.json`
resta nello skill scope (gestito da `google_api.py`, importarlo richiede
fork dello skill — out-of-scope ADR 0131).

Uso:
  python3 -m credentials_migrate                  # migra tutto
  python3 -m credentials_migrate --dry-run        # mostra cosa farebbe
  python3 -m credentials_migrate --account NAME   # solo un account
  python3 -m credentials_migrate --apis           # solo API keys + telegram
  python3 -m credentials_migrate --all            # SMTP + APIs + telegram

Determinismo §7.9: lookup tabellare + file I/O, nessun LLM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as _C  # noqa: E402 — §7.11
import credentials as cr  # noqa: E402
import mail_client as mc  # noqa: E402


_LEGACY_ACCOUNTS = ("metnos_system", "metnos_secondary", "account_personal")


def _legacy_creds(account: str) -> dict | None:
    """Legge usando la logica pre-store di `mail_client._account_creds`."""
    # Bypass dello store: temporaneamente mascheriamo
    # `_load_from_credentials_store` per andare al ramo env.
    orig = mc._load_from_credentials_store
    try:
        mc._load_from_credentials_store = lambda _a: None
        try:
            return mc._account_creds(account)
        except ValueError:
            return None
    finally:
        mc._load_from_credentials_store = orig


def _dynamic_accounts_dir() -> Path:
    return _C.PATH_USER_CONFIG / "mail"


def _discover_dynamic_accounts() -> list[str]:
    d = _dynamic_accounts_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.stem for p in d.iterdir()
        if p.is_file() and p.suffix == ".env"
    )


def migrate_one(account: str, *, dry_run: bool = False) -> dict:
    """Migra `account` dallo store env al credentials store cifrato.
    Ritorna `{ok, action, domain, account, reason?}`.

    action ∈ {"created", "skip_already_present", "skip_no_legacy"}.
    """
    domain = f"smtp_{account}"
    existing = cr.load(domain)
    if isinstance(existing, dict) and existing.get("user"):
        return {"ok": True, "action": "skip_already_present",
                "account": account, "domain": domain}
    legacy = _legacy_creds(account)
    if not legacy or not legacy.get("user") or not legacy.get("password"):
        return {"ok": False, "action": "skip_no_legacy",
                "account": account, "domain": domain,
                "reason": "no legacy file or missing user/password"}
    payload = {
        "user":       legacy["user"],
        "password":   legacy["password"],
        "imap_host":  legacy.get("imap_host"),
        "imap_port":  legacy.get("imap_port"),
        "smtp_host":  legacy.get("smtp_host"),
        "smtp_port":  legacy.get("smtp_port"),
        "verify_tls": legacy.get("verify_tls", True),
    }
    if dry_run:
        return {"ok": True, "action": "would_create",
                "account": account, "domain": domain,
                "user": legacy["user"]}
    cr.store(domain, payload)
    return {"ok": True, "action": "created", "account": account,
            "domain": domain, "user": legacy["user"]}


def migrate_all_smtp(*, dry_run: bool = False) -> list[dict]:
    discovered = list(_LEGACY_ACCOUNTS) + _discover_dynamic_accounts()
    seen, accounts = set(), []
    for a in discovered:
        if a in seen:
            continue
        seen.add(a)
        accounts.append(a)
    return [migrate_one(a, dry_run=dry_run) for a in accounts]


# --- API keys + Telegram bot (ADR 0131 extended, 14/5/2026) ----------------

def _read_env_var_from_files(name: str, paths: list[Path]) -> str | None:
    """Legge `name=value` dal primo file disponibile fra `paths`."""
    for p in paths:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                # Anti-whitespace per chiavi sk-ant-/sk- (line-wrap copy-paste)
                if (v.startswith("sk-ant-") or v.startswith("sk-")) and any(c.isspace() for c in v):
                    v = "".join(v.split())
                if v:
                    return v
    return None


_API_KEY_SOURCES: dict[str, tuple[list[Path], str]] = {
    # domain        : ([files in order], env_var_name)
    "anthropic_api_key": (
        [_C.PATH_USER_CONFIG / "credentials.env",
         _C.PATH_USER_CONFIG / "anthropic.env"],
        "ANTHROPIC_API_KEY",
    ),
    "openai_api_key": (
        [_C.PATH_USER_CONFIG / "credentials.env",
         _C.PATH_USER_CONFIG / "openai.env"],
        "OPENAI_API_KEY",
    ),
    "google_maps_api_key": (
        [_C.PATH_USER_CONFIG / "google_maps.env",
         _C.PATH_USER_CONFIG / "credentials.env"],
        "GOOGLE_MAPS_API_KEY",
    ),
    "telegram_bot_token": (
        [_C.PATH_USER_CONFIG / "credentials.env"],
        "TELEGRAM_BOT_TOKEN",
    ),
    "telegram_chat_id_host": (
        [_C.PATH_USER_CONFIG / "credentials.env"],
        "TELEGRAM_CHAT_ID",
    ),
}


def migrate_one_api(domain: str, *, dry_run: bool = False) -> dict:
    src = _API_KEY_SOURCES.get(domain)
    if src is None:
        return {"ok": False, "action": "skip_unknown_domain",
                "domain": domain, "reason": "domain non in tabella"}
    paths, env_name = src
    existing = cr.load(domain)
    if isinstance(existing, dict) and existing.get("value"):
        return {"ok": True, "action": "skip_already_present", "domain": domain}
    value = _read_env_var_from_files(env_name, paths)
    if not value:
        return {"ok": False, "action": "skip_no_source",
                "domain": domain,
                "reason": f"{env_name} non trovato in {[str(p) for p in paths]}"}
    payload = {"value": value, "_env_var": env_name}
    if dry_run:
        masked = (value[:8] + "..." + value[-4:]) if len(value) > 14 else "***"
        return {"ok": True, "action": "would_create",
                "domain": domain, "preview": masked}
    cr.store(domain, payload)
    masked = (value[:8] + "..." + value[-4:]) if len(value) > 14 else "***"
    return {"ok": True, "action": "created",
            "domain": domain, "preview": masked}


def migrate_all_apis(*, dry_run: bool = False) -> list[dict]:
    return [migrate_one_api(d, dry_run=dry_run)
            for d in _API_KEY_SOURCES.keys()]


def migrate_all(*, dry_run: bool = False) -> list[dict]:
    """Migra TUTTO: SMTP + API keys + Telegram. Compat: prima invocazione."""
    return migrate_all_smtp(dry_run=dry_run) + migrate_all_apis(dry_run=dry_run)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Migra credenziali → store cifrato Fernet (ADR 0131).",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Mostra cosa migrerebbe senza scrivere.")
    p.add_argument("--account", help="Migra un solo account SMTP "
                   "(es. metnos_system).")
    p.add_argument("--apis", action="store_true",
                   help="Migra solo API keys + Telegram bot.")
    p.add_argument("--all", action="store_true",
                   help="Migra TUTTO: SMTP + API keys + Telegram.")
    args = p.parse_args()
    if args.account:
        results = [migrate_one(args.account, dry_run=args.dry_run)]
    elif args.apis:
        results = migrate_all_apis(dry_run=args.dry_run)
    elif args.all:
        results = migrate_all(dry_run=args.dry_run)
    else:
        # Default storico: solo SMTP (back-compat ADR 0131 v1).
        results = migrate_all_smtp(dry_run=args.dry_run)
    rc = 0
    for r in results:
        marker = "OK" if r.get("ok") else "FAIL"
        domain = r.get("domain", "?")
        line = f"[{marker}] {r['action']:24s} domain={domain:30s}"
        if r.get("account"):
            line += f"  account={r['account']}"
        if r.get("user"):
            line += f"  user={r['user']}"
        if r.get("preview"):
            line += f"  preview={r['preview']}"
        if r.get("reason"):
            line += f"  reason={r['reason']}"
        print(line)
        if not r.get("ok") and r.get("action") not in (
                "skip_no_legacy", "skip_no_source"):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
