"""Test del modulo runtime.prompt_loader (ADR 0092, 5/5/2026).

Lang esplicito al call site (5/5/2026): `prompt_loader.get(role, lang, **vars)`
richiede `lang` come parametro obbligatorio.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestPromptLoaderBasic(unittest.TestCase):
    """Smoke-level: il loader carica e renderizza il PLANNER reale."""

    def test_get_planner_returns_non_empty(self):
        import prompt_loader
        # Fase C (11/5/2026): il planner usa compose() (3-layer). `get()`
        # solleva su `planner.j2` perche' il file legacy e' stato rimosso.
        out = prompt_loader.compose(
            "planner",
            "it",
            sections=None,  # all
            vocab_actions="read,write",
            vocab_objects="files,messages",
            vocab_qualifiers="_csv",
            project_paths="(none)",
            users_known="(none)",
        )
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 1000)
        self.assertIn("Sei il pianificatore di Metnos", out)

    def test_get_planner_substitutes_vars(self):
        import prompt_loader
        out = prompt_loader.compose(
            "planner",
            "it",
            sections=None,
            vocab_actions="MARKER_ACTIONS_XYZ",
            vocab_objects="MARKER_OBJECTS_XYZ",
            vocab_qualifiers="MARKER_QUAL_XYZ",
            project_paths="MARKER_PROJ_XYZ",
            users_known="MARKER_USERS_XYZ",
        )
        self.assertIn("MARKER_ACTIONS_XYZ", out)
        self.assertIn("MARKER_OBJECTS_XYZ", out)
        self.assertIn("MARKER_QUAL_XYZ", out)
        self.assertIn("MARKER_PROJ_XYZ", out)
        self.assertIn("MARKER_USERS_XYZ", out)

    def test_get_missing_template_raises(self):
        import prompt_loader
        with self.assertRaises(Exception):
            prompt_loader.get("nonexistent_role_xyz", "it")

    def test_planner_preserves_escape_sequences(self):
        """Il template deve preservare `{{stepN.field}}` (escapato via {% raw %})."""
        import prompt_loader
        out = prompt_loader.compose(
            "planner",
            "it",
            sections=None,
            vocab_actions="x", vocab_objects="x", vocab_qualifiers="x",
            project_paths="x", users_known="x",
        )
        self.assertIn("{{stepN.field}}", out)
        self.assertIn("{{step1.entries}}", out)


class TestByteEquivalenceWithLegacy(unittest.TestCase):
    """Verifica che il loader produca lo stesso output del PLANNER legacy
    dato lo stesso set di kwargs (vocab + project_paths + users_known reali)."""

    def test_byte_equivalence_with_runtime_helpers(self):
        # Carica i helper veri usati da agent_runtime.run_turn
        import prompt_loader
        from vocab import (
            render_actions_inline as _vocab_actions,
            render_objects_inline as _vocab_objects,
            render_qualifiers_inline as _vocab_qualifiers,
        )
        from agent_runtime import (
            _render_project_paths_block,
            _render_users_known_block,
        )
        out = prompt_loader.compose(
            "planner",
            "it",
            sections=None,
            vocab_actions=_vocab_actions(),
            vocab_objects=_vocab_objects(),
            vocab_qualifiers=_vocab_qualifiers(),
            project_paths=_render_project_paths_block(),
            users_known=_render_users_known_block(),
        )
        # La size esatta dipende dal vocab + users + projects correnti; ma
        # devono essere presenti i marker stabili del PLANNER.
        self.assertIn("Sei il pianificatore di Metnos", out)
        self.assertIn("DATA PIPING", out)
        self.assertIn("CHIUSURA TURNO", out)
        self.assertIn("SCOPE DEFAULT", out)
        # Non deve restare nessun __PLACEHOLDER__ non risolto.
        self.assertNotIn("__VOCAB_OBJECTS__", out)
        self.assertNotIn("__VOCAB_ACTIONS__", out)
        self.assertNotIn("__PROJECT_PATHS__", out)
        self.assertNotIn("__USERS_KNOWN__", out)


class TestValidateInvariant(unittest.TestCase):
    """Boot-time invariant: ogni sub-dir lingua ha lo stesso set di file di it/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validate_invariant_passes_when_only_it_present(self):
        base = Path(self.tmp) / "prompts"
        (base / "it").mkdir(parents=True)
        (base / "it" / "planner.j2").write_text("hello")
        import prompt_loader as pl
        old_base = pl._BASE
        try:
            pl._BASE = base
            pl.validate_invariant()  # no raise
        finally:
            pl._BASE = old_base

    def test_validate_invariant_passes_when_aligned(self):
        base = Path(self.tmp) / "prompts"
        (base / "it").mkdir(parents=True)
        (base / "en").mkdir(parents=True)
        (base / "it" / "foo.j2").write_text("ciao")
        (base / "en" / "foo.j2").write_text("hello")
        import prompt_loader as pl
        old_base = pl._BASE
        try:
            pl._BASE = base
            pl.validate_invariant()  # no raise
        finally:
            pl._BASE = old_base

    def test_validate_invariant_fails_when_secondary_missing_file(self):
        base = Path(self.tmp) / "prompts"
        (base / "it").mkdir(parents=True)
        (base / "en").mkdir(parents=True)
        (base / "it" / "vaglio.j2").write_text("ciao")
        (base / "it" / "synt.j2").write_text("synt it")
        (base / "en" / "vaglio.j2").write_text("hello")
        # en/ manca synt.j2
        import prompt_loader as pl
        old_base = pl._BASE
        try:
            pl._BASE = base
            with self.assertRaises(RuntimeError) as ctx:
                pl.validate_invariant()
            msg = str(ctx.exception)
            self.assertIn("en", msg)
            # Post-Fase C: il messaggio elenca "roles" (stem, no .j2). Per
            # file flat top-level role = stem; per split planner role include
            # path subdir come `planner/_core`.
            self.assertIn("synt", msg)
        finally:
            pl._BASE = old_base

    def test_validate_invariant_fails_when_canonical_dir_missing(self):
        base = Path(self.tmp) / "prompts"
        # Niente it/ → fail
        (base / "en").mkdir(parents=True)
        import prompt_loader as pl
        old_base = pl._BASE
        try:
            pl._BASE = base
            with self.assertRaises(RuntimeError):
                pl.validate_invariant()
        finally:
            pl._BASE = old_base


