"""Invocazioni skill-backed: rilevazione + extras sandbox + pin placement (10/7).

Bug B2: da quando bubblewrap esiste sul sistema (9/7), gli executor Google
giravano in bwrap SENZA la skill home (token OAuth invisibile) e i dispatcher
`metnos:*` anche senza rete → OGNI op Google chiedeva il setup OAuth in loop.
Bug B1: la destinazione appiccicosa mandava invocazioni client=google_workspace
sul device, dove il modulo gw non esiste per costruzione (C7).

SoT identita': `vocab.PROVIDER_SKILLS` (guard di copertura in
test_provider_axis_naming). Qui: la rilevazione deterministica
`sandbox.invocation_skills` (5 segnali), `skill_extras`, e l'effetto
rete/bind su `wrap_command`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

import sandbox  # noqa: E402
import vocab  # noqa: E402


def _ex(name="find_files", *, schema=None, caps=None, prov=None):
    return SimpleNamespace(
        name=name,
        args_schema=schema or {},
        capabilities=caps or [],
        provenance=prov or {},
        code_path=Path("executors") / name / f"{name}.py",
    )


# ── invocation_skills: i 5 segnali ───────────────────────────────────────────

def test_signal_client_arg():
    got = sandbox.invocation_skills(_ex(), {"client": "google_workspace"})
    assert got == ["google-workspace"]


def test_signal_name_suffix_photos():
    got = sandbox.invocation_skills(_ex("write_images_google_photos"), {})
    assert got == ["google-workspace"]      # photos usa la STESSA skill (D4)


def test_signal_name_suffix_github():
    got = sandbox.invocation_skills(_ex("find_issues_github"), {})
    assert got == ["github"]


def test_signal_manifest_client_enum():
    schema = {"properties": {"client": {"enum": ["google_workspace"],
                                        "default": "google_workspace"}}}
    got = sandbox.invocation_skills(_ex("read_files_doc", schema=schema), {})
    assert got == ["google-workspace"]      # anche con arg client ASSENTE


def test_signal_provenance_skill_id():
    got = sandbox.invocation_skills(
        _ex("send_messages_google_workspace",
            prov={"skill_id": "google-workspace"}), {})
    assert got == ["google-workspace"]


def test_signal_capability_skill_family():
    got = sandbox.invocation_skills(
        _ex("future_tool", caps=[{"name": "skill:homeassistant", "hint": []}]), {})
    assert got == ["homeassistant"]


def test_no_signal_local_stays_tight():
    """Caso LOCALE: nessun segnale → nessun extra (zero allargamento sandbox)."""
    assert sandbox.invocation_skills(_ex(), {"query": "x"}) == []
    assert sandbox.invocation_skills(_ex(), {"client": "local"}) == []


def test_signals_dedup():
    schema = {"properties": {"client": {"enum": ["google_workspace"]}}}
    got = sandbox.invocation_skills(
        _ex("read_files_doc", schema=schema), {"client": "google_workspace"})
    assert got == ["google-workspace"]      # un solo binding, non duplicato


# ── skill_extras ─────────────────────────────────────────────────────────────

def test_skill_extras_existing_home(tmp_path, monkeypatch):
    """Home esistente → bind. Ermetico: METNOS_SKILL_HOME e' l'override test
    UFFICIALE di `_skill_home` (path completo della skill, no concat)."""
    home = tmp_path / "gw-home"
    home.mkdir()
    monkeypatch.setenv("METNOS_SKILL_HOME", str(home))
    paths, net = sandbox.skill_extras(["google-workspace"])
    assert net is True
    assert paths == [home]


def test_skill_extras_missing_skill_no_bind_but_net(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_SKILL_HOME", str(tmp_path / "non-esiste"))
    paths, net = sandbox.skill_extras(["skill-inesistente-xyz"])
    assert paths == [] and net is True      # needs_inputs onesto a valle


def test_skill_extras_empty():
    assert sandbox.skill_extras([]) == ([], False)


# ── effetto su wrap_command / bwrap args ─────────────────────────────────────

@pytest.mark.skipif(not sandbox.bwrap_available(), reason="bwrap assente")
def test_force_net_removes_unshare_net(tmp_path):
    code = tmp_path / "x.py"; code.write_text("print('hi')")
    ex = _ex(); ex.code_path = code
    base = sandbox.wrap_command(ex, ["python3", str(code)])
    forced = sandbox.wrap_command(ex, ["python3", str(code)], force_net=True)
    assert "--unshare-net" in base
    assert "--unshare-net" not in forced


@pytest.mark.skipif(not sandbox.bwrap_available(), reason="bwrap assente")
def test_net_kind_grants_network(tmp_path):
    """`net:read` (grafia usata da find_files/get_files) abilita la rete come
    `network:*` — prima il kind `net` era ignorato → --unshare-net spurio."""
    code = tmp_path / "x.py"; code.write_text("print('hi')")
    ex = _ex(caps=[{"name": "net:read", "hint": []}]); ex.code_path = code
    cmd = sandbox.wrap_command(ex, ["python3", str(code)])
    assert "--unshare-net" not in cmd


@pytest.mark.skipif(not sandbox.bwrap_available(), reason="bwrap assente")
def test_extra_rw_binds_skill_home(tmp_path):
    code = tmp_path / "x.py"; code.write_text("print('hi')")
    home = tmp_path / "skillhome"; home.mkdir()
    ex = _ex(); ex.code_path = code
    cmd = sandbox.wrap_command(ex, ["python3", str(code)], extra_rw=[home])
    joined = " ".join(cmd)
    assert f"--bind {home} {home}" in joined


# ── B1: il pin server e' guidato dalla stessa rilevazione ───────────────────

def test_provider_backed_invocation_is_server_pinned_signal():
    """agent_runtime nega `_device_ok` quando invocation_skills e' non-vuoto:
    qui il contratto del segnale (il gate vive in invoke_executor)."""
    assert sandbox.invocation_skills(_ex(), {"client": "google_workspace"})
    assert not sandbox.invocation_skills(_ex(), {"client": "local"})
    for prov, skill in vocab.PROVIDER_SKILLS.items():
        assert sandbox.invocation_skills(_ex(f"read_files_{prov}"), {}) == [skill]
