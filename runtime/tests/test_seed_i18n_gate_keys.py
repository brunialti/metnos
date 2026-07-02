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

# Chiavi user-facing introdotte dal flusso gate-resume/consenso (20/6).
_REQUIRED_KEYS = (
    "MSG_CONSENT_GATE_OUTBOUND",
    "MSG_CONSENT_GATE_OUTBOUND_N",
    "MSG_CONSENT_GATE_OUTBOUND_BRIEF",
    "MSG_GATE_NO_ACTION",
    "MSG_DIALOG_COMPLETED",
    "MSG_DIALOG_STEP_ERROR",
    "MSG_DIALOG_STEP_REPROMPT",
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


if __name__ == "__main__":
    unittest.main()
