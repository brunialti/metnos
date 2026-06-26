#!/usr/bin/env python3
"""detection_lexicon_seed — fonte canonica IT+EN dei lessici di detection.

Unico punto in cui i lessici NL vivono nel codice (come il seed delle chiavi
i18n). Ogni `register(...)` e' idempotente: scrive solo le righe (concept,
lang) mancanti, quindi e' sicuro richiamarlo ad ogni boot.

REGOLA DI MIGRAZIONE (a regressione zero): per ogni concept, l'unione delle
forme it+en deve essere ESATTAMENTE l'insieme del costrutto hardcoded che
sostituisce. Il matcher unisce sempre {lingua_corrente} ∪ {it,en}, quindi su
istanze it/en il comportamento e' identico. Le righe per lingue nuove le
popola il daemon di traduzione (`jobs/detection_translate_pending.py`).

Per i regex morfologici "intrecciati" (unita' temporali it+en nello stesso
pattern) il pattern hand-tuned e' archiviato verbatim sotto it ED en (il
merge deduplica → compilato una volta) cosi' la copertura it/en e' corretta;
la sintesi per lingue nuove la fa il daemon dal word-list tradotto.
"""
from __future__ import annotations

import detection_lexicon as _dl
from vocab import PROVIDER_SUFFIXES  # identità provider = vocabolario (SoT unica)


# Marker NL (linguistici, i18n) per ogni provider — SOLO i VALORI. Le CHIAVI
# (l'identità dei provider) NON si ripetono qui: derivano da
# `vocab.PROVIDER_SUFFIXES` (SoT unica). Un nuovo provider si aggiunge in vocab
# + qui i suoi marker; le due liste devono coprire lo stesso set (guard:
# `test_provider_markers_cover_suffixes`). Le altre lingue le sintetizza il
# daemon dal word-list tradotto (it={} → fallback en).
#
# DEBITO i18n (NON sanare finché lo scope è IT+EN, §1 — YAGNI): questo `{it,en}`
# è un dict a 2 LOCALI FISSI, non i18n vero. Conseguenza: «aggiungi una lingua =
# lascia cadere un file» NON funziona — il 3° locale richiede PRIMA il refactor
# verso il DB i18n locale-driven (chiavi semantiche + fallback xx→en, come
# `messages.get`/METNOS_LANG §11) + `validate_invariant` su set-locali APERTO
# (oggi esige parità IT/EN incondizionata). Vedi memoria project-i18n-lexicon-debt.
_PROVIDER_MARKERS_EN = {
    "github": ["github", "pr", "issue", "issues", "repo", "repository",
               "commit", "branch", "workflow", "gist", "fork", "merge"],
    "google_workspace": ["google", "drive", "gmail", "gdrive", "workspace",
                         "calendar google", "g suite"],
}


