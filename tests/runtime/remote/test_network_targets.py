from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

import network_targets as targets
from safety.canonicalize import declared_network_target_index, validate_argv
from tests.runtime.infra.test_admin_request_fidelity import seeded_db, spawned


@pytest.fixture
def registry(monkeypatch):
    import credentials
    import devices
    import target_device

    rows = []
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda actor: actor)
    monkeypatch.setattr(devices, "host_user_id", lambda: "owner")
    monkeypatch.setattr(devices, "list_devices", lambda: rows)
    monkeypatch.setattr(credentials, "list_domains", lambda: [])
    monkeypatch.setattr(target_device, "_server_aliases", lambda: ("server-name",))
    monkeypatch.setattr(targets.time, "time", lambda: 1000)
    return rows


def device(name="desk-one", *, owner="owner", address="192.0.2.7", timestamp=1000, id="device-one"):
    return SimpleNamespace(
        id=id, name=name, owner_user_id=owner,
        profile_json=json.dumps({"network_observation": {
            "address": address, "observed_at": timestamp,
        }}),
    )


@pytest.mark.parametrize("literal", ["192.0.2.7", "::1", "2001:db8::7", "127.0.0.1"])
def test_literal_does_not_read_any_source(monkeypatch, literal):
    import devices
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda actor: pytest.fail("source read"))
    assert targets.resolve_network_name(literal, actor="owner") is None


def test_signed_device_address_and_casefold(registry):
    registry.append(device())
    assert targets.resolve_network_name("DESK-ONE", actor="owner") == ("192.0.2.7", ("device",))


@pytest.mark.parametrize("timestamp", [699, 1031, None, "1000", True])
def test_stale_future_or_unsigned_timestamp_never_refreshes_via_liveness(registry, timestamp):
    row = device(timestamp=timestamp)
    row.last_heartbeat = "2099-01-01T00:00:00Z"
    registry.append(row)
    with pytest.raises(targets.NetworkTargetError, match="ADDRESS_UNAVAILABLE"):
        targets.resolve_network_name("desk-one", actor="owner")


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0", "224.0.0.1", "fe80::1%7", "not an IP"])
def test_device_does_not_publish_unusable_remote_address(registry, address):
    registry.append(device(address=address))
    with pytest.raises(targets.NetworkTargetError, match="ADDRESS_UNAVAILABLE"):
        targets.resolve_network_name("desk-one", actor="owner")


def test_owner_isolation_and_unknown_name(registry, monkeypatch):
    import credentials
    registry.append(device())
    monkeypatch.setattr(credentials, "network_addresses_for_name", lambda name: pytest.fail("host vault read"))
    assert targets.resolve_network_name("desk-one", actor="guest") is None


def test_duplicate_identity_is_ambiguous_even_with_same_ip(registry):
    registry.extend([device(), device(id="second-device")])
    with pytest.raises(targets.NetworkTargetError, match="NAME_AMBIGUOUS"):
        targets.resolve_network_name("desk-one", actor="owner")


def test_server_source_uses_configured_alias(registry, monkeypatch):
    monkeypatch.setattr(targets, "_server_addresses", lambda: {"192.0.2.10"})
    assert targets.resolve_network_name("server-name", actor="owner") == ("192.0.2.10", ("server",))


def test_server_uses_default_interface_not_vpn_or_loopback(monkeypatch):
    import psutil
    monkeypatch.setattr(targets.Path, "read_text", lambda self:
        "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
        "lan 00000000 0100000A 0003 0 0 100 00000000\n"
        "vpn 00000000 0100000A 0003 0 0 900 00000000\n")
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {
        name: SimpleNamespace(isup=True) for name in ("lan", "vpn", "lo")})
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        name: [SimpleNamespace(family=socket.AF_INET, address=address)]
        for name, address in (("lan", "192.0.2.10"), ("vpn", "192.0.2.20"), ("lo", "127.0.0.1"))})
    assert targets._server_addresses() == {"192.0.2.10"}


def test_credential_projection_contains_only_matching_network_metadata(registry, monkeypatch):
    import credentials
    monkeypatch.setattr(credentials, "list_domains", lambda: ["appliance"])
    monkeypatch.setattr(credentials, "load", lambda binding: {
        "name": "nas-one", "ip": "192.0.2.44", "password": "192.0.2.99",
        "api_key": "do-not-return", "form_data": {"host": "192.0.2.98"},
        "imap_host": "mail.example.test", "imap_ip": "192.0.2.45",
    })
    assert targets.resolve_network_name("NAS-ONE", actor="owner") == ("192.0.2.44", ("credential",))
    assert targets.resolve_network_name("mail.example.test", actor="owner") == ("192.0.2.45", ("credential",))
    assert targets.resolve_network_name("do-not-return", actor="owner") is None


