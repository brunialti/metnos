"""Test loader rendering description per lang corrente con fallback first-available.

ADR 0092 Phase 4 (5/5/2026): pattern latest-wins simmetrico, NO source-of-truth
canonica. Loader risolve `manifest["description"][current_lang]`; se mancante,
fallback alla prima lingua disponibile in ordine alfabetico.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _build_test_manifest(d: Path, *, name: str, description_table: dict[str, str],
                         args_table: dict[str, dict[str, str]] | None = None) -> Path:
    """Scrive un manifest TOML di test con `[description]` table multilingua.

    args_table: optional dict {arg_name: {lang: text}}.
    """
    args_table = args_table or {}
    lines = [
        'manifest_format = "1.0"',
        f'name = "{name}"',
        'version = "0.1.0"',
        'author = "test"',
        'affinity = []',
        '',
        '[description]',
    ]
    for lang, text in description_table.items():
        # Single-line, no escape (test inputs).
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
    for arg_name, lang_table in args_table.items():
        lines.append('')
        lines.append(f'[args.properties.{arg_name}]')
        lines.append('type = "string"')
        lines.append('')
        lines.append(f'[args.properties.{arg_name}.description]')
        for lang, text in lang_table.items():
            lines.append(f'{lang} = "{text}"')
    sub = d / name
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "manifest.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Il loader (loader.py §7.3 24/5/2026) richiede il file code E la presenza
    # di `if __name__ == "__main__":` (dispatch subprocess), anche con verify=False.
    # Senza, l'executor viene scartato e cat.get(name) ritorna None. Stub minimo.
    (sub / "x.py").write_text(
        "import json, sys\n"
        "def invoke(args):\n    return {'ok': True}\n"
        'if __name__ == "__main__":\n'
        "    json.dump(invoke(json.load(sys.stdin)), sys.stdout)\n",
        encoding="utf-8")
    return sub


class TestLoaderDescriptionLang(unittest.TestCase):
    """Verify loader resolves description per current_lang with fallback."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old_default_lang = None
        # config.DEFAULT_LANG e' calcolato all'import; per controllarlo nei
        # test patchiamo il modulo.

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Reset _envs/state se contaminato.

    def _load(self, lang: str):
        """Carica il catalogo nella lingua esplicita della richiesta."""
        from loader import load_catalog
        return load_catalog(
            executors_dir=self.tmp,
            verify=False,
            include_synth=False,
            include_verb_unique=False,
            lang=lang,
        )

    def test_description_resolved_for_current_lang_it(self):
        _build_test_manifest(
            self.tmp,
            name="ex_it",
            description_table={"it": "Descrizione italiana", "en": "English description"},
        )
        cat = self._load("it")
        ex = cat.get("ex_it")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.description, "Descrizione italiana")

    def test_description_resolved_for_current_lang_en(self):
        _build_test_manifest(
            self.tmp,
            name="ex_en",
            description_table={"it": "Descrizione italiana", "en": "English description"},
        )
        cat = self._load("en")
        ex = cat.get("ex_en")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.description, "English description")

    def test_fallback_first_available_alphabetical(self):
        # Solo IT presente, lang corrente = en → fallback alphabetical first.
        _build_test_manifest(
            self.tmp,
            name="ex_only_it",
            description_table={"it": "Solo italiano"},
        )
        cat = self._load("en")
        ex = cat.get("ex_only_it")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.description, "Solo italiano")

    def test_fallback_picks_alphabetically_first(self):
        # FR + IT presenti, lang corrente = en → fallback prende FR (f < i).
        _build_test_manifest(
            self.tmp,
            name="ex_fr_it",
            description_table={"fr": "Description française", "it": "Descrizione italiana"},
        )
        cat = self._load("en")
        ex = cat.get("ex_fr_it")
        self.assertEqual(ex.description, "Description française")

    def test_args_description_resolved_per_lang(self):
        _build_test_manifest(
            self.tmp,
            name="ex_args",
            description_table={"it": "top-level it"},
            args_table={
                "foo": {"it": "Argomento foo IT", "en": "Foo argument EN"},
            },
        )
        cat_it = self._load("it")
        ex = cat_it.get("ex_args")
        self.assertEqual(
            ex.args_schema["properties"]["foo"]["description"],
            "Argomento foo IT",
        )
        cat_en = self._load("en")
        ex2 = cat_en.get("ex_args")
        self.assertEqual(
            ex2.args_schema["properties"]["foo"]["description"],
            "Foo argument EN",
        )

    def test_args_description_fallback(self):
        # foo solo in IT, current_lang=en → fallback alphabetical IT.
        _build_test_manifest(
            self.tmp,
            name="ex_args_only_it",
            description_table={"it": "top-level it"},
            args_table={"foo": {"it": "Solo italiano"}},
        )
        cat = self._load("en")
        ex = cat.get("ex_args_only_it")
        self.assertEqual(
            ex.args_schema["properties"]["foo"]["description"],
            "Solo italiano",
        )

    def test_explicit_resource_language_selects_separate_cached_catalogs(self):
        _build_test_manifest(
            self.tmp,
            name="ex_context",
            description_table={"it": "Descrizione italiana", "en": "English description"},
        )
        from loader import load_catalog

        cat_it = load_catalog(
            executors_dir=self.tmp, verify=False,
            include_synth=False, include_verb_unique=False, lang="it",
        )
        cat_en = load_catalog(
            executors_dir=self.tmp, verify=False,
            include_synth=False, include_verb_unique=False, lang="en",
        )

        self.assertEqual(cat_it.get("ex_context").description,
                         "Descrizione italiana")
        self.assertEqual(cat_en.get("ex_context").description,
                         "English description")
        self.assertIsNot(cat_it, cat_en)


