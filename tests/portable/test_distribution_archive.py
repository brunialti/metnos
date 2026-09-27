"""The source archive must install without exposing private neighbouring files."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


def test_archive_keeps_required_public_boundary_artifacts(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required to exercise source distribution")
    root = Path(__file__).resolve().parents[2]
    shutil.copyfile(root / ".gitattributes", tmp_path / ".gitattributes")
    shutil.copyfile(root / ".gitignore", tmp_path / ".gitignore")
    public = {
        "internal/reports/rm0007-m4-boundary-inventory.json",
        "internal/tools/render_contract_boundary_policy.py",
        "install/data/i18n_seed.sqlite",
    }
    private = {
        "internal/reports/private.json",
        "internal/tools/private.py",
        "internal/tools/private/nested.py",
        "internal/design/private.md",
        "internal/AGENTS.md",
        "install/data/private.sqlite",
    }
    for relative in public | private:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path)

    git("init", "-q")
    git("add", ".")
    git("add", "-f", "install/data/private.sqlite")
    tree = git("write-tree").decode().strip()
    with tarfile.open(fileobj=io.BytesIO(git("archive", tree))) as archive:
        included = {entry.name for entry in archive.getmembers() if entry.isfile()}
    assert public <= included
    assert not private & included
