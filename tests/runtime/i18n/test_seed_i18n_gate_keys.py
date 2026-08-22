"""test_seed_i18n_gate_keys — il seed bundled (install/data/i18n_seed.sqlite)
contiene le chiavi i18n del flusso gate-resume/dialogo (FASE 3 issue-flow).

Un fresh install seeda i18n da questo file (INSTALL_NOTES: «A fresh install MUST
seed the full catalog or user-facing strings render as <missing:MSG_*>»). Se una
rigenerazione del seed perde queste chiavi, il consent-gate e i messaggi di
dialogo uscirebbero come «<missing:...>». Questo guard lo intercetta (IT+EN).
"""
from __future__ import annotations

import sqlite3
import unittest
import re
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_SEED_DB = _RUNTIME.parent / "install" / "data" / "i18n_seed.sqlite"
_CHAT_TEMPLATE = _RUNTIME / "templates" / "chat.html"
_DIALOG_TEMPLATE = _RUNTIME / "templates" / "dialog_form.html"
_BUILDS_TEMPLATE = _RUNTIME / "templates" / "builds.html"
_SERVICES_TEMPLATE = _RUNTIME / "templates" / "services.html"
_SERVICE_HEALTH_MONITOR = _RUNTIME / "service_health_monitor.py"

_REQUIRED_FROM_STEP_KEYS = (
    "ERR_FROM_STEP_TYPE",
    "ERR_FROM_STEP_RANGE",
    "ERR_FROM_STEP_RESULT_INVALID",
    "ERR_FROM_STEP_LIST_MISSING",
)

# La chat e' una superficie i18n completa: il gate ricava le chiavi dal
# template, quindi ogni nuova label Jinja/JavaScript diventa automaticamente
# obbligatoria in IT+EN sul fresh install. Le chiavi d'errore delle API di
# sessione non compaiono nel template ma possono essere mostrate dalla chat.
_REQUIRED_CHAT_SERVER_KEYS = (
    "ERR_CHAT_SESSION_WRITE_DENIED",
    "ERR_CHAT_SESSION_REGISTER",
    "ERR_CHAT_SESSION_RESOLUTION_TOKEN",
    "ERR_CHAT_SESSION_RESOLUTION_CHANGED",
    "ERR_CHAT_SESSION_TOKEN_REQUIRED",
    "ERR_CHAT_CONVERSATION_REQUIRED",
    "ERR_CHAT_CONVERSATION_FORBIDDEN",
    "ERR_DIALOG_PARSE_KIND",
    "MSG_APPROVAL_ACCEPTED",
    "MSG_APPROVAL_REJECTED",
    "MSG_APPROVAL_QUESTION",
    "MSG_APPROVAL_METADATA",
    "MSG_APPROVAL_TERRITORY_SESSION",
    "MSG_APPROVAL_TERRITORY_PERMANENT",
    "MSG_REVERSIBILITY_REVERSIBLE",
    "MSG_REVERSIBILITY_IRREVERSIBLE",
    "MSG_REVERSIBILITY_PARTIAL",
    "ERR_EXECUTOR_CATALOG_EMPTY",
    "MSG_DESCRIBE_DIRECT_LINKS",
    "MSG_DESCRIBE_PATHS",
    "MSG_UNTITLED",
)


def _required_chat_keys() -> tuple[str, ...]:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_CHAT_TEMPLATE, _DIALOG_TEMPLATE)
    )
    template_keys = set(re.findall(r"msg\(\s*['\"]([A-Z0-9_.-]+)['\"]", source))
    return tuple(sorted(template_keys | set(_REQUIRED_CHAT_SERVER_KEYS)))


def _required_build_keys() -> tuple[str, ...]:
    """Chiavi letterali possedute dalla pagina Settings > Creazione indici."""
    source = _BUILDS_TEMPLATE.read_text(encoding="utf-8")
    return tuple(sorted(set(
        re.findall(r"msg\(\s*['\"]([A-Z0-9_.-]+)['\"]", source)
    )))


