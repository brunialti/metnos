# SPDX-License-Identifier: MIT
"""The credentials a user can name, as shown in the user's profile.

One row per stored credential (sites, mailboxes and services) and per site
named without credentials. Sites and mailboxes carry the user's name and
synonyms; other kinds are listed only. No secret value is read into a row:
site and service rows come from the administrative listing, mailbox rows
from the mailbox list and the mailbox login (an address, not a secret).
"""
from __future__ import annotations

from logging_setup import get_logger

import credential_admin
import credentials
import sites_origin
import users

log = get_logger(__name__)

_MAIL_PREFIXES = ("smtp_", "imap_")


def _mail_accounts() -> list[str]:
    try:
        import mail_client
        return list(mail_client.list_known_accounts())
    except Exception as exc:  # noqa: BLE001 -- a broken mailbox stays visible elsewhere
        log.warning("mailbox listing failed: %r", exc)
        return []


def _mail_address(account: str) -> str:
    try:
        import mail_client
        return str(mail_client._account_creds(account).get("user") or "")
    except Exception:  # noqa: BLE001
        return ""


def _names_text(entry: dict | None) -> tuple[str, str]:
    if not entry:
        return "", ""
    return entry["name"], ", ".join(entry["synonyms"])


def profile_rows(user_id: str) -> list[dict]:
    """Rows of the profile's Credentials section, sites first, then mail."""
    named = users.list_invocation_names(user_id)
    named_sites = [e for e in named if e["kind"] == "site"]
    named_mail = {e["target"]: e for e in named if e["kind"] == "mail"}
    used_sites: set[str] = set()
    rows: list[dict] = []
    for record in credential_admin.list_credentials():
        binding = record["binding"]
        user_text = next((f["text"] for f in record["fields"]), "")
        public_fields = record["fields"]
        if binding.startswith(_MAIL_PREFIXES) or record["kind"] == "mail":
            continue  # listed once below, by mailbox
        if record["kind"] == "site":
            entry = next((e for e in named_sites
                          if e["target"] not in used_sites
                          and sites_origin.same_site_origin(
                              sites_origin.origin_of_url(e["target"]), binding)),
                         None)
            if entry:
                used_sites.add(entry["target"])
            name, synonyms = _names_text(entry)
            target = entry["target"] if entry else f"https://{binding}/"
            rows.append({
                "kind": "site", "kind_label_key": record["kind_label_key"],
                "binding": binding, "user_text": user_text,
                "target": target, "public_fields": public_fields,
                "nameable": True, "has_credential": True,
                "name": name or users.propose_invocation_name("site", target, named),
                "synonyms": synonyms, "names_saved": bool(entry),
            })
        else:
            rows.append({
                "kind": record["kind"], "kind_label_key": record["kind_label_key"],
                "binding": binding, "user_text": user_text, "target": "",
                "public_fields": public_fields,
                "nameable": False, "has_credential": True,
                "name": "", "synonyms": "",
            })
    for entry in named_sites:
        if entry["target"] in used_sites:
            continue
        name, synonyms = _names_text(entry)
        rows.append({
            "kind": "site", "kind_label_key": "MSG_CREDENTIAL_KIND_OPTION_SITE",
            "binding": "", "user_text": "", "target": entry["target"],
            "nameable": True, "has_credential": False,
            "name": name, "synonyms": synonyms, "names_saved": True,
        })
    for account in _mail_accounts():
        entry = named_mail.pop(account, None)
        name, synonyms = _names_text(entry)
        rows.append({
            "kind": "mail", "kind_label_key": "MSG_CREDENTIAL_KIND_OPTION_MAIL",
            "binding": account, "user_text": _mail_address(account),
            "target": account, "nameable": True, "has_credential": True,
            "name": name or users.propose_invocation_name("mail", account, named),
            "synonyms": synonyms, "names_saved": bool(entry),
        })
    # A mailbox named earlier and since removed keeps its names visible so
    # they can be deleted; a request using them reaches the mail executor,
    # which reports the unknown mailbox.
    for account, entry in sorted(named_mail.items()):
        name, synonyms = _names_text(entry)
        rows.append({
            "kind": "mail", "kind_label_key": "MSG_CREDENTIAL_KIND_OPTION_MAIL",
            "binding": account, "user_text": "", "target": account,
            "nameable": True, "has_credential": False,
            "name": name, "synonyms": synonyms, "names_saved": True,
        })
    credentials.assert_no_secrets_in_return(rows)
    return rows


def mail_account_known(account: str) -> bool:
    return account in _mail_accounts()
