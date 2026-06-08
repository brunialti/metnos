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


if __name__ == "__main__":
    unittest.main()
