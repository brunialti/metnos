"""Test pattern latest-wins su Layer 1 (prompts .j2) + helpers `.lang_state.json`.

Estensione ADR 0092 (6/5/2026): file siblings `runtime/prompts/<lang>/.lang_state.json`
con map `{role: {version_hash, source_lang, source_hash}}`.

Pattern latest-wins simmetrico:
- IT non e' source-of-truth canonica: qualsiasi lingua editata diventa
  edit-source per le altre.
- detect edit via hash content (no mtime): robusto a touch/sed.
- edit-source fra piu' lingue edite: mtime piu' recente, tie-break alfabetico.
- ritraduzione: se source_hash != edit_source.version_hash o file mancante.
- idempotenza: secondo cycle senza edit non triggera nulla.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _h(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestLangStateRoundTrip(unittest.TestCase):
    """load_lang_state / save_lang_state in `prompt_loader`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Patch _BASE in prompt_loader to point al tmp.
        import prompt_loader
        self._orig_base = prompt_loader._BASE
        prompt_loader._BASE = self.tmp
        # Reset env cache so tests don't leak
        prompt_loader._envs.clear()
        self.prompt_loader = prompt_loader

    def tearDown(self):
        self.prompt_loader._BASE = self._orig_base
        self.prompt_loader._envs.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty(self):
        out = self.prompt_loader.load_lang_state("xx")
        self.assertEqual(out, {})

    def test_save_then_load_roundtrip(self):
        state = {
            "planner": {
                "version_hash": _h("Sei un pianificatore."),
                "source_lang": None,
                "source_hash": None,
            },
            "synt_naming": {
                "version_hash": _h("Naming convention."),
                "source_lang": "it",
                "source_hash": _h("Naming convention IT."),
            },
        }
        (self.tmp / "it").mkdir()
        self.prompt_loader.save_lang_state("it", state)
        # File esiste in posizione attesa.
        self.assertTrue((self.tmp / "it" / ".lang_state.json").is_file())
        out = self.prompt_loader.load_lang_state("it")
        self.assertEqual(out, state)

    def test_save_creates_dir(self):
        # Lang dir mancante: save deve crearla.
        state = {"planner": {"version_hash": _h("X"), "source_lang": None,
                              "source_hash": None}}
        self.prompt_loader.save_lang_state("fr", state)
        self.assertTrue((self.tmp / "fr").is_dir())
        self.assertTrue((self.tmp / "fr" / ".lang_state.json").is_file())

    def test_load_corrupt_json_returns_empty(self):
        (self.tmp / "it").mkdir()
        (self.tmp / "it" / ".lang_state.json").write_text("not valid json {")
        out = self.prompt_loader.load_lang_state("it")
        self.assertEqual(out, {})