_REQUIRED_UI_SERVICES_DYNAMIC_KEYS = (
    "UI_SERVICES_ACTION_DISABLE_FEATURE",
    "UI_SERVICES_ACTION_ENABLE_FEATURE",
    "UI_SERVICES_ACTION_START",
    "UI_SERVICES_ACTION_STOP",
    "UI_SERVICES_ACTION_RESTART",
    "UI_SERVICES_STATUS_RUNNING",
    "UI_SERVICES_STATUS_FAILED",
    "UI_SERVICES_STATUS_DEGRADED",
    "UI_SERVICES_STATUS_TRANSITIONING",
    "UI_SERVICES_STATUS_MISSING",
    "UI_SERVICES_STATUS_STOPPED",
    "UI_SERVICES_DESIRED_RUNNING",
    "UI_SERVICES_DESIRED_STOPPED",
    "UI_SERVICES_SCOPE_USER",
    "UI_SERVICES_SCOPE_SYSTEM",
    "UI_SERVICES_LRE_HEALTH_ALREADY_ACTIVE",
    "UI_SERVICES_LRE_HEALTH_BINDINGS_UNAVAILABLE",
    "UI_SERVICES_LRE_HEALTH_CONFIG_INVALID",
    "UI_SERVICES_LRE_HEALTH_DATABASE_UNAVAILABLE",
    "UI_SERVICES_LRE_HEALTH_DEADLINE_EXCEEDED",
    "UI_SERVICES_LRE_HEALTH_FEATURE_DISABLED",
    "UI_SERVICES_LRE_HEALTH_RECOVERY_FAILED",
    "UI_SERVICES_LRE_HEALTH_RECOVERY_INCOMPLETE",
    "UI_SERVICES_LRE_HEALTH_SCHEMA_INCOMPATIBLE",
    "UI_SERVICES_LRE_HEALTH_STALE",
    "UI_SERVICES_LRE_HEALTH_STARTUP_FAILED",
    "UI_SERVICES_LRE_HEALTH_STATE_MISMATCH",
    "UI_SERVICES_LRE_HEALTH_STOPPED",
    "UI_SERVICES_LRE_HEALTH_UNAVAILABLE",
    "UI_SERVICES_LRE_HEALTH_WORKER_CYCLE_FAILED",
)


def _required_services_keys() -> tuple[str, ...]:
    """Literal and dynamically composed keys owned by Settings > Services."""
    source = _SERVICES_TEMPLATE.read_text(encoding="utf-8")
    literal = {
        key for key in re.findall(
            r"msg\(\s*['\"]([A-Z0-9_.-]+)['\"]", source,
        )
        if not key.endswith("_")
    }
    return tuple(sorted(
        literal | set(_REQUIRED_UI_SERVICES_DYNAMIC_KEYS)
    ))


def _required_service_alert_keys() -> tuple[str, ...]:
    """Keys rendered by the transition-based administrator notifier."""
    source = _SERVICE_HEALTH_MONITOR.read_text(encoding="utf-8")
    return tuple(sorted(set(re.findall(
        r"messages\.get\(\s*[\"']([A-Z0-9_.-]+)[\"']", source,
    ))))

# Chiavi user-facing introdotte dal flusso gate-resume/consenso (20/6) +
# gate mutazioni-di-massa (6/7).
_REQUIRED_KEYS = (
    "ERR_LRE_REQUEST_INVALID",
    "ERR_LRE_CONFIG_INVALID",
    "ERR_LRE_DISABLED",
    "ERR_LRE_EXECUTOR_CONTRACT_UNSUPPORTED",
    "ERR_LRE_IDEMPOTENCY_CONFLICT",
    "ERR_LRE_PLAN_NOT_ADMISSIBLE",
    "ERR_LRE_WORKER_UNAVAILABLE",
    "ERR_LRE_SUBMISSION_FAILED",
    "MSG_LRE_SUBMITTED",
    "MSG_LRE_SUBMITTED_WITH_SUMMARY",
    "MSG_CREDENTIAL_KIND_TITLE",
    "MSG_CREDENTIAL_KIND_PROMPT",
    "MSG_CREDENTIAL_KIND_OPTION_MAIL",
    "MSG_CREDENTIAL_KIND_OPTION_SITE",
    "MSG_CREDENTIAL_KIND_OPTION_API",
    "MSG_CREDENTIAL_ACCOUNT_NAME_PROMPT",
    "MSG_CREDENTIAL_FIELDS_TITLE",
    "MSG_CREDENTIAL_FIELD_USER",
    "MSG_CREDENTIAL_FIELD_PASSWORD",
    "MSG_CREDENTIAL_FIELD_IMAP_HOST",
    "MSG_CREDENTIAL_FIELD_IMAP_PORT",
    "MSG_CREDENTIAL_FIELD_SMTP_HOST",
    "MSG_CREDENTIAL_FIELD_SMTP_PORT",
    "MSG_CREDENTIAL_FIELD_USERNAME",
    "MSG_CREDENTIAL_FIELD_TOTP_SECRET",
    "MSG_CREDENTIAL_FIELD_API_KEY",
    "MSG_CREDENTIAL_KIND_OPTION_GITHUB",
    "MSG_CREDENTIAL_FIELD_GITHUB_TOKEN",
    "MSG_CREDENTIAL_FIELD_GITHUB_REPO",
    "MSG_CONSENT_GATE_OUTBOUND",
    "MSG_CONSENT_GATE_OUTBOUND_N",
    "MSG_CONSENT_GATE_OUTBOUND_BRIEF",
    "MSG_CONSENT_GATE_EXTERNAL_CONTEXT",
    "MSG_GATE_NO_ACTION",
    "MSG_DIALOG_COMPLETED",
    "MSG_DIALOG_STEP_ERROR",
    "MSG_DIALOG_STEP_REPROMPT",
    "MSG_DIALOG_TEMPORARILY_UNAVAILABLE",
    "MSG_CONSENT_GATE_MASS_MUTATION",
    "MSG_CONSENT_GATE_MASS_MUTATION_GENERIC",
    "MSG_REQUIRED_ACTION_NOT_PLANNED",
    "MSG_ACTION_DELETE",
    "MSG_ACTION_MOVE",
    "MSG_LOCAL_HERE",
    # Fase 7 A.1 differito (7/7)
    "MSG_DEFER_TITLE",
    "MSG_DEFER_OFFER",
    "MSG_DEFER_QUEUED",
    "MSG_DEFER_DONE",
    "MSG_DEFER_EXPIRED",
    # Fase 7 A.0/A.2/B.4 risultato-tardivo delle invocazioni remote (7/7):
    # DONE/FAILED erano nel seed ma non guardate; EXPIRED = scadenza B.4.
    "MSG_LATE_RESULT_DONE",
    "MSG_LATE_RESULT_FAILED",
    "MSG_LATE_RESULT_EXPIRED",
)

