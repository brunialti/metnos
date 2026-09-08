"""Portable characterization of the pure host-provisioning journal."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import inspect
import json
from pathlib import Path

import pytest

import executor_birth_account_identity as identity
import executor_birth_canonical as canonical
import executor_birth_crypto_framing as framing
import executor_birth_host_layout as layout
import executor_birth_host_path_policy as path_policy
import executor_birth_host_provisioning_evidence as evidence
import executor_birth_host_provisioning_journal as journal


_RECORD_DOMAIN_V1 = b"metnos.executor-birth.host-provisioning-record/v1\0"
_DIGEST_A = "sha256:" + "a" * 64


def _snapshot(**changes) -> identity.PosixAccountSnapshotV1:
    values = {
        "name": "metnos",
        "uid": 991,
        "gid": 992,
        "home": "/var/lib/metnos-service",
        "shell": "/usr/sbin/nologin",
    }
    values.update(changes)
    record = identity.PosixAccountRecordV1(**values)
    return identity.PosixAccountSnapshotV1(record, (record.gid,))


def _conforming_observation(
    account: identity.PosixAccountSnapshotV1,
) -> layout.HostLayoutObservationV1:
    spec = layout.build_host_layout_spec_v1(account)
    objects = tuple(
        layout.HostPathObservationV1(
            item.path,
            layout.HostNodeKindV1.directory,
            item.ownership.uid,
            item.ownership.gid,
            item.mode,
            False,
            False,
        )
        for item in spec.objects
    )
    return layout.HostLayoutObservationV1(account, objects)


def _records() -> tuple[journal.HostProvisioningRecordV1, ...]:
    account = _snapshot()
    observation = _conforming_observation(account)
    planned = journal.plan_host_provisioning_v1()
    account_ready = journal.record_account_ready_v1(planned, account)
    layout_ready = journal.record_layout_ready_v1(
        account_ready, account, observation,
    )
    verified = journal.record_host_verified_v1(
        layout_ready, account, observation,
    )
    return planned, account_ready, layout_ready, verified


def _encoded_value(record: journal.HostProvisioningRecordV1) -> dict[str, object]:
    return json.loads(journal.encode_host_provisioning_record_v1(record))


def _rehash(value: dict[str, object]) -> bytes:
    value = dict(value)
    material = dict(value)
    material.pop("record_sha256")
    payload = canonical.encode_canonical_ascii_v1(material)
    value["record_sha256"] = framing.framed_sha256_v1(
        _RECORD_DOMAIN_V1, payload,
    )
    return canonical.encode_canonical_ascii_v1(value)


def test_canonical_and_framing_owners_have_golden_bytes() -> None:
    value = {"z": "é", "a": [1, True, None]}
    encoded = canonical.encode_canonical_ascii_v1(value)
    assert encoded == b'{"a":[1,true,null],"z":"\\u00e9"}'
    assert canonical.decode_canonical_ascii_v1(encoded, maximum=128) == value
    assert framing.framed_sha256_v1(
        b"metnos.executor-birth.test/v1\0", encoded,
    ) == "sha256:ffcd72ee201cfeacae2a8e0e1949e078e9eb289b45551526570015548846e838"


def test_exact_fsm_intents_sequence_chain_and_carry_forward() -> None:
    records = _records()
    assert tuple(record.state for record in records) == (
        journal.HostProvisioningStateV1.PLANNED,
        journal.HostProvisioningStateV1.ACCOUNT_READY,
        journal.HostProvisioningStateV1.LAYOUT_READY,
        journal.HostProvisioningStateV1.HOST_VERIFIED,
    )
    assert tuple(record.intent for record in records) == (
        journal.HostProvisioningIntentV1.ENSURE_ACCOUNT,
        journal.HostProvisioningIntentV1.ENSURE_LAYOUT,
        journal.HostProvisioningIntentV1.VERIFY_HOST,
        None,
    )
    assert tuple(record.sequence for record in records) == (0, 1, 2, 3)
    assert records[0].previous_record_sha256 is None
    assert tuple(record.previous_record_sha256 for record in records[1:]) == tuple(
        record.record_sha256 for record in records[:-1]
    )
    assert len({record.request_id for record in records}) == 1
    assert len({record.policy_sha256 for record in records}) == 1
    assert records[0].account_sha256 is None
    assert records[0].layout_spec_sha256 is None
    assert records[0].layout_observation_sha256 is None
    assert len({record.account_sha256 for record in records[1:]}) == 1
    assert len({record.layout_spec_sha256 for record in records[1:]}) == 1
    assert records[1].layout_observation_sha256 is None
    assert records[2].layout_observation_sha256 == records[3].layout_observation_sha256


def test_records_and_complete_chain_round_trip_canonically() -> None:
    records = _records()
    encoded = tuple(
        journal.encode_host_provisioning_record_v1(record) for record in records
    )
    decoded = journal.decode_host_provisioning_chain_v1(encoded)
    assert decoded == records
    assert tuple(
        journal.encode_host_provisioning_record_v1(record) for record in decoded
    ) == encoded


def test_request_policy_and_typed_evidence_are_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()
    first_observation = _conforming_observation(first)
    second_observation = _conforming_observation(second)
    assert evidence.host_provisioning_request_id_v1() == (
        "sha256:59035c3ad264c0733fdb737472a190ced0e6ed98fa31e0f2f06cb7fd1948275d"
    )
    assert evidence.host_provisioning_policy_sha256_v1() == (
        "sha256:9893adfcf88f69900130c77f0f00b43b8652f72796e2a563ecbac3faa248d019"
    )
    assert evidence.host_account_snapshot_sha256_v1(first) == (
        evidence.host_account_snapshot_sha256_v1(second)
    )
    assert evidence.host_layout_spec_sha256_v1(first) == (
        evidence.host_layout_spec_sha256_v1(second)
    )
    assert evidence.host_layout_observation_sha256_v1(
        first, first_observation,
    ) == evidence.host_layout_observation_sha256_v1(second, second_observation)


def test_policy_digest_and_layout_share_the_immutable_path_owner(
    monkeypatch,
) -> None:
    original = path_policy.HOST_PATH_POLICY_V1
    with pytest.raises(FrozenInstanceError):
        original[-1].mode = 0o710
    changed = original[:-1] + (replace(original[-1], mode=0o710),)
    baseline = evidence.host_provisioning_policy_sha256_v1()
    monkeypatch.setattr(path_policy, "HOST_PATH_POLICY_V1", changed)
    assert layout.build_host_layout_spec_v1(_snapshot()).objects[-1].mode == 0o710
    assert evidence.host_provisioning_policy_sha256_v1() != baseline


def test_policy_digest_binds_the_canonical_trust_anchors(monkeypatch) -> None:
    baseline = evidence.host_provisioning_policy_sha256_v1()
    monkeypatch.setattr(
        path_policy, "HOST_TRUST_ANCHORS_V1",
        path_policy.HOST_TRUST_ANCHORS_V1[:-1],
    )
    assert evidence.host_provisioning_policy_sha256_v1() != baseline


def test_only_specific_typed_transition_constructors_are_public() -> None:
    assert not any(name.startswith("append") for name in journal.__all__)
    assert tuple(inspect.signature(journal.plan_host_provisioning_v1).parameters) == ()
    assert tuple(inspect.signature(journal.record_account_ready_v1).parameters) == (
        "planned", "account",
    )
    assert tuple(inspect.signature(journal.record_layout_ready_v1).parameters) == (
        "account_ready", "account", "observation",
    )
    assert tuple(inspect.signature(journal.record_host_verified_v1).parameters) == (
        "layout_ready", "account", "observation",
    )
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_account_ready_v1(journal.plan_host_provisioning_v1(), object())


def test_wrong_predecessor_and_changed_typed_identity_are_rejected() -> None:
    planned, account_ready, layout_ready, _ = _records()
    account = _snapshot()
    observation = _conforming_observation(account)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_account_ready_v1(account_ready, account)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_layout_ready_v1(planned, account, observation)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_host_verified_v1(account_ready, account, observation)
    changed = _snapshot(uid=993)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_layout_ready_v1(
            account_ready, changed, _conforming_observation(changed),
        )
    assert layout_ready.state is journal.HostProvisioningStateV1.LAYOUT_READY


def test_nonconverged_typed_observation_is_rejected() -> None:
    planned = journal.plan_host_provisioning_v1()
    account = _snapshot()
    account_ready = journal.record_account_ready_v1(planned, account)
    spec = layout.build_host_layout_spec_v1(account)
    missing = layout.HostLayoutObservationV1(
        account,
        tuple(layout.HostPathObservationV1(
            item.path, layout.HostNodeKindV1.missing,
        ) for item in spec.objects),
    )
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.record_layout_ready_v1(account_ready, account, missing)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(unexpected=True),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(sequence=True),
        lambda value: value.update(state="ACCOUNT_READY"),
        lambda value: value.update(intent=None),
        lambda value: value.update(request_id=_DIGEST_A),
        lambda value: value.update(record_sha256=_DIGEST_A),
    ],
)
def test_closed_record_schema_types_grammar_and_hash_fail_closed(mutate) -> None:
    value = _encoded_value(journal.plan_host_provisioning_v1())
    mutate(value)
    encoded = canonical.encode_canonical_ascii_v1(value)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_record_v1(encoded)


def test_duplicate_noncanonical_and_oversize_documents_fail_closed() -> None:
    encoded = journal.encode_host_provisioning_record_v1(
        journal.plan_host_provisioning_v1(),
    )
    duplicate = encoded.replace(
        b'{"account_sha256":null,',
        b'{"account_sha256":null,"account_sha256":null,',
        1,
    )
    noncanonical = encoded.replace(b',"intent"', b', "intent"', 1)
    oversized = b"{" + b" " * journal.MAX_HOST_PROVISIONING_RECORD_BYTES_V1
    for invalid in (duplicate, noncanonical, oversized, b"", bytearray(encoded)):
        with pytest.raises(journal.HostProvisioningJournalError):
            journal.decode_host_provisioning_record_v1(invalid)


def test_state_jump_and_chain_start_fail_closed() -> None:
    planned, account_ready, _, _ = _records()
    jumped = _encoded_value(account_ready)
    jumped["state"] = "LAYOUT_READY"
    jumped["intent"] = "VERIFY_HOST"
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_record_v1(_rehash(jumped))
    encoded_account = journal.encode_host_provisioning_record_v1(account_ready)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_chain_v1((encoded_account,))
    assert planned.sequence == 0


def test_chain_rejects_validly_rehashed_link_and_digest_tampering() -> None:
    records = _records()
    encoded = [journal.encode_host_provisioning_record_v1(item) for item in records]
    bad_link = _encoded_value(records[1])
    bad_link["previous_record_sha256"] = _DIGEST_A
    forged_link = _rehash(bad_link)
    journal.decode_host_provisioning_record_v1(forged_link)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_chain_v1((encoded[0], forged_link))
    changed_digest = _encoded_value(records[2])
    changed_digest["account_sha256"] = _DIGEST_A
    forged_digest = _rehash(changed_digest)
    journal.decode_host_provisioning_record_v1(forged_digest)
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_chain_v1(
            (encoded[0], encoded[1], forged_digest),
        )


@pytest.mark.parametrize("encoded", [(), (b"{}",) * 5, [], None])
def test_chain_has_a_closed_bounded_container_shape(encoded) -> None:
    with pytest.raises(journal.HostProvisioningJournalError):
        journal.decode_host_provisioning_chain_v1(encoded)


def test_split_modules_are_pure_small_and_have_short_functions(tmp_path: Path) -> None:
    modules = (canonical, framing, evidence, journal)
    before = tuple(tmp_path.iterdir())
    forbidden = {"os", "pwd", "grp", "subprocess", "tempfile", "shutil"}
    for module in modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not imports & forbidden
        assert len(source.splitlines()) <= 400
        function_sizes = [
            node.end_lineno - node.lineno + 1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert max(function_sizes) <= 40
    assert tuple(tmp_path.iterdir()) == before