class TestAlignPromptsPattern(unittest.TestCase):
    """Pattern latest-wins su `align_prompts`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # i18n_translator align_prompts usa `Path(__file__).parent / "prompts"`.
        # Costruiamo la struttura prompts/ DENTRO al tmp per simulare:
        #   tmp/i18n_translator.py (file fittizio)
        #   tmp/prompts/it/...
        #   tmp/prompts/en/...
        self.prompts_root = self.tmp / "prompts"
        self.prompts_root.mkdir(parents=True)
        (self.prompts_root / "it").mkdir(parents=True)
        (self.prompts_root / "en").mkdir(parents=True)
        # Fake i18n_translator.py file in tmp per Path(__file__).parent =tmp.
        (self.tmp / "i18n_translator.py").write_text("# fake")
        import prompt_loader
        import i18n_translator
        self._orig_pl_base = prompt_loader._BASE
        prompt_loader._BASE = self.prompts_root
        prompt_loader._envs.clear()
        self.prompt_loader = prompt_loader
        self.i18n_translator = i18n_translator

    def tearDown(self):
        self.prompt_loader._BASE = self._orig_pl_base
        self.prompt_loader._envs.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_role(self, role: str, content_by_lang: dict[str, str]):
        """Crea i file `.j2` per ogni lang con content dato."""
        for lang, content in content_by_lang.items():
            d = self.prompts_root / lang
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{role}.j2").write_text(content, encoding="utf-8")

    def _patch_align_prompts(self):
        """Mock `align_prompts` per usare self.tmp invece di runtime/prompts."""
        # align_prompts costruisce prompts_dir = Path(__file__).parent/prompts.
        # Patchiamo `Path` usato dentro i18n_translator? Simpler: monkey-patch
        # la costante.
        # Strategia: chiamare align_prompts con un override iniettato via
        # patch di `runtime_dir = Path(__file__).parent` non e' diretto. Usiamo
        # mock.patch su `Path` dentro il modulo. Semplice: rinominiamo
        # internal var. Qui usiamo unittest.mock.patch su `Path(__file__).parent`
        # via patching di __file__.
        from unittest.mock import patch
        return patch("i18n_translator.Path", side_effect=Path)

    def test_first_run_translates_missing_targets(self):
        """First run (state vuoto): IT presente, EN mancante → genera candidate EN."""
        self._setup_role("greeting", {"it": "Ciao mondo!"})
        # Mock translate_prompt_file per evitare LLM call reale.
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            mtp.return_value = {
                "ok": True, "role": "greeting",
                "candidate_path": str(self.tmp / "en/_pending/greeting.j2.candidate"),
                "it_len": 10, "en_len": 12, "ratio": 1.2, "validation": [],
            }
            # Patch the runtime_dir used inside align_prompts.
            with mock.patch.object(
                self.i18n_translator, "Path", lambda p: Path(p)
            ):
                # Override the module __file__ for path resolution
                with mock.patch.object(
                    self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
                ):
                    results = self.i18n_translator.align_prompts(target_langs=["it", "en"])
            # translate_prompt_file should have been called for it→en
            self.assertTrue(mtp.called)
            # results should include role="greeting" with status aligned
            roles_in_results = {r.get("role") for r in results}
            self.assertIn("greeting", roles_in_results)

    def test_idempotent_no_edit_no_retranslate(self):
        """Secondo run senza modifiche → nessuna call al translator."""
        self._setup_role("greeting", {"it": "Ciao!", "en": "Hi!"})
        # Manually populate state file as if first cycle had completed.
        cur_it_hash = _h("Ciao!")
        cur_en_hash = _h("Hi!")
        (self.prompts_root / "it" / ".lang_state.json").write_text(json.dumps({
            "greeting": {"version_hash": cur_it_hash, "source_lang": None,
                          "source_hash": None},
        }), encoding="utf-8")
        (self.prompts_root / "en" / ".lang_state.json").write_text(json.dumps({
            "greeting": {"version_hash": cur_en_hash, "source_lang": "it",
                          "source_hash": cur_it_hash},
        }), encoding="utf-8")
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            with mock.patch.object(
                self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
            ):
                results = self.i18n_translator.align_prompts(target_langs=["it", "en"])
            # No retranslation should happen.
            self.assertFalse(mtp.called,
                f"translate_prompt_file should NOT be called on idempotent run; "
                f"results={results}")

    def test_edit_detected_via_hash_change(self):
        """Edit IT (hash cambia) → marca newly_edited e ritraduce verso EN."""
        # Setup: state con hash vecchio per IT.
        self._setup_role("greeting", {"it": "NUOVO IT!", "en": "Hi!"})
        old_it_hash = _h("VECCHIO IT")  # diverso dal corrente
        cur_en_hash = _h("Hi!")
        (self.prompts_root / "it" / ".lang_state.json").write_text(json.dumps({
            "greeting": {"version_hash": old_it_hash, "source_lang": None,
                          "source_hash": None},
        }), encoding="utf-8")
        (self.prompts_root / "en" / ".lang_state.json").write_text(json.dumps({
            "greeting": {"version_hash": cur_en_hash, "source_lang": "it",
                          "source_hash": old_it_hash},
        }), encoding="utf-8")
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            mtp.return_value = {
                "ok": True, "role": "greeting",
                "candidate_path": str(self.tmp / "en/_pending/greeting.j2.candidate"),
                "validation": [],
            }
            with mock.patch.object(
                self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
            ):
                results = self.i18n_translator.align_prompts(target_langs=["it", "en"])
            # IT è stata edited (hash file != hash state), should trigger
            # translate it→en.
            self.assertTrue(mtp.called,
                f"Expected translate call after edit detected; results={results}")

    def test_edit_source_resolution_mtime_then_alpha(self):
        """Quando piu' lang edite simultaneamente: mtime piu' recente vince,
        tie-break alfabetico."""
        # Crea IT e EN con state vuoto per entrambi (entrambi "edited").
        self._setup_role("greeting", {"it": "ITedit", "en": "ENedit", "fr": "FRedit"})
        # Mtime: rendi EN piu' recente di IT e FR.
        en_path = self.prompts_root / "en" / "greeting.j2"
        it_path = self.prompts_root / "it" / "greeting.j2"
        fr_path = self.prompts_root / "fr" / "greeting.j2"
        # Set diversi mtime
        now = time.time()
        os.utime(it_path, (now - 100, now - 100))
        os.utime(fr_path, (now - 50, now - 50))
        os.utime(en_path, (now, now))  # piu' recente

        # state vuoto per tutti
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            mtp.return_value = {"ok": True, "role": "greeting",
                                  "candidate_path": "x", "validation": []}
            with mock.patch.object(
                self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
            ):
                results = self.i18n_translator.align_prompts(
                    target_langs=["it", "en", "fr"],
                )
            # First run: all 3 lang have state empty (source_hash=None) →
            # first_run path → fill missing, but here all 3 lang are present.
            # First run rule: skip retranslate, backfill source_hash.
            # → mtp NOT called on first_run when all lang present.
            self.assertFalse(mtp.called,
                f"first_run with all langs present should be no-op; "
                f"results={results}")
            # Verifica edit_source = en (mtime piu' recente).
            for r in results:
                if r.get("role") == "greeting":
                    self.assertEqual(r.get("edit_source"), "en")

    def test_dry_run_no_writes(self):
        """dry_run=True: niente file scritti, solo report."""
        self._setup_role("greeting", {"it": "Ciao!"})
        with mock.patch.object(
            self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
        ):
            self.i18n_translator.align_prompts(target_langs=["it", "en"],
                                                  dry_run=True)
        # No state file should be written.
        self.assertFalse((self.prompts_root / "it" / ".lang_state.json").is_file())
        self.assertFalse((self.prompts_root / "en" / ".lang_state.json").is_file())


class TestSha256Helper(unittest.TestCase):
    """Sanity check su _sha256_text (helper riusato dal pattern)."""

    def test_format(self):
        from i18n_translator import _sha256_text
        out = _sha256_text("hello")
        self.assertTrue(out.startswith("sha256:"))
        self.assertEqual(len(out), len("sha256:") + 64)


class TestE2ESimulatedEdit(unittest.TestCase):
    """E2E: simula edit di prompts/it/planner.j2 → daemon detecta → traduce
    verso en/. Mostra lang_state.json prima/dopo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.prompts_root = self.tmp / "prompts"
        self.prompts_root.mkdir(parents=True)
        (self.prompts_root / "it").mkdir(parents=True)
        (self.prompts_root / "en").mkdir(parents=True)
        (self.tmp / "i18n_translator.py").write_text("# fake")
        import prompt_loader
        import i18n_translator
        self._orig_pl_base = prompt_loader._BASE
        prompt_loader._BASE = self.prompts_root
        prompt_loader._envs.clear()
        self.prompt_loader = prompt_loader
        self.i18n_translator = i18n_translator

    def tearDown(self):
        self.prompt_loader._BASE = self._orig_pl_base
        self.prompt_loader._envs.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_e2e_edit_it_triggers_translate_en(self):
        """E2E flow:
        1. Genesi: planner.j2 IT + EN allineati. Daemon ciclo 1: backfill state.
        2. User edita planner.j2 IT (hash cambia).
        3. Daemon ciclo 2: detecta edit, edit_src=it, traduce → en/_pending/.
        """
        # 1. Genesi
        it_planner_orig = "Sei il pianificatore di Metnos. Versione 1."
        en_planner_orig = "You are the Metnos planner. Version 1."
        (self.prompts_root / "it" / "planner.j2").write_text(it_planner_orig)
        (self.prompts_root / "en" / "planner.j2").write_text(en_planner_orig)

        # Daemon ciclo 1: state vuoto → first_run backfill.
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            with mock.patch.object(
                self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
            ):
                cycle1 = self.i18n_translator.align_prompts(target_langs=["it", "en"])
            self.assertFalse(mtp.called, "first_run should NOT translate")

        # State ora popolato.
        it_state = self.prompt_loader.load_lang_state("it")
        en_state = self.prompt_loader.load_lang_state("en")
        # IT version_hash su file IT.
        self.assertEqual(it_state["planner"]["version_hash"], _h(it_planner_orig))
        # EN version_hash su file EN, source_hash backfillato sulla edit_src
        # alfabetic-tiebreak (qui mtime uguale → alfabetico → 'en' o 'it' a
        # parita' di mtime; su python sorted con (-mtime, lang) → 'en' e' first).
        self.assertEqual(en_state["planner"]["version_hash"], _h(en_planner_orig))
        # 2. User edita IT (cambia il content).
        it_planner_new = "Sei il pianificatore di Metnos. Versione 2 NUOVA."
        (self.prompts_root / "it" / "planner.j2").write_text(it_planner_new)
        # Forza mtime IT > EN per assicurare che IT sia edit-source.
        en_path = self.prompts_root / "en" / "planner.j2"
        it_path = self.prompts_root / "it" / "planner.j2"
        now = time.time()
        os.utime(en_path, (now - 100, now - 100))
        os.utime(it_path, (now, now))

        # Daemon ciclo 2: detecta IT edit, edit_src=it (mtime piu' recente),
        # traduce verso en.
        with mock.patch.object(self.i18n_translator, "translate_prompt_file") as mtp:
            mtp.return_value = {
                "ok": True, "role": "planner",
                "candidate_path": str(self.prompts_root / "en/_pending/planner.j2.candidate"),
                "validation": [],
            }
            with mock.patch.object(
                self.i18n_translator, "__file__", str(self.tmp / "i18n_translator.py")
            ):
                cycle2 = self.i18n_translator.align_prompts(target_langs=["it", "en"])
            self.assertTrue(mtp.called,
                f"cycle2: edit detected, should translate. results={cycle2}")
            # Verifica chiamata: source_lang=it, target_lang=en.
            args, kwargs = mtp.call_args
            self.assertEqual(kwargs.get("source_lang"), "it")
            self.assertEqual(kwargs.get("target_lang"), "en")

        # State dopo cycle2: IT version_hash aggiornato.
        it_state_after = self.prompt_loader.load_lang_state("it")
        self.assertEqual(
            it_state_after["planner"]["version_hash"], _h(it_planner_new),
            "IT version_hash should be updated to new content hash"
        )
        # EN state: source_hash punta al nuovo IT hash.
        en_state_after = self.prompt_loader.load_lang_state("en")
        self.assertEqual(
            en_state_after["planner"]["source_hash"], _h(it_planner_new),
            "EN source_hash should track new IT version_hash"
        )
        # Print state prima/dopo per visibilita' nei log del run.
        print("\n=== lang_state.json BEFORE edit ===")
        print(f"  it: {it_state.get('planner')}")
        print(f"  en: {en_state.get('planner')}")
        print("=== lang_state.json AFTER edit + cycle2 ===")
        print(f"  it: {it_state_after.get('planner')}")
        print(f"  en: {en_state_after.get('planner')}")


if __name__ == "__main__":
    unittest.main()
