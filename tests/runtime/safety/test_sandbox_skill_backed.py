"""Invocazioni skill-backed: rilevazione + extras sandbox + pin placement (10/7).

Bug B2: da quando bubblewrap esiste sul sistema (9/7), gli executor Google
giravano in bwrap SENZA la skill home (token OAuth invisibile) e i dispatcher
`metnos:*` anche senza rete → OGNI op Google chiedeva il setup OAuth in loop.
Bug B1: la destinazione appiccicosa mandava invocazioni client=google_workspace
sul device, dove il modulo gw non esiste per costruzione (C7).

SoT identita': `vocab.PROVIDER_SKILLS` (guard di copertura in
test_provider_axis_naming). Qui: la capability condizionale conforme, il
ripiego legacy dei 5 segnali, `skill_extras`, e l'effetto rete/bind su
`wrap_command`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

import sandbox  # noqa: E402
import vocab  # noqa: E402


def _ex(name="find_files", *, schema=None, caps=None, prov=None,
        standard_state="legacy"):
    return SimpleNamespace(
        name=name,
        args_schema=schema or {},
        capabilities=caps or [],
        provenance=prov or {},
        standard_state=standard_state,
        code_path=Path("executors") / name / f"{name}.py",
    )


# ── invocation_skills: dichiarazione conforme + ripiego legacy ───────────────

def _provider_cap(binding="google-workspace", *, values=None):
    return {
        "name": "provider:access",
        "hint": [binding],
        "when": {
            "arg": "client",
            "values": values or ["google_workspace"],
        },
    }


def _provider_schema(*values, default="local"):
    return {
        "properties": {
            "client": {"enum": list(values), "default": default},
        },
    }


def test_declared_provider_capability_follows_selected_backend():
    ex = _ex(
        schema=_provider_schema("local", "google_workspace"),
        caps=[_provider_cap()],
        standard_state="declared",
    )
    assert sandbox.invocation_skills(ex, {"client": "local"}) == []
    assert sandbox.invocation_skills(
        ex, {"client": "google_workspace"},
    ) == ["google-workspace"]


def test_declared_executor_never_derives_authority_from_legacy_signals():
    ex = _ex(
        "find_issues_github",
        schema=_provider_schema("local", "google_workspace"),
        prov={"skill_id": "google-workspace"},
        caps=[{"name": "skill:google-workspace", "hint": []}],
        standard_state="declared",
    )
    assert sandbox.invocation_skills(
        ex, {"client": "google_workspace"},
    ) == []


def test_declared_malformed_or_unknown_binding_grants_nothing():
    malformed = _provider_cap()
    malformed["when"] = {"arg": "client"}
    unknown = _provider_cap("invented-provider")
    ex = _ex(
        schema=_provider_schema("local", "google_workspace"),
        caps=[malformed, unknown],
        standard_state="declared",
    )
    assert sandbox.invocation_skills(
        ex, {"client": "google_workspace"},
    ) == []


def test_declared_two_providers_activate_only_selected_binding():
    ex = _ex(
        schema=_provider_schema("local", "google_workspace", "github"),
        caps=[_provider_cap(), _provider_cap("github", values=["github"])],
        standard_state="declared",
    )
    assert sandbox.invocation_skills(ex, {"client": "local"}) == []
    assert sandbox.invocation_skills(ex, {"client": "github"}) == ["github"]
    assert sandbox.invocation_skills(
        ex, {"client": "google_workspace"},
    ) == ["google-workspace"]

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


# ── local semantic resources: exact, read-only, fail-closed ─────────────────

def test_index_read_binds_only_declared_image_subtree(tmp_path, monkeypatch):
    import config

    index_root = tmp_path / "indices"
    image = index_root / "image"
    other = index_root / "mail"
    image.mkdir(parents=True)
    other.mkdir()
    monkeypatch.setattr(config, "PATH_INDEX_IMAGE", image)

    assert sandbox._index_resource_paths(["image"]) == [image]
    assert sandbox._index_resource_paths(["invented"]) == []
    assert sandbox._index_resource_paths([str(index_root / "**")]) == []

    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{"name": "index:read", "hint": ["image"]}],
    )
    assert str(image) in args
    assert args[args.index(str(image)) - 1] == "--ro-bind"
    assert str(other) not in args


def test_person_registry_binds_only_db_and_existing_sidecars(
        tmp_path, monkeypatch):
    import config

    data = tmp_path / "data"
    data.mkdir()
    db = data / "persons.sqlite"
    wal = data / "persons.sqlite-wal"
    shm = data / "persons.sqlite-shm"
    unrelated = data / "users.db"
    for path in (db, wal, shm, unrelated):
        path.write_bytes(b"x")
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    paths = sandbox._managed_local_resource_paths(
        ["persons_registry:local"], writable=False,
    )

    assert paths == [db, wal, shm]
    assert unrelated not in paths


def test_managed_hash_cache_binds_only_its_private_subtree(
        tmp_path, monkeypatch):
    import config

    cache_root = tmp_path / "cache"
    unrelated = cache_root / "other"
    unrelated.mkdir(parents=True)
    monkeypatch.setattr(config, "PATH_USER_CACHE", cache_root)

    paths = sandbox._managed_local_resource_paths(
        ["file_hash_cache:local"], writable=True,
    )

    expected = cache_root / "file_hashes"
    assert paths == [expected]
    assert expected.is_dir()
    assert unrelated not in paths
    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{
            "name": "metnos:cache",
            "hint": ["file_hash_cache:local"],
        }],
    )
    assert args[args.index(str(expected)) - 1] == "--bind"


def test_filesystem_argument_authority_binds_only_signed_argument(
        tmp_path):
    reference = tmp_path / "reference.jpg"
    unrelated = tmp_path / "unrelated.jpg"
    reference.write_bytes(b"image")
    unrelated.write_bytes(b"private")
    ex = _ex(
        "find_images_indices",
        schema={"properties": {
            "reference_images": {"type": "array", "items": {"type": "string"}},
            "paths_filter": {"type": "array", "items": {"type": "string"}},
        }},
        caps=[{"name": "fs:read", "hint": ["arg:reference_images"]}],
        standard_state="declared",
    )

    paths = sandbox.filesystem_extras(ex, {
        "reference_images": [str(reference)],
        "paths_filter": [str(unrelated)],
    })

    assert paths == [reference]
    assert unrelated not in paths


def test_filesystem_argument_resolves_known_alias_before_sandbox(
        tmp_path, monkeypatch):
    import path_alias

    media_root = tmp_path / "media"
    pictures = media_root / "Immagini"
    pictures.mkdir(parents=True)
    monkeypatch.setattr(path_alias, "candidate_roots", lambda: [media_root])
    ex = _ex(
        "find_files_hash",
        schema={"properties": {"base_path": {"type": "string"}}},
        caps=[{"name": "fs:read", "hint": ["arg:base_path"]}],
        standard_state="declared",
    )

    paths = sandbox.filesystem_extras(
        ex, {"base_path": "/server/Immagini"},
    )

    assert paths == [pictures]


def test_read_path_is_canonicalized_for_executor_and_sandbox(
        tmp_path, monkeypatch):
    """A logical path and its read-only grant must name the same object."""
    import path_alias

    workspace = tmp_path / "workspace"
    target = tmp_path / "media" / "Immagini"
    workspace.mkdir()
    target.mkdir(parents=True)
    (workspace / "Immagini").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(path_alias, "workspace_default", lambda: workspace)
    monkeypatch.setattr(path_alias, "candidate_roots", lambda: [workspace])
    ex = _ex(
        "find_files_hash",
        schema={"properties": {"base_path": {"type": "string"}}},
        caps=[{"name": "fs:read", "hint": ["arg:base_path"]}],
        standard_state="declared",
    )

    resolved = sandbox.resolve_filesystem_read_args(
        ex, {"base_path": "Immagini"})

    logical = workspace / "Immagini"
    assert resolved["base_path"] == str(logical)
    assert sandbox.filesystem_extras(ex, resolved) == [logical]


def test_filesystem_argument_without_capability_grants_nothing(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"image")
    assert sandbox.filesystem_extras(
        _ex(standard_state="declared"),
        {"reference_images": [str(reference)]},
    ) == []


# ── mail_extras: local IMAP authority, scoped credentials, fail-closed ───────

def _mail_ex(*, caps=None):
    return _ex(
        "read_messages",
        schema={"properties": {
            "account": {"type": ["string", "array"],
                        "default": "metnos_system"},
            "client": {"type": "string",
                       "enum": ["metnos", "google_workspace"]},
            "via_channel": {"type": "string", "default": "email"},
        }},
        caps=caps or [],
        standard_state="declared",
    )


def test_mail_account_argument_without_capability_grants_nothing():
    ex = _mail_ex()
    assert sandbox.mail_extras(
        ex, {"account": "all", "client": "metnos"},
    ) == ([], False)


@pytest.mark.parametrize("mail_capability", ["mail:read", "mail:write", "mail:send"])
def test_mail_local_binds_only_selected_account_read_only(
        tmp_path, monkeypatch, mail_capability):
    import config
    import credentials

    config_root = tmp_path / "config"
    vault = config_root / "credentials"
    mail_dir = config_root / "mail"
    vault.mkdir(parents=True)
    mail_dir.mkdir()
    admin_key = config_root / "admin.key"
    admin_key.write_text("key", encoding="utf-8")
    selected = vault / "smtp_work.json.age"
    selected.write_text("encrypted", encoding="utf-8")
    unrelated_mail = vault / "smtp_private.json.age"
    unrelated_mail.write_text("encrypted", encoding="utf-8")
    unrelated_web = vault / "example.com.json.age"
    unrelated_web.write_text("encrypted", encoding="utf-8")
    selected_env = mail_dir / "work.env"
    selected_env.write_text("USER=x", encoding="utf-8")

    monkeypatch.setattr(config, "PATH_USER_CONFIG", config_root)
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", admin_key)

    paths, net = sandbox.mail_extras(
        _mail_ex(caps=[{"name": mail_capability, "hint": ["work"]}]),
        {"account": "work", "client": "metnos", "via_channel": "email"},
    )

    assert net is True
    assert paths == [admin_key, selected, selected_env]
    assert unrelated_mail not in paths
    assert unrelated_web not in paths

    bwrap = sandbox._build_bwrap_args(
        Path(__file__), capabilities=[], extra_ro=paths, force_net=net,
    )
    assert "--unshare-net" not in bwrap
    for path in paths:
        position = bwrap.index(str(path))
        assert bwrap[position - 1] == "--ro-bind"


@pytest.mark.parametrize("capability,channel,account,selected", [
    ("mail:send", "email", None, "work"),
    ("mail:send", "auto", None, "work"),
    ("mail:send", "email", "personal", "personal"),
    ("mail:read", "email", None, "metnos_system"),
    ("mail:write", "email", None, "metnos_system"),
    ("mail:send", "telegram", None, None),
    ("mail:read", "auto", None, None),
])
def test_mail_sender_default_matches_child_without_exposing_other_accounts(
        tmp_path, monkeypatch, capability, channel, account, selected):
    import config
    import credentials

    vault = tmp_path / "credentials"
    vault.mkdir()
    files = {name: vault / f"smtp_{name}.json.age"
             for name in ("work", "personal", "metnos_system")}
    for path in files.values():
        path.write_bytes(b"encrypted-fixture")
    foreign = vault / "private-site.json.age"
    foreign.write_bytes(b"not-mail")
    monkeypatch.setattr(config, "PATH_USER_CONFIG", tmp_path)
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", tmp_path / "absent.key")
    monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", "work")
    paths, network = sandbox.mail_extras(
        _mail_ex(caps=[{"name": capability}]),
        {"account": account, "via_channel": channel},
    )
    assert paths == ([] if selected is None else [files[selected]])
    assert network is (selected is not None)
    assert foreign not in paths
    assert vault not in paths


@pytest.mark.parametrize("account", ["all", "ALL", ["work", "personal"], ["all"]])
@pytest.mark.parametrize("real_manifest", [False, True])
def test_mail_send_never_expands_multiple_credentials(tmp_path, monkeypatch, account, real_manifest):
    import config
    import credentials

    vault = tmp_path / "credentials"
    vault.mkdir()
    (vault / "smtp_work.json.age").write_bytes(b"encrypted")
    (vault / "smtp_personal.json.age").write_bytes(b"encrypted")
    monkeypatch.setattr(config, "PATH_USER_CONFIG", tmp_path)
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    executor = _mail_ex(caps=[{"name": "mail:send"}])
    if real_manifest:
        import tomllib

        path = Path(__file__).resolve().parents[3] / "executors/send_messages/manifest.toml"
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
        assert manifest["args"]["properties"]["account"]["type"] == "string"
        executor = _ex(manifest["name"], schema=manifest["args"],
                       caps=manifest["capabilities"], standard_state="declared")
    assert sandbox.mail_extras(executor, {"account": account}) == ([], True)


def test_mail_send_conditional_capability_does_not_grant_outside_condition(monkeypatch):
    monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", "work")
    executor = _mail_ex(caps=[{
        "name": "mail:send", "when": {"arg": "via_channel", "values": ["email"]},
    }])
    assert sandbox.mail_extras(executor, {"via_channel": "auto"}) == ([], False)


def test_mail_all_expands_only_mail_credentials(tmp_path, monkeypatch):
    import config
    import credentials

    config_root = tmp_path / "config"
    vault = config_root / "credentials"
    mail_dir = config_root / "mail"
    vault.mkdir(parents=True)
    mail_dir.mkdir()
    admin_key = config_root / "admin.key"
    admin_key.write_text("key", encoding="utf-8")
    smtp_a = vault / "smtp_a.json.age"
    smtp_a.write_text("encrypted", encoding="utf-8")
    web = vault / "bank.example.json.age"
    web.write_text("encrypted", encoding="utf-8")
    env_a = mail_dir / "a.env"
    env_a.write_text("USER=x", encoding="utf-8")

    monkeypatch.setattr(config, "PATH_USER_CONFIG", config_root)
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", admin_key)

    paths, net = sandbox.mail_extras(
        _mail_ex(caps=[{"name": "mail:read", "hint": ["all"]}]),
        {"account": "all", "client": "metnos"},
    )

    assert net is True
    assert admin_key in paths and smtp_a in paths and env_a in paths
    assert web not in paths


@pytest.mark.parametrize(
    "args",
    [
        {"client": "google_workspace", "via_channel": "email"},
        {"client": "metnos", "via_channel": "telegram"},
    ],
)
def test_mail_non_imap_branch_gets_no_local_credentials_or_network(args):
    ex = _mail_ex(caps=[{"name": "mail:read", "hint": ["all"]}])
    assert sandbox.mail_extras(ex, args) == ([], False)


def test_mail_unsafe_account_never_derives_a_path(tmp_path, monkeypatch):
    import config
    import credentials

    monkeypatch.setattr(config, "PATH_USER_CONFIG", tmp_path)
    monkeypatch.setattr(credentials, "CRED_DIR", tmp_path / "credentials")
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", tmp_path / "missing.key")
    ex = _mail_ex(caps=[{"name": "mail:read", "hint": ["mail"]}])

    paths, net = sandbox.mail_extras(
        ex, {"account": "../../admin.key", "client": "metnos"},
    )
    assert paths == []
    assert net is True


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
def test_network_dot_kind_grants_network(tmp_path):
    """La notazione ``network.read`` usata da find_urls e' equivalente."""
    code = tmp_path / "x.py"; code.write_text("print('hi')")
    ex = _ex(caps=[{"name": "network.read", "hint": []}]); ex.code_path = code
    cmd = sandbox.wrap_command(ex, ["python3", str(code)])
    assert sandbox._capability_kind("network.read") == "network"
    assert sandbox._capability_mode("network.read") == "read"
    assert "--unshare-net" not in cmd


