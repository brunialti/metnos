"""Clamp enum-aware di `backend_resolver.resolve_backend_arg` (6/7/2026).

Bug misurato: la risoluzione per-OBJECT ignorava la capacità del singolo TOOL
(l'enum dell'arg di backend nel manifest). Due rotture live:
  - share_files (gw-only, «local fs non ha ACL») è dell'object `files` il cui
    default è local → OGNI «condividi X con Y» senza marker drive riceveva
    client="local" → ERR_NOT_APPLICABLE;
  - find_events_empty aveva l'enum stantio ["local"] mentre l'object `events`
    default a gw (fix separato: enum allineato all'executor, che gw lo supporta).

Regola (§2.8/§7.3): il DEFAULT per-object è clampato all'enum del tool (niente
injection fuori-enum: il default dell'executor è il proprietario onesto); il
provider ESPLICITO nella query NON è clampato — se il tool non lo supporta,
l'executor risponde «client non applicabile», più onesto di un fallback muto.

NIENTE LLM (§7.9), niente creds: gli scenari usano schema passati inline.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

import backend_resolver as br  # noqa: E402


def _schema(*enum_vals):
    return {"type": "object",
            "properties": {"client": {"type": "string",
                                      "enum": list(enum_vals)}}}


GW_ONLY = _schema("google_workspace")
LOCAL_ONLY = _schema("local")
MULTI = _schema("local", "google_workspace")


def test_default_out_of_enum_is_not_injected():
    # share_files: object files → default local, ma il tool è gw-only.
    out = br.resolve_backend_arg("share_files", {},
                                 "condividi il file report.pdf con Mario",
                                 args_schema=GW_ONLY)
    assert "client" not in out  # niente injection: default executor (gw) onesto


def test_explicit_provider_is_never_clamped():
    # «su google drive» esplicito su un tool local-only: injection COMUNQUE →
    # l'executor risponde ERR_NOT_APPLICABLE (onesto), non un finto not-found.
    out = br.resolve_backend_arg("delete_files", {},
                                 "cancella il file budget.xlsx su google drive",
                                 args_schema=LOCAL_ONLY)
    assert out.get("client") == "google_workspace"


def test_default_in_enum_still_injected():
    out = br.resolve_backend_arg("delete_files", {},
                                 "cancella il file /tmp/vecchio.log",
                                 args_schema=LOCAL_ONLY)
    assert out.get("client") == "local"


def test_multi_provider_tool_unchanged_both_paths():
    plain = br.resolve_backend_arg("find_files", {}, "trova i pdf in /tmp",
                                   args_schema=MULTI)
    assert plain.get("client") == "local"
    drive = br.resolve_backend_arg("find_files", {},
                                   "cerca su google drive il budget",
                                   args_schema=MULTI)
    assert drive.get("client") == "google_workspace"


def test_no_schema_keeps_legacy_behavior():
    # Chiamanti senza schema: comportamento invariato (nessun clamp).
    out = br.resolve_backend_arg("share_files", {},
                                 "condividi il file report.pdf con Mario")
    assert out.get("client") == "local"


def test_explicit_already_set_is_respected():
    # Un client già esplicito e disponibile non viene toccato (invariante
    # pre-esistente: scope_sink clause-scoped non va scavalcato).
    out = br.resolve_backend_arg("find_files", {"client": "local"},
                                 "cerca su google drive il budget",
                                 args_schema=MULTI)
    assert out.get("client") == "local"


def test_explicit_provider_reports_only_user_marker():
    assert br.explicit_provider(
        "events", "create it on Google primary calendar") == "google_workspace"
    assert br.explicit_provider(
        "events", "in the fixture's Google primary calendar") == "google_workspace"
    assert br.explicit_provider("events", "create it in my calendar") is None
    assert br.explicit_provider("unknown", "on Google") is None


def test_dirs_object_registered_like_files():
    """dirs mono→multi (7/7/2026): default local §10.3, gw solo su marker
    esplicito — stesso modello di files, owner = OBJECT_BACKENDS["dirs"]."""
    assert br.object_of("create_dirs") == "dirs"
    plain = br.resolve_backend_arg("find_dirs", {},
                                   "che cartelle ho in /tmp?",
                                   args_schema=MULTI)
    assert plain.get("client") == "local"
    drive = br.resolve_backend_arg("create_dirs", {},
                                   "crea una cartella Fatture su google drive",
                                   args_schema=MULTI)
    assert drive.get("client") == "google_workspace"


def test_undeclared_backend_arg_not_injected():
    """Tool dell'object che NON dichiara l'arg di backend (caso reale
    list_dirs, pure-local): niente injection — scrivere `client` a chi lo
    ignora è junk (§7.3). Vale per default ED esplicito."""
    no_client = {"type": "object",
                 "properties": {"path": {"type": "string"}}}
    for q in ("che file ci sono in ~/note?",
              "elenca le cartelle su google drive"):
        out = br.resolve_backend_arg("list_dirs", {}, q, args_schema=no_client)
        assert "client" not in out, q
