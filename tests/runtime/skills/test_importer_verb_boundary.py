"""Test ADR 0128 — importer verb-as-data boundary.

Verifica che la tabella contestuale `skill_vocab_map.json::contextual`
+ helper `runtime/skill_translator.py::resolve_name/resolve_context` +
verifier `runtime/importer_verb_verify.py::check_plan` lavorino come una
sola unita' deterministica.

Determinismo §7.9: nessun LLM, solo lookup tabellare.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


from skill_translator import (  # noqa: E402
    resolve_name,
    resolve_context,
    resolve_reverse_pattern,
    SkillTranslateError,
)
from importer_verb_verify import (  # noqa: E402
    classify_mismatch,
    check_plan,
)
from vocab import ACTIONS, DESTRUCTIVE_VERBS, ACTION_MAPPING  # noqa: E402


# ---------------------------------------------------------------------------
# 1. share verb registrato nel vocab
# ---------------------------------------------------------------------------


def test_share_verb_in_actions():
    """ACTIONS ha share come 23° verbo (post-ADR 0128)."""
    assert "share" in ACTIONS
    # 23 → 26: +open/login/act (dominio sites, RATIFICATO D-A 10/7/2026).
    # 26 → 27: +install (dominio packages, ADR 0209, 16/8/2026).
    # 27 → 28: +run (avvio di software gia' installato, ADR 0218).
    assert len(ACTIONS) == 28


def test_run_is_bilingual_mutating_and_distinct_from_open():
    """Avviare software non deve collassare sull'apertura di un sito."""
    assert "run" in DESTRUCTIVE_VERBS
    run = ACTION_MAPPING["run"]
    assert "avvia" in run["it"]
    assert "run" in run["en"]
    assert "NON open" in run["boundary"]["it"]
    assert "NOT open" in run["boundary"]["en"]


def test_share_in_destructive_verbs():
    """share = grant ACL remoto, ha side-effect → DESTRUCTIVE."""
    assert "share" in DESTRUCTIVE_VERBS


def test_share_in_action_mapping_with_boundary():
    """ACTION_MAPPING ha l'entry share con boundary IT+EN."""
    assert "share" in ACTION_MAPPING
    share = ACTION_MAPPING["share"]
    assert "it" in share and len(share["it"]) >= 3
    assert "en" in share and len(share["en"]) >= 3
    assert "OUTBOUND CONSENT" in share["boundary"]


# ---------------------------------------------------------------------------
# 2. resolve_name usa la tabella contextual
# ---------------------------------------------------------------------------


def test_contextual_drive_share_resolves_to_share_files():
    """drive.share -> share_files (verb=share). ADR 0128 cluster H8."""
    name, verb, obj, qual = resolve_name("drive", "share")
    assert verb == "share"
    assert obj == "files"
    assert name == "share_files"


def test_contextual_docs_append_resolves_to_write_files_text():
    """docs.append -> write_files_text (body modify, NOT change). H5."""
    name, verb, obj, qual = resolve_name("docs", "append")
    assert verb == "write"
    assert obj == "files"
    assert qual == "text"
    assert name == "write_files_text"


def test_contextual_sheets_update_resolves_to_set_files_xlsx():
    """sheets.update -> set_files_xlsx (state upsert, NOT change). H6."""
    name, verb, obj, qual = resolve_name("sheets", "update")
    assert verb == "set"
    assert obj == "files"
    assert qual == "xlsx"
    assert name == "set_files_xlsx"


def test_contextual_gmail_modify_resolves_to_set_messages():
    """gmail.modify -> set_messages (label state update, NOT change). H7."""
    name, verb, obj, qual = resolve_name("gmail", "modify")
    assert verb == "set"
    assert obj == "messages"
    assert name == "set_messages"


def test_contextual_gmail_reply_resolves_to_threaded_send():
    """gmail.reply resta send ma ha una modalità canonica distinta da send."""
    name, verb, obj, qual = resolve_name("gmail", "reply")
    assert (verb, obj, qual) == ("send", "messages", "thread")
    assert name == "send_messages_thread"


def test_contextual_gmail_labels_resolves_to_label_listing():
    """gmail.labels enumera le etichette senza introdurre verbi provider."""
    name, verb, obj, qual = resolve_name("gmail", "labels")
    assert (verb, obj, qual) == ("list", "messages", "labels")
    assert name == "list_messages_labels"


