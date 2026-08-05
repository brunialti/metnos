"""SoT unica degli scope OAuth google-workspace (10/7/2026).

Il drift fra le TRE liste (google_api.SCOPES, setup.SCOPES, preset "all" di
skill_oauth_providers.json) ha materializzato una regressione live: il
re-consent guidato dal dialog usava la preset — token rigenerato SENZA
gmail.send / gmail.readonly / cloud-vision → invio mail e vision rotti in
silenzio. Ora la lista vive in `scripts/_scopes.py` e questo guard impone la
parita' su TUTTE le copie (repo, preset JSON, copia skill INSTALLATA).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_REPO = _RUNTIME.parent
_SCRIPTS = _REPO / "executors/skills/google-workspace/scripts"


def _scopes_from(path: Path) -> list[str]:
    spec = importlib.util.spec_from_file_location(f"_scopes_{path.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.SCOPES)


def _canonical() -> list[str]:
    return _scopes_from(_SCRIPTS / "_scopes.py")


def test_preset_all_equals_canonical():
    """La preset "all" del dialog OAuth = SoT (era la lista driftata che ha
    rigenerato il token monco)."""
    data = json.loads((_RUNTIME / "skill_oauth_providers.json").read_text())
    options = data["providers"]["google-workspace"]["scopes_options"]
    all_opt = next(o for o in options if o["label"] == "all")
    assert set(all_opt["scopes"]) == set(_canonical()), (
        "preset 'all' driftata dalla SoT _scopes.py — il re-consent "
        "rigenererebbe un token monco")


def test_presets_are_subsets_of_canonical():
    """Ogni preset parziale (calendar, bundle) ⊆ SoT: mai uno scope fuori
    catalogo."""
    data = json.loads((_RUNTIME / "skill_oauth_providers.json").read_text())
    canon = set(_canonical())
    for opt in data["providers"]["google-workspace"]["scopes_options"]:
        extra = set(opt["scopes"]) - canon
        assert not extra, f"preset {opt['label']!r} ha scope fuori SoT: {extra}"


def test_scripts_import_canonical():
    """google_api.py e setup.py non ridichiarano liste proprie: importano la SoT."""
    for name in ("google_api.py", "setup.py"):
        src = (_SCRIPTS / name).read_text()
        assert "from _scopes import SCOPES" in src, f"{name} non importa la SoT"
        assert "googleapis.com/auth/gmail.readonly" not in src, (
            f"{name} ridichiara una lista scope propria (drift possibile)")


def test_oauth_writers_keep_sensitive_files_private():
    """Token, client secret and pending PKCE state must not inherit a group-
    readable umask.  The runtime refresh path has a behavioral mode test in
    test_google_token_refresh; this is the bundle-level drift guard."""
    expected = {
        "google_api.py": ("TOKEN_PATH.chmod(0o600)",),
        "gws_bridge.py": ("token_path.chmod(0o600)",),
        "setup.py": (
            "TOKEN_PATH.chmod(0o600)",
            "CLIENT_SECRET_PATH.chmod(0o600)",
            "PENDING_AUTH_PATH.chmod(0o600)",
        ),
    }
    for name, guards in expected.items():
        source = (_SCRIPTS / name).read_text()
        for guard in guards:
            assert guard in source, f"{name} lacks private-file guard {guard}"


def test_installed_skill_copy_aligned():
    """La copia INSTALLATA della skill (quella che la sandbox ESEGUE) ha la
    stessa SoT del repo. Senza sync il fix non e' in esercizio (gotcha 10/7:
    il sub-comando photos mancava dall'installato)."""
    installed = Path.home() / ".local/share/metnos/skills/google-workspace/scripts/_scopes.py"
    if not installed.exists():
        import pytest
        pytest.skip("skill google-workspace non installata su questa macchina")
    assert _scopes_from(installed) == _canonical(), (
        "copia installata driftata dal repo — risincronizzare gli script")


def test_runtime_executes_versioned_builtin_google_api():
    """Il codice first-party viene dal checkout corrente, non da una copia
    per-utente che può restare indietro. Le sole credenziali restano nella
    skill home dell'utente."""
    from backends import _google_api_runner as runner

    assert runner._skill_root().resolve() == _SCRIPTS.parent.resolve()
    assert (runner._skill_root() / "scripts/google_api.py").read_bytes() == (
        _SCRIPTS / "google_api.py").read_bytes()
