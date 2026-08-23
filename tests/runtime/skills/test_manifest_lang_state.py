"""Test `manifest.lang_state.json` round-trip + version_hash detection.

ADR 0092 Phase 4 (5/5/2026): file siblings di manifest.toml che traccia
version_hash + source_lang + source_hash per ogni risorsa testuale per lingua.

Pattern latest-wins: la lingua con `version_hash` divergente dal precedente
snapshot (calcolato sul testo corrente) e' la "edit source"; le altre
lingue vengono ritradotte con `source_hash = edit_source.version_hash`.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from i18n_translator import (  # noqa: E402
    _decide_edit_source,
    _enumerate_textual_resources,
    _load_lang_state,
    _save_lang_state,
    _sha256_text,
    align_manifest_descriptions,
)


def _h(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestSha256TextHelper(unittest.TestCase):

    def test_format(self):
        out = _sha256_text("hello")
        self.assertTrue(out.startswith("sha256:"))
        # Hex prefix '2cf24d' come sha256('hello').
        self.assertEqual(
            out,
            "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    def test_unicode_safe(self):
        out = _sha256_text("ciao 日本")
        self.assertTrue(out.startswith("sha256:"))


class TestLoadSaveLangState(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_file_returns_empty_dict(self):
        out = _load_lang_state(self.tmp / "nonexistent.json")
        self.assertEqual(out, {})

    def test_save_then_load_roundtrip(self):
        path = self.tmp / "manifest.lang_state.json"
        state = {
            "description": {
                "it": {
                    "version_hash": _h("Ciao"),
                    "source_lang": None,
                    "source_hash": None,
                },
                "en": {
                    "version_hash": _h("Hello"),
                    "source_lang": "it",
                    "source_hash": _h("Ciao"),
                },
            },
        }
        _save_lang_state(path, state)
        loaded = _load_lang_state(path)
        self.assertEqual(loaded, state)

    def test_corrupt_file_returns_empty(self):
        path = self.tmp / "corrupt.json"
        path.write_text("{not valid json", encoding="utf-8")
        out = _load_lang_state(path)
        self.assertEqual(out, {})


class TestEnumerateTextualResources(unittest.TestCase):

    def test_top_level_description(self):
        manifest = {
            "description": {"it": "Ciao", "en": "Hello"},
            "args": {"properties": {}},
        }
        out = _enumerate_textual_resources(manifest)
        self.assertEqual(len(out), 1)
        key, table = out[0]
        self.assertEqual(key, "description")
        self.assertEqual(table, {"it": "Ciao", "en": "Hello"})

    def test_args_description(self):
        manifest = {
            "description": {"it": "Top"},
            "args": {
                "properties": {
                    "foo": {
                        "type": "string",
                        "description": {"it": "Foo IT", "en": "Foo EN"},
                    },
                    "bar": {"type": "integer"},
                },
            },
        }
        out = _enumerate_textual_resources(manifest)
        keys = [k for k, _ in out]
        self.assertIn("description", keys)
        self.assertIn("args.properties.foo.description", keys)
        # bar non ha description → non listato.
        self.assertNotIn("args.bar.description", keys)

    def test_legacy_string_description_skipped(self):
        # Se per qualche motivo description e' string (non dict), NON viene listata.
        manifest = {"description": "legacy string", "args": {"properties": {}}}
        out = _enumerate_textual_resources(manifest)
        self.assertEqual(out, [])


class TestDecideEditSource(unittest.TestCase):

    def test_no_edit_when_state_matches(self):
        lang_table = {"it": "Ciao", "en": "Hello"}
        state = {
            "description": {
                "it": {"version_hash": _h("Ciao"), "source_lang": None,
                       "source_hash": None},
                "en": {"version_hash": _h("Hello"), "source_lang": "it",
                       "source_hash": _h("Ciao")},
            },
        }
        out = _decide_edit_source(state, "description", lang_table)
        self.assertIsNone(out)

    def test_edit_source_detected_when_text_changed(self):
        # IT modificato: state ha vecchio hash, lang_table ha nuovo testo.
        lang_table = {"it": "Ciao MODIFICATO", "en": "Hello"}
        state = {
            "description": {
                "it": {"version_hash": _h("Ciao"), "source_lang": None,
                       "source_hash": None},
                "en": {"version_hash": _h("Hello"), "source_lang": "it",
                       "source_hash": _h("Ciao")},
            },
        }
        out = _decide_edit_source(state, "description", lang_table)
        self.assertEqual(out, "it")

    def test_edit_source_alphabetical_tiebreak(self):
        # Sia IT sia EN cambiati: tie-break alfabetico → en (e < i).
        lang_table = {"it": "Ciao NEW", "en": "Hello NEW"}
        state = {
            "description": {
                "it": {"version_hash": _h("Ciao"), "source_lang": None,
                       "source_hash": None},
                "en": {"version_hash": _h("Hello"), "source_lang": "it",
                       "source_hash": _h("Ciao")},
            },
        }
        out = _decide_edit_source(state, "description", lang_table)
        self.assertEqual(out, "en")

    def test_lang_not_in_state_treated_as_edited(self):
        # Lang aggiunta nuova rispetto a state → e' "newly edited".
        lang_table = {"it": "Ciao", "en": "Hello"}
        state = {
            "description": {
                "it": {"version_hash": _h("Ciao"), "source_lang": None,
                       "source_hash": None},
                # en NON e' presente in state.
            },
        }
        out = _decide_edit_source(state, "description", lang_table)
        self.assertEqual(out, "en")  # only edit candidate


class TestAlignManifestDescriptionsDryRun(unittest.TestCase):
    """Smoke test su `align_manifest_descriptions(dry_run=True)`.

    Non chiama LLM (dry_run skippa la traduzione effettiva). Verifica che
    il sweep enumera correttamente i manifest e calcola gli edit-source.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_manifest(self, name: str, *, desc_table: dict[str, str]) -> Path:
        d = self.tmp / name
        d.mkdir()
        # Costruzione manuale TOML.
        lines = [
            'manifest_format = "1.0"',
            f'name = "{name}"',
            'version = "0.1.0"',
            'author = "test"',
            'affinity = []',
            '',
            '[description]',
        ]
        for lang, text in desc_table.items():
            lines.append(f'{lang} = "{text}"')
        lines.extend([
            '',
            '[code]',
            'files = ["x.py"]',
            'digest = "sha256:abc"',
            '',
            '[args]',
            'type = "object"',
            'required = []',
        ])
        (d / "manifest.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def test_dry_run_no_state_no_translate(self):
        # Senza state file, ogni lang in lang_table e' "edited" (state vuoto).
        # Edit source = primo alphabetical (en < it).
        # Dry run → annota le traduzioni che farebbe.
        d = self._build_manifest(
            "ex_a", desc_table={"it": "Ciao", "en": "Hello"},
        )
        results = align_manifest_descriptions(
            executor_dirs=[self.tmp],
            target_langs=["it", "en"],
            tier="wise",
            resign=False,
            dry_run=True,
        )
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["status"], "dry_run")
        # Almeno un'azione would_translate.
        self.assertGreaterEqual(r.get("n_would_translate", 0), 1)

    def test_in_sync_state_no_action(self):
        d = self._build_manifest(
            "ex_b", desc_table={"it": "Ciao", "en": "Hello"},
        )
        # Crea state coerente: it nuova edit-source, en synced.
        state = {
            "description": {
                "it": {
                    "version_hash": _h("Ciao"),
                    "source_lang": None,
                    "source_hash": None,
                },
                "en": {
                    "version_hash": _h("Hello"),
                    "source_lang": "it",
                    "source_hash": _h("Ciao"),
                },
            },
        }
        (d / "manifest.lang_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results = align_manifest_descriptions(
            executor_dirs=[self.tmp],
            target_langs=["it", "en"],
            tier="wise",
            resign=False,
            dry_run=True,
        )
        r = results[0]
        # No edit source detected (ogni lang corrisponde a state) → nessuna
        # would_translate.
        self.assertEqual(r["status"], "dry_run")
        self.assertEqual(r.get("n_would_translate", 0), 0)


