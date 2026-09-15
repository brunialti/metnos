"""An effect on another domain cannot fulfil the requested action."""
from types import SimpleNamespace

import pytest

from engine.dispatch import _dropped_required_verbs, run_turn
from engine.types import Framework, Intent, StepSpec


def plan(tool, args=None):
    return Framework(steps=[StepSpec(tool=tool, args=args or {}),
                            StepSpec(tool="final_answer", args={})])


@pytest.mark.parametrize("query", [
    "chiudi expressvpn su pc-roberto", "close the application on my PC",
])
def test_preferences_do_not_close_processes(query):
    fw = plan("set_preferences", {"key": "sites_stealth", "value": "off"})
    intent = Intent(verb="set", object="processes", actions=[])
    assert _dropped_required_verbs(fw, query, intent) == {"set"}


@pytest.mark.parametrize("verb,obj,tool", [
    ("set", "issues", "set_preferences"),
    ("send", "messages", "send_files"),
    ("delete", "tasks", "delete_credentials"),
])
def test_same_verb_on_an_unrelated_domain_is_missing(verb, obj, tool):
    intent = Intent(verb=verb, object=obj)
    assert _dropped_required_verbs(plan(tool), "opaque request", intent) == {verb}


def test_all_requested_domains_must_be_covered():
    intent = Intent(verb="set", object="preferences", actions=[
        {"verb": "set", "object": "preferences"},
        {"verb": "set", "object": "issues"},
    ])
    assert _dropped_required_verbs(plan("set_preferences"), "opaque", intent) == {"set"}


def test_actual_preferences_remain_supported():
    intent = Intent(verb="set", object="preferences")
    assert not _dropped_required_verbs(plan("set_preferences"), "opaque", intent)
    assert _dropped_required_verbs(plan("get_preferences"), "opaque", intent) == {"set"}


def test_signed_planning_alias_and_file_carriers_remain_supported():
    catalog = [SimpleNamespace(name="run_processes", planning_object_aliases=["packages"])]
    assert not _dropped_required_verbs(
        plan("run_processes"), "opaque", Intent(verb="run", object="packages"), catalog)
    assert not _dropped_required_verbs(
        plan("delete_files"), "opaque", Intent(verb="delete", object="images"))


def test_misrouted_turn_never_invokes_preferences(monkeypatch):
    from engine import cluster, proposer, terminator
    monkeypatch.setenv("METNOS_ENGINE", "simple")
    monkeypatch.setenv("METNOS_FASTPATH", "0")
    monkeypatch.setattr(cluster, "embed", lambda _: None)
    monkeypatch.setattr(proposer, "get_proposer", lambda: SimpleNamespace(
        propose=lambda **_: plan("set_preferences", {"key": "sites_stealth", "value": "off"})))
    monkeypatch.setattr(terminator, "get_terminator", lambda: SimpleNamespace(
        explain=lambda **_: SimpleNamespace(final_text="not completed")))
    invoked = []
    result = run_turn(
        query="chiudi expressvpn su pc-roberto",
        intent=Intent(verb="set", object="processes"),
        catalog=[SimpleNamespace(name="set_preferences", args_schema={
            "type": "object", "properties": {"key": {"type": "string"},
                                                "value": {"type": "string"}}})],
        invoke_executor_cb=lambda name, args: invoked.append(name) or {"ok": True},
        turn_id="required-action-domain")
    assert invoked == []
    assert result.error_class == "capability_missing"


@pytest.mark.parametrize("query", ["chiudi word", "close the application"])
def test_unavailable_primary_verb_uses_existing_lexical_action(query):
    from engine.dispatch import _fix_unroutable_verbs
    intent = Intent(verb="act", object="processes", actions=[])
    _fix_unroutable_verbs(intent, query, [SimpleNamespace(name="set_processes")])
    assert intent.verb == "set"


def test_existing_primary_action_is_never_replaced():
    from engine.dispatch import _fix_unroutable_verbs
    intent = Intent(verb="act", object="sites", actions=[])
    _fix_unroutable_verbs(intent, "chiudi la finestra", [SimpleNamespace(name="act_sites")])
    assert intent.verb == "act"


@pytest.mark.parametrize("path", [
    "/var/lib/example/.local/share/app/workspace/live-check-123/photos",
    "/tmp/read-send-delete/photos", "~/share/check/photos", "./read/send/photos",
    "../read/send/photos", r"C:\share\check\photos", r"\\server\share\delete",
    "'/tmp/read-send/photos'", '"/tmp/read-send/photos"', "`/tmp/read-send/photos`",
])
def test_path_components_do_not_invent_required_actions(path):
    from prefilter import tokenize
    query = "aggiorna l'indice delle foto nella cartella " + path
    intent = Intent(verb="create", object="images", actions=[])
    assert not {"read", "send", "delete", "share", "check"} & tokenize(query)
    assert not _dropped_required_verbs(plan("create_images_indices"), query, intent)
    # The actual requested mutation must still be covered.
    assert _dropped_required_verbs(plan("find_images_indices"), query, intent) == {"create"}


def test_real_actions_outside_paths_remain_required():
    query = "leggi i file in /tmp/delete/share e cancella i file in /tmp/read/check"
    intent = Intent(verb="read", object="files", actions=[
        {"verb": "read", "object": "files"}, {"verb": "delete", "object": "files"}])
    assert _dropped_required_verbs(plan("read_files"), query, intent) == {"delete"}
