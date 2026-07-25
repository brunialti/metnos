"""Una procedura ESTRANEA non diventa contesto generativo.

Una procedura porta passi numerati e condizioni di arresto che il composer
deve riportare per intero: lasciata accanto a una domanda su un'ALTRA pagina
cattura la risposta (turno reale: il dettaglio utente risposto con la
procedura delle proposte). La regola e' sul TEMA, non sul tipo: se la fonte
primaria e' la stessa pagina, o non e' una pagina affatto, la procedura resta
— spesso e' l'unica che attesta route e campi, e togliela apre un buco
(misurato sui casi della console delle proposte).
"""

from __future__ import annotations

from tutor.compose import Composition
from tutor.models import TutorPrincipal, TutorRequest
from tutor.semantic import SemanticContext, SourceHit
from tutor.service import answer_request
from tutor.sources import KnowledgeUnit


def _unit(kind: str, key: str) -> KnowledgeUnit:
    ref = (f"runtime:ui_surface:{key}:it" if kind == "ui_surface"
           else f"runtime:ui_surface:{key}:procedure:it")
    return KnowledgeUnit(
        unit_id=f"unit-{kind}-{key}", concept_id=f"c-{kind}-{key}",
        lang="it", audience="user", source_kind=kind, authority="registry",
        priority=1, title=f"Titolo {key}", text=f"corpo {key}",
        semantic="", source_ref=ref, content_hash="hash",
    )


def _hit(unit: KnowledgeUnit, score: float) -> SourceHit:
    return SourceHit(source_type="knowledge", source_id=unit.unit_id,
                     lang="it", score=score, unit=unit)


def _principal() -> TutorPrincipal:
    return TutorPrincipal(user_id="u1", actor="host", audience="instance_admin",
                          channel="http", conversation_id="proc-1")


def _run(monkeypatch, hits):
    seen: dict[str, str] = {}
    monkeypatch.setattr("tutor.catalog.load_cards", lambda: ())
    monkeypatch.setattr("tutor.service.retrieve_sources",
                        lambda *a, **k: SemanticContext(hits, top_score=0.9))
    monkeypatch.setattr("tutor.mode.classify_mode", lambda *a, **k: "EXPLAIN")

    def fake_compose(**kwargs):
        seen["context"] = kwargs["context"]
        return Composition("answer", "Risposta.")

    monkeypatch.setattr("tutor.compose.compose_answer", fake_compose)
    answer = answer_request(TutorRequest(
        "Nel dettaglio di un utente cosa modifico?", "it", _principal()))
    return answer, seen


def test_foreign_procedure_is_dropped_from_generative_context(monkeypatch):
    surface = _unit("ui_surface", "users")
    procedure = _unit("ui_procedure", "changes")
    answer, seen = _run(monkeypatch, (_hit(surface, 0.9), _hit(procedure, 0.8)))
    assert answer is not None
    assert "corpo users" in seen["context"]
    assert "corpo changes" not in seen["context"]
    # e nemmeno nella checklist che il composer deve coprire
    assert "changes" not in seen["context"].split("[COVERAGE_LEDGER]")[-1]
    assert surface.unit_id in " ".join(answer.source_ids)
    assert procedure.unit_id not in " ".join(answer.source_ids)


def test_procedure_of_the_same_page_stays_in_context(monkeypatch):
    """Stessa pagina = co-tematica: la procedura resta, perche' e' spesso
    l'unica fonte che attesta route, campi e condizioni di arresto."""

    surface = _unit("ui_surface", "changes")
    procedure = _unit("ui_procedure", "changes")
    answer, seen = _run(monkeypatch, (_hit(surface, 0.9), _hit(procedure, 0.8)))
    assert answer is not None
    assert "corpo changes" in seen["context"]
    assert procedure.unit_id in " ".join(answer.source_ids)


def test_procedure_stays_when_the_primary_is_not_a_page(monkeypatch):
    """Primaria non-UI: nessuna pagina in competizione, nessun motivo di
    scartare la procedura (misurato: la sua rimozione toglieva la route)."""

    doc = KnowledgeUnit(
        unit_id="unit-doc", concept_id="c-doc", lang="it", audience="user",
        source_kind="published_doc", authority="documentation", priority=2,
        title="Documento", text="corpo doc", semantic="",
        source_ref="doc:public:changes:it", content_hash="hash",
    )
    procedure = _unit("ui_procedure", "changes")
    answer, seen = _run(monkeypatch, (_hit(doc, 0.9), _hit(procedure, 0.8)))
    assert answer is not None
    assert "corpo changes" in seen["context"]
    assert procedure.unit_id in " ".join(answer.source_ids)


def test_primary_procedure_is_still_returned_verbatim(monkeypatch):
    procedure = _unit("ui_procedure", "changes")
    surface = _unit("ui_surface", "users")
    answer, seen = _run(monkeypatch, (_hit(procedure, 0.9), _hit(surface, 0.8)))
    assert answer is not None
    # nessuna composizione: il testo della procedura è consegnato letteralmente
    assert "context" not in seen
    assert answer.answer_md.startswith("corpo changes")


def test_context_of_procedures_only_keeps_the_primary(monkeypatch):
    first = _unit("ui_procedure", "changes")
    second = _unit("ui_procedure", "devices")
    answer, _seen = _run(monkeypatch, (_hit(first, 0.9), _hit(second, 0.85)))
    assert answer is not None
    assert answer.source_ids == (f"knowledge:{first.unit_id}",)
