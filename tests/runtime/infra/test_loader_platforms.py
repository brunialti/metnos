"""test_loader_platforms — campo manifest `platforms` (W3.2, executor
remoti §16.3 design doc).

Assente = default onesto ["linux"] (il parco esistente e' nato POSIX).
Presente = lista non vuota di valori nel vocabolario chiuso
{"linux","windows","macos"}, altrimenti REJECT `invalid_platforms` — mai
un default silenzioso su un manifest malformato (§2.8).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from loader import load_catalog  # noqa: E402


class TestLoaderPlatforms(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, name: str, platforms_line: str) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        body = f'''manifest_format = "1.0"
name = "{name}"
version = "0.1.0"
author = "test"
affinity = []
{platforms_line}

[description]
it = "Executor di test per platforms"

[code]
files = ["x.py"]
digest = "sha256:abc"

[args]
type = "object"
required = []
'''
        (d / "manifest.toml").write_text(body, encoding="utf-8")
        (d / "x.py").write_text(
            "def invoke(args):\n    return {}\n"
            'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
        return d

    def test_platforms_absent_defaults_to_linux(self):
        self._write_manifest("plat_absent", "")
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        ex = cat.executors.get("plat_absent")
        self.assertIsNotNone(ex, msg=f"rejected: {cat.rejected}")
        self.assertEqual(ex.platforms, ["linux"])

    def test_platforms_explicit_linux_windows_accepted(self):
        self._write_manifest("plat_both", 'platforms = ["linux", "windows"]')
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        ex = cat.executors.get("plat_both")
        self.assertIsNotNone(ex, msg=f"rejected: {cat.rejected}")
        self.assertEqual(ex.platforms, ["linux", "windows"])

    def test_platforms_empty_list_rejected(self):
        self._write_manifest("plat_empty", "platforms = []")
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        self.assertNotIn("plat_empty", cat.executors)
        reasons = [r for p, r in cat.rejected if "plat_empty" in p]
        self.assertTrue(reasons, msg=f"non rejected: {cat.rejected}")
        self.assertEqual(reasons[0], "invalid_platforms")

    def test_platforms_unknown_value_rejected(self):
        self._write_manifest("plat_bogus", 'platforms = ["bogus"]')
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        self.assertNotIn("plat_bogus", cat.executors)
        reasons = [r for p, r in cat.rejected if "plat_bogus" in p]
        self.assertEqual(reasons, ["invalid_platforms"])

    def test_platforms_non_list_rejected(self):
        self._write_manifest("plat_string", 'platforms = "linux"')
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        self.assertNotIn("plat_string", cat.executors)
        reasons = [r for p, r in cat.rejected if "plat_string" in p]
        self.assertEqual(reasons, ["invalid_platforms"])

    def test_platforms_single_value_accepted(self):
        self._write_manifest("plat_win_only", 'platforms = ["windows"]')
        cat = load_catalog(executors_dir=self.tmp, verify=False, include_synth=False)
        ex = cat.executors.get("plat_win_only")
        self.assertIsNotNone(ex, msg=f"rejected: {cat.rejected}")
        self.assertEqual(ex.platforms, ["windows"])


if __name__ == "__main__":
    unittest.main()