# Chiavi user-facing del path upload-default faceless (1/7): la risposta parte
# dalla descrizione VLM + esito ricerca foto-simili (separazione, dispatch.py).
_REQUIRED_UPLOAD_KEYS = (
    "MSG_UPLOAD_PHOTO_DESC",
    "MSG_UPLOAD_NO_SIMILAR",
    "MSG_UPLOAD_SIMILAR_COUNT",
)

_REQUIRED_EXECUTOR_STANDARD_KEYS = (
    "MSG_SYNTH_CANDIDATE_CREATED",
    "ERR_SYNTH_EXPECTED_NAME_REQUIRED",
    "ERR_SYNTH_MULTISTAGE_FAILED",
    "MSG_SYNTH_ALREADY_AVAILABLE",
    "MSG_SYNTH_CANONICAL_REDIRECT",
    "MSG_SYNTH_IMPORTED_REDIRECT",
    "MSG_SYNTH_BINDING_REDIRECT",
    "MSG_SYNTH_PROGRESS_START",
    "MSG_SYNTH_PROGRESS_INTERRUPTED",
    "MSG_SYNTH_PROGRESS_INSTALL_FAILED",
    "MSG_SYNTH_PROGRESS_REJECTED",
    "MSG_SYNTH_PROGRESS_ABANDONED",
    "MSG_SYNTH_REASON_OUT_OF_VOCAB",
    "MSG_SYNTH_REASON_STAGE_ERROR",
    "PROMPT_SYNTH_TOOL_DESCRIPTION",
    "PROMPT_SYNTH_EXPECTED_NAME",
    "PROMPT_SYNTH_INTENT",
    "ERR_FILE_READ_FAILED",
    "ERR_WORKSHEET_INDEX_INVALID",
    "ERR_WORKSHEET_NOT_FOUND",
    "ERR_PERSONS_REGISTRY_UNAVAILABLE",
)

_REQUIRED_STRATO3_KEYS = (
    "MSG_STRATO3_TITLE",
    "MSG_STRATO3_DESCRIPTION",
    "MSG_STRATO3_PROMPT",
    "MSG_STRATO3_ACTION_RETRY",
    "MSG_STRATO3_ACTION_SYNTH",
    "MSG_STRATO3_ACTION_FRONTIER",
    "MSG_STRATO3_ACTION_REFORMULATE",
    "MSG_STRATO3_ACTION_ABANDON",
    "PROMPT_STRATO3_SYNTH_QUERY",
    "PROMPT_STRATO3_FRONTIER_QUERY",
)

_REQUIRED_CHANNEL_KEYS = (
    "MSG_TELEGRAM_PHOTO_BATCH_CAPTION",
)

_REQUIRED_PROMOTER_DIGEST_KEYS = (
    "MSG_PROMOTER_BUTTON_CONFIRM",
    "MSG_PROMOTER_BUTTON_ROLLBACK",
    "MSG_PROMOTER_BUTTON_OPEN_REVIEW",
    "MSG_PROMOTER_DIGEST_HEADER",
    "MSG_PROMOTER_DIGEST_GRACE_UNTIL",
    "MSG_PROMOTER_DIGEST_NO_EXAMPLE",
    "MSG_PROMOTER_DIGEST_FOOTER",
    "MSG_PROMOTER_AGGREGATED_BODY",
)

