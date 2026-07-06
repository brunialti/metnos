"""Politica di marcatura `runtime_resolved` dei config-args (6/7/2026).

Chiude il residuo FASE 3 della provenienza args (spec
`internal/design/spec_args_provenance_architecture.md`, esito PROV.3): ogni
arg di configurazione per-NOME (client/account/provider) è o MARCATO
`runtime_resolved` (il proposer non lo mostra all'LLM, il runtime lo
inietta/il default vale) o ESENTE perché intent-bearing (l'LLM o un guard
clause-scoped è scrittore legittimo).

La TABELLA sotto è la fonte unica della politica: un tool nuovo/rinominato
con un config-arg DEVE essere aggiunto qui deliberatamente (il test fallisce
altrimenti). Un flip di enum (es. delete_files che diventa multi-provider)
fa scattare l'invariante generica → la marcatura va ridiscussa, non
ereditata.

Ermetico: legge i manifest.toml con tomllib, nessun loader/firma/LLM (§7.9).
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

import arg_provenance as ap  # noqa: E402

_EXECUTORS = _RT.parent / "executors"
_CONFIG_NAMES = ("client", "account", "provider")

# ── POLITICA (fonte unica) ──────────────────────────────────────────────────
# MARCATI: config pura — mono-provider (enum 1), plumbing plugin (no enum),
# o multi-provider con owner runtime COMPLETO (events: backend_resolver).
EXPECTED_MARKED = frozenset({
    # dirs (mono local)
    "create_dirs.client", "delete_dirs.client", "find_dirs.client",
    # contacts (mono gw)
    "find_contacts.client", "read_contacts.client",
    # events/calendars (owner completo = backend_resolver whole-query)
    "read_events.client", "delete_events.client", "create_events.client",
    "find_events_empty.client",
    "create_calendars.client", "delete_calendars.client",
    # urls/session (httpx|playwright: implementazione, playwright stub;
    # il JS-render opt-in è il flag js_render, non client)
    "find_urls.client", "read_urls_html.client", "read_urls_pdf.client",
    "login_session.client",
    # mail plumbing (no enum o mono gw)
    "read_messages.client", "send_messages.client",
    "reply_messages.client", "set_messages.client",
    # files MONO-provider (la cautela multi-provider non li copre):
    # delete/move = solo local (nessun handler gw nell'executor);
    # share + trio _doc = solo gw (Doc/ACL sono entita' Drive by design)
    "delete_files.client", "move_files.client", "share_files.client",
    "create_files_doc.client", "read_files_doc.client",
    "write_files_doc.client",
})

# ESENTI (intent-bearing, restano visibili all'LLM):
# - client dei tool files MULTI-provider: clause-derived, li scrivono
#   scope_sink/align_provider dal testo della clausola (PROV.3);
# - move_messages.client (metnos|gmail): nessun owner runtime, l'LLM è
#   l'unico scrittore del ramo Gmail;
# - account mail: nominato/lista/'all' — mail_account_resolver delega i casi
#   2+ account al planner; send-from è intento.
EXPECTED_VISIBLE = frozenset({
    # files multi-provider REALI (l'executor ha entrambi i backend;
    # write_files via lazy-gw — enum allineato 6/7/2026)
    "find_files.client", "read_files.client", "write_files.client",
    "read_files_spreadsheet.client", "write_files_spreadsheet.client",
    "create_files_spreadsheet.client",
    "move_messages.client",
    "read_messages.account", "send_messages.account", "move_messages.account",
})


def _iter_config_args():
    """(tool, arg, spec, schema) per ogni config-arg dichiarato nei manifest."""
    for mf in sorted(_EXECUTORS.glob("*/manifest.toml")):
        try:
            data = tomllib.loads(mf.read_text(encoding="utf-8"))
        except Exception as ex:  # noqa: BLE001
            raise AssertionError(f"manifest non parsabile: {mf}: {ex}")
        tool = data.get("name") or mf.parent.name
        schema = data.get("args") or {}
        props = schema.get("properties") or {}
        for arg in _CONFIG_NAMES:
            spec = props.get(arg)
            if isinstance(spec, dict):
                yield tool, arg, spec, schema


def test_every_config_arg_is_in_the_policy_table():
    known = EXPECTED_MARKED | EXPECTED_VISIBLE
    seen = {f"{t}.{a}" for t, a, _s, _sc in _iter_config_args()}
    new = seen - known
    assert not new, (
        f"config-arg NUOVI fuori politica: {sorted(new)}. Decidi: marcarli "
        f"runtime_resolved (config pura) o esentarli (intent-bearing) e "
        f"aggiorna la tabella QUI + la spec provenienza.")
    gone = known - seen
    assert not gone, (
        f"la politica cita config-arg che non esistono più: {sorted(gone)} "
        f"(tool rimosso/rinominato?): pulisci la tabella.")


def test_marked_and_visible_match_the_manifests():
    for tool, arg, spec, _schema in _iter_config_args():
        key = f"{tool}.{arg}"
        marked = bool(spec.get("runtime_resolved"))
        if key in EXPECTED_MARKED:
            assert marked, (f"{key}: la politica lo vuole runtime_resolved "
                            f"ma il manifest non lo marca")
        elif key in EXPECTED_VISIBLE:
            assert not marked, (
                f"{key}: è intent-bearing (l'LLM/guard clause-scoped è uno "
                f"scrittore legittimo) ma è marcato runtime_resolved — "
                f"il ramo non-default diventa irraggiungibile")


def test_multi_provider_files_client_is_never_marked():
    """Invariante generica anti-drift: un flip di enum (mono→multi) su un tool
    dell'object `files` DEVE far ridiscutere la marcatura, non ereditarla."""
    for tool, arg, spec, _schema in _iter_config_args():
        if arg != "client":
            continue
        enum = spec.get("enum") if isinstance(spec.get("enum"), list) else []
        if len(enum) >= 2 and "files" in tool.split("_"):
            assert not spec.get("runtime_resolved"), (
                f"{tool}.client: multi-provider files è clause-derived "
                f"(PROV.3) — MAI runtime_resolved")


