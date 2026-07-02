"""change_intents_i18n — bootstrap delle chiavi i18n usate dalla UI
/admin/changes (ADR 0158). Idempotente: register_key_if_missing skip
se gia' presente in DB.

Famiglia chiavi `UI_CHANGE_*`. Convenzione naming §6.1 + dedup CLAUDE.md.
Chiavi attivamente usate dal template `templates/changes.html` e dagli
handler in `http_routes_admin.py`.
"""
from __future__ import annotations

import i18n


_KEYS_IT_EN: list[tuple[str, str, str]] = [
    # Titolo + sottotitolo
    ("UI_CHANGE_TITLE",
     "Cambiamenti al sistema",
     "System changes"),
    ("UI_CHANGE_SUBTITLE",
     "Un solo elenco per proposte, accettate, applicate, in osservazione, consolidate. Filtra e agisci.",
     "One unified list for proposals, accepted, applied, observed, finalized. Filter and act."),

    # Tabs (stato canonical)
    ("UI_CHANGE_TAB_PROPOSED",    "Proposte",          "Proposed"),
    ("UI_CHANGE_TAB_ACCEPTED",    "Da applicare",      "To apply"),
    ("UI_CHANGE_TAB_APPLIED",     "Applicate",         "Applied"),
    ("UI_CHANGE_TAB_OBSERVED",    "In osservazione",   "Observing"),
    ("UI_CHANGE_TAB_FINALIZED",   "Consolidate",       "Finalized"),
    ("UI_CHANGE_TAB_REJECTED",    "Scartate",          "Rejected"),
    ("UI_CHANGE_TAB_STAGED",      "Differite",         "Staged"),
    ("UI_CHANGE_TAB_ROLLED_BACK", "Rollback",          "Rolled back"),
    ("UI_CHANGE_TAB_FAILED",      "Fallite",           "Failed"),

    # Filtri
    ("UI_CHANGE_FILTER_FAMILY",    "Sorgente",      "Source"),
    ("UI_CHANGE_FILTER_KIND",      "Tipo",          "Kind"),
    ("UI_CHANGE_FILTER_MIN_SCORE", "Score minimo",  "Min score"),
    ("UI_CHANGE_FILTER_LIMIT",     "Mostra",        "Show"),
    ("UI_CHANGE_FILTER_ALL",       "(tutti)",       "(all)"),

    # Conteggi
    ("UI_CHANGE_SHOWN",     "mostrate",  "shown"),
    ("UI_CHANGE_TOTAL_TAB", "in totale", "in tab"),

    # Colonne
    ("UI_CHANGE_COL_SCORE",   "Score",   "Score"),
    ("UI_CHANGE_COL_KIND",    "Tipo",    "Kind"),
    ("UI_CHANGE_COL_TARGET",  "Bersaglio", "Target"),
    ("UI_CHANGE_COL_ORIGIN",  "Origine", "Origin"),
    ("UI_CHANGE_COL_SUMMARY", "Riassunto", "Summary"),
    ("UI_CHANGE_COL_ACTIONS", "Azioni",  "Actions"),

    # Intent kinds (chiavi maiuscole per coerenza Jinja)
    ("UI_CHANGE_KIND_CREATE_EXECUTOR",      "Crea executor",         "Create executor"),
    ("UI_CHANGE_KIND_EXTEND_EXECUTOR",      "Estendi executor",      "Extend executor"),
    ("UI_CHANGE_KIND_DEDUPE_EXECUTORS",     "Unifica executor",      "Dedupe executors"),
    ("UI_CHANGE_KIND_MATERIALIZE_PIPELINE", "Materializza pipeline", "Materialize pipeline"),
    ("UI_CHANGE_KIND_CACHE_PATTERN",        "Cache pattern",         "Cache pattern"),
    ("UI_CHANGE_KIND_REJECT_PATTERN",       "Bandisci pattern",      "Reject pattern"),

    # Legenda (D.4 review 13/6: family/kind senza spiegazione + ID nascosto)
    ("UI_CHANGE_LEGEND", "Legenda", "Legend"),
    ("UI_CHANGE_LEGEND_FAMILIES",
     "Sorgente: canonical = promozione fastpath L0 · introvertiva = analisi "
     "dei turni (dedupe/generalize) · telos = lenti generative notturne · "
     "synt = richieste di sintesi · multi_tool = catene osservate · "
     "user = richieste dell'utente.",
     "Source: canonical = L0 fastpath promotion · introvertiva = turn "
     "analysis (dedupe/generalize) · telos = nightly generative lenses · "
     "synt = synthesis requests · multi_tool = observed chains · "
     "user = user requests."),
    ("UI_CHANGE_LEGEND_KINDS",
     "Tipo: crea/estendi/unifica executor · materializza pipeline (piano "
     "L1) · cache pattern (piano L0) · bandisci pattern.",
     "Kind: create/extend/dedupe executor · materialize pipeline (L1 "
     "plan) · cache pattern (L0 plan) · reject pattern."),
    ("UI_CHANGE_LEGEND_KIND_VS_MODULE",
     "Il TIPO è l'azione proposta; il modulo sotto la sorgente (es. "
     "generalize) è il MECCANISMO che l'ha generata. specialize è ritirato "
     "(2/7/2026): un default d'argomento è compito della cache L0, non di "
     "un executor nuovo.",
     "KIND is the proposed action; the module under the source (e.g. "
     "generalize) is the MECHANISM that produced it. specialize was "
     "retired (2026-07-02): baking an argument default is the L0 cache's "
     "job, not a new executor's."),

    # Dettagli
    ("UI_CHANGE_DETAILS",          "Dettagli",         "Details"),
    ("UI_CHANGE_DISCOVERED",       "Scoperta",         "Discovered"),
    ("UI_CHANGE_EFFECT",           "Effetto",          "Effect"),
    ("UI_CHANGE_METRICS",          "Metriche",         "Metrics"),
    ("UI_CHANGE_ROLLBACK_REASON",  "Motivo rollback",  "Rollback reason"),
    ("UI_CHANGE_FAILED_REASON",    "Motivo fallimento","Failed reason"),
    ("UI_CHANGE_CONVERGENCE_TIP",
     "Numero di sorgenti distinte che propongono cose equivalenti",
     "Number of distinct sources proposing equivalent things"),

    # Bottoni / badge
    ("UI_CHANGE_BTN_ACCEPT",   "Accetta",   "Accept"),
    ("UI_CHANGE_BTN_REJECT",   "Rifiuta",   "Reject"),
    ("UI_CHANGE_BTN_STAGE",    "Differisci", "Stage"),
    ("UI_CHANGE_BTN_ROLLBACK", "Rollback",  "Rollback"),
    ("UI_CHANGE_BTN_RETRY",    "Riprova",   "Retry"),
    ("UI_CHANGE_CONFIRM_ROLLBACK",
     "Sicuro? Verra' ripristinato lo stato precedente.",
     "Sure? Previous state will be restored."),
    ("UI_CHANGE_BADGE_ACCEPTED",    "accettata",      "accepted"),
    ("UI_CHANGE_BADGE_APPLIED",     "applicata",      "applied"),
    ("UI_CHANGE_BADGE_OBSERVED",    "in osservazione","observing"),
    ("UI_CHANGE_BADGE_FINALIZED",   "consolidata",    "finalized"),
    ("UI_CHANGE_BADGE_REJECTED",    "scartata",       "rejected"),
    ("UI_CHANGE_BADGE_ROLLED_BACK", "rollback",       "rolled back"),
    ("UI_CHANGE_BADGE_FAILED",      "fallita",        "failed"),
    ("UI_CHANGE_AWAITING_APPLY",
     "in coda per l'applicazione (daemon notturno)",
     "queued for application (nightly daemon)"),

    # Empty state
    ("UI_CHANGE_EMPTY",
     "Nessun cambiamento in questo stato. Cambia tab o filtri.",
     "No changes in this state. Switch tab or filters."),

    # Feedback handler
    ("MSG_CHANGE_DECISION_OK",
     "Decisione registrata: {action}",
     "Decision recorded: {action}"),
    ("ERR_CHANGE_NOT_FOUND",
     "Cambiamento non trovato: {id}",
     "Change not found: {id}"),
    ("ERR_CHANGE_INVALID_ACTION",
     "Azione non valida: {action}",
     "Invalid action: {action}"),

    # Deprecation banner (mostrato in /admin/proposals e /admin/promotions
    # vecchi finche' rimangono attivi come legacy view)
    ("UI_CHANGE_DEPRECATION_TITLE",
     "Pagina sostituita (ADR 0158)",
     "Page superseded (ADR 0158)"),
    ("UI_CHANGE_DEPRECATION_BODY",
     "Questa vista e' stata fusa nel lifecycle unificato.",
     "This view has been merged into the unified lifecycle."),
    ("UI_CHANGE_DEPRECATION_LINK",
     "Vai a /admin/changes",
     "Go to /admin/changes"),
]


_BOOTSTRAPPED = False


def bootstrap_keys() -> int:
    """Registra le chiavi i18n se mancanti. Ritorna n. di scritte (0 se
    tutto gia' presente). Sicuro a chiamarsi piu' volte."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return 0
    n = 0
    for key, it_text, en_text in _KEYS_IT_EN:
        if i18n.register_key_if_missing(key, it_text, en_text, needs_translation=False):
            n += 1
    _BOOTSTRAPPED = True
    return n
