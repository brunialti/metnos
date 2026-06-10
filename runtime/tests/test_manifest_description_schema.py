"""Test schema validation per description manifest multilingua (ADR 0092 Phase 4).

Verifica che:
- Schema NUOVO `[description] <lang> = "..."` sia accettato.
- Schema LEGACY `description = "..."` flat sia REJECTED dal loader (errore
  esplicito, niente backward-compat — CLAUDE.md §7.1).
- args.properties.<arg>.description segue lo stesso pattern.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from loader import _resolve_lang_text, load_catalog  # noqa: E402


class TestResolveLangText(unittest.TestCase):
    """Unit del helper `_resolve_lang_text`."""

    def test_dict_with_current_lang_returns_it(self):
        out = _resolve_lang_text(
            {"it": "Ciao", "en": "Hello"},
            where="x.description", current_lang="it",
        )
        self.assertEqual(out, "Ciao")

    def test_dict_with_current_lang_returns_en(self):
        out = _resolve_lang_text(
            {"it": "Ciao", "en": "Hello"},
            where="x.description", current_lang="en",
        )
        self.assertEqual(out, "Hello")

    def test_dict_fallback_first_alphabetical(self):
        # Lingua corrente non disponibile → primo alphabetico.
        out = _resolve_lang_text(
            {"it": "Ciao", "fr": "Salut"},
            where="x.description", current_lang="en",
        )
        self.assertEqual(out, "Salut")  # fr < it
        out2 = _resolve_lang_text(
            {"it": "Ciao", "es": "Hola"},
            where="x.description", current_lang="xx",
        )
        self.assertEqual(out2, "Hola")  # es < it

    def test_empty_dict_returns_empty_string(self):
        out = _resolve_lang_text({}, where="x.description", current_lang="it")
        self.assertEqual(out, "")

    def test_legacy_string_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            _resolve_lang_text(
                "Ciao mondo", where="find_files.description", current_lang="it",
            )
        msg = str(ctx.exception)
        self.assertIn("find_files.description", msg)
        self.assertIn("legacy", msg.lower())

    def test_none_or_empty_value(self):
        # None / non-str non-dict → empty string (manifest in costruzione).
        out = _resolve_lang_text(None, where="x.description", current_lang="it")
        self.assertEqual(out, "")


class TestLoaderRejectsLegacy(unittest.TestCase):
    """Loader scarta manifest con schema legacy `description = "..."` flat."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, name: str, body: str) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.toml").write_text(body, encoding="utf-8")
        # Stub code: il loader scarta su code_path illeggibile / senza __main__
        # PRIMA di validare la description → senza, la reason non la citerebbe.
        (d / "x.py").write_text(
            "def invoke(args):\n    return {}\n"
            'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
        return d

    def test_legacy_flat_description_rejected(self):
        body = '''manifest_format = "1.0"
name = "legacy_executor"
version = "0.1.0"
author = "test"
description = "Schema flat legacy"
affinity = []

[code]
files = ["x.py"]
digest = "sha256:abc"

[args]
type = "object"
required = []
'''
        d = self._write_manifest("legacy_executor", body)
        # Use verify=False to skip Ed25519 (we're not testing signature here).
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        self.assertNotIn("legacy_executor", cat.executors)
        # Almeno un rejected con motivo che cita 'legacy' o 'description'.
        rejected_for_legacy = [
            (path, reason) for path, reason in cat.rejected
            if str(d) in path
        ]
        self.assertTrue(rejected_for_legacy,
                        msg=f"manifest non rejected: {cat.rejected}")
        msg = rejected_for_legacy[0][1]
        self.assertTrue(
            "legacy" in msg.lower() or "schema" in msg.lower(),
            msg=f"reason atteso 'legacy/schema', got: {msg}",
        )

    def test_new_schema_description_accepted(self):
        body = '''manifest_format = "1.0"
name = "new_executor"
version = "0.1.0"
author = "test"
affinity = []

[description]
it = "Schema nuovo multilingua"

[code]
files = ["x.py"]
digest = "sha256:abc"

[args]
type = "object"
required = []
'''
        d = self._write_manifest("new_executor", body)
        # Non c'e' code file `x.py` reale ne firma → rejected, ma per altri motivi.
        # Verifichiamo solo che il parsing della description NON e' la causa.
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        # Cerca eventuale rejection per la description.
        rejected_for_legacy = [
            (path, reason) for path, reason in cat.rejected
            if str(d) in path and ("legacy" in reason.lower() or "schema" in reason.lower())
        ]
        self.assertFalse(
            rejected_for_legacy,
            msg=f"new schema rejected per description: {rejected_for_legacy}",
        )

    def test_legacy_args_description_rejected(self):
        body = '''manifest_format = "1.0"
name = "legacy_args"
version = "0.1.0"
author = "test"
affinity = []

[description]
it = "Schema nuovo top-level"

[code]
files = ["x.py"]
digest = "sha256:abc"

[args]
type = "object"
required = []

[args.properties.foo]
type = "string"
description = "Legacy flat su arg"
'''
        d = self._write_manifest("legacy_args", body)
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        self.assertNotIn("legacy_args", cat.executors)
        rejected = [reason for path, reason in cat.rejected if str(d) in path]
        self.assertTrue(rejected, msg=f"non rejected: {cat.rejected}")
        joined = " ".join(rejected)
        self.assertIn("legacy_args.args.properties.foo.description",
                      joined,
                      msg=f"reason non cita arg description: {joined}")


class TestRealCatalogCapabilitiesSchema(unittest.TestCase):
    """Invariant cross-catalog: ogni manifest reale (handcrafted + skill
    bundle) deve avere `capabilities` come array of tables `[[capabilities]]`,
    NON come dict `[capabilities]`. Regressione 24/5/2026: `find_contacts`
    e `read_contacts` usavano dict-form → /admin/executors 500.
    """

    def test_all_real_manifests_have_array_of_tables_capabilities(self):
        import tomllib
        repo_executors = Path(__file__).resolve().parents[2] / "executors"
        user_executors = Path.home() / ".local/share/metnos/executors"
        bad = []
        for root in (repo_executors, user_executors):
            if not root.exists():
                continue
            for manifest_path in root.rglob("manifest.toml"):
                try:
                    parsed = tomllib.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except tomllib.TOMLDecodeError:
                    continue
                caps = parsed.get("capabilities")
                if caps is None:
                    continue
                if isinstance(caps, dict):
                    bad.append(
                        f"{manifest_path}: dict-form `[capabilities]`, "
                        f"use `[[capabilities]]` array of tables"
                    )
                    continue
                if not isinstance(caps, list):
                    bad.append(f"{manifest_path}: tipo {type(caps).__name__}")
                    continue
                for i, c in enumerate(caps):
                    if not isinstance(c, dict) or "name" not in c:
                        bad.append(
                            f"{manifest_path}: capabilities[{i}]={c!r} "
                            f"manca campo `name`"
                        )
        self.assertFalse(
            bad,
            msg="Manifest con capabilities malformato:\n  " + "\n  ".join(bad),
        )


class TestSendMessagesSelfSendGuidance(unittest.TestCase):
    """Regressione 8/6/2026: «inviami/mandami X su telegram» falliva con
    «Manca to, to_user» — il proposer Mētis (produzione) non emetteva il
    destinatario self. Fix §2.5/§7.3: la description di send_messages dichiara
    il token self-send `${RUNTIME:actor}`, che DEVE sopravvivere al troncamento
    del pool (cap RENDER_BUDGET) per restare visibile a OGNI proposer."""

    def _desc(self):
        import tomllib
        manifest = (Path(__file__).resolve().parents[2]
                    / "executors" / "send_messages" / "manifest.toml")
        return tomllib.loads(manifest.read_text(encoding="utf-8"))["description"]

    def test_manifest_description_declares_self_send_token(self):
        desc = self._desc()
        for lang in ("it", "en"):
            self.assertIn("${RUNTIME:actor}", desc.get(lang, ""),
                          msg=f"send_messages.description[{lang}] manca self-send token")

    def test_self_send_token_survives_pool_truncation(self):
        # La testa renderizzata nel pool (cap RENDER_BUDGET, fino a OUT:) DEVE
        # ancora contenere il token: se finisce oltre il cap il proposer non lo
        # vede e ricompare il bug 8/6 (send senza destinatario).
        from manifest_rules import render_head
        desc = self._desc()
        for lang in ("it", "en"):
            head = render_head(desc[lang])
            self.assertIn("${RUNTIME:actor}", head,
                          msg=f"self-send token troncato dal pool [{lang}]: {head!r}")


class TestHeadBudgetEnforced(unittest.TestCase):
    """Check DETERMINISTICO (11/6/2026, mandato Roberto): la testa §2.5
    (inizio -> 'OUT:' escluso) di OGNI manifest, per OGNI lingua del
    `[description]`, DEVE stare nel budget HEAD_MAX. Regressione foto
    `9400d90`: la testa di find_images_indices era 956>240 -> il render del
    pool troncava la disambiguazione -> misroute. La scansione 10/6 ne ha
    trovate altre 32 (stessa classe di difetto latente): prima era solo un
    WARN (`manifest_normalize.length_warn`), qui diventa ENFORCED.
    SoT della logica: `manifest_rules.HEAD_MAX` + `manifest_normalize.length_warn`
    (stessa estrazione testa del linter/render)."""

    ROOTS = (
        Path(__file__).resolve().parents[2] / "executors",
        Path.home() / ".local/share/metnos/executors",
    )

    def test_every_manifest_head_within_budget(self):
        import tomllib
        from manifest_normalize import length_warn
        from manifest_rules import HEAD_MAX
        bad = []
        seen = 0
        for root in self.ROOTS:
            if not root.exists():
                continue
            for mp in sorted(root.rglob("manifest.toml")):
                try:
                    parsed = tomllib.loads(mp.read_text(encoding="utf-8"))
                except tomllib.TOMLDecodeError:
                    continue  # malformato: lo scarta gia' il loader
                desc = parsed.get("description")
                if not isinstance(desc, dict):
                    continue  # legacy flat: rejected dal loader (test sopra)
                for lang in sorted(desc):
                    text = desc[lang]
                    if not isinstance(text, str):
                        continue
                    seen += 1
                    warn = length_warn(text)
                    if warn and "testa" in warn:
                        head_part = warn.split(",")[0].strip()  # "testa N>MAX"
                        bad.append(f"{mp.parent.name} [{lang}]: {head_part} "
                                   f"({mp})")
        self.assertGreater(seen, 0, msg="scansione vuota: nessun manifest letto")
        self.assertFalse(
            bad,
            msg=(f"{len(bad)} teste §2.5 oltre HEAD_MAX={HEAD_MAX} — il render "
                 f"del pool TRONCA la disambiguazione (misroute, cfr. 9400d90). "
                 f"Accorcia la testa (SCOPO+PATTERN concisi + NON: essenziale; "
                 f"dettagli args -> [args].description):\n  " + "\n  ".join(bad)),
        )


class TestPhotoSiblingsDisambiguation(unittest.TestCase):
    """Regressione 10/6/2026 (split static-first): col pool adiacente alla
    query, le description dominano il framing. La testa di find_images_indices
    debordava il budget → il criterio composizione (min_face_pixels, viso in
    primo piano) e il NON: erano TRONCATI, e get_files non dichiarava il
    boundary verso il fratello-foto → «cerca foto ... viso in primo piano»
    instradava a get_files. Fix §2.5: testa nel budget + boundary reciproco
    nel capitolo NON: (ombra strutturale C_BUDGET di manifest_lint)."""

    def _desc(self, executor: str):
        import tomllib
        manifest = (Path(__file__).resolve().parents[2]
                    / "executors" / executor / "manifest.toml")
        return tomllib.loads(manifest.read_text(encoding="utf-8"))["description"]

    def test_find_images_indices_head_keeps_face_criterion(self):
        # Il criterio composizione-volto e il boundary verso get_files DEVONO
        # sopravvivere al render del pool (cap RENDER_BUDGET, fino a OUT:).
        from manifest_rules import render_head
        desc = self._desc("find_images_indices")
        for lang in ("it", "en"):
            head = render_head(desc[lang])
            self.assertIn("min_face_pixels", head,
                          msg=f"criterio volto troncato dal pool [{lang}]: {head!r}")
            self.assertIn("get_files", head,
                          msg=f"boundary -> get_files troncato [{lang}]: {head!r}")

    def test_get_files_head_redirects_subject_search(self):
        # get_files (EXIF di path noti) DEVE dichiarare il redirect verso
        # find_images_indices per la ricerca foto per soggetto, visibile
        # nella testa renderizzata.
        from manifest_rules import render_head
        desc = self._desc("get_files")
        for lang in ("it", "en"):
            head = render_head(desc[lang])
            self.assertIn("find_images_indices", head,
                          msg=f"boundary -> find_images_indices assente [{lang}]: {head!r}")

    def test_render_head_never_splits_last_word(self):
        # SoT del troncamento: un token mutilato (es. `min_face_pixel` da
        # `min_face_pixels=40000`) sembra un arg valido e inganna l'LLM (§7.3).
        from manifest_rules import render_head, RENDER_BUDGET
        long_desc = ("SCOPO: x. PATTERN: tool(" + "a" * 40 + "); "
                     + "alfa beta " * 30 + "min_face_pixels=40000. NON: y. OUT: z.")
        head = render_head(long_desc)
        self.assertLessEqual(len(head), RENDER_BUDGET)
        # L'ultimo token del render DEVE essere un token intero della sorgente.
        last = head.split()[-1]
        self.assertIn(last, long_desc.split(),
                      msg=f"ultimo token mutilato dal taglio: {last!r}")


if __name__ == "__main__":
    unittest.main()