_REQUIRED_PROMOTER_EXAMPLE_KEYS = (
    "MSG_PROMOTER_COMMENTARY_TITLE",
    "MSG_PROMOTER_COMMENTARY_UNAVAILABLE",
    "MSG_PROMOTER_EXAMPLE_CALLS_60D",
    "MSG_PROMOTER_EXAMPLE_CURRENT_PIPELINE",
    "MSG_PROMOTER_EXAMPLE_DOES_NOT_REPLACE",
    "MSG_PROMOTER_EXAMPLE_NEW_PIPELINE",
    "MSG_PROMOTER_EXAMPLE_QUERY",
    "MSG_PROMOTER_EXAMPLE_REPLACEMENT_SHARE",
    "MSG_PROMOTER_EXAMPLE_REPLACES",
    "MSG_PROMOTER_EXAMPLE_SCOPE_MUTATIONS",
    "MSG_PROMOTER_EXAMPLE_SCOPE_OTHER",
    "MSG_PROMOTER_EXAMPLE_SCOPE_READS",
    "MSG_PROMOTER_EXAMPLE_SYNTHETIC_QUERY",
    "MSG_PROMOTER_EXAMPLE_UNDEFINED",
    "MSG_PROMOTER_INSUFFICIENT_DATA",
    "MSG_PROMOTER_PERF_FREQUENCY_MISSING",
    "MSG_PROMOTER_PERF_FREQUENCY_VALUE",
    "MSG_PROMOTER_PERF_TIME_MISSING",
    "MSG_PROMOTER_PERF_TIME_VALUE",
    "MSG_PROMOTER_PERF_TITLE",
    "MSG_PROMOTER_PERF_TOKENS_MISSING",
    "MSG_PROMOTER_PERF_TOKENS_VALUE",
)

# Dominio sites F1/F2: tassonomia login + gate azioni sensibili. Queste chiavi
# devono esistere anche su fresh install, non soltanto nel DB di esercizio.
_REQUIRED_SITES_KEYS = (
    "MSG_SITES_ALLOWLIST_APPROVAL_TITLE",
    "MSG_SITES_ALLOWLIST_APPROVAL_PROMPT",
    "MSG_CREDENTIALS_STORED",
    "MSG_SITES_APPROVAL_TITLE",
    "MSG_SITES_APPROVAL_PROMPT",
    "MSG_SITES_CREDENTIAL_ORIGIN_APPROVAL_TITLE",
    "MSG_SITES_CREDENTIAL_ORIGIN_APPROVAL_PROMPT",
    "MSG_SITES_ACTIONS_COMPLETED",
    "MSG_SITES_RC_TWO_FACTOR_REQUIRED",
    "MSG_SITES_RC_TWO_FACTOR_PUSH_REQUIRED",
    "MSG_SITES_RC_ACCOUNT_LOCKED",
    "MSG_SITES_RC_CAPTCHA_REQUIRED",
    "MSG_SITES_RC_CREDENTIAL_USE_DISABLED",
    "MSG_SITES_RC_CREDENTIALS_MISSING",
    "MSG_SITES_RC_LOGIN_FAILED",
    "MSG_SITES_RC_LOGIN_TIMEOUT",
    "MSG_SITES_RC_ORIGIN_UNVERIFIED",
    "MSG_SITES_RC_PASSWORD_WRONG",
    "MSG_SITES_RC_SELECTOR_MISSING",
    "MSG_SITES_RC_SESSION_LOST",
    "MSG_SITES_RC_VAULT_ERROR",
    "MSG_SITES_RC_SIDE_BROWSER_UNAVAILABLE",
    "MSG_SETTINGS_STEALTH_TITLE",
    "MSG_SETTINGS_STEALTH_DESCRIPTION",
    "MSG_SETTINGS_STEALTH_MASTER",
    "MSG_SETTINGS_STEALTH_MASTER_HELP",
    "MSG_SETTINGS_STEALTH_WEBDRIVER",
    "MSG_SETTINGS_STEALTH_WEBDRIVER_HELP",
    "MSG_SETTINGS_STEALTH_USER_AGENT",
    "MSG_SETTINGS_STEALTH_USER_AGENT_HELP",
    "MSG_SETTINGS_STEALTH_MOBILE",
    "MSG_SETTINGS_STEALTH_MOBILE_HELP",
    "MSG_SETTINGS_STEALTH_CONTEXT_COHERENCE",
    "MSG_SETTINGS_STEALTH_CONTEXT_COHERENCE_HELP",
    "MSG_SETTINGS_STEALTH_BROWSER_APIS",
    "MSG_SETTINGS_STEALTH_BROWSER_APIS_HELP",
    "MSG_SETTINGS_STEALTH_HUMAN_DELAYS",
    "MSG_SETTINGS_STEALTH_HUMAN_DELAYS_HELP",
    "MSG_SETTINGS_STEALTH_FOCUS_EVENTS",
    "MSG_SETTINGS_STEALTH_FOCUS_EVENTS_HELP",
    "MSG_SETTINGS_STEALTH_SESSION_REUSE",
    "MSG_SETTINGS_STEALTH_SESSION_REUSE_HELP",
    "MSG_SETTINGS_WEB_BROWSING_TITLE",
    "MSG_SETTINGS_WEB_BROWSING_DESCRIPTION",
    "MSG_SETTINGS_BROWSER_MODE",
    "MSG_SETTINGS_BROWSER_HEADLESS",
    "MSG_SETTINGS_BROWSER_HEADLESS_HELP",
    "MSG_SETTINGS_BROWSER_SIDE",
    "MSG_SETTINGS_BROWSER_SIDE_HELP",
)