def test_systemd_read_mounts_runtime_sockets_read_only():
    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{"name": "systemd:read", "hint": []}],
    )
    if Path("/run/systemd").exists():
        pos = args.index("/run/systemd")
        assert args[pos - 1] == "--ro-bind"


def test_system_read_exposes_only_requested_introspection_resources():
    code = Path(__file__)
    args = sandbox._build_bwrap_args(
        code,
        capabilities=[{
            "name": "system:read",
            "hint": ["processes", "health", "network_interfaces", "service_status"],
        }],
    )

    assert "--unshare-net" not in args
    if Path("/run/systemd").exists():
        pos = args.index("/run/systemd")
        assert args[pos - 1] == "--ro-bind"


@pytest.mark.parametrize(
    ("hint", "host_network", "service_surface"),
    [
        ("processes", False, False),
        ("health", False, False),
        ("network_interfaces", True, False),
        ("service_status", False, True),
    ],
)
def test_system_read_hints_grant_only_their_declared_surface(
        hint, host_network, service_surface):
    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{"name": "system:read", "hint": [hint]}],
    )

    assert ("--unshare-net" not in args) is host_network
    visible_service_paths = [
        str(path) for path in (
            Path("/run/systemd"), Path("/run/dbus"),
            Path(f"/run/user/{os.getuid()}"),
        )
        if path.exists()
    ]
    for path in visible_service_paths:
        assert (path in args) is service_surface
        if service_surface:
            assert args[args.index(path) - 1] == "--ro-bind"


