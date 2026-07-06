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
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
_SEED_DB = _RUNTIME.parent / "install" / "data" / "i18n_seed.sqlite"

# Chiavi user-facing introdotte dal flusso gate-resume/consenso (20/6) +
# gate mutazioni-di-massa (6/7).
_REQUIRED_KEYS = (
    "MSG_CONSENT_GATE_OUTBOUND",
    "MSG_CONSENT_GATE_OUTBOUND_N",
    "MSG_CONSENT_GATE_OUTBOUND_BRIEF",
    "MSG_GATE_NO_ACTION",
    "MSG_DIALOG_COMPLETED",
    "MSG_DIALOG_STEP_ERROR",
    "MSG_DIALOG_STEP_REPROMPT",
    "MSG_CONSENT_GATE_MASS_MUTATION",
    "MSG_ACTION_DELETE",
    "MSG_ACTION_MOVE",
    "MSG_LOCAL_HERE",
)

# Chiavi user-facing del path upload-default faceless (1/7): la risposta parte
# dalla descrizione VLM + esito ricerca foto-simili (separazione, dispatch.py).
_REQUIRED_UPLOAD_KEYS = (
    "MSG_UPLOAD_PHOTO_DESC",
    "MSG_UPLOAD_NO_SIMILAR",
    "MSG_UPLOAD_SIMILAR_COUNT",
)

# Chiavi user-facing del batch truncation §2.7/§2.11 (2/7): etichette
# `truncated_what` (MSG_OBJECT_*) + dialog di allargamento cap.
_REQUIRED_TRUNCATION_KEYS = (
    "MSG_OBJECT_ENTRIES",
    "MSG_OBJECT_LINES",
    "MSG_OBJECT_URLS",
    "MSG_OBJECT_PATHS",
    "MSG_OBJECT_FILES",
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
    "UI_CHANGE_BTN_REJECT",
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


class TestSeedHasGateKeys(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
