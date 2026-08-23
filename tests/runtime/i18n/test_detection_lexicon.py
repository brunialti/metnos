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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

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


def test_asserted_at_is_domain_neutral_and_resets_at_clause_boundaries():
    samples = (
        ("usa il computer, senza ripiegare sul server", "server", False),
        ("use the computer without falling back to the server", "server", False),
        ("non sul computer, ma sul server", "server", True),
        ("not on the computer but on the server", "server", True),
        ("esegui sul server", "server", True),
        ("run on the server", "server", True),
    )
    for text, marker, expected in samples:
        assert dl.asserted_at(text, text.index(marker)) is expected


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
                    "commit", "branch", "workflow", "gist", "fork", "merge"},
        "_google_photos": {"google photos", "google foto", "google photo",
                           "foto google", "gphotos"}},
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
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")
    dl._invalidate()
    try:
        assert dl.match("notify.request", "send me the photos")
        assert dl.match("undo.grammar_marker", "undo")
        assert dl.search("tasks.schedule_phrase", "every 2 hours")
        # e una nuova lingua e' segnalata come scoperta dal guard
        assert not dl.verify_coverage("fr")["ok"]
    finally:
        dl._invalidate()


# ── confirm.yes / confirm.no: autorita' unica, e niente forme estranee ─────
#
# Migrazione del 16/8/2026 (kind `regex` -> `phrases`). I due concetti sono
# la porta da cui passano TUTTE le conferme dell'utente: una regressione qui
# significa o che non si riesce a confermare, o che una frase qualunque viene
# letta come un si'. Questi test fissano il contratto misurato.

def test_conferme_sono_phrases_a_parola_intera():
    """Non regex: elenchi di forme, traducibili dal daemon per ogni lingua."""
    import detection_lexicon as _dl
    for concept in ("confirm.yes", "confirm.no"):
        risolto = _dl._resolve(concept)
        assert risolto is not None, concept
        assert risolto[0] == "phrases", f"{concept}: kind={risolto[0]}"
        assert risolto[1] == "word", f"{concept}: match_mode={risolto[1]}"


def test_conferme_riconoscono_le_forme_reali():
    """NB: `match` e' la primitiva del lessico e cerca la forma OVUNQUE nel
    testo. Non e' il criterio di conferma: quello lo decide
    `channels/daemon._classify_yes_no`, che ancora la forma all'INIZIO della
    risposta proprio perche' «come si fa?» contiene un «si» e non e' un si'."""
    import detection_lexicon as _dl
    for forma in ("si", "sì", "SI", "ok", "OK", "okay", "yes", "y"):
        assert _dl.match("confirm.yes", forma), forma
    for forma in ("no", "NO", "annulla", "lascia", "niente", "n", "stop",
                  "no grazie"):
        assert _dl.match("confirm.no", forma), forma


def test_conferme_non_scattano_dentro_altre_parole():
    """`word` e non `substring`: la ragione per cui il match_mode e' fissato."""
    import detection_lexicon as _dl
    for testo in ("sinistra", "asino", "yesterday", "okkupato", "nord",
                  "stopwords", "annullalo"):
        assert not _dl.match("confirm.yes", testo), testo
        assert not _dl.match("confirm.no", testo), testo


def test_confirm_yes_non_contiene_le_forme_di_allargamento():
    """REGRESSIONE, ed era un rischio di sicurezza.

    Fino al 16/8/2026 `confirm.yes` conteneva «alza|aumenta|rilancia|piu'»,
    residuo del dialogo di allargamento dei risultati — reso non bloccante il
    3/6 e da allora mai piu' aperto. Restavano pero' vive sulle carte di
    approvazione: rispondere «aumenta» a «approvo questo comando
    privilegiato?» valeva SI'. Se qualcuno le reintroduce, questo test cade.
    """
    import detection_lexicon as _dl
    for forma in ("alza", "aumenta", "rilancia", "più", "piu"):
        assert not _dl.match("confirm.yes", forma), forma


