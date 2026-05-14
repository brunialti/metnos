"""runtime/credentials_migrate.py — migrazione one-shot dei segreti
nel store cifrato `runtime.credentials` (ADR 0089 + ADR 0131, 14/5/2026).

Sorgenti pre-migrazione (centralizzate da qui):
  - `~/.config/metnos/mail.env`              → smtp_metnos_system + smtp_metnos_roberto
  - `~/.config/mykleos/mail.env`             → smtp_mykleos (legacy register.it)
  - `~/.config/metnos/mail/<account>.env`    → smtp_<account> (dynamic accounts)

NB: il file OAuth `~/.local/share/metnos/skills/google-workspace/google_token.json`
resta nello skill scope. Il refresh token e' gestito da `google_api.py`
direttamente; importarlo nel credentials store richiederebbe di
modificare lo skill agentskills.io (out-of-scope, vedi ADR 0131
sezione «Open»).

Uso:
  python3 -m credentials_migrate                  # migra tutto
  python3 -m credentials_migrate --dry-run        # mostra cosa farebbe
  python3 -m credentials_migrate --account NAME   # solo un account

Determinismo §7.9: lookup tabellare + file I/O, nessun LLM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import credentials as cr  # noqa: E402
import mail_client as mc  # noqa: E402


_LEGACY_ACCOUNTS = ("metnos_system", "metnos_roberto", "mykleos")


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
    return Path.home() / ".config" / "metnos" / "mail"


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


def migrate_all(*, dry_run: bool = False) -> list[dict]:
    discovered = list(_LEGACY_ACCOUNTS) + _discover_dynamic_accounts()
    seen, accounts = set(), []
    for a in discovered:
        if a in seen:
            continue
        seen.add(a)
        accounts.append(a)
    return [migrate_one(a, dry_run=dry_run) for a in accounts]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Migra mail.env → credentials store cifrato (ADR 0131).",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Mostra cosa migrerebbe senza scrivere.")
    p.add_argument("--account", help="Migra un solo account (es. metnos_system).")
    args = p.parse_args()
    results = ([migrate_one(args.account, dry_run=args.dry_run)]
                if args.account
                else migrate_all(dry_run=args.dry_run))
    rc = 0
    for r in results:
        marker = "OK" if r.get("ok") else "FAIL"
        line = (f"[{marker}] {r['action']:24s} domain={r['domain']:30s} "
                f"account={r['account']}")
        if r.get("user"):
            line += f"  user={r['user']}"
        if r.get("reason"):
            line += f"  reason={r['reason']}"
        print(line)
        if not r.get("ok") and r.get("action") != "skip_no_legacy":
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