def test_conflicting_sources_do_not_pick_one(registry, monkeypatch):
    import credentials
    registry.append(device())
    monkeypatch.setattr(credentials, "network_addresses_for_name", lambda name: [{"192.0.2.8"}])
    with pytest.raises(targets.NetworkTargetError, match="NAME_AMBIGUOUS"):
        targets.resolve_network_name("desk-one", actor="owner")


@pytest.mark.parametrize(("argv", "index"), [
    (["ping", "-c", "2", "desk-one"], 3),
    (["ping", "desk-one", "-c", "2"], 1),
    (["ping", "-c2", "desk-one"], 2),
    (["ping", "-I", "lan", "desk-one"], 3),
    (["ping", "-4", "--", "desk-one"], 3),
    (["ping", "-I", "desk-one"], None),
    (["ping", "-Z", "desk-one"], None),
    (["ping", "one", "two"], None),
    (["cat", "desk-one"], None),
])
def test_only_declared_destination_is_rewritten(argv, index):
    assert declared_network_target_index(argv[0], argv) == index


def test_resolution_preserves_wrapper_count_and_bound_provenance(registry):
    from system.admin import _operands_in_request
    registry.append(device())
    before = validate_argv(["sudo", "ping", "-c", "2", "desk-one"])
    after, binding = targets.bind_network_target(before, actor="owner")
    assert after.argv == ("sudo", "ping", "-c", "2", "192.0.2.7")
    assert _operands_in_request(after, "fai 2 ping a DESK-ONE", network_binding=binding)
    assert not _operands_in_request(after, "fai 2 ping a another-machine", network_binding=binding)
    assert not _operands_in_request(after, "fai 3 ping a desk-one", network_binding=binding)
    assert not _operands_in_request(after, "fai 2 ping a desk-one")


def test_planner_substitution_has_no_runtime_proof(registry):
    from system.admin import _operands_in_request
    registry.append(device())
    after, binding = targets.bind_network_target(validate_argv(["ping", "-c", "4", "192.0.2.99"]), actor="owner")
    assert binding is None
    assert not _operands_in_request(after, "fa ping a desk-one", network_binding=binding)


def test_unknown_hostname_keeps_dns_path(registry):
    original = validate_argv(["ping", "-c", "4", "external.example.test"])
    after, binding = targets.bind_network_target(original, actor="owner")
    assert after is original
    assert binding is None


@pytest.mark.parametrize(("query", "proposal", "runs"), [
    ("fai 2 ping a desk-one", "ping -c 2 desk-one", True),
    ("fa ping a desk-one", "ping desk-one", True),
    ("fai 2 ping a desk-one", "ping -c 4 desk-one", False),
    ("fa ping a desk-one", "ping 192.0.2.99", False),
])
def test_admin_runs_only_runtime_resolved_faithful_destination(
        registry, seeded_db, spawned, monkeypatch, query, proposal, runs):
    import system.admin as admin
    registry.append(device())
    monkeypatch.setattr(admin, "_is_admin_actor", lambda actor: actor == "owner")
    result = admin.invoke(
        intent=query, command_proposed=proposal, request_text=query, actor="owner",
        # A model-supplied proof is ignored, not converted into authority.
        network_binding=targets.NetworkTargetBinding(3, "desk-one", "192.0.2.99", ("device",)),
    )
    assert bool(spawned) is runs
    if runs:
        assert result["argv"][-1] == "192.0.2.7"
        assert result["argv"][1:3] == ["-c", "2" if "2" in query else "4"]
    else:
        assert result["approval_required"] is True


def test_consent_for_old_address_does_not_authorize_new_address(registry, seeded_db, monkeypatch):
    import system.admin as admin
    monkeypatch.setattr(admin, "_is_admin_actor", lambda actor: actor == "owner")
    registry.append(device())
    before, _ = targets.bind_network_target(validate_argv(["ping", "desk-one"]), actor="owner")
    token = admin._sign_consent_token(before, "owner")
    registry[:] = [device(address="192.0.2.8")]
    after, _ = targets.bind_network_target(validate_argv(["ping", "desk-one"]), actor="owner")
    assert not admin._verify_consent_token(token, after, "owner")


def test_ipv6_request_never_receives_ipv4_address(registry):
    registry.append(device())
    with pytest.raises(targets.NetworkTargetError, match="ADDRESS_UNAVAILABLE"):
        targets.bind_network_target(validate_argv(["ping", "-6", "desk-one"]), actor="owner")


def test_errors_are_localized():
    import i18n
    for code in ("ERR_NETWORK_NAME_AMBIGUOUS", "ERR_NETWORK_ADDRESS_UNAVAILABLE"):
        for lang in ("it", "en"):
            with i18n.language_context(lang):
                assert "<missing:" not in i18n.get(code)
