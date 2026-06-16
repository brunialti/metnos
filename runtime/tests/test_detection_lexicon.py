#!/usr/bin/env python3
"""Test del sottosistema detection_lexicon (gemello i18n lato input).

Garanzie verificate:
  1. Copertura it/en completa (ogni concept ha forme native) — guard
     anti-silenzio verde sulle lingue seedate.
  2. Lingua sconosciuta => verify_coverage la rende ESPLICITA (no silent
     failure): tutti i concept risultano `missing`.
  3. Match positivo/negativo per i concept della wave 1.
  4. word-boundary vs substring (no falsi positivi qua⊆qualcosa).
  5. Union {lingua_corrente} ∪ {it,en}: su una lingua nuova i comandi-prestito
     it/en continuano a matchare (best-effort) finche' il daemon non traduce.
"""
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import detection_lexicon as dl  # noqa: E402
import i18n  # noqa: E402


def test_coverage_it_en_complete():
    dl.ensure_seeded()
    for lang in ("it", "en"):
        cov = dl.verify_coverage(lang)
        assert cov["ok"], f"{lang} non coperta: {cov['missing']}"
        assert cov["covered"] == cov["total"] > 0


def test_unknown_lang_reports_all_missing():
    dl.ensure_seeded()
    cov = dl.verify_coverage("xx")
    assert not cov["ok"]
    assert set(cov["missing"]) == set(dl.registered_concepts())


POSITIVE = {
    "undo.grammar_marker": ["annulla l'ultimo", "undo", "torna indietro"],
    "undo.intent_bypass": ["annulla", "please undo", "ripristina"],
    "tasks.marker": ["crea un task", "promemoria", "every reminder", "storico"],
    "tasks.schedule_phrase": ["ogni 30 minuti", "every 2 hours", "fra 2 ore"],
    "tasks.recurrence_phrase": ["ogni giorno", "every week"],
    "tasks.recurrence_word": ["daily report", "weekly"],
    "skills.marker": ["elenca le skill", "quali capacità", "module attivi"],
    "notify.request": ["mandami una mail", "send me the report", "notify me"],
    "output.count_request": ["quante mail", "how many files", "numero di foto"],
    "output.visualize_request": ["mostrami le foto", "show me", "visualizza"],
    "web.cookie_banner": ["Questo sito utilizza cookie per migliorare",
                          "We use cookies to improve"],
}

NEGATIVE = {
    "undo.grammar_marker": ["qualcosa di buono", "leggi la mail"],
    "tasks.marker": ["leggi ogni messaggio", "cerca le foto"],
    "tasks.schedule_phrase": ["leggi ogni messaggio", "cerca le foto"],
    # recurrence_phrase e' STRETTO: esclude il ramo fra/tra (non e' ricorrenza)
    "tasks.recurrence_phrase": ["differenza tra 2 file", "fra 2 ore"],
    "skills.marker": ["elenca i file", "attiva la luce"],
    "notify.request": ["cerca le foto di ieri"],
    "output.count_request": ["leggi le mail"],
    "output.visualize_request": ["cancella i file"],
}


def test_positive_matches():
    dl.ensure_seeded()
    for concept, samples in POSITIVE.items():
        for q in samples:
            assert dl.match(concept, q) or dl.search(concept, q), \
                f"{concept!r} dovrebbe matchare {q!r}"


def test_negative_matches():
    dl.ensure_seeded()
    for concept, samples in NEGATIVE.items():
        for q in samples:
            assert not (dl.match(concept, q) or dl.search(concept, q)), \
                f"{concept!r} NON dovrebbe matchare {q!r}"


def test_channel_mapping():
    dl.ensure_seeded()
    cm = dl.mapping("notify.channel")
    assert "email" in cm and "telegram" in cm
    assert any("mail" in f for f in cm["email"])
    assert any("telegram" in f for f in cm["telegram"])


# Snapshot CONGELATO degli insiemi ORIGINALI (pre-migrazione) it∪en. Guard
# anti-drift: l'union della lingua corrente (it -> it∪en) DEVE eguagliarli,
# altrimenti il seed e' derivato → regressione silenziosa di detection.
_ORIGINAL_PHRASE_UNION = {
    "undo.grammar_marker": {"annulla", "annullare", "annullo", "annullala",
        "undo", "ripristina", "ripristino", "ripristinare", "torna indietro",
        "torna su", "rollback", "disfa", "disfare", "annulla l'ultimo"},
    "undo.intent_bypass": {"annulla", "annullare", "annullo", "undo", "revert",
        "rollback", "ripristina", "torna indietro", "indietreggia", "anull"},
    "tasks.marker": {"task", "tasks", "schedule", "scheduled", "schedula",
        "schedulare", "ricorrente", "ricorrenti", "promemoria", "reminder",
        "timer", "ricordami", "ricordati", "ricorda", "remind", "daily",
        "weekly", "hourly", "storico", "history", "esecuzione", "esecuzioni",
        "cancella task", "elenca task", "lista task"},
    "tasks.recurrence_word": {"daily", "weekly", "hourly"},
    "skills.marker": {"skill", "skills", "capacità", "capacita", "capability",
        "capabilities", "modulo", "moduli", "module", "modules"},
    "notify.request": {"mandami", "manda", "inviami", "invia", "notificami",
        "scrivimi", "avvisami", "informami", "rispondimi", "send me",
        "email me", "notify me", "let me know"},
    "count.quantifier": {"quanti ", "quante ", "how many ", " count ",
        " conta ", "numero di "},
    "health.imperative": {"kill", "uccidi", "ferma", "termina", "stop ",
        "spegni", "manda", "invia", "scrivi", "esegui", "lancia", "riavvia",
        "restart"},
    "compound.connector_word": {"e", "and", "poi", "then", "after", "finally",
        "infine"},
}

_ORIGINAL_MAPPING_UNION = {
    "notify.channel": {"email": {"email", "e-mail", "mail", "posta"},
                       "telegram": {"telegram", "telegrami", "chat",
                                    "messaggio telegram"}},
    "provider.markers": {
        "_google_workspace": {"google", "drive", "gmail", "gdrive",
                              "workspace", "calendar google", "g suite"},
        "_github": {"github", "pr", "issue", "issues", "repo", "repository",
                    "commit", "branch", "workflow", "gist", "fork", "merge"}},
}


def test_union_equals_original_snapshot():
    """it∪en del lessico == insiemi hardcoded originali (a regressione zero)."""
    dl.ensure_seeded()
    for concept, expected in _ORIGINAL_PHRASE_UNION.items():
        assert set(dl.forms(concept)) == expected, f"drift in {concept}"
    for concept, expected in _ORIGINAL_MAPPING_UNION.items():
        got = {k: set(v) for k, v in dl.mapping(concept).items()}
        assert got == expected, f"drift mapping in {concept}"


def test_union_keeps_it_en_on_foreign_lang(monkeypatch):
    """Su una lingua non seedata, l'union it/en preserva i comandi-prestito
    (best-effort) — il matching NON crolla in silenzio."""
    monkeypatch.setattr(i18n, "_lang_cache", "fr")
    dl._invalidate()
    try:
        assert dl.match("notify.request", "send me the photos")
        assert dl.match("undo.grammar_marker", "undo")
        assert dl.search("tasks.schedule_phrase", "every 2 hours")
        # e una nuova lingua e' segnalata come scoperta dal guard
        assert not dl.verify_coverage("fr")["ok"]
    finally:
        monkeypatch.setattr(i18n, "_lang_cache", "it")
        dl._invalidate()