class TestRealCatalogLangStatePresence(unittest.TestCase):
    """Verifica che i 54 manifest reali abbiano manifest.lang_state.json."""

    def test_real_executors_have_lang_state(self):
        roots = [
            Path(__file__).resolve().parents[3] / "executors",
            Path.home() / ".local" / "share" / "metnos" / "executors",
        ]
        n_total = 0
        n_with_state = 0
        for root in roots:
            if not root.exists():
                continue
            for manifest in root.glob("*/manifest.toml"):
                n_total += 1
                state = manifest.parent / "manifest.lang_state.json"
                if state.is_file():
                    n_with_state += 1
        self.assertGreaterEqual(n_total, 50)
        # Tutti i 54 dovrebbero avere lang_state dopo migrazione.
        self.assertEqual(n_with_state, n_total,
                         msg=f"manifest senza lang_state: {n_total - n_with_state}/{n_total}")

    def test_lang_state_is_valid_json_with_expected_keys(self):
        # Sample: find_files.
        path = Path(__file__).resolve().parents[3] / "executors/find_files/manifest.lang_state.json"
        if not path.is_file():
            self.skipTest("find_files manifest.lang_state.json not present")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("description", data)
        # Per find_files dovrebbe esistere almeno la lingua "it".
        self.assertIn("it", data["description"])
        entry = data["description"]["it"]
        self.assertIn("version_hash", entry)
        self.assertTrue(entry["version_hash"].startswith("sha256:"))
        # Initial state: source_lang/source_hash null.
        self.assertIsNone(entry["source_lang"])


if __name__ == "__main__":
    unittest.main()
