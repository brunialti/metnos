"""Test del modulo i18n_translator esteso per traduzione di prompt LLM lunghi
(ADR 0092 Phase 3, 5/5/2026).

I test coprono unicamente la pipeline DETERMINISTICA (mask/unmask, mappa
prescrittiva, validation). La call LLM e' montata stub-side: nessun
network, nessun LLM reale richiesto per il green.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import i18n_translator as itx  # noqa: E402


class TestMaskInvariantSpans(unittest.TestCase):
    """Sentinel di preservazione: i pattern critici NON devono essere
    inviati al LLM. Vengono sostituiti con sentinel UUID e ripristinati
    nel post-pass."""

    def test_jinja_expression_masked(self):
        text = "Hello {{ name }} world"
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertNotIn("{{ name }}", masked)
        self.assertEqual(len(mapping), 1)
        # restore deve riportare la stringa originale
        restored = itx._restore_invariant_spans(masked, mapping)
        self.assertEqual(restored, text)

    def test_jinja_raw_block_masked(self):
        text = "Esempio {% raw %}{{stepN.field}}{% endraw %} ecco"
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertNotIn("{% raw %}", masked)
        self.assertNotIn("{{stepN.field}}", masked)
        # raw block deve essere mascherato come UN solo span
        self.assertEqual(len(mapping), 1)
        restored = itx._restore_invariant_spans(masked, mapping)
        self.assertEqual(restored, text)

    def test_inline_backtick_masked(self):
        text = "Use `read_messages` then `describe_entries`"
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertNotIn("`read_messages`", masked)
        self.assertNotIn("`describe_entries`", masked)
        self.assertEqual(len(mapping), 2)
        restored = itx._restore_invariant_spans(masked, mapping)
        self.assertEqual(restored, text)

    def test_code_fence_block_masked(self):
        text = "Header\n```python\ndef foo(): pass\n```\nFooter"
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertNotIn("def foo(): pass", masked)
        # un solo span per blocco
        self.assertEqual(len(mapping), 1)
        restored = itx._restore_invariant_spans(masked, mapping)
        self.assertEqual(restored, text)

    def test_jinja_if_endif_masked(self):
        text = "Pre {% if x %}A{% else %}B{% endif %} Post"
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertNotIn("{% if x %}", masked)
        self.assertNotIn("{% endif %}", masked)
        # 3 control tags + nessun {{ }}: tre span
        self.assertEqual(len(mapping), 3)
        restored = itx._restore_invariant_spans(masked, mapping)
        self.assertEqual(restored, text)

    def test_planner_real_sample_roundtrip(self):
        """Sample dal planner.j2 reale: tutti gli span devono ripristinarsi."""
        text = (
            "DEVI: usare {{ vocab_objects }} come riferimento.\n"
            "OK: `read_messages(account=\"all\")`.\n"
            "ESEMPIO {% raw %}{{step1.entries}}{% endraw %}.\n"
            "```python\nx = 1\n```\nFine."
        )
        masked, mapping = itx._mask_invariant_spans(text)
        self.assertGreater(len(mapping), 0)
        self.assertEqual(itx._restore_invariant_spans(masked, mapping), text)

    def test_nested_sentinel_restoration(self):
        """Caso reale dal planner.j2: backtick inline che WRAPPA un raw block.

        Il primo pattern (raw block) maschera l'interno → S1.
        Il pattern inline-backticks (ultimo) vede `S1` e maschera tutto → S2.
        Restore in singola passata fallirebbe (S1 viene replaced PRIMA di S2
        ma S1 non e' visibile fino a che S2 e' replaced).
        Restore loop fino a fixpoint deve gestire questo caso.
        """
        text = "Use placeholder syntax `{% raw %}{{stepN.field}}{% endraw %}` here."
        masked, mapping = itx._mask_invariant_spans(text)
        # Almeno 2 sentinel: il raw block + l'inline backtick wrapping.
        self.assertGreaterEqual(len(mapping), 2)
        restored = itx._restore_invariant_spans(masked, mapping)
        # Deve riportare il testo originale identico
        self.assertEqual(restored, text,
                          "nested sentinels should restore to original text")


class TestPrescriptiveMap(unittest.TestCase):
    """Mappa fissa CLAUDE.md §6: DEVI→MUST, NON DEVI→MUST NOT, OK:→OK:,
    ERRORE:→ERROR:, E' UN ERRORE→THIS IS AN ERROR. Forza l'imperativo."""

    def test_basic_mapping(self):
        text = "DEVI: fare X.\nNON DEVI: fare Y.\nOK: caso valido.\nERRORE: caso errato.\nQuesto comportamento E' UN ERRORE."
        out = itx._apply_prescriptive_map(text)
        self.assertIn("MUST: fare X.", out)
        self.assertIn("MUST NOT: fare Y.", out)
        self.assertIn("OK: caso valido.", out)
        self.assertIn("ERROR: caso errato.", out)
        self.assertIn("THIS IS AN ERROR", out)
        self.assertNotIn("DEVI:", out)
        self.assertNotIn("NON DEVI:", out)
        self.assertNotIn("ERRORE:", out)

    def test_accented_E_apostrophe(self):
        # "È UN ERRORE" anche con E maiuscolo accentato
        text = "Pattern X. È UN ERRORE."
        out = itx._apply_prescriptive_map(text)
        self.assertIn("THIS IS AN ERROR", out)


class TestExtractPlaceholders(unittest.TestCase):
    def test_extract_simple(self):
        text = "Hello {{ name }} and {{ count }} times {{ name }}"
        phs = itx._extract_jinja_placeholders(text)
        self.assertEqual(phs, {"name", "count"})

    def test_extract_with_filters(self):
        text = "{{ name | upper }} : {{ items | length }}"
        phs = itx._extract_jinja_placeholders(text)
        self.assertEqual(phs, {"name", "items"})

    def test_no_placeholders(self):
        self.assertEqual(itx._extract_jinja_placeholders("plain text"), set())


class TestValidateTranslation(unittest.TestCase):
    """Validation: sintassi Jinja2 + placeholder match + len ratio."""

    def test_valid_short_translation(self):
        it_text = "Ciao {{ name }}, hai {{ count }} mail."
        en_text = "Hello {{ name }}, you have {{ count }} emails."
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertTrue(ok, f"unexpected errors: {errors}")

    def test_missing_placeholder(self):
        it_text = "Ciao {{ name }} hai {{ count }} mail."
        en_text = "Hello {{ name }} you have many emails."
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("placeholder_missing_in_en" in e for e in errors))

    def test_extra_placeholder(self):
        it_text = "Hello {{ name }}"
        en_text = "Hello {{ name }} {{ extra }}"
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("placeholder_extra_in_en" in e for e in errors))

    def test_len_ratio_too_short(self):
        it_text = "x" * 1000
        en_text = "x" * 100  # 0.1× = troppo corto
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("len_ratio_out_of_range" in e for e in errors))

    def test_len_ratio_too_long(self):
        it_text = "x" * 100
        en_text = "x" * 200  # 2.0× = troppo lungo
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("len_ratio_out_of_range" in e for e in errors))

    def test_len_ratio_in_normal_band(self):
        it_text = "x" * 1000
        en_text = "x" * 900  # 0.9× = ok
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertTrue(ok)

    def test_jinja_syntax_error_in_en(self):
        it_text = "Hello {{ name }}"
        en_text = "Hello {{ name }"  # parentesi sbilanciate
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("jinja_syntax" in e for e in errors))

    def test_sentinel_leak_detected(self):
        it_text = "Hello {{ name }} world"
        # Sentinel format leaked nel testo (ricorda __METNOS_INV_<hex>__)
        en_text = ("Hello {{ name }} world with leftover "
                   "__METNOS_INV_abcdef0123456789abcdef0123456789ab__")
        ok, errors = itx._validate_translation(it_text, en_text)
        self.assertFalse(ok)
        self.assertTrue(any("sentinel_leak" in e for e in errors),
                         f"expected sentinel_leak in errors, got: {errors}")


class TestTranslatePromptFile(unittest.TestCase):
    """End-to-end con LLM stubbato: scrive file IT temp, monta stub, verifica
    che mask→llm→unmask→prescriptive_map→validate→write produca un candidato.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Crea fake prompts/it/foo.j2 nel tmp e monkey-patch i18n_translator
        # per usarlo come BASE.
        (self.tmp / "it").mkdir()
        self.role = "fake_role"
        src = (
            "Header.\n"
            "DEVI: usare {{ name }}.\n"
            "OK: `tool_x`.\n"
            "ERRORE: confondere E' UN ERRORE.\n"
        )
        (self.tmp / "it" / f"{self.role}.j2").write_text(src, encoding="utf-8")
        self._patch = mock.patch.object(
            itx, "_llm_call_for_prompt",
            side_effect=self._fake_llm,
        )
        self._patch.start()
        # Patch Path(__file__).parent in translate_prompt_file: lui usa
        # Path(itx.__file__).parent -> runtime/. Per test monkey-patch
        # dirigiamo via temp dir creando una runtime/prompts struct.
        self._orig_runtime = Path(itx.__file__).parent
        # Crea symlink runtime/prompts puntante al tmp
        # Soluzione piu' pulita: monkey-patch translate_prompt_file via wrapping.

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_llm(self, prompt: str, max_tokens: int = 32000,
                   tier: str = "wise") -> str:
        """Fake LLM: ritorna una traduzione plausibile preservando i sentinel.

        L'input contiene `Source (...) prompt below.` poi delimitatore. Lo
        recuperiamo e applichiamo trasformazioni semplici per simulare
        un translator buono (che NON tocca i sentinel).
        """
        # Estrai la parte source tra le linee di tratti
        marker = "────────────────────────────────────────────"
        if marker in prompt:
            parts = prompt.split(marker)
            if len(parts) >= 3:
                src = parts[1].strip()
                # Traduzione "fake": IT→EN parziale. Non tocca sentinel
                # __METNOS_INV_<...>__ ne' DEVI/NON DEVI/OK/ERRORE (saranno
                # rimappati dalla post-pass).
                en = (src
                      .replace("Header.", "Header.")
                      .replace("usare", "use")
                      .replace("confondere", "confuse"))
                return en
        return prompt

    def test_translate_writes_candidate(self):
        # Wrap: redirige _BASE-equivalent path tramite mock di Path
        with mock.patch.object(
            itx, "_PROMPT_FILE_TEMPLATE", itx._PROMPT_FILE_TEMPLATE
        ):
            # Trick: il tmp deve avere struttura runtime/prompts/it/...
            # Per mantenere il test self-contained, montiamo tmp come
            # PROMPTS dir di translate_prompt_file via path patch.
            orig_parent = Path(itx.__file__).parent
            # Crea dir prompts dentro tmp
            (self.tmp / "prompts").mkdir(exist_ok=True)
            (self.tmp / "prompts" / "it").mkdir(exist_ok=True)
            src_real = self.tmp / "prompts" / "it" / f"{self.role}.j2"
            src_real.write_text(
                (self.tmp / "it" / f"{self.role}.j2").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            class FakePath:
                def __init__(self, *a, **k):
                    raise AssertionError("dummy")

            # Patcha __file__ del modulo per ridirigere parent
            orig_file = itx.__file__
            try:
                # Crea un finto modulo file in tmp/runtime
                runtime_dir = self.tmp
                fake_file = runtime_dir / "i18n_translator.py"
                fake_file.write_text("# stub", encoding="utf-8")
                itx.__file__ = str(fake_file)
                res = itx.translate_prompt_file(self.role,
                                                  target_lang="en",
                                                  source_lang="it")
            finally:
                itx.__file__ = orig_file

            self.assertTrue(res.get("ok"), f"validation: {res.get('validation')}")
            self.assertEqual(res["role"], self.role)
            cand = Path(res["candidate_path"])
            self.assertTrue(cand.is_file())
            cand_text = cand.read_text(encoding="utf-8")
            # Post-pass deve aver applicato la mappa prescrittiva
            self.assertIn("MUST:", cand_text)
            self.assertIn("ERROR:", cand_text)
            self.assertIn("THIS IS AN ERROR", cand_text)
            # Sentinel non devono restare nel file finale
            self.assertNotIn("__METNOS_INV_", cand_text)
            # Placeholder Jinja2 deve essere ripristinato
            self.assertIn("{{ name }}", cand_text)
            # Backtick `tool_x` deve essere ripristinato letteralmente
            self.assertIn("`tool_x`", cand_text)


class TestTranslatePromptFileMissingSource(unittest.TestCase):
    def test_returns_error_dict(self):
        res = itx.translate_prompt_file("__nonexistent_xyz__",
                                          target_lang="en", source_lang="it")
        self.assertFalse(res["ok"])
        self.assertIn("source not found", res["error"])


class TestTranslateAllPromptsSkipExistingSynced(unittest.TestCase):
    """Smoke su translate_all_prompts: non deve chiamare LLM se il target gia'
    esiste (skip). Fallback semplice senza filesystem mock."""

    def test_skip_existing_returns_early(self):
        # Tutti i prompts/it/*.j2 reali esistono ma prompts/en/*.j2 NON
        # ancora. Mock _llm_call_for_prompt per fallback rapido.
        with mock.patch.object(itx, "_llm_call_for_prompt", return_value=""):
            results = itx.translate_all_prompts(
                target_lang="en", source_lang="it",
                skip_existing_synced=True,
            )
        # Risultati per tutti i ruoli IT (non skipped a meno che en/<role>.j2
        # esista). Almeno uno e' stato processato (con LLM stub vuoto).
        self.assertGreater(len(results), 0)


if __name__ == "__main__":
    unittest.main()