# Chiavi user-facing del batch truncation §2.7/§2.11 (2/7): etichette
# `truncated_what` (MSG_OBJECT_*) + dialog di allargamento cap.
_REQUIRED_TRUNCATION_KEYS = (
    "MSG_OBJECT_ENTRIES",
    "MSG_OBJECT_LINES",
    "MSG_OBJECT_URLS",
    "MSG_OBJECT_PATHS",
    "MSG_OBJECT_FILES",
    "MSG_OBJECT_DUPLICATE_FILES",
    "MSG_OBJECT_EVENTS",
    "MSG_OBJECT_PROPOSALS",
    "MSG_OBJECT_IMAGE_FILES",
    "MSG_OBJECT_SOURCES",
    "MSG_OBJECT_PROCESSES",
    "MSG_CAP_EXPAND_ASK",
    "MSG_CAP_EXPAND_TITLE",
)

# Chiave user-facing del gate platforms W3.2 (executor remoti §16.1/§16.3,
# 3/7): il messaggio quando un executor non supporta l'OS del device.
_REQUIRED_PLACEMENT_KEYS = (
    "ERR_DEVICE_PLATFORM_UNSUPPORTED",
    # remote_exec §7.13 (6/7): seeding inline rimosso, chiavi SOLO nel seed.
    "ERR_DEVICE_TIMEOUT",
    "ERR_DEVICE_UNREACHABLE",
    "ERR_DEVICE_UNKNOWN",
    "ERR_DEVICE_AMBIGUOUS",
    "ERR_DEVICE_NONE_AVAILABLE",
)

# Chiavi user-facing dell'onestà mutating §2.8 + final degenere (6/7): il
# finalizer (agent_runtime._enforce_mutating_honesty / blocco degenere) le
# risolve via `msg()` PURO — §7.13, niente più seeding bilingue in-linea nel
# sorgente. Senza la rete `register_key_if_missing`, un seed che le perde fa
# uscire «<missing:MSG_*>» su un fresh install. Questo guard lo intercetta.
_REQUIRED_HONESTY_KEYS = (
    "MSG_TERM_PATH_NOT_FOUND_ACTION",
    "MSG_MUTATE_FAILED_NONE_DONE",
    "MSG_MUTATE_NONE_DONE",
    "MSG_MUTATE_PARTIAL",
    "MSG_DEGENERATE_FINAL_MUTATIONS",
    "MSG_DEGENERATE_FINAL_ITEM_ONE",
    "MSG_DEGENERATE_FINAL_ITEMS",
    "MSG_PARTIAL_ITEM_FAILURE",
    "MSG_FALSE_SUCCESS_NOTICE",
    "MSG_FALSE_MUTATION_NOTICE",
    "MSG_MUTATE_TIMEOUT_UNCERTAIN",
    "MSG_MUTATE_PROTECTED_SKIPPED",
)

_REQUIRED_DOCUMENT_AUDIT_KEYS = (
    "MSG_DOCUMENT_AUDIT_HEADER",
    "MSG_DOCUMENT_AUDIT_CONTRADICTION",
    "MSG_DOCUMENT_AUDIT_UNREADABLE",
    "MSG_DOCUMENT_AUDIT_DUPLICATES",
)

