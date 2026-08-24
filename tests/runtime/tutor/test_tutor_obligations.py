from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_structural_gate_covers_elliptical_compound_in_supported_languages():
    from tutor.obligations import _compound_candidate

    assert _compound_candidate(
        "Explain which controls apply when I ask to read a file and which "
        "apply when I ask to modify it")
    assert _compound_candidate(
        "Spiega quali controlli si applicano quando chiedo di leggere un file "
        "e quali si applicano quando chiedo di modificarlo")
    assert not _compound_candidate("Come posso leggere un file?")


@pytest.mark.parametrize(("query", "expected"), (
    ("Quali controlli valgono per la lettura e quali valgono per la modifica?", True),
    ("Se il file viene letto, che cosa accade; se viene modificato, che cosa cambia?", True),
    ("Spiega come funziona la lettura. Poi spiega come funziona la modifica.", True),
    ("Descrivi i controlli della lettura; descrivi i controlli della modifica.", True),
    ("Quali controlli si applicano alla lettura di un file?", False),
    ("Come funziona il ripristino?", False),
    ("Which controls apply to reading and which apply to modifying a file?", True),
    ("If a file is read, what happens; if it is changed, what differs?", True),
    ("Explain how file reading works. Then explain how file modification works.", True),
    ("Describe the controls for file reading; describe the controls for file modification.", True),
    ("Which controls apply to reading one file?", False),
    ("How does undo work?", False),
))
def test_structural_gate_bilingual_certification_corpus(query, expected):
    from tutor.obligations import _compound_candidate

    assert _compound_candidate(query) is expected


def test_simple_question_returns_without_invoking_classifier(monkeypatch):
    import tutor.obligations as subject

    monkeypatch.setattr(
        subject, "_compound_candidate", lambda _query: False)
    result = subject.classify_question_obligations(
        "Come posso leggere un file?", "it", deadline_at=10**12)

    assert result.queries == ("Come posso leggere un file?",)
    assert result.decomposed is False


def test_classifier_accepts_only_closed_json(monkeypatch):
    import tutor.obligations as subject

    monkeypatch.setattr(subject, "_compound_candidate", lambda _query: True)
    monkeypatch.setattr(
        "llm_helpers.call_llm",
        lambda *_args, **_kwargs: (
            '{"obligations":["Quali controlli si applicano alla lettura di '
            'un file?","Quali controlli si applicano alla modifica di un file?"]}',
            {},
        ),
    )
    monkeypatch.setattr(
        "executor_scheduler.invoke_scheduled",
        lambda _executor, callback, **_kwargs: callback(),
    )
    result = subject.classify_question_obligations(
        "domanda composta", "it", deadline_at=10**12)

    assert result.decomposed is True
    assert len(result.queries) == 2
    assert "lettura di un file" in result.queries[0]
    assert "modifica di un file" in result.queries[1]


def test_merge_reserves_each_obligation_primary_before_support():
    from tutor.obligations import merge_contexts
    from tutor.semantic import SemanticContext, SourceHit

    def hit(source_id: str):
        return SourceHit("knowledge", source_id, "it", 0.9,
                         unit=SimpleNamespace())

    merged = merge_contexts((
        SemanticContext((hit("read"), hit("read-arg")), 0.91),
        SemanticContext((hit("write"), hit("write-arg")), 0.90),
    ), maximum=3)

    assert [row.source_id for row in merged.hits] == [
        "read", "write", "read-arg",
    ]


def test_obligation_block_exposes_evidence_gaps_without_becoming_a_source():
    from tutor.obligations import render_question_obligations

    rendered = render_question_obligations(
        ("question one", "question two"),
        (("knowledge:read", "knowledge:policy"), ()),
    )

    assert "obligation=1; evidence=available" in rendered
    assert "source_ids=knowledge:read,knowledge:policy" in rendered
    assert "obligation=2; evidence=missing" in rendered
    assert "source_ids=none" in rendered
    assert "[SOURCE" not in rendered


def test_source_map_retains_per_obligation_provenance_after_global_merge():
    from tutor.obligations import map_context_sources
    from tutor.semantic import SemanticContext, SourceHit

    def hit(source_id: str):
        return SourceHit("knowledge", source_id, "it", 0.9,
                         unit=SimpleNamespace())

    shared = hit("policy")
    read = hit("read")
    write = hit("write")
    contexts = (
        SemanticContext((read, shared), 0.91),
        SemanticContext((write, shared), 0.90),
    )

    mapped = map_context_sources(
        contexts,
        (read, write, shared),
        source_id=lambda row: f"{row.source_type}:{row.source_id}",
    )

    assert mapped == (
        ("knowledge:read", "knowledge:policy"),
        ("knowledge:write", "knowledge:policy"),
    )
