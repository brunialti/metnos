"""Correttore di bozze deterministico (Passo 2, ADR 0198).

La risposta composta viene riletta MECCANICAMENTE contro le voci strutturate
del ledger di copertura: percorsi delle superfici con campi e controlli, aree
e provider dell'inventario, finalità dei tool, condizioni di arresto, divieto
dei marker interni. Voci mancanti = UNA sola ricomposizione con l'elenco
esplicito dei buchi, poi si consegna comunque (cap onesto, niente loop).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tutor.compose import Composition
from tutor.models import TutorPrincipal, TutorRequest
from tutor.mode import ModeDecision
from tutor.semantic import SemanticContext, SourceHit
from tutor.service import (
    _coverage_items, _find_gaps, _render_ledger, answer_request,
)
from tutor.sources import KnowledgeUnit


def _unit(kind: str, *, key: str = "", text: str = "corpo",
          lang: str = "it") -> KnowledgeUnit:
    if kind == "ui_surface":
        ref = f"runtime:ui_surface:{key}:{lang}"
    elif kind == "capability_catalog":
        ref = "runtime:capability_catalog:overview#1"
    else:
        kind = "executor_manifest"
        ref = f"manifest:{key}:{lang}"
    return KnowledgeUnit(
        unit_id=f"unit-{kind}-{key or 'x'}-{lang}",
        concept_id=f"concept-{kind}",
        lang=lang,
        audience="user",
        source_kind=kind,
        authority="registry",
        priority=1,
        title="Titolo",
        text=text,
        semantic="",
        source_ref=ref,
        content_hash="hash",
    )


def _hit(unit: KnowledgeUnit) -> SourceHit:
    return SourceHit(
        source_type="knowledge", source_id=unit.unit_id,
        lang=unit.lang, score=0.9, unit=unit,
    )


def _surface_complete_text(key: str = "changes") -> str:
    """Testo che rappresenta OGNI voce della superficie, dal registro stesso."""

    from ui_surfaces import by_key
    surface = by_key(key)
    parts = [surface.route]
    parts += list(surface.visible("it")) + list(surface.controls("it"))
    parts += list(surface.stop_conditions("it"))
    return " ".join(parts)


def _principal() -> TutorPrincipal:
    return TutorPrincipal(
        user_id="u1", actor="host", audience="instance_admin",
        channel="http", conversation_id="repair-1",
    )


def _patch_context(monkeypatch, hits: tuple[SourceHit, ...]):
    context = SemanticContext(hits, top_score=0.9)
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: SimpleNamespace(
            version="sha256:test-catalog", cards=(),
            units=tuple(hit.unit for hit in hits if hit.unit is not None),
            card_index=None, knowledge_index=None,
        ),
    )
    monkeypatch.setattr(
        "tutor.service.retrieve_sources", lambda *a, **k: context)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *a, **k: ModeDecision("EXPLAIN", True))


# ---------------------------------------------------------------- rilettura

def test_surface_gaps_route_items_and_stop_conditions():
    coverage = _coverage_items((_hit(_unit("ui_surface", key="changes")),))
    surface = coverage["surfaces"][0]
    assert surface["route"] == "/admin/changes"
    assert surface["stop_conditions"]

    complete = _surface_complete_text("changes")
    assert _find_gaps(coverage, complete, "it") == []

    without_route = complete.replace("/admin/changes", "")
    gaps = _find_gaps(coverage, without_route, "it")
    assert any("/admin/changes" in gap for gap in gaps)

    without_stop = complete.replace(surface["stop_conditions"][0], "")
    gaps = _find_gaps(coverage, without_stop, "it")
    assert any("stop condition" in gap for gap in gaps)


def test_inflected_roots_cover_checklist_items():
    coverage = _coverage_items((_hit(_unit("ui_surface", key="changes")),))
    complete = _surface_complete_text("changes")
    # Flessioni: la radice copre, la parola letterale non serve.
    inflected = complete.replace("riprova", "riprovare la generazione")
    assert not any(
        "riprova" in gap for gap in _find_gaps(coverage, inflected, "it"))


def test_internal_markers_force_a_gap_even_without_ledger():
    gaps = _find_gaps({}, "Risposta con from_step esposto.", "it")
    assert gaps and "from_step" in gaps[0]
    assert _find_gaps({}, "Risposta pulita.", "it") == []


def test_inventory_areas_providers_and_tool_purposes():
    inventory = _unit(
        "capability_catalog",
        text=("Panoramica.\n- posta [leggi, invia]\n- file [trova]\n"
              "Provider: GitHub, Google Workspace."),
    )
    manifest = _unit(
        "manifest", key="compute_files_hash",
        text=("Scheda del tool compute_files_hash. "
              "Calcola l'impronta dei file locali."),
    )
    coverage = _coverage_items((_hit(inventory), _hit(manifest)))
    assert set(coverage["areas"]) == {"posta", "file"}
    assert "GitHub" in coverage["providers"]
    assert coverage["tools"]

    good = ("Gestisco la posta e i file, calcolo le impronte. "
            "Supporto GitHub e Google Workspace.")
    assert _find_gaps(coverage, good, "it") == []

    silent = "Gestisco i file. Supporto GitHub e Google Workspace."
    gaps = _find_gaps(coverage, silent, "it")
    assert any("posta" in gap for gap in gaps)
    assert any("impronta" in gap for gap in gaps)


def test_render_ledger_lists_every_family():
    coverage = _coverage_items((
        _hit(_unit("ui_surface", key="changes")),
        _hit(_unit("capability_catalog", text="- posta [leggi]")),
    ))
    ledger = _render_ledger(coverage)
    assert ledger.startswith("[COVERAGE_LEDGER]")
    assert "areas=posta" in ledger
    assert "surfaces=" in ledger and "/admin/changes" in ledger
    assert _render_ledger(
        {"providers": [], "areas": [], "operations": [], "tools": [],
         "surfaces": []}) == ""


# ------------------------------------------------------------- integrazione

def test_repair_recomposes_once_with_explicit_gap_list(monkeypatch):
    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="changes")),))
    complete = _surface_complete_text("changes")
    calls: list[dict] = []

    def fake_compose(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return Composition("answer", "Bozza senza il percorso richiesto.")
        return Composition("answer", complete)

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Come uso la console delle proposte?", "it", _principal()))
    assert answer is not None and answer.esito == "fondata"
    assert len(calls) == 2
    assert "[REVISION]" in calls[1]["context"]
    assert "/admin/changes" in calls[1]["context"]
    assert answer.repair_pass == 1
    assert any("/admin/changes" in voice for voice in answer.repair_missing)
    assert answer.answer_md == complete


def test_repair_cap_delivers_first_draft_when_revision_fails(monkeypatch):
    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="changes")),))
    calls: list[int] = []

    def fake_compose(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return Composition("answer", "Bozza incompleta ma onesta.")
        return Composition("unavailable")

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Come uso la console delle proposte?", "it", _principal()))
    assert answer is not None
    assert len(calls) == 2
    assert answer.repair_pass == 1
    assert answer.answer_md == "Bozza incompleta ma onesta."


def test_no_gaps_means_no_second_composition(monkeypatch):
    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="changes")),))
    complete = _surface_complete_text("changes")
    calls: list[int] = []

    def fake_compose(**kwargs):
        calls.append(1)
        return Composition("answer", complete)

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Come uso la console delle proposte?", "it", _principal()))
    assert answer is not None
    assert len(calls) == 1
    assert answer.repair_pass == 0
    assert answer.repair_missing == ()


def test_repair_telemetry_lands_in_turn_record(tmp_path, monkeypatch):
    import json

    import config
    from tutor.telemetry import record

    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="changes")),))
    monkeypatch.setattr(config, "PATH_TURNS", tmp_path)

    def fake_compose(**kwargs):
        return Composition("answer", "Bozza senza percorso.")

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    request = TutorRequest(
        "Come uso la console delle proposte?", "it", _principal())
    answer = answer_request(request)
    record(request, answer)
    row = json.loads(next(tmp_path.glob("*.jsonl")).read_text().splitlines()[0])
    assert row["tutor_repair_pass"] == 1
    assert any("/admin/changes" in voice
               for voice in row["tutor_repair_missing"])


def test_short_labels_need_every_word_at_root_level():
    """Etichetta corta = nome esatto di un controllo: «esegui ora» non è
    coperto da un «eseguire» isolato, ma «riprova» resta coperto da
    «riprovare» (radice, non forma letterale)."""

    coverage = {"providers": [], "areas": [], "operations": [], "tools": [],
                "surfaces": [{
                    "entry": "x", "label": "Timer", "route": "/admin/timers",
                    "visible": (), "controls": ("abilita", "esegui ora"),
                    "stop_conditions": (),
                }]}
    partial = ("Dalla pagina /admin/timers puoi abilitare un task oppure "
               "eseguire le operazioni previste.")
    gaps = _find_gaps(coverage, partial, "it")
    assert any("esegui ora" in gap for gap in gaps)
    assert not any("abilita" in gap for gap in gaps)

    complete = partial + " Il comando esegui ora lo fa partire subito."
    assert _find_gaps(coverage, complete, "it") == []


# ------------------------------------------- difetti trovati dall'audit 25/7

def test_identifier_labels_match_the_slug_the_answer_writes():
    """`\\b` non trova confine dopo un underscore: una voce presente alla
    lettera veniva dichiarata mancante (misurato su `events_empty`)."""

    from tutor.service import _label_covered, _root_hit

    text = "Copro le aree `events_empty` e `files_spreadsheet`."
    assert _root_hit("empty", text)
    assert _label_covered("events_empty", text, {})
    assert not _label_covered("messages", text, {})


def test_ledger_is_scoped_to_the_primary_page():
    """La checklist UI segue soltanto una superficie primaria.

    Una pagina secondaria resta evidenza per il compositore, ma non diventa
    una sezione obbligatoria in una risposta su un altro argomento.
    """

    from tutor.service import _ledger_scope

    users = _hit(_unit("ui_surface", key="users"))
    changes = _hit(_unit("ui_surface", key="changes"))
    doc = _hit(_unit("capability_catalog", text="- posta [leggi]"))
    scoped = _ledger_scope((users, changes, doc), users)
    assert users in scoped and doc in scoped
    assert changes not in scoped
    # Primaria non-UI: le pagine secondarie non entrano nella checklist.
    assert _ledger_scope((doc, users, changes), doc) == (doc,)


def test_secondary_ui_surface_is_context_not_mandatory_coverage(monkeypatch):
    """Regressione del turno 1ab456aa, senza codificare embedder o Modifiche."""

    doc = _hit(_unit("capability_catalog", text="- modelli [configura]"))
    surface = _hit(_unit("ui_surface", key="changes"))
    _patch_context(monkeypatch, (doc, surface))
    seen: dict[str, str] = {}

    def fake_compose(**kwargs):
        seen["context"] = kwargs["context"]
        return Composition("answer", "Configura il modello nel file indicato.")

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Come configuro questo componente?", "it", _principal()))
    assert answer is not None and answer.repair_pass == 0
    # La fonte secondaria è ancora leggibile dal compositore.
    assert "knowledge:unit-ui_surface-changes-it" in seen["context"]
    # Ma il ledger non impone percorso e contenuti della pagina.
    ledger = seen["context"].split("[COVERAGE_LEDGER]")[-1]
    assert "/admin/changes" not in ledger


def test_composer_ledger_excludes_the_other_page(monkeypatch):
    """Verifica il CABLAGGIO, non solo l'helper: la checklist che arriva al
    composer non deve contenere le voci della pagina non primaria."""

    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="users")),
                                 _hit(_unit("ui_surface", key="changes"))))
    seen: dict[str, str] = {}

    def fake_compose(**kwargs):
        seen["context"] = kwargs["context"]
        return Composition("answer", "Risposta con /admin/users.")

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Cosa contiene il dettaglio di un utente?", "it", _principal()))
    assert answer is not None
    ledger = seen["context"].split("[COVERAGE_LEDGER]")[-1]
    assert "/admin/users" in ledger
    assert "/admin/changes" not in ledger
    # il corpo della pagina vicina resta pure evidenza: si restringe la
    # CHECKLIST, non il contesto
    assert "corpo" in seen["context"] or "Titolo" in seen["context"]


def test_revision_is_re_read_and_the_better_draft_wins(monkeypatch):
    """La ricomposizione integra i buchi elencati ma puo' perderne un altro:
    consegnarla alla cieca peggiora la risposta. Si sceglie meccanicamente
    la versione con meno buchi, senza chiamate aggiuntive."""

    _patch_context(monkeypatch, (_hit(_unit("ui_surface", key="changes")),))
    complete = _surface_complete_text("changes")
    # bozza: manca solo la route · revisione: ha la route ma perde tre voci
    draft = complete.replace("/admin/changes", "")
    worse = " ".join(complete.split()[:6]) + " /admin/changes"
    calls: list[dict] = []

    def fake_compose(**kwargs):
        calls.append(kwargs)
        return Composition("answer", draft if len(calls) == 1 else worse)

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Come uso la console delle proposte?", "it", _principal()))
    assert answer is not None and len(calls) == 2
    assert answer.repair_pass == 1
    # consegnata la BOZZA: la revisione aveva piu' buchi
    assert answer.answer_md == draft