class TestRootResolution(unittest.TestCase):
    """Boot-time: la directory canonica `it/` deve esistere; il loader la
    risolve lazy dentro `_env_for(lang)`."""

    def test_canonical_it_dir_exists(self):
        import prompt_loader as pl
        self.assertTrue((pl._BASE / "it").is_dir())

    def test_planner_template_exists_in_it(self):
        """Fase C (11/5/2026): il planner e' splittato in 3 layer.
        Verifica che esistano _core + _footer + almeno una sezione."""
        import prompt_loader as pl
        self.assertTrue((pl._BASE / "it" / "planner" / "_core.j2").is_file())
        self.assertTrue((pl._BASE / "it" / "planner" / "_footer.j2").is_file())
        sections = list((pl._BASE / "it" / "planner" / "sections").glob("*.j2"))
        self.assertGreater(len(sections), 0)

    def test_unknown_lang_raises_runtime_error(self):
        import prompt_loader as pl
        with self.assertRaises(RuntimeError) as ctx:
            pl.get("planner", "xx_nonexistent_xx",
                   vocab_actions="x", vocab_objects="x", vocab_qualifiers="x",
                   project_paths="x", users_known="x")
        self.assertIn("xx_nonexistent_xx", str(ctx.exception))


class TestMultiLangIsolation(unittest.TestCase):
    """Verifica che get(role, 'it') e get(role, 'en') ritornino contenuti
    diversi senza alcun env globale: solo il param `lang` discrimina."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "it").mkdir()
        (self.tmp / "en").mkdir()
        (self.tmp / "it" / "foo.j2").write_text("CIAO_{{ name }}_IT", encoding="utf-8")
        (self.tmp / "en" / "foo.j2").write_text("HELLO_{{ name }}_EN", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_langs_isolated(self):
        import prompt_loader as pl
        old_base = pl._BASE
        old_envs = pl._envs.copy()
        try:
            pl._BASE = self.tmp
            pl._envs = {}  # reset cache
            out_it = pl.get("foo", "it", name="Roberto")
            out_en = pl.get("foo", "en", name="Roberto")
            self.assertIn("CIAO_Roberto_IT", out_it)
            self.assertIn("HELLO_Roberto_EN", out_en)
            self.assertNotEqual(out_it, out_en)
        finally:
            pl._BASE = old_base
            pl._envs = old_envs

    def test_env_cache_per_lang(self):
        import prompt_loader as pl
        old_base = pl._BASE
        old_envs = pl._envs.copy()
        try:
            pl._BASE = self.tmp
            pl._envs = {}
            pl.get("foo", "it", name="x")
            pl.get("foo", "en", name="x")
            self.assertIn("it", pl._envs)
            self.assertIn("en", pl._envs)
            # Stesso lang riusa la stessa Environment (cache hit).
            env_it_first = pl._envs["it"]
            pl.get("foo", "it", name="y")
            self.assertIs(pl._envs["it"], env_it_first)
        finally:
            pl._BASE = old_base
            pl._envs = old_envs


if __name__ == "__main__":
    unittest.main()