def test_contextual_docs_create_resolves_to_create_files_text():
    """docs.create -> create_files_text (terminal, NOT set upsert). H9."""
    name, verb, obj, qual = resolve_name("docs", "create")
    assert verb == "create"
    assert obj == "files"
    assert qual == "text"
    assert name == "create_files_text"


def test_contextual_sheets_create_resolves_to_create_files_xlsx():
    """sheets.create -> create_files_xlsx (terminal, NOT set upsert). H10."""
    name, verb, obj, qual = resolve_name("sheets", "create")
    assert verb == "create"
    assert obj == "files"
    assert qual == "xlsx"
    assert name == "create_files_xlsx"


def test_contextual_calendar_create_resolves_to_create_events():
    """calendar.create -> create_events (terminal). Estensione H10'."""
    name, verb, obj, qual = resolve_name("calendar", "create")
    assert verb == "create"
    assert obj == "events"
    assert name == "create_events"


def test_contextual_gmail_get_resolves_to_read_messages():
    """gmail.get MESSAGE_ID = fetch content -> read_messages. H1 (ricontrollo)."""
    name, verb, obj, qual = resolve_name("gmail", "get")
    assert verb == "read"
    assert obj == "messages"


def test_contextual_drive_download_resolves_to_read_files():
    """drive.download FILE_ID = fetch content -> read_files. H2 (ricontrollo)."""
    name, verb, obj, qual = resolve_name("drive", "download")
    assert verb == "read"


def test_contextual_drive_get_resolves_to_get_files():
    """drive.get legge metadata; download resta la lettura del contenuto."""
    name, verb, obj, qual = resolve_name("drive", "get")
    assert (verb, obj, qual) == ("get", "files", "")
    assert name == "get_files"


def test_contextual_drive_create_folder_resolves_to_create_dirs():
    """drive.create-folder = create + object_override dirs."""
    name, verb, obj, qual = resolve_name("drive", "create-folder")
    assert verb == "create"
    assert obj == "dirs"
    assert name == "create_dirs"


# ---------------------------------------------------------------------------
# 3. resolve_context expose target_kind/side_effect/verb
# ---------------------------------------------------------------------------


def test_resolve_context_for_drive_share():
    ctx = resolve_context("drive", "share")
    assert ctx["verb"] == "share"
    assert ctx["target_kind"] == "share_access"
    assert ctx["side_effect"] == "acl_grant"


def test_resolve_context_for_gmail_modify():
    ctx = resolve_context("gmail", "modify")
    assert ctx["target_kind"] == "state_labels"
    assert ctx["side_effect"] == "state_update"
    assert ctx["verb"] == "set"


def test_resolve_context_unknown_returns_empty():
    """Domain/action non in contextual -> dict vuoto (no raise)."""
    assert resolve_context("calendar", "ufoize") == {}


# ---------------------------------------------------------------------------
# 4. Fallback legacy quando contextual manca
# ---------------------------------------------------------------------------


def test_legacy_fallback_when_no_contextual_cell(tmp_path):
    """Se la cella `contextual[<dom>:<act>]` manca ma `actions[<act>]` c'e',
    il fallback flat resta. Lo dimostriamo via vocab_map iniettato."""
    vm = {
        "actions": {"list": {"verb": "read"}},
        "domains": {"calendar": "events"},
        "contextual": {},  # vuoto
    }
    name, verb, obj, qual = resolve_name("calendar", "list", vocab_map=vm)
    assert verb == "read"
    assert obj == "events"


def test_legacy_fallback_unknown_action_raises():
    """Senza contextual e senza actions[action] -> SkillTranslateError."""
    vm = {
        "actions": {},
        "domains": {"drive": "files"},
        "contextual": {},
    }
    with pytest.raises(SkillTranslateError):
        resolve_name("drive", "share", vocab_map=vm)


# ---------------------------------------------------------------------------
# 5. resolve_reverse_pattern per share
# ---------------------------------------------------------------------------


def test_reverse_pattern_share_files():
    """share_files -> revoke ACL via delete_files_permissions_by_id."""
    reversible, pat = resolve_reverse_pattern("share", "files")
    assert reversible is True
    assert pat == "delete_files_permissions_by_id"


