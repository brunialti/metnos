from __future__ import annotations

import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

from install import preflight
from install import sidecar
from install.phases import phase2_infra, phase3_code


ROOT = Path(__file__).resolve().parents[3]


def test_direct_preflight_disk_check_does_not_create_user_data(
        tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "new-user" / "data"
    observed: list[Path] = []

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        observed.append(Path(path))
        return SimpleNamespace(free=20 * 1024 ** 3)

    monkeypatch.setenv("METNOS_USER_DATA", str(target))
    monkeypatch.setattr(preflight.shutil, "disk_usage", fake_disk_usage)

    result = preflight.check_disk(min_free_gb=8)

    assert result.ok
    assert not target.exists()
    assert observed == [tmp_path]


def test_install_manifest_is_current_parseable_inventory() -> None:
    path = ROOT / "install" / "manifest.toml"
    text = path.read_text(encoding="utf-8")
    manifest = tomllib.loads(text)

    assert manifest["meta"]["installer_entry"] == "install/bootstrap.sh"
    assert manifest["meta"]["manifest_schema_version"] == 2
    assert manifest["meta"]["version"] == "0.1.0"
    assert "changelog" not in manifest
    assert any(
        model["name"] == "embedding_text_bge_m3" and model["required"]
        for model in manifest["models"]["entry"]
    )
    assert "jsonschema==4.10.3" in manifest["runtime"]["python_packages"][
        "required"
    ]
    lre_config = next(
        entry for entry in manifest["config_files"]["entry"]
        if entry["path"].endswith("/lre.env")
    )
    assert lre_config["mode"] == "0600"
    for stale_name in ("myclaw", "suprastructure", "giorgio2", "minilm"):
        assert stale_name not in text.lower()


def test_every_manifest_service_source_exists() -> None:
    with (ROOT / "install" / "manifest.toml").open("rb") as handle:
        services = tomllib.load(handle)["services"]["entry"]

    missing = [
        service["unit_file_local"]
        for service in services
        if not (ROOT / service["unit_file_local"]).is_file()
    ]
    assert missing == []


def test_mandatory_embedder_hashes_match_manifest(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(tmp_path))
    component = phase2_infra._bge_m3()
    code_hashes = {asset.dest.name: asset.sha256 for asset in component.assets}

    with (ROOT / "install" / "manifest.toml").open("rb") as handle:
        models = tomllib.load(handle)["models"]["entry"]
    model = next(m for m in models if m["name"] == "embedding_text_bge_m3")
    manifest_hashes = {
        Path(item["local"]).name: item["sha256"] for item in model["files"]
    }

    assert component.mandatory
    assert code_hashes == manifest_hashes
    assert all(code_hashes.values())


def test_integrity_pinned_embedder_uses_one_immutable_source_revision(
        tmp_path: Path, monkeypatch) -> None:
    """A checksum and a moving Hub ref are not a reproducible artifact pin."""
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(tmp_path))
    component = phase2_infra._bge_m3()
    revisions: set[str] = set()

    for asset in component.assets:
        assert asset.sha256 and re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
        match = re.search(r"/resolve/([0-9a-f]{40})/", asset.url)
        assert match, f"moving or non-canonical Hugging Face URL: {asset.url}"
        revisions.add(match.group(1))

    with (ROOT / "install" / "manifest.toml").open("rb") as handle:
        models = tomllib.load(handle)["models"]["entry"]
    model = next(m for m in models if m["name"] == "embedding_text_bge_m3")

    assert revisions == {model["source_revision"]}


def test_vlm_sidecar_uses_the_manifest_immutable_revision() -> None:
    with (ROOT / "install" / "manifest.toml").open("rb") as handle:
        models = tomllib.load(handle)["models"]["entry"]
    model = next(m for m in models if m["name"] == "vlm_default")

    assert re.fullmatch(r"[0-9a-f]{40}", sidecar._VLM_REVISION)
    assert sidecar._VLM_REVISION == model["source_revision"]
    assert {item["name"] for item in model["files"]} == {
        sidecar._VLM_MODEL,
        sidecar._VLM_MMPROJ,
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        for item in model["files"]
    )


def test_source_verification_requires_the_runnable_tree() -> None:
    assert set(phase3_code._EXPECTED_SOURCE_DIRS) == {
        "install", "runtime", "executors", "docs",
    }


def test_phase3_compiles_and_verifies_tutor_with_installed_python(
        tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    venv = repo / ".venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(repo))
    monkeypatch.setenv("METNOS_VENV", str(venv))
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="sha256:" + "a" * 64 + "\n",
            stderr="",
        )

    monkeypatch.setattr(phase3_code.subprocess, "run", fake_run)
    result = phase3_code._compile_tutor_catalog()

    assert result["compiled"] is True
    assert observed["command"][0] == str(python)
    assert "compile_catalog" in observed["command"][2]
    assert "verify_catalog" in observed["command"][2]
    assert observed["cwd"] == str(repo)
    assert str(repo / "runtime") in observed["env"]["PYTHONPATH"]
    assert observed["timeout"] == phase3_code._TUTOR_COMPILE_TIMEOUT_S


def test_current_installer_guides_contain_no_retired_project_history() -> None:
    guides = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "install/INSTALL.md",
            "install/README.md",
            "install/INSTALL_NOTES.md",
            "install/public/README.md",
        )
    ).lower()

    for retired in ("myclaw", "suprastructure", "giorgio2", "minilm"):
        assert retired not in guides
    assert "[[changelog" not in guides
    assert "google workspace / github" not in guides
    assert "google workspace is connected after installation" in guides


def test_public_install_text_does_not_overclaim_preflight_or_first_turn() -> None:
    public_readme = (ROOT / "install" / "public" / "README.md").read_text()
    quick_tours = "\n".join(
        (ROOT / "docs" / lang / "Metnos_QuickTour.html").read_text()
        for lang in ("it", "en")
    )

    assert "pre-flight only, writes nothing" not in public_readme
    assert "verifies the fresh\ninstance with a real turn" not in public_readme
    assert "chiude con un turno applicativo" not in quick_tours
    assert "finishes with an application turn" not in quick_tours
    assert "solo verifica: non scrive nulla" not in quick_tours
    assert "pre-flight only — writes nothing" not in quick_tours