def register_all() -> None:
    R = _dl.register

    # ── UNDO ───────────────────────────────────────────────────────────
    # tool_grammar._UNDO_MARKERS (word-boundary, query_has_undo_marker)
    R("undo.grammar_marker", "phrases", match_mode="word",
      it=["annulla", "annullare", "annullo", "annullala", "ripristina",
          "ripristino", "ripristinare", "torna indietro", "torna su",
          "disfa", "disfare", "annulla l'ultimo"],
      en=["undo", "rollback"])
    # intent_extractor._UNDO_PATTERNS (substring, bypass deterministico)
    R("undo.intent_bypass", "phrases", match_mode="substring",
      it=["annulla", "annullare", "annullo", "ripristina", "torna indietro",
          "indietreggia", "anull"],
      en=["undo", "revert", "rollback"])

    # ── MAIL: posta indesiderata ───────────────────────────────────────
    # junk_mail_resolver: «sposta/cancella/filtra le email di spam / la posta
    # indesiderata» → filtro su category_hints (bulk/newsletter), NON
    # classify(junk) (= spam evidente, ~0; controprova 23/6, opzione b Roberto).
    R("mail.junk_terms", "phrases", match_mode="substring",
      it=["spam", "posta indesiderata", "indesiderata", "spazzatura",
          "posta spazzatura"],
      en=["spam", "junk", "junk mail"])

    # ── TASKS / SCHEDULING ─────────────────────────────────────────────
    # tool_grammar._TASKS_MARKERS (word-boundary)
    R("tasks.marker", "phrases", match_mode="word",
      it=["schedula", "schedulare", "ricorrente", "ricorrenti", "promemoria",
          "ricordami", "ricordati", "ricorda", "storico", "esecuzione",
          "esecuzioni", "cancella task", "elenca task", "lista task"],
      en=["task", "tasks", "schedule", "scheduled", "reminder", "remind",
          "timer", "daily", "weekly", "hourly", "history"])
    # tool_grammar._RECURRENCE_WORDS (word-boundary)
    R("tasks.recurrence_word", "phrases", match_mode="word",
      it=[], en=["daily", "weekly", "hourly"])
    # tool_grammar._RE_SCHEDULE_PHRASE (regex intrecciato → verbatim it+en)
    _SCHEDULE = (
        r"\b(?:ogni|every)\s+(?:\d+\s*)?"
        r"(?:second|minut|min\b|or[ae]\b|giorn|d[ìi]\b|settiman|mes[ei]\b|ann|"
        r"day|hour|week|month|year)"
        r"|\b(?:fra|tra)\s+(?:\d+|un[ao']?|mezz)"
    )
    R("tasks.schedule_phrase", "regex", it=[_SCHEDULE], en=[_SCHEDULE])
    # tool_grammar._RE_RECURRENCE_PHRASE (sottoinsieme stretto)
    _RECUR = (
        r"\b(?:ogni|every)\s+(?:\d+\s*)?"
        r"(?:second|minut|min\b|or[ae]\b|giorn|d[ìi]\b|settiman|mes[ei]\b|ann|"
        r"day|hour|week|month|year)"
    )
    R("tasks.recurrence_phrase", "regex", it=[_RECUR], en=[_RECUR])

    # ── SKILLS ─────────────────────────────────────────────────────────
    # tool_grammar._SKILLS_MARKERS (word-boundary)
    R("skills.marker", "phrases", match_mode="word",
      it=["capacità", "capacita", "modulo", "moduli"],
      en=["skill", "skills", "capability", "capabilities", "module",
          "modules"])

    # ── NOTIFY ─────────────────────────────────────────────────────────
    # orchestration._NOTIFY_HINTS (substring)
    R("notify.request", "phrases", match_mode="substring",
      it=["mandami", "manda", "inviami", "invia", "notificami", "scrivimi",
          "avvisami", "informami", "rispondimi"],
      en=["send me", "email me", "notify me", "let me know"])
    # orchestration._CHANNEL_HINTS (mapping canale -> forme)
    R("notify.channel", "mapping",
      it={"email": ["posta"],
          "telegram": ["telegrami", "messaggio telegram"]},
      en={"email": ["email", "e-mail", "mail"],
          "telegram": ["telegram", "chat"]})

    # ── OUTPUT INTENT ──────────────────────────────────────────────────
    # output_policy._COUNT_MARKERS (regex, split pulito it/en)
    R("output.count_request", "regex",
      it=[r"\b(quant[io]|quante|numero di|conta)\b"],
      en=[r"\b(count|how many|how much)\b"])
    # output_policy._VISUALIZE_MARKERS (regex, split pulito it/en)
    R("output.visualize_request", "regex",
      it=[r"\b(mostra|mostrami|fammi vedere|vedi|visualizz\w*|guarda)\b"],
      en=[r"\b(show|show me|display|view|let me see)\b"])

    # ── WEB SCRAPING ───────────────────────────────────────────────────
    # output_format._COOKIE_BANNER_MARKERS (substring)
    R("web.cookie_banner", "phrases", match_mode="substring",
      it=["questo sito utilizza cookie", "uso dei cookie", "accetta i cookie",
          "cookie tecnici", "proseguendo nella navigazione",
          "informativa sulla privacy"],
      en=["this site uses cookies", "we use cookies", "accept cookies",
          "cookie policy", "by continuing to browse", "privacy policy"])

    # ── CONFERME DIALOGO (channels/daemon._YES_PATTERN/_NO_PATTERN) ─────
    R("confirm.yes", "regex",
      it=[r"\b(s[iì]|alza|aumenta|rilancia|più)\b"],
      en=[r"\b(yes|y|ok|okay)\b"])
    R("confirm.no", "regex",
      it=[r"\b(no|annulla|lascia|niente)\b"], en=[r"\b(n|stop)\b"])

    # ── OBJECT CLASSIFICATION (store sink) ─────────────────────────────
    # dispatch._normalize_store_clauses (D2-c, 18/6): riferimento a uno
    # STORE/ARCHIVIO/RACCOLTA interno (object=entries). Forme con
    # preposizione: lo store-sink, non il sostantivo nudo (evita "vai allo
    # store"). Sicuro a falsi-positivi: il flip a entries scatta SOLO su una
    # clausola NON-routabile (tool-existence guard), mai su tool reali.
    R("object.store_sink", "phrases", match_mode="substring",
      it=["nello store", "nel store", "dallo store", "dal store", "allo store",
          "nell'archivio", "nell archivio", "dall'archivio", "nella raccolta",
          "dalla raccolta", "store interno", "archivio interno",
          "raccolta dati", "database interno", "datastore"],
      en=["in the store", "to the store", "into the store", "from the store",
          "internal store", "data store", "datastore", "internal archive",
          "internal collection", "data collection"])

    # ── CONNETTORI MULTI-STEP ──────────────────────────────────────────
    # compound_decomposer._CONNECTOR_PATTERN: solo le PAROLE (i simboli ,;&&
    # sono lingua-invarianti, restano nel builder del pattern di split).
    R("compound.connector_word", "phrases", match_mode="word",
      it=["e", "poi", "infine"], en=["and", "then", "after", "finally"])
    # agent_runtime._MULTISTEP_CONJUNCTIONS_RE (regex)
    R("query.multistep", "regex",
      it=[r"\b(e\s+poi|e\s+dopo|e\s+inoltre|e\s+anche|inoltre|poi|"
          r"dopodiche'|dopodiche)\b"],
      en=[r"\b(and\s+then|and\s+also|and\s+after|moreover|then|afterwards|"
          r"additionally)\b"])

    # ── PROVIDER (tool_grammar._PROVIDER_SUFFIX_MARKERS) ────────────────
    # Brand/nomi propri en-canonici; le altre lingue aggiungono nomi comuni
    # localizzati via daemon. mapping suffix -> markers (match word).
    # CHIAVI derivate da vocab.PROVIDER_SUFFIXES (SoT unica dell'identità
    # provider, zero duplicazione); VALORI = i marker NL i18n qui sopra.
    R("provider.markers", "mapping", match_mode="word",
      it={},
      en={f"_{p}": _PROVIDER_MARKERS_EN[p] for p in PROVIDER_SUFFIXES})

    # ── PLANNER MARKERS (agent_runtime) ────────────────────────────────
    # _LLM_REFUSAL_MARKERS (substring) — testo di rifiuto LLM in un arg
    R("llm.refusal_marker", "phrases", match_mode="substring",
      it=["come modello linguistico", "come un modello linguistico",
          "in qualità di assistente", "in qualita di assistente",
          "in qualità di modello", "in qualita di modello",
          "non ho accesso diretto", "non ho accesso ai tuoi",
          "non posso eseguire questa", "non posso completare questa",
          "non sono in grado di", "mi dispiace, non posso",
          "mi dispiace ma non posso", "non posso fornire",
          "non posso accedere"],
      en=["as a language model", "as an ai", "as an a.i",
          "as an artificial intelligence", "i do not have access",
          "i don't have access", "i'm unable to", "i am unable to",
          "i cannot fulfill", "i cannot assist", "i can't assist",
          "i'm sorry, i can"])
    # _HEALTH_IMPERATIVE_KEYWORDS (substring; "stop " con spazio finale)
    R("health.imperative", "phrases", match_mode="substring",
      it=["uccidi", "ferma", "termina", "spegni", "manda", "invia", "scrivi",
          "esegui", "lancia", "riavvia"],
      en=["kill", "stop ", "restart"])
    # _COUNT_QUANTIFIER_MARKERS (substring; spazi significativi preservati)
    R("count.quantifier", "phrases", match_mode="substring",
      it=["quanti ", "quante ", " conta ", "numero di "],
      en=["how many ", " count "])
    # _RESUME_AFTER_DIALOG_HINTS_IT/EN (substring; gia' separati per lingua)
    R("dialog.resume_hint", "phrases", match_mode="substring",
      it=["mandami", "manda", "inviami", "invia", "notificami", "scrivimi",
          "avvisami", "informami",
          "e poi crea", "e poi prenota", "e poi fissa", "e poi sposta",
          "e poi cancella", "e poi invia", "e poi manda",
          "e crea", "e prenota", "e fissa", "e sposta", "e cancella",
          "e invia", "e manda"],
      en=["send me", "notify me", "tell me", "email me", "let me know",
          "and create", "and book", "and schedule", "and move",
          "and delete", "and send", "and notify",
          "then create", "then book", "then schedule", "then send"])
