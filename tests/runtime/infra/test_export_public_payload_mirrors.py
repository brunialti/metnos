"""The public export preserves a file identical to a signed payload (I-038).

A builtin copy is a signed payload mirroring its runtime module, and the
loader requires both byte-equal.  The export rewrote only the module, so the
two differed in the release.  The real sanitising block runs on a tiny tree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPORTER = (ROOT / "scripts" / "export-public.sh").read_text(encoding="utf-8")
START = "declare -A SIGNED_PAYLOADS=()"
END = 'done < <(find "$DEST" -type f -print0)'


def _sanitize(tree: Path) -> subprocess.CompletedProcess:
    block = EXPORTER[EXPORTER.index(START):EXPORTER.index(END) + len(END)]
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + block],
        env={"DEST": str(tree), "PYTHON": sys.executable, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    )


def test_mirror_of_a_signed_payload_is_preserved_and_other_files_rewritten(tmp_path):
    text = "# see CLAUDE.md §6\n"
    contract = tmp_path / "runtime/builtin_executor_contracts/tool"
    contract.mkdir(parents=True)
    (contract / "manifest.toml").write_text('[code]\nfiles = ["implementation.py.src"]\n')
    (contract / "implementation.py.src").write_text(text)
    module = tmp_path / "runtime/system/tool.py"
    module.parent.mkdir(parents=True)
    module.write_text(text)
    # Same size, different bytes: not a mirror, so it is sanitised as before.
    other = tmp_path / "runtime/other.py"
    other.write_text(text.replace("6", "7"))

    result = _sanitize(tmp_path)

    assert module.read_text() == text
    assert (contract / "implementation.py.src").read_text() == text
    assert other.read_text() == "# see the design guide §7\n"
    assert "runtime/system/tool.py" in result.stderr
    assert "runtime/other.py" not in result.stderr
