"""I-035: a whitelisted admin command runs silently only if the request names it.

Real turn 62061e40dab84d02: «fa ping a pc-roberto» executed ``ping -c 4
8.8.8.8`` without a card and was reported as done.  Silence is now earned only
when every literal operand of the command is a whole word of the user's
original request; otherwise the ordinary approval card shows the exact command.
Only the sudoer spawn is simulated.  Addresses in these cases are test data.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from safety.canonicalize import validate_argv


@pytest.fixture
def seeded_db(monkeypatch, tmp_path):
    monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
    import safety.storage as storage_mod
    importlib.reload(storage_mod)
    import safety.seed_bootstrap as seed
    importlib.reload(seed)
    assert seed.bootstrap_safety_seed().upgraded
    import system.admin as admin
    monkeypatch.setattr(admin._C, "PATH_USER_DATA", tmp_path)
    return tmp_path


@pytest.fixture
def spawned(monkeypatch):
    import system.admin as admin
    calls = []

    def spawn(*, decision, intent_text, actor):
        calls.append(list(decision.argv))
        return {"ok": True, "decision": "execute_silent",
                "argv": list(decision.argv), "summary": ""}

    monkeypatch.setattr(admin, "_spawn_via_sudoer", spawn)
    return calls


@pytest.mark.parametrize(("argv", "request_text", "expected"), [
    (["ping", "-c", "4", "8.8.8.8"], "fa ping a pc-roberto", False),
    (["ping", "192.168.1.137"], "fa ping a 192.168.1.137", True),
    # The declared default count is covered only when no number is stated.
    (["ping", "-c", "4", "192.168.1.137"], "fa ping a 192.168.1.137", True),
    (["ping", "-c", "4", "pc-roberto"], "fa ping a pc-roberto", True),
    (["ping", "pc-roberto"], "fa ping a pc-roberto", True),
    # An explicit count prevails; a different number never runs silently.
    (["ping", "-c", "3", "pc-roberto"], "fai 3 ping a pc-roberto", True),
    (["ping", "-c", "4", "pc-roberto"], "fai 3 ping a pc-roberto", False),
    (["ping", "-c", "9", "pc-roberto"], "fa ping a pc-roberto", False),
    # Punctuation that may belong to a target is never removed.
    (["ping", "-c", "4", "192.168.1.137"], "fa ping a 192.168.1.137.", False),
    # Attached or undeclared forms stay operands.
    (["ping", "-c4", "192.168.1.137"], "fa ping a 192.168.1.137", False),
    (["ping", "-n", "134744072"], "fa ping a pc-roberto", False),
    (["ping", "-q", "134744072"], "fa ping a pc-roberto", False),
    (["ping", "-xHOST"], "fa ping a pc-roberto", False),
    # A whole word, never a fragment of a longer one.
    (["ping", "8.8.8.8"], "fa ping a 18.8.8.80", False),
    # A number can be an address: it is an operand like any other.
    (["ping", "134744072"], "fa ping a pc-roberto", False),
    (["ping", "--host=8.8.8.8"], "fa ping a pc-roberto", False),
    (["ping", "--host=pc-roberto"], "fa ping a pc-roberto", True),
    # No normalisation may create a different literal.
    (["ping", "1"], "fa ping a ::1", False),
    (["ping", "1"], "fa ping a 1::", False),
    (["ping", "::1"], "fa ping a ::1", True),
    (["cat", "/etc/hosts"], "leggi /etc/hosts", True),
    (["cat", "/etc/Hosts"], "leggi /etc/hosts", False),
    # Words are taken exactly as written: nothing is ever stripped.
    (["systemctl", "status", "nginx"], "mostra lo status di nginx grazie", True),
    (["systemctl", "status", "nginx"], "mostra lo «status» di nginx grazie", False),
    (["cat", "/tmp/note"], "leggi /tmp/note)", False),
    (["systemctl", "status", "nginx"], None, False),
    (["systemctl", "status", "nginx"], "   ", False),
])
def test_operands_must_be_whole_words_of_the_request(argv, request_text, expected):
    from system.admin import _operands_in_request

    assert _operands_in_request(validate_argv(argv), request_text) is expected


@pytest.mark.parametrize(("argv", "request_text", "expected"), [
    # The action fixed by the matched pattern is already approved.
    (["systemctl", "restart", "nginx"], "riavvia nginx", True),
    (["systemctl", "restart", "nginx"], "riavvia apache", False),
    # A different first positional is not the approved action.
    (["systemctl", "--host", "estraneo", "restart", "nginx"], "riavvia nginx", False),
])
def test_only_the_action_fixed_by_the_pattern_is_covered(argv, request_text, expected):
    from system.admin import _operands_in_request

    assert _operands_in_request(validate_argv(argv), request_text,
                                approved_action="restart") is expected


def test_sudo_wrapper_is_not_an_operand():
    from system.admin import _operands_in_request

    validated = validate_argv(["sudo", "systemctl", "status", "nginx"])
    assert _operands_in_request(validated, "status di nginx") is True


@pytest.mark.parametrize(("request_text", "proposal", "expected"), [
    # No number requested: the declared default joins the command and runs.
    ("fa ping a pc-roberto", "ping pc-roberto", [["ping", "-c", "4", "pc-roberto"]]),
    # An explicit count prevails.
    ("fai 3 ping a pc-roberto", "ping -c 3 pc-roberto", [["ping", "-c", "3", "pc-roberto"]]),
    # A stated count that the command does not carry never runs silently.
    ("fai 3 ping a pc-roberto", "ping pc-roberto", []),
    ("fai 3 ping a pc-roberto", "ping -c 4 pc-roberto", []),
    ("fa ping a pc-roberto", "ping -c 9 pc-roberto", []),
    ("fa ping a pc-roberto", "ping -c 4 8.8.8.8", []),
])
def test_effective_command_reaches_the_spawn(seeded_db, spawned, request_text,
                                             proposal, expected):
    import system.admin as admin

    result = admin._invoke_impl(intent="ping", command_proposed=proposal,
                                request_text=request_text)
    assert spawned == expected
    if not expected:
        assert result["decision"] == "approval_required"


def test_consent_binds_the_normalized_command(seeded_db, spawned):
    import system.admin as admin

    first = admin._invoke_impl(intent="ping", command_proposed="ping pc-roberto",
                               request_text="fai 3 ping a pc-roberto")
    # The card shows the command that would really run.
    assert first["argv"] == ["ping", "-c", "4", "pc-roberto"]
    assert spawned == []
    admin._invoke_impl(intent="ping", command_proposed="ping pc-roberto",
                       request_text="fai 3 ping a pc-roberto",
                       actor_consent_token=first["consent_token"])
    assert spawned == [["ping", "-c", "4", "pc-roberto"]]


def test_whitelisted_command_named_by_the_request_runs_silently(seeded_db, spawned):
    import system.admin as admin

    admin._invoke_impl(intent="stato di nginx",
                       command_proposed="systemctl status nginx",
                       request_text="come sta nginx")
    assert spawned == [["systemctl", "status", "nginx"]]


@pytest.mark.parametrize("request_text", ["dimmi lo status del server web", None, ""])
def test_unnamed_operand_gets_the_existing_card_and_never_runs(
        seeded_db, spawned, request_text):
    import system.admin as admin

    result = admin._invoke_impl(intent="stato di nginx",
                                command_proposed="systemctl status nginx",
                                request_text=request_text)
    assert spawned == []
    assert result["decision"] == "approval_required"
    assert result["approval_card"]["options"] == list(admin._ADMIN_APPROVAL_OPTIONS)
    assert result["audit"]["silent_withheld"] == "operands_not_in_request"


def test_consent_is_bound_to_the_exact_command(seeded_db, spawned):
    import system.admin as admin

    first = admin._invoke_impl(intent="stato", command_proposed="systemctl status nginx",
                               request_text="controlla il server web")
    token = first["consent_token"]
    assert token and spawned == []

    # The card for A does not authorise B.
    other = admin._invoke_impl(intent="stato", command_proposed="systemctl status sshd",
                               request_text="controlla il server web",
                               actor_consent_token=token)
    assert spawned == []
    assert other["decision"] == "approval_required"

    # The same token still authorises exactly A, once.
    admin._invoke_impl(intent="stato", command_proposed="systemctl status nginx",
                       request_text="controlla il server web",
                       actor_consent_token=token)
    assert spawned == [["systemctl", "status", "nginx"]]


def test_credentials_resume_carries_the_original_request(seeded_db, monkeypatch):
    import credentials
    import system.admin as admin

    monkeypatch.setattr(credentials, "list_domains", lambda: [])
    result = admin._invoke_impl(
        intent="monta il nas",
        command_proposed=("mount -t cifs //192.0.2.20/Public /mnt/nas "
                          "-o credentials=${METNOS_CIFS_CREDS},uid=1000"),
        request_text="monta //192.0.2.20/Public su /mnt/nas")
    assert result["decision"] == "needs_inputs"
    resume = result["needs_inputs"]["on_complete"]["resume_args"]
    assert resume["request_text"] == "monta //192.0.2.20/Public su /mnt/nas"


def _dispatch(monkeypatch, *, args, request_text):
    """The real by-name dispatcher; only the verb-unique call is observed."""
    import agent_runtime
    import executor_scheduler
    import loader

    seen = {}

    def capture(verb, *, caller, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "decision": "execute_silent", "argv": []}

    monkeypatch.setattr(loader, "invoke_verb_unique", capture)
    monkeypatch.setattr(executor_scheduler, "invoke_scheduled",
                        lambda _executor, call: call())
    admin_entry = SimpleNamespace(name="admin", args_schema={}, timeout_s=30)
    agent_runtime.invoke_tool_by_name(
        "admin", args, catalog=[admin_entry], actor="host", channel="http",
        request_text=request_text)
    return seen


def test_planner_cannot_supply_the_request_text(monkeypatch):
    args = {"intent": "ping", "command_proposed": "ping 8.8.8.8",
            "request_text": "ping 8.8.8.8"}
    assert "request_text" not in _dispatch(monkeypatch, args=args, request_text=None)
    seen = _dispatch(monkeypatch, args=args, request_text="fa ping a pc-roberto")
    assert seen["request_text"] == "fa ping a pc-roberto"


def test_only_opted_in_builtins_receive_the_request(monkeypatch):
    import loader

    loader.boot_register_verb_unique_builtins()
    entries = {verb: entry.get("accepts_request_text")
               for verb, entry in loader.VERB_UNIQUE_REGISTRY.items()}
    assert entries["admin"] is True
    assert all(flag is False for verb, flag in entries.items() if verb != "admin")