def test_system_read_unknown_hint_grants_no_network_or_service_surface():
    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{"name": "system:read", "hint": ["invented"]}],
    )

    assert "--unshare-net" in args


def test_system_read_executables_is_closed_read_only_introspection():
    args = sandbox._build_bwrap_args(
        Path(__file__),
        capabilities=[{"name": "system:read", "hint": ["executables"]}],
    )

    assert "--unshare-net" in args
    for path in (Path("/run/systemd"), Path("/run/dbus")):
        if path.exists():
            assert str(path) not in args


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


def test_declared_provider_authority_drives_rw_network_and_server_pin(
        tmp_path, monkeypatch):
    import agent_runtime
    import remote_exec

    home = tmp_path / "google-workspace"
    home.mkdir()
    monkeypatch.setenv("METNOS_SKILL_HOME", str(home))
    code = tmp_path / "provider_executor.py"
    code.write_text("print('unused')", encoding="utf-8")
    ex = _ex(
        schema=_provider_schema("local", "google_workspace"),
        caps=[_provider_cap()],
        standard_state="declared",
    )
    ex.code_path = code
    ex.placement = {"scope": "any", "device_ok": True}
    ex.platforms = ["linux"]
    ex.revertible = False

    captured = {}

    def fake_wrap(executor, command, **kwargs):
        captured["extra_rw"] = kwargs.get("extra_rw")
        captured["force_net"] = kwargs.get("force_net")
        return command

    class Completed:
        stdout = '{"ok": true}'
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sandbox, "wrap_command", fake_wrap)
    monkeypatch.setattr(agent_runtime.subprocess, "run", lambda *a, **kw: Completed())
    monkeypatch.setattr(
        remote_exec, "invoke_remote",
        lambda *a, **kw: pytest.fail("provider invocation reached the device"),
    )

    result = agent_runtime.invoke_executor(
        ex, {"client": "google_workspace"}, target_device="laptop",
        actor="host", channel="test",
    )

    assert result["ok"] is True
    assert captured == {"extra_rw": [home], "force_net": True}