class TestRealCatalogResolution(unittest.TestCase):
    """Smoke test contro il catalog reale (54 manifest migrated)."""

    def test_real_catalog_loads_with_default_lang(self):
        from loader import load_catalog
        cat = load_catalog(verify=True)
        # Almeno gli executor canonici noti devono essere presenti.
        self.assertIn("find_files", cat.executors)
        self.assertIn("read_files", cat.executors)
        self.assertIn("get_now", cat.executors)
        # description e' una stringa NON vuota.
        ex = cat.get("find_files")
        self.assertIsInstance(ex.description, str)
        self.assertGreater(len(ex.description), 50)

    def test_real_catalog_args_description_is_string(self):
        from loader import load_catalog
        cat = load_catalog(verify=True)
        ex = cat.get("find_files")
        props = ex.args_schema.get("properties", {})
        self.assertIn("base_path", props)
        bp_desc = props["base_path"].get("description")
        self.assertIsInstance(bp_desc, str)
        self.assertGreater(len(bp_desc), 10)


class TestResolveLangTextEnFallback(unittest.TestCase):
    """§K (15/6/2026): per una descrizione non tradotta nella lingua target il
    ripiego è EN ESPLICITO (non la prima lingua in ordine alfabetico)."""

    def test_missing_target_falls_back_to_en(self):
        from loader import _resolve_lang_text
        v = {"it": "IT", "en": "EN", "de": "DE"}
        # 'fr' assente: deve dare EN, non 'DE' (primo alfabetico)
        self.assertEqual(
            _resolve_lang_text(v, where="t.description", current_lang="fr"), "EN")

    def test_target_present_wins(self):
        from loader import _resolve_lang_text
        v = {"it": "IT", "en": "EN"}
        self.assertEqual(
            _resolve_lang_text(v, where="t.description", current_lang="it"), "IT")

    def test_no_en_falls_back_alphabetical(self):
        from loader import _resolve_lang_text
        v = {"it": "IT", "de": "DE"}  # niente EN
        self.assertEqual(
            _resolve_lang_text(v, where="t.description", current_lang="fr"), "DE")


if __name__ == "__main__":
    unittest.main()