def test_reverse_pattern_share_events():
    """share_events -> revoke ACL via delete_events_permissions_by_id."""
    reversible, pat = resolve_reverse_pattern("share", "events")
    assert reversible is True
    assert pat == "delete_events_permissions_by_id"


# ---------------------------------------------------------------------------
# 6. classify_mismatch riconosce le 5 famiglie di drift
# ---------------------------------------------------------------------------


def test_classify_mismatch_get_drift():
    assert classify_mismatch("get", "read") == "get_drift"


def test_classify_mismatch_change_overload_write():
    """change scelto dove l'expected e' write (docs append, body modify)."""
    assert classify_mismatch("change", "write") == "change_overload"


def test_classify_mismatch_change_overload_set():
    """change scelto dove l'expected e' set (gmail modify, labels)."""
    assert classify_mismatch("change", "set") == "change_overload"


def test_classify_mismatch_set_overload_create():
    """set scelto dove l'expected e' create (terminal)."""
    assert classify_mismatch("set", "create") == "set_overload"


def test_classify_mismatch_share_drift_from_set():
    """set scelto dove l'expected e' share (drive.share)."""
    assert classify_mismatch("set", "share") == "share_drift"


def test_classify_mismatch_aligned_returns_empty():
    assert classify_mismatch("read", "read") == ""


def test_classify_mismatch_unknown_pair():
    assert classify_mismatch("compress", "render") == "unknown"


# ---------------------------------------------------------------------------
# 7. check_plan integration (verifier deterministico)
# ---------------------------------------------------------------------------


def _plan_shim(verb: str, dom: str = "", act: str = "") -> object:
    class P:
        pass
    p = P()
    p.verb = verb
    p.skill_domain = dom
    p.skill_action = act
    return p


def test_check_plan_aligned_for_drive_share_with_share_verb():
    plan = _plan_shim("share", "drive", "share")
    v = check_plan(plan)
    assert v.aligned is True
    assert v.chosen_verb == "share"
    assert v.expected_verb == "share"


def test_check_plan_aligned_for_calendar_list_with_read():
    plan = _plan_shim("read", "calendar", "list")
    v = check_plan(plan)
    assert v.aligned is True


def test_check_plan_share_drift_when_set_chosen_for_drive_share():
    """set scelto dove drive.share richiede share. ADR 0128 cluster H8."""
    plan = _plan_shim("set", "drive", "share")
    v = check_plan(plan)
    assert v.aligned is False
    assert v.mismatch_kind == "share_drift"
    assert v.expected_verb == "share"


def test_check_plan_change_overload_when_change_for_docs_append():
    """change scelto dove docs.append richiede write."""
    plan = _plan_shim("change", "docs", "append")
    v = check_plan(plan)
    assert v.aligned is False
    assert v.mismatch_kind == "change_overload"


def test_check_plan_set_overload_when_set_for_docs_create():
    """set scelto dove docs.create richiede create."""
    plan = _plan_shim("set", "docs", "create")
    v = check_plan(plan)
    assert v.aligned is False
    assert v.mismatch_kind == "set_overload"


def test_check_plan_get_drift_when_get_for_gmail_get():
    """get scelto dove gmail.get richiede read (fetch content)."""
    plan = _plan_shim("get", "gmail", "get")
    v = check_plan(plan)
    assert v.aligned is False
    assert v.mismatch_kind == "get_drift"
    assert v.expected_verb == "read"


def test_check_plan_no_context_returns_aligned_noop():
    """Plan senza domain/action: verifier no-op (aligned=True)."""
    plan = _plan_shim("read")
    v = check_plan(plan)
    assert v.aligned is True
    assert "no skill context" in v.mismatch_reason


def test_check_plan_unknown_context_fails_closed():
    """Una action sconosciuta non può attraversare il boundary senza prova."""
    plan = _plan_shim("read", "calendar", "ufoize")
    v = check_plan(plan)
    assert v.aligned is False
    assert v.mismatch_kind == "uncovered_context"
    assert "no contextual or legacy action rule" in v.mismatch_reason


def test_check_plan_uses_known_legacy_action_when_context_cell_is_missing():
    vm = {
        "actions": {"list": {"verb": "read"}},
        "domains": {"calendar": "events"},
        "contextual": {},
    }
    v = check_plan(_plan_shim("read", "calendar", "list"), vocab_map=vm)
    assert v.aligned is True
    assert v.expected_verb == "read"