def test_declared_mail_authority_drives_readonly_bind_and_network(
        tmp_path, monkeypatch):
    import agent_runtime
    import config
    import credentials

    config_root = tmp_path / "config"
    vault = config_root / "credentials"
    mail_dir = config_root / "mail"
    vault.mkdir(parents=True)
    mail_dir.mkdir()
    admin_key = config_root / "admin.key"
    encrypted = vault / "smtp_work.json.age"
    account_env = mail_dir / "work.env"
    admin_key.write_text("key", encoding="utf-8")
    encrypted.write_text("encrypted", encoding="utf-8")
    account_env.write_text("USER=x", encoding="utf-8")
    monkeypatch.setattr(config, "PATH_USER_CONFIG", config_root)
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", admin_key)

    code = tmp_path / "mail_executor.py"
    code.write_text("print('unused')", encoding="utf-8")
    ex = _mail_ex(caps=[{"name": "mail:read", "hint": ["work"]}])
    ex.code_path = code
    ex.placement = {"scope": "host", "device_ok": False}
    ex.platforms = ["linux"]
    ex.revertible = False

    captured = {}

    def fake_wrap(executor, command, **kwargs):
        captured["extra_ro"] = kwargs.get("extra_ro")
        captured["extra_rw"] = kwargs.get("extra_rw")
        captured["force_net"] = kwargs.get("force_net")
        return command

    class Completed:
        stdout = '{"ok": true}'
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sandbox, "wrap_command", fake_wrap)
    monkeypatch.setattr(agent_runtime.subprocess, "run", lambda *a, **kw: Completed())

    result = agent_runtime.invoke_executor(
        ex,
        {"account": "work", "client": "metnos", "via_channel": "email"},
        actor="host",
        channel="test",
    )

    assert result["ok"] is True
    assert captured == {
        "extra_ro": [admin_key, encrypted, account_env],
        "extra_rw": [],
        "force_net": True,
    }