_REQUIRED_TUTOR_KEYS = (
    "MSG_TUTOR_PENDING_PRESERVED",
    "MSG_TUTOR_MIXED_CLARIFY",
    "MSG_TUTOR_LACUNA",
    "MSG_TUTOR_ADMIN_REQUIRED",
    "MSG_TUTOR_CATALOG_INCOMPLETE",
    "MSG_TUTOR_UNAVAILABLE",
    "MSG_TUTOR_HANDOFF_TITLE",
    "MSG_TUTOR_HANDOFF_DESCRIPTION",
    "MSG_TUTOR_HANDOFF_PROMPT",
    "MSG_TUTOR_HANDOFF_CONTINUE",
    "MSG_TUTOR_HANDOFF_CANCEL",
    "MSG_TUTOR_HANDOFF_CANCELLED",
    "MSG_TUTOR_HANDOFF_INVALID",
    "MSG_TUTOR_HANDOFF_STALE",
    "MSG_TUTOR_HANDOFF_REPLAYED",
    "MSG_TUTOR_HANDOFF_FAILED",
)


# Chiavi UI /admin/changes (ADR 0158; 7/7): il bootstrap runtime
# change_intents_i18n è stato RITIRATO (§7.13: testi solo nel seed) — senza
# queste chiavi la pagina admin renderizza <missing:UI_CHANGE_*>.
_REQUIRED_UI_CHANGE_KEYS = (
    "UI_CHANGE_TITLE",
    "UI_CHANGE_SUBTITLE",
    "UI_CHANGE_TAB_PROPOSED",
    "UI_CHANGE_TAB_ACCEPTED",
    "UI_CHANGE_TAB_APPLIED",
    "UI_CHANGE_TAB_OBSERVED",
    "UI_CHANGE_TAB_FINALIZED",
    "UI_CHANGE_TAB_REJECTED",
    "UI_CHANGE_TAB_STAGED",
    "UI_CHANGE_TAB_ROLLED_BACK",
    "UI_CHANGE_TAB_FAILED",
    "UI_CHANGE_FILTER_FAMILY",
    "UI_CHANGE_FILTER_KIND",
    "UI_CHANGE_FILTER_MIN_SCORE",
    "UI_CHANGE_FILTER_LIMIT",
    "UI_CHANGE_FILTER_ALL",
    "UI_CHANGE_SHOWN",
    "UI_CHANGE_TOTAL_TAB",
    "UI_CHANGE_COL_SCORE",
    "UI_CHANGE_COL_KIND",
    "UI_CHANGE_COL_TARGET",
    "UI_CHANGE_COL_ORIGIN",
    "UI_CHANGE_COL_SUMMARY",
    "UI_CHANGE_COL_ACTIONS",
    "UI_CHANGE_KIND_CREATE_EXECUTOR",
    "UI_CHANGE_KIND_EXTEND_EXECUTOR",
    "UI_CHANGE_KIND_DEDUPE_EXECUTORS",
    "UI_CHANGE_KIND_MATERIALIZE_PIPELINE",
    "UI_CHANGE_KIND_CACHE_PATTERN",
    "UI_CHANGE_KIND_REJECT_PATTERN",
    "UI_CHANGE_LEGEND",
    "UI_CHANGE_LEGEND_FAMILIES",
    "UI_CHANGE_LEGEND_KINDS",
    "UI_CHANGE_LEGEND_KIND_VS_MODULE",
    "UI_CHANGE_DETAILS",
    "UI_CHANGE_DISCOVERED",
    "UI_CHANGE_EFFECT",
    "UI_CHANGE_METRICS",
    "UI_CHANGE_ROLLBACK_REASON",
    "UI_CHANGE_FAILED_REASON",
    "UI_CHANGE_CONVERGENCE_TIP",
    "UI_CHANGE_BTN_ACCEPT",
    "MSG_BTN_REJECT",
    "UI_CHANGE_BTN_STAGE",
    "UI_CHANGE_BTN_ROLLBACK",
    "UI_CHANGE_BTN_RETRY",
    "UI_CHANGE_CONFIRM_ROLLBACK",
    "UI_CHANGE_BADGE_ACCEPTED",
    "UI_CHANGE_BADGE_APPLIED",
    "UI_CHANGE_BADGE_OBSERVED",
    "UI_CHANGE_BADGE_FINALIZED",
    "UI_CHANGE_BADGE_REJECTED",
    "UI_CHANGE_BADGE_ROLLED_BACK",
    "UI_CHANGE_BADGE_FAILED",
    "UI_CHANGE_AWAITING_APPLY",
    "UI_CHANGE_EMPTY",
    "MSG_CHANGE_DECISION_OK",
    "ERR_CHANGE_NOT_FOUND",
    "ERR_CHANGE_INVALID_ACTION",
    "UI_CHANGE_DEPRECATION_TITLE",
    "UI_CHANGE_DEPRECATION_BODY",
    "UI_CHANGE_DEPRECATION_LINK",
)

