"""change_applier_extend — test manifest patch + rollback_blob + re-sign.

Usa fixture executor temporaneo con manifest minimo. Sign keys mockate.

Run: `python3 -m pytest tests/runtime/learning/test_change_applier_extend.py -v`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


_SAMPLE_MANIFEST = """\
manifest_format = "1.0"

name        = "find_things"
version     = "0.1.0"
author      = "test"
affinity    = ["find", "things"]

[description]
it = "Test."
en = "Test."

[code]
files  = ["find_things.py"]
digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

[args]
type     = "object"
required = ["q"]

[args.properties.q]
type        = "string"

[args.properties.q.description]
it = "Query"
en = "Query"
"""


_SAMPLE_CODE = """def invoke(q, **kw):
    return {"ok": True, "entries": []}
"""


class TestExtendExecutor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        import config as C
        # Punta executors a tmp
        self.exec_root = self.tmpdir / "executors"
        self.exec_root.mkdir()
        self.target_dir = self.exec_root / "find_things"
        self.target_dir.mkdir()
        (self.target_dir / "manifest.toml").write_text(_SAMPLE_MANIFEST)
        (self.target_dir / "find_things.py").write_text(_SAMPLE_CODE)
        C.PATH_EXECUTORS = self.exec_root
        C.PATH_USER_DATA = self.tmpdir / "share"
        C.PATH_USER_STATE = self.tmpdir / "state"
        C.PATH_USER_DATA.mkdir()
        C.PATH_USER_STATE.mkdir()
        # Synth fallback dir (non usata, ma referenziata da _resolve_executor_dir)
        C.PATH_SYNTH_EXECUTORS = C.PATH_USER_DATA / "executors"
        for m in list(sys.modules):
            if m.startswith("runtime.change_applier") or m == "change_applier":
                del sys.modules[m]
            if m.startswith("runtime.change_intents"):
                del sys.modules[m]
        import change_intents as ci_mod
        ci_mod.init_db()
        self.ci_mod = ci_mod

    def tearDown(self):
        self.tmp.cleanup()

    def _make_ci(self, arg_name="kind", arg_type="string"):
        return self.ci_mod.ChangeIntent.new(
            origin_family="telos", origin_module="scamper",
            intent_kind=self.ci_mod.KIND_EXTEND_EXECUTOR,
            intent_target="find_things",
            intent_summary=f"Aggiungi arg {arg_name}",
            intent_body={
                "arg_name": arg_name, "arg_type": arg_type,
                "desc_it": f"Filtra per {arg_name}",
                "desc_en": f"Filter by {arg_name}",
            },
            score=0.5,
        )

    def test_extend_appends_section_and_creates_rollback(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("kind", "string")
        # mock sign_executor (richiede keys reali)
        with mock.patch("sign.sign_executor", return_value=("sha256:fake", Path("/tmp/fake.sig"))):
            effect = extend_executor_manifest(ci)
        self.assertEqual(effect["executor_name"], "find_things")
        self.assertEqual(effect["arg_added"], "kind")
        self.assertEqual(effect["arg_type"], "string")
        self.assertIn("rollback_blob_path", effect)
        # Manifest aggiornato
        new_text = (self.target_dir / "manifest.toml").read_text()
        self.assertIn("[args.properties.kind]", new_text)
        # Parsa valido
        parsed = tomllib.loads(new_text)
        self.assertIn("kind", parsed["args"]["properties"])
        # Rollback blob esiste
        rb = Path(effect["rollback_blob_path"])
        self.assertTrue(rb.exists())
        self.assertNotIn("[args.properties.kind]", rb.read_text())

    def test_extend_idempotent(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("kind", "string")
        with mock.patch("sign.sign_executor", return_value=("sha256:fake", Path("/tmp/fake.sig"))):
            effect1 = extend_executor_manifest(ci)
            effect2 = extend_executor_manifest(ci)
        self.assertNotIn("already_extended", effect1)
        self.assertTrue(effect2.get("already_extended"))

    def test_extend_unknown_target_raises(self):
        from change_applier_extend import extend_executor_manifest
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="t", origin_module="m",
            intent_kind=self.ci_mod.KIND_EXTEND_EXECUTOR,
            intent_target="does_not_exist",
            intent_summary="x",
            intent_body={"arg_name": "kind"},
        )
        with self.assertRaises(RuntimeError):
            extend_executor_manifest(ci)

    def test_extend_invalid_arg_type_raises(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("foo", "unknown_type")
        with self.assertRaises(ValueError):
            extend_executor_manifest(ci)

    def test_extend_invalid_arg_name_raises(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("bad-name-with-dashes", "string")
        with self.assertRaises(ValueError):
            extend_executor_manifest(ci)

    def test_extend_boolean_type(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("recursive", "boolean")
        with mock.patch("sign.sign_executor", return_value=("sha256:fake", Path("/tmp/fake.sig"))):
            effect = extend_executor_manifest(ci)
        self.assertEqual(effect["arg_type"], "boolean")
        new_text = (self.target_dir / "manifest.toml").read_text()
        parsed = tomllib.loads(new_text)
        self.assertEqual(parsed["args"]["properties"]["recursive"]["type"], "boolean")
        self.assertFalse(parsed["args"]["properties"]["recursive"]["default"])

    def test_extend_sign_failure_restores_manifest(self):
        from change_applier_extend import extend_executor_manifest
        ci = self._make_ci("kind", "string")
        original_text = (self.target_dir / "manifest.toml").read_text()
        with mock.patch("sign.sign_executor", side_effect=RuntimeError("sign broke")):
            with self.assertRaises(RuntimeError):
                extend_executor_manifest(ci)
        # Manifest restored
        post_text = (self.target_dir / "manifest.toml").read_text()
        self.assertEqual(original_text, post_text)


if __name__ == "__main__":
    unittest.main()