def test_declared_local_backend_remains_device_eligible(tmp_path, monkeypatch):
    import agent_runtime
    import devices
    import placement
    import remote_exec

    code = tmp_path / "local_executor.py"
    code.write_text("print('unused')", encoding="utf-8")
    ex = _ex(
        schema=_provider_schema("local", "google_workspace"),
        caps=[_provider_cap()],
        standard_state="declared",
    )
    ex.code_path = code
    ex.placement = {"scope": "any", "device_ok": True}
    ex.platforms = ["linux"]
    ex.revertible = False
    # Server-owned runtime observations must be injected only after placement
    # chooses the server; a device run must neither call nor receive them.
    ex.args_schema["properties"]["server_observation"] = {
        "type": "object", "runtime_resolved": True,
        "runtime_source": "test_server_observation",
    }
    monkeypatch.setitem(
        agent_runtime._RUNTIME_ARG_SOURCES,
        "test_server_observation",
        lambda: pytest.fail("server observation resolved for remote run"),
    )
    # Same declaration emitted for generated/Synt executors: a remote run must
    # receive the central budget too (class 0 remains exactly one worker).
    ex.execution_policy_declared = True
    ex.execution_policy = {
        "effect": "unknown",
        "parallelism_class": 0,
        "resource_class": "default",
        "concurrency_key": "none",
        "equivalence_gate": "unverified",
    }

    monkeypatch.setattr(devices, "owner_id_for_actor", lambda actor: "host")
    monkeypatch.setattr(devices, "list_devices", lambda: [])
    monkeypatch.setattr(placement, "choose_placement", lambda *a, **kw: "dev-1")
    captured = {}

    def fake_remote(executor, args, target, **kwargs):
        captured.update(
            args=args,
            target=target,
            env_injections=kwargs.get("env_injections"),
        )
        return {"ok": True}

    monkeypatch.setattr(remote_exec, "invoke_remote", fake_remote)

    result = agent_runtime.invoke_executor(
        ex, {"client": "local"}, target_device="laptop",
        actor="host", channel="test",
    )

    assert result["ok"] is True
    assert captured == {
        "args": {
            "client": "local", "_actor": "host", "_channel": "test",
        },
        "target": "dev-1",
        "env_injections": {"METNOS_EXECUTOR_ASSIGNED_WORKERS": "1"},
    }