_REQUIRED_UI_VIRT_KEYS = (
    "UI_SETTINGS_SKIP_CONTENT",
    "UI_SETTINGS_MENU_TOGGLE",
    "UI_SETTINGS_CHAT",
    "UI_SETTINGS_CONSOLE",
    "UI_SETTINGS_NAV_ARIA",
    "UI_SETTINGS_LOGOUT",
    "UI_VIRT_PAGE_TITLE",
    "UI_VIRT_EYEBROW",
    "UI_VIRT_TITLE",
    "UI_VIRT_LEAD",
    "UI_VIRT_REFRESH",
    "UI_VIRT_RELOAD",
    "UI_VIRT_REDACTION_NOTE",
    "UI_VIRT_BINDING",
    "UI_VIRT_GENERATION_POLICY",
    "UI_VIRT_PARAMETERS",
    "UI_VIRT_EDIT",
    "UI_VIRT_SAVE",
    "UI_VIRT_CANCEL",
    "UI_VIRT_RESET",
    "UI_VIRT_RESET_CONFIRM",
    "UI_VIRT_SAVED",
    "UI_VIRT_RESET_DONE",
    "UI_VIRT_EDIT_ERROR_CONFLICT",
    "UI_VIRT_EDIT_ERROR_INVALID",
    "UI_VIRT_EDIT_ERROR_WRITE",
    "UI_VIRT_CONFIG_FILE",
    "UI_VIRT_STATUS",
    "UI_VIRT_STATUS_INVALID",
    "UI_VIRT_STATUS_CONFIGURED",
    "UI_VIRT_STATUS_DEFAULTS",
    "UI_VIRT_ENV_OVERRIDE",
    "UI_VIRT_ERROR",
    "UI_VIRT_ERROR_TOML",
    # ADR 0207: fast/middle/wise mancanti nel file ricadono sul binding
    # predefinito, quindi le due diagnosi "tier obbligatorio assente" non
    # sono piu' raggiungibili. Resta la sola diagnosi per-ruolo.
    "UI_VIRT_ERROR_LLM_PROVIDER",
    "UI_VIRT_ERROR_LLM_CONFIG",
    "UI_VIRT_ORIGIN_CONFIGURED",
    "UI_VIRT_ORIGIN_ALIAS",
    "UI_VIRT_ORIGIN_FALLBACK",
    "UI_VIRT_ORIGIN_DEFAULTS",
    "UI_VIRT_VALUE_REDACTED",
    "UI_VIRT_VALUE_SANITIZED",
    "UI_VIRT_VALUE_TRUNCATED",
    "UI_VIRT_NO_FIELDS",
    "UI_VIRT_NO_ROLES",
    "UI_VIRT_FAMILY_LLM",
    "UI_VIRT_FAMILY_LLM_DESC",
    "UI_VIRT_LLM_TIP",
    "UI_VIRT_FAMILY_EMBEDDING",
    "UI_VIRT_FAMILY_EMBEDDING_DESC",
    "UI_VIRT_EMBEDDING_READONLY_TIP",
    "UI_VIRT_FAMILY_VLM",
    "UI_VIRT_FAMILY_VLM_DESC",
    "UI_VIRT_VLM_TIP",
    # Una descrizione per scheda: senza queste la pagina elenca sette nomi
    # tecnici senza dire quale lavoro passa da ciascuno.
    "UI_VIRT_ROLE_LLM_FAST_MICRO",
    "UI_VIRT_ROLE_LLM_FAST_PROCEDURAL",
    "UI_VIRT_ROLE_LLM_FAST_FIDELITY",
    "UI_VIRT_ROLE_LLM_MIDDLE",
    "UI_VIRT_ROLE_LLM_WISE",
    "UI_VIRT_ROLE_LLM_CREATIVE",
    "UI_VIRT_ROLE_LLM_FRONTIER",
    "UI_VIRT_ROLE_EMBEDDING_TEXT",
    "UI_VIRT_ROLE_EMBEDDING_IMAGE",
    "UI_VIRT_ROLE_VLM_DEFAULT",
)