def test_il_seed_riallinea_una_riga_divergente():
    """Il seed e' l'autorita' sulle lingue che dichiara.

    Senza questo, cambiare il KIND di un concetto raggiungerebbe solo le
    installazioni NUOVE: quelle esistenti terrebbero la riga vecchia per
    sempre, e le due divergerebbero in silenzio.
    """
    import json
    import detection_lexicon as _dl
    import detection_lexicon_seed as _seed
    conn = _dl._open()
    try:
        conn.execute(
            "UPDATE detection_lexicon SET kind='regex', "
            "match_mode='substring', payload=? "
            "WHERE concept='confirm.yes' AND lang='it'",
            (json.dumps([r"\bmai\b"]),))
        conn.commit()
        _dl._invalidate("confirm.yes")
        _seed.register_all()
        _dl._invalidate("confirm.yes")
        risolto = _dl._resolve("confirm.yes")
        assert risolto[0] == "phrases"
        assert risolto[1] == "word"
        assert _dl.match("confirm.yes", "si")
        assert not _dl.match("confirm.yes", "mai")
    finally:
        # Il DB del lessico e' condiviso da tutta la sessione di test: se
        # questo test cade a meta', senza ripristino corrompe ogni file
        # raccolto dopo.
        _seed.register_all()
        _dl._invalidate("confirm.yes")


def test_unione_prende_la_forma_dalla_lingua_dell_istanza():
    """La PRIMA lingua della catena detta kind e severita' del match.

    Prima li dettava l'ULTIMA iterata, per puro ordine: una riga tradotta con
    `match_mode='substring'` allentava il confronto di tutte le altre, e su
    `confirm.*` significava che «sinistra» valeva un si'.
    """
    import detection_lexicon as _dl
    import detection_lexicon_seed as _seed
    conn = _dl._open()
    try:
        conn.execute(
            "UPDATE detection_lexicon SET match_mode='substring' "
            "WHERE concept='confirm.yes' AND lang='en'")
        conn.commit()
        _dl._invalidate("confirm.yes")
        assert _dl._resolve("confirm.yes")[1] == "word"
        assert not _dl.match("confirm.yes", "sinistra")
    finally:
        conn.execute(
            "UPDATE detection_lexicon SET match_mode='word' "
            "WHERE concept='confirm.yes' AND lang='en'")
        conn.commit()
        _seed.register_all()
        _dl._invalidate("confirm.yes")


def test_seed_su_db_non_scrivibile_non_abortisce_il_resto(monkeypatch, tmp_path):
    """Una riga non scrivibile non deve fermare il seed a meta'.

    Prima l'eccezione usciva da `register`: i concetti dichiarati DOPO non
    venivano registrati, `ensure_seeded` la inghiottiva e non riprovava mai
    piu' nel processo, e la connessione globale restava in transazione
    bloccando ogni altro scrittore.
    """
    import sqlite3
    import detection_lexicon as _dl

    # DB isolato: non si tocca il lessico condiviso della sessione.
    percorso = tmp_path / "detection.sqlite"
    conn = sqlite3.connect(str(percorso))
    conn.executescript(_dl._SCHEMA)
    conn.commit()
    monkeypatch.setattr(_dl, "_conn", conn)
    monkeypatch.setattr(_dl, "_seeded", True)   # niente ri-seed automatico

    assert _dl.register("prova.concetto", "phrases",
                        it=["alfa"], en=["alpha"], match_mode="word") is True

    class _SolaLettura(sqlite3.Connection):
        pass

    def _ro_execute(sql, *a, **k):
        if sql.lstrip().upper().startswith("UPDATE"):
            raise sqlite3.OperationalError(
                "attempt to write a readonly database")
        return sqlite3.Connection.execute(conn, sql, *a, **k)

    class _Proxy:
        def __getattr__(self, nome):
            return getattr(conn, nome)
        execute = staticmethod(_ro_execute)

    monkeypatch.setattr(_dl, "_conn", _Proxy())
    # Riga presente ma divergente: entra nel ramo UPDATE, che ora fallisce.
    assert _dl.register("prova.concetto", "phrases",
                        it=["beta"], en=["beta"], match_mode="word") is False
    # La connessione reale NON deve essere rimasta in transazione.
    assert not conn.in_transaction
    # E il concetto e' rimasto quello di prima.
    monkeypatch.setattr(_dl, "_conn", conn)
    _dl._invalidate("prova.concetto")
    assert _dl.match("prova.concetto", "alfa")
    assert not _dl.match("prova.concetto", "beta")