def test_marked_args_are_never_required():
    """Un arg marcato non può essere `required`: l'LLM non lo vede e non lo
    può fornire — il piano fallirebbe la validazione per costruzione."""
    for tool, arg, spec, schema in _iter_config_args():
        if spec.get("runtime_resolved"):
            req = schema.get("required") or []
            assert arg not in req, f"{tool}.{arg}: runtime_resolved MA required"


def test_provenance_report_shows_zero_unmarked():
    """Stato-zero raggiunto (marcatura 6/7/2026): da qui in poi
    n_unmarked_config > 0 = drift reale da sanare, non backlog."""
    catalog = []
    for mf in sorted(_EXECUTORS.glob("*/manifest.toml")):
        data = tomllib.loads(mf.read_text(encoding="utf-8"))
        catalog.append(SimpleNamespace(name=data.get("name") or mf.parent.name,
                                       args_schema=data.get("args") or {}))
    rep = ap.provenance_report(catalog)
    assert rep["n_unmarked_config"] == 0, rep["unmarked_config"]


def test_exemptions_match_is_intent_bearing_config():
    """La tabella EXPECTED_VISIBLE e le regole di `is_intent_bearing_config`
    devono coincidere sui manifest reali (due formulazioni, una politica)."""
    for tool, arg, spec, _schema in _iter_config_args():
        key = f"{tool}.{arg}"
        exempt = ap.is_intent_bearing_config(tool, arg, spec)
        assert exempt == (key in EXPECTED_VISIBLE), (
            f"{key}: is_intent_bearing_config={exempt} ma la tabella dice "
            f"{'VISIBLE' if key in EXPECTED_VISIBLE else 'MARKED'}")