class TestSeedHasGateKeys(unittest.TestCase):
    def test_from_step_errors_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): (text, pending) for key, lang, text, pending
                    in conn.execute(
                        "SELECT key, lang, text, needs_translation FROM i18n "
                        "WHERE key IN ({})".format(
                            ",".join("?" * len(_REQUIRED_FROM_STEP_KEYS))),
                        _REQUIRED_FROM_STEP_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_FROM_STEP_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text, pending = rows.get((key, lang), (None, None))
                    self.assertTrue(text and "<missing" not in text)
                    self.assertEqual(pending, 0)

    def test_seed_file_exists(self):
        self.assertTrue(_SEED_DB.is_file(), f"seed mancante: {_SEED_DB}")

    def test_gate_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_KEYS))), _REQUIRED_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_chat_keys_present_it_en(self):
        required = _required_chat_keys()
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(required))), required,
            )}
        finally:
            conn.close()
        for key in required:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(
                        text and "<missing" not in text,
                        f"seed manca {key}[{lang}] per la finestra chat",
                    )

    def test_upload_faceless_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_UPLOAD_KEYS))),
                _REQUIRED_UPLOAD_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_UPLOAD_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_executor_standard_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_EXECUTOR_STANDARD_KEYS))),
                _REQUIRED_EXECUTOR_STANDARD_KEYS,
            )}
        finally:
            conn.close()
        for key in _REQUIRED_EXECUTOR_STANDARD_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(text and "<missing" not in text)

    def test_strato3_and_promoter_keys_present_it_en(self):
        required = (
            _REQUIRED_STRATO3_KEYS
            + _REQUIRED_PROMOTER_DIGEST_KEYS
            + _REQUIRED_PROMOTER_EXAMPLE_KEYS
            + _REQUIRED_CHANNEL_KEYS
        )
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(required))), required,
            )}
        finally:
            conn.close()
        for key in required:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(text and "<missing" not in text)

    def test_sites_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_SITES_KEYS))),
                _REQUIRED_SITES_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_SITES_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(txt and "<missing" not in txt,
                                    f"seed manca {key}[{lang}]")

    def test_truncation_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_TRUNCATION_KEYS))),
                _REQUIRED_TRUNCATION_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_TRUNCATION_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_placement_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_PLACEMENT_KEYS))),
                _REQUIRED_PLACEMENT_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_PLACEMENT_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_ui_change_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_UI_CHANGE_KEYS))),
                _REQUIRED_UI_CHANGE_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_UI_CHANGE_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_ui_virt_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_UI_VIRT_KEYS))),
                _REQUIRED_UI_VIRT_KEYS,
            )}
        finally:
            conn.close()
        for key in _REQUIRED_UI_VIRT_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(
                        text and "<missing" not in text,
                        f"seed manca {key}[{lang}] per Settings > Modelli",
                    )

    def test_ui_build_keys_present_it_en(self):
        required = _required_build_keys()
        self.assertTrue(required)
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(required))), required,
            )}
        finally:
            conn.close()
        for key in required:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(
                        text and "<missing" not in text,
                        f"seed manca {key}[{lang}] per Settings > Creazione indici",
                    )

    def test_ui_services_keys_present_it_en(self):
        required = _required_services_keys()
        self.assertTrue(required)
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(required))), required,
            )}
        finally:
            conn.close()
        for key in required:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(
                        text and "<missing" not in text,
                        f"seed manca {key}[{lang}] per Settings > Servizi",
                    )

    def test_service_alert_keys_present_it_en(self):
        required = _required_service_alert_keys()
        self.assertTrue(required)
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(required))), required,
            )}
        finally:
            conn.close()
        for key in required:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(
                        text and "<missing" not in text,
                        f"seed manca {key}[{lang}] per gli avvisi servizi",
                    )

    def test_honesty_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(k, lang): text for k, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_HONESTY_KEYS))),
                _REQUIRED_HONESTY_KEYS)}
        finally:
            conn.close()
        for key in _REQUIRED_HONESTY_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    txt = rows.get((key, lang))
                    self.assertTrue(
                        txt and "<missing" not in txt,
                        f"seed manca {key}[{lang}] (rigenera install/data/"
                        f"i18n_seed.sqlite dalla i18n.sqlite di esercizio)")

    def test_document_audit_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_DOCUMENT_AUDIT_KEYS))),
                _REQUIRED_DOCUMENT_AUDIT_KEYS,
            )}
        finally:
            conn.close()
        for key in _REQUIRED_DOCUMENT_AUDIT_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(text and "<missing" not in text)

    def test_tutor_keys_present_it_en(self):
        conn = sqlite3.connect(str(_SEED_DB))
        try:
            rows = {(key, lang): text for key, lang, text in conn.execute(
                "SELECT key, lang, text FROM i18n WHERE key IN ({})".format(
                    ",".join("?" * len(_REQUIRED_TUTOR_KEYS))),
                _REQUIRED_TUTOR_KEYS,
            )}
        finally:
            conn.close()
        for key in _REQUIRED_TUTOR_KEYS:
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    text = rows.get((key, lang))
                    self.assertTrue(text and "<missing" not in text)


if __name__ == "__main__":
    unittest.main()
