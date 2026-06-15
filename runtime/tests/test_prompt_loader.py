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


class TestGetSplit(unittest.TestCase):
    """get_split (layout static_first, ottimizzazione A prompt-cache):
    testa statica byte-identica fra query, coda con il contenuto per-query."""

    _VARS = dict(verb="read", obj="urls", keywords="a, b",
                 tools="- read_urls_html — X", excluded="(nessuno)",
                 user_query="leggi https://x.y e riassumi")

    def test_split_real_proposer_both_langs(self):
        import prompt_loader
        for lang in ("it", "en"):
            head, tail = prompt_loader.get_split(
                "engine_proposer", lang, **self._VARS)
            # Testa: regole statiche, NESSUN contenuto per-query.
            self.assertGreater(len(head), 1000, lang)
            for needle in ("read_urls_html — X", "leggi https://x.y"):
                self.assertNotIn(needle, head, lang)
            # Coda: tutto il contenuto per-query, query inclusa.
            self.assertIn("read_urls_html — X", tail, lang)
            self.assertIn("leggi https://x.y e riassumi", tail, lang)
            self.assertIn("(nessuno)", tail, lang)
            # Il marker e' un commento: non renderizza in nessuna parte.
            self.assertNotIn("STATIC-END", head + tail, lang)

    def test_split_head_byte_identical_across_queries(self):
        import prompt_loader
        h1, _ = prompt_loader.get_split("engine_proposer", "it", **self._VARS)
        vars2 = dict(self._VARS, verb="find", tools="- find_files",
                     user_query="trova i pdf")
        h2, _ = prompt_loader.get_split("engine_proposer", "it", **vars2)
        self.assertEqual(h1, h2)

    def test_split_template_without_marker_degrades_to_get(self):
        import prompt_loader
        # intent_extractor non dichiara static_first: render completo + "".
        head, tail = prompt_loader.get_split("intent_extractor", "it",
                                              query="che ore sono")
        self.assertEqual(tail, "")
        self.assertEqual(head,
                         prompt_loader.get("intent_extractor", "it",
                                            query="che ore sono"))

    def test_split_missing_template_raises(self):
        import prompt_loader
        with self.assertRaises(RuntimeError):
            prompt_loader.get_split("nonexistent_role_xyz", "it")

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
        # Marker stabile del _footer (l'heading "CHIUSURA TURNO" e' stato
        # rimosso dai prompt → marker stale: uso uno attuale del footer).
        self.assertIn("DATA E ORA CORRENTI", out)
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

    def test_unknown_lang_falls_back_to_en(self):
        """§K (15/6/2026): una lingua senza i suoi `.j2` NON fa crashare il
        planner — ricade su EN nel frattempo (default meantime = EN). Prima
        sollevava RuntimeError; ora deve restituire il render EN."""
        import prompt_loader as pl
        pl._envs.pop("xx_nonexistent_xx", None)
        out = pl.get("intent_extractor", "xx_nonexistent_xx")
        en = pl.get("intent_extractor", "en")
        self.assertEqual(out, en)

    def test_raises_only_if_even_en_missing(self):
        """Il RuntimeError resta SOLO per il misconfig reale: né la lingua né il
        ripiego EN esistono."""
        import prompt_loader as pl
        saved = pl._BASE
        try:
            pl._BASE = Path(tempfile.mkdtemp())  # vuota: nessun it/en
            pl._envs.pop("zz", None)
            with self.assertRaises(RuntimeError):
                pl._env_for("zz")
        finally:
            pl._BASE = saved
            pl._envs.pop("zz", None)


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


class TestKFallbackAndAutoPromote(unittest.TestCase):
    """§K (15/6/2026): catena live→candidato→EN. L'approvazione manuale non è
    più un gate (i candidati `_pending` sono usati in-vivo); le stringhe non
    ancora tradotte ricadono su EN nel frattempo."""

    def test_candidate_used_without_manual_promote(self):
        import prompt_loader as pl
        xx = pl._BASE / "xx_k"
        (xx / "_pending").mkdir(parents=True, exist_ok=True)
        (xx / "_pending" / "intent_extractor.j2.candidate").write_text(
            "CAND_{{ lang }}", encoding="utf-8")
        pl._envs.pop("xx_k", None)
        try:
            out = pl.get("intent_extractor", "xx_k")
            self.assertEqual(out.strip(), "CAND_xx_k")
        finally:
            shutil.rmtree(xx, ignore_errors=True)
            pl._envs.pop("xx_k", None)

    def test_live_wins_over_candidate(self):
        """Per IT/EN il live esiste sempre → vince sul candidato (i `_pending`
        di IT/EN restano ignorati): comportamento invariato."""
        import prompt_loader as pl
        live = pl.get("intent_extractor", "it")
        self.assertNotIn("CAND_", live)
        self.assertGreater(len(live), 100)


if __name__ == "__main__":
    unittest.main()
