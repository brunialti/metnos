from __future__ import annotations

import copy
from dataclasses import replace
import inspect
import json
from pathlib import Path, PurePosixPath
import re

import pytest

from contract_bootstrap import ACTIVE_RELATIVE, STORE_RELATIVE
import executor_birth_legacy_state as legacy
import executor_birth_legacy_state_journal as journal_module
import executor_birth_legacy_state_policy as policy_module
import executor_birth_legacy_state_request as request_module
import executor_birth_account_identity as account_identity
import executor_birth_host_layout as host_layout
from executor_birth_authoring import authoring_paths
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1


DISTRIBUTION = "sha256:" + "a" * 64
SERVICE = (41, 42)


def _request() -> legacy.LegacyStateRequestV1:
    record = account_identity.PosixAccountRecordV1(
        "metnos", SERVICE[0], SERVICE[1],
        "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    return legacy.build_legacy_state_request_v1(
        account_identity.PosixAccountSnapshotV1(record, (SERVICE[1],)),
        DISTRIBUTION,
    )


def _directory(path: str, owner=SERVICE):
    return legacy.LegacyPathObservationV1(
        PurePosixPath(path), legacy.LegacyNodeKindV1.directory,
        owner[0], owner[1], 0o700, 2, None, None,
    )


def _file(path: str, payload: bytes, owner=SERVICE, *, nlink=1):
    return legacy.LegacyPathObservationV1(
        PurePosixPath(path), legacy.LegacyNodeKindV1.regular_file,
        owner[0], owner[1], 0o600, nlink, len(payload),
        legacy.legacy_state_file_sha256_v1(payload),
    )


def _observation(*entries) -> legacy.LegacyStateObservationV1:
    return legacy.LegacyStateObservationV1(tuple(sorted(
        entries, key=lambda item: item.relative_path.as_posix().encode("utf-8"),
    )))


def _real_authoring(owner=SERVICE):
    canonical = Path(
        "/var/lib/metnos-service/.local/state/metnos/"
        "contract-authoring/v1/core/sample"
    )
    control = authoring_paths(canonical, "core:sample/manifest.toml").control.name
    base = "contract-authoring/v1/core"
    return _observation(
        _directory("contract-authoring", owner),
        _directory("contract-authoring/v1", owner),
        _directory(base, owner),
        _directory(f"{base}/sample", owner),
        _file(f"{base}/sample/manifest.toml", b"name='sample'\n", owner),
        _directory(f"{base}/{control}", owner),
        _file(f"{base}/{control}/authoring.lock", b"\0", owner),
        _file(f"{base}/{control}/version.json", b"{}", owner),
    )


def test_classifies_fresh_service_and_root_adoption() -> None:
    request = _request()
    assert legacy.classify_legacy_state_v1(
        request, _observation(),
    ) is legacy.LegacyStateDispositionV1.fresh
    assert legacy.classify_legacy_state_v1(
        request, _real_authoring(),
    ) is legacy.LegacyStateDispositionV1.exact_service
    assert legacy.classify_legacy_state_v1(
        request, _real_authoring((0, 0)),
    ) is legacy.LegacyStateDispositionV1.root_adoption_required


def test_real_authoring_paths_and_store_layout_are_accepted() -> None:
    store = STORE_RELATIVE.as_posix()
    publication = _observation(
        _directory(STORE_RELATIVE.parent.as_posix()),
        _directory(store),
        _directory(store + "/" + "b" * 64),
        _directory(store + "/" + "b" * 64 + "/generations"),
        _file(ACTIVE_RELATIVE.as_posix(), b"v1\n"),
        _file(".contract-publications-v1.catalog-admission.lock", b"\0"),
    )
    combined = _observation(*_real_authoring().entries, *publication.entries)
    assert legacy.classify_legacy_state_v1(
        _request(), combined,
    ) is legacy.LegacyStateDispositionV1.exact_service


@pytest.mark.parametrize("mutation", ("extra", "symlink", "hardlink", "staging", "control"))
def test_invalid_authoring_inventory_is_rejected(mutation: str) -> None:
    entries = list(_real_authoring().entries)
    if mutation == "extra":
        entries.append(_directory("contract-authoring/unexpected"))
    elif mutation == "symlink":
        entries.append(legacy.LegacyPathObservationV1(
            PurePosixPath("contract-authoring/v1/core/link"),
            legacy.LegacyNodeKindV1.other, *SERVICE, 0o777, 1, None, None,
        ))
    elif mutation == "hardlink":
        entries.append(_file(
            "contract-authoring/v1/core/sample/linked.py", b"x", nlink=2,
        ))
    elif mutation == "staging":
        entries.append(_directory(
            "contract-authoring/v1/core/.birth-stage-" + "c" * 64,
        ))
    else:
        entries.append(_directory(
            "contract-authoring/v1/core/.sample.birth-control-" + "d" * 64,
        ))
    assert legacy.classify_legacy_state_v1(
        _request(), _observation(*entries),
    ) is legacy.LegacyStateDispositionV1.invalid


def test_exact_closed_record_chain_round_trips_and_carries_digests() -> None:
    request = _request()
    before, after = _real_authoring((0, 0)), _real_authoring()
    planned = legacy.plan_legacy_state_v1(request)
    inventoried = legacy.record_legacy_state_inventoried_v1(
        planned, request, before,
    )
    adopted = legacy.record_authoring_adopted_v1(
        inventoried, request, before, after,
    )
    ready = legacy.record_legacy_state_ready_v1(adopted, request, after)
    records = (planned, inventoried, adopted, ready)
    assert [item.state for item in records] == list(legacy.LegacyStateV1)
    assert [item.intent for item in records] == [
        legacy.LegacyStateIntentV1.INVENTORY,
        legacy.LegacyStateIntentV1.ADOPT_AUTHORING,
        legacy.LegacyStateIntentV1.CONVERGE_CONTRACTS_AND_VERIFY,
        None,
    ]
    encoded = tuple(legacy.encode_legacy_state_record_v1(item) for item in records)
    assert legacy.decode_legacy_state_chain_v1(encoded) == records
    assert ready.inventory_sha256 == inventoried.inventory_sha256
    assert inventoried.adoption_target_sha256 == after.observation_sha256
    assert ready.adoption_target_sha256 == inventoried.adoption_target_sha256
    assert ready.authoring_sha256 == adopted.authoring_sha256
    assert ready.ready_sha256 == adopted.authoring_sha256


def test_protocol_has_stable_golden_digests() -> None:
    request = _request()
    observation = _observation()
    planned = legacy.plan_legacy_state_v1(request)
    assert legacy.legacy_state_policy_sha256_v1() == (
        "sha256:cbbc2c326de1aef494f9a31a6ab0437e7f67e4af018de0e09920962347752f12"
    )
    assert request.request_id == (
        "sha256:9839f15abbaab1b6fb19bdb6292e55368f6aa7aa85f1681f775907acc948142f"
    )
    assert observation.observation_sha256 == (
        "sha256:8080b6a3a55bcaa5c4142928f04172d19296e2e00b44407dd40724504537aacb"
    )
    assert planned.record_sha256 == (
        "sha256:ef7522a9f78acdeeda551a991cfcb08b5105294d9907c6d4a5fb3b6ab5d345d4"
    )


def test_records_reject_invalid_transition_tamper_and_duplicate_keys() -> None:
    request = _request()
    planned = legacy.plan_legacy_state_v1(request)
    with pytest.raises(legacy.LegacyStateError, match="birth_legacy_state_invalid"):
        legacy.record_authoring_adopted_v1(
            planned, request, _real_authoring(), _real_authoring(),
        )
    encoded = legacy.encode_legacy_state_record_v1(planned)
    value = json.loads(encoded)
    value["request_id"] = "sha256:" + "f" * 64
    tampered = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(legacy.LegacyStateError):
        legacy.decode_legacy_state_record_v1(tampered)
    duplicate = encoded[:-1] + b',"state":"PLANNED"}'
    with pytest.raises(legacy.LegacyStateError):
        legacy.decode_legacy_state_record_v1(duplicate)
    value = json.loads(encoded)
    value["schema_version"] = True
    wrong_type = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    with pytest.raises(legacy.LegacyStateError):
        legacy.decode_legacy_state_record_v1(wrong_type)


def test_terminal_record_carries_a_distinct_post_convergence_observation() -> None:
    request = _request()
    planned = legacy.plan_legacy_state_v1(request)
    inventoried = legacy.record_legacy_state_inventoried_v1(
        planned, request, _real_authoring((0, 0)),
    )
    adopted = legacy.record_authoring_adopted_v1(
        inventoried, request, _real_authoring((0, 0)), _real_authoring(),
    )
    ready = legacy.record_legacy_state_ready_v1(
        adopted, request, _real_authoring(),
    )
    value = json.loads(legacy.encode_legacy_state_record_v1(ready))
    value["ready_sha256"] = "sha256:" + "f" * 64
    unsigned = dict(value)
    unsigned.pop("record_sha256")
    value["record_sha256"] = framed_sha256_v1(
        b"metnos.executor-birth.legacy-state-record/v1\0",
        encode_canonical_ascii_v1(unsigned),
    )
    decoded = legacy.decode_legacy_state_record_v1(
        encode_canonical_ascii_v1(value),
    )
    assert decoded.ready_sha256 == "sha256:" + "f" * 64


def test_adoption_delta_rejects_every_change_except_authoring_owner() -> None:
    request = _request()
    root_before = _real_authoring((0, 0))
    service = _real_authoring()
    assert legacy.legacy_state_adoption_delta_valid_v1(
        request, root_before, service,
    )
    assert legacy.legacy_state_adoption_delta_valid_v1(
        request, service, service,
    )
    assert legacy.legacy_state_adoption_delta_valid_v1(
        request, _observation(), _observation(),
    )
    changed = list(service.entries)
    changed[-1] = _file(changed[-1].relative_path.as_posix(), b"changed")
    assert not legacy.legacy_state_adoption_delta_valid_v1(
        request, root_before, _observation(*changed),
    )
    assert not legacy.legacy_state_adoption_delta_valid_v1(
        request, _observation(), service,
    )


def test_adoption_target_resumes_only_the_same_partially_chowned_tree() -> None:
    request = _request()
    original = _real_authoring((0, 0))
    final = _real_authoring()
    mixed = _observation(*(
        replace(entry, uid=SERVICE[0], gid=SERVICE[1])
        if index % 2 else entry
        for index, entry in enumerate(original.entries)
    ))
    target = legacy.legacy_state_adoption_target_sha256_v1(request, original)
    assert target == final.observation_sha256
    assert legacy.legacy_state_adoption_target_sha256_v1(request, mixed) == target
    assert legacy.legacy_state_adoption_resume_valid_v1(request, target, mixed)
    inventoried = legacy.record_legacy_state_inventoried_v1(
        legacy.plan_legacy_state_v1(request), request, original,
    )
    adopted = legacy.record_authoring_adopted_v1(
        inventoried, request, mixed, final,
    )
    assert adopted.authoring_sha256 == target
    changed = list(mixed.entries)
    changed[-1] = _file(changed[-1].relative_path.as_posix(), b"changed")
    assert not legacy.legacy_state_adoption_resume_valid_v1(
        request, target, _observation(*changed),
    )


def test_adoption_preserves_publication_and_control_entries() -> None:
    request = _request()
    publication = (
        _directory("contract-publications"),
        _directory("contract-publications/v1"),
        _file("contract-publications.ACTIVE", b"v1\n"),
        _file(".contract-publications-v1.catalog-admission.lock", b"\0"),
    )
    before = _observation(*_real_authoring((0, 0)).entries, *publication)
    after = _observation(*_real_authoring().entries, *publication)
    assert legacy.legacy_state_adoption_delta_valid_v1(request, before, after)
    changed = list(after.entries)
    index = next(
        index for index, item in enumerate(changed)
        if item.relative_path.as_posix() == "contract-publications.ACTIVE"
    )
    changed[index] = _file("contract-publications.ACTIVE", b"bad")
    assert not legacy.legacy_state_adoption_delta_valid_v1(
        request, before, _observation(*changed),
    )
    changed[index] = _file(
        "contract-publications.ACTIVE", b"v1\n", owner=(0, 0),
    )
    assert not legacy.legacy_state_adoption_delta_valid_v1(
        request, before, _observation(*changed),
    )


def test_record_requires_the_exact_inventoried_observation() -> None:
    request = _request()
    before, after = _real_authoring((0, 0)), _real_authoring()
    inventoried = legacy.record_legacy_state_inventoried_v1(
        legacy.plan_legacy_state_v1(request), request, before,
    )
    with pytest.raises(legacy.LegacyStateError):
        legacy.record_authoring_adopted_v1(
            inventoried, request, _observation(), after,
        )


def test_request_is_factory_bound_and_raw_test_request_cannot_start_journal() -> None:
    request = _request()
    assert request.state_root == PurePosixPath(
        "/var/lib/metnos-service/.local/state/metnos"
    )
    with pytest.raises(TypeError):
        legacy.LegacyStateRequestV1(  # type: ignore[call-arg]
            request.state_root, *SERVICE, DISTRIBUTION,
        )
    raw = request_module._build_legacy_state_request_for_test_v1(
        PurePosixPath("/tmp/state"), *SERVICE, DISTRIBUTION,
    )
    with pytest.raises(legacy.LegacyStateError):
        legacy.plan_legacy_state_v1(raw)


@pytest.mark.parametrize("path", ["/tmp/bad\0name", "/tmp/bad\udcffname"])
def test_raw_request_paths_fail_with_typed_error(path: str) -> None:
    with pytest.raises(legacy.LegacyStateError):
        request_module._build_legacy_state_request_for_test_v1(
            PurePosixPath(path), *SERVICE, DISTRIBUTION,
        )


def test_canonical_request_rejects_replace_to_data_or_cache_role() -> None:
    request = _request()
    spec = host_layout.build_host_layout_spec_v1(request._account)
    paths = {
        item.role: item.path for item in spec.objects
        if item.role in {host_layout.HostPathRoleV1.data, host_layout.HostPathRoleV1.cache}
    }
    for role in (host_layout.HostPathRoleV1.data, host_layout.HostPathRoleV1.cache):
        mutant = replace(request, state_root=paths[role])
        with pytest.raises(legacy.LegacyStateError):
            legacy.require_canonical_legacy_state_request_v1(mutant)
        with pytest.raises(legacy.LegacyStateError):
            legacy.plan_legacy_state_v1(mutant)
        with pytest.raises(legacy.LegacyStateError):
            legacy.classify_legacy_state_v1(mutant, _observation())


def test_canonical_request_denies_copy_and_deepcopy() -> None:
    request = _request()
    with pytest.raises(legacy.LegacyStateError):
        copy.copy(request)
    with pytest.raises(legacy.LegacyStateError):
        copy.deepcopy(request)


def test_every_journal_effect_boundary_revalidates_request() -> None:
    request = _request()
    before, after = _real_authoring((0, 0)), _real_authoring()
    planned = legacy.plan_legacy_state_v1(request)
    inventoried = legacy.record_legacy_state_inventoried_v1(
        planned, request, before,
    )
    adopted = legacy.record_authoring_adopted_v1(
        inventoried, request, before, after,
    )
    spec = host_layout.build_host_layout_spec_v1(request._account)
    cache = next(
        item.path for item in spec.objects
        if item.role is host_layout.HostPathRoleV1.cache
    )
    mutant = replace(request, state_root=cache)
    calls = (
        lambda: legacy.record_legacy_state_inventoried_v1(planned, mutant, before),
        lambda: legacy.legacy_state_adoption_delta_valid_v1(mutant, before, after),
        lambda: legacy.record_authoring_adopted_v1(
            inventoried, mutant, before, after,
        ),
        lambda: legacy.record_legacy_state_ready_v1(adopted, mutant, after),
    )
    for call in calls:
        with pytest.raises(legacy.LegacyStateError):
            call()


def test_bad_typed_account_is_translated_to_legacy_error() -> None:
    bad = account_identity.PosixAccountSnapshotV1(object(), ())
    with pytest.raises(legacy.LegacyStateError):
        legacy.build_legacy_state_request_v1(bad, DISTRIBUTION)


def test_scaffolding_nonfile_fields_and_node_type_are_closed() -> None:
    request = _request()
    assert legacy.classify_legacy_state_v1(
        request, _observation(_directory("contract-authoring")),
    ) is legacy.LegacyStateDispositionV1.invalid
    with pytest.raises(legacy.LegacyStateError):
        legacy.LegacyPathObservationV1(
            PurePosixPath("contract-authoring"),
            legacy.LegacyNodeKindV1.directory, *SERVICE, 0o700, 2, 1, None,
        )
    with pytest.raises(legacy.LegacyStateError):
        legacy.LegacyPathObservationV1(
            PurePosixPath("contract-authoring"), object(),
            *SERVICE, 0o700, 2, None, None,
        )
    assert legacy.classify_legacy_state_v1(
        request, _observation(_directory("contract-publications")),
    ) is legacy.LegacyStateDispositionV1.invalid
    assert legacy.classify_legacy_state_v1(
        request, _observation(
            _directory("contract-publications"),
            _directory("contract-publications/v1"),
            _directory("contract-publications/v1/.birth-backup-" + "e" * 64),
        ),
    ) is legacy.LegacyStateDispositionV1.invalid


def test_policy_digest_binds_fsm_regex_and_control_leaves(monkeypatch) -> None:
    baseline = legacy.legacy_state_policy_sha256_v1()
    mutants = (
        ("LEGACY_STATE_FSM_V1", (("PLANNED", None),)),
        ("_CONTROL_RE", re.compile(r"never\Z")),
        ("_TRANSACTION_RE", re.compile(r"also-never\Z")),
        ("_CONTROL_LEAF_SIZES", {"authoring.lock": 2, "version.json": None}),
    )
    for name, value in mutants:
        with monkeypatch.context() as scoped:
            scoped.setattr(policy_module, name, value)
            assert legacy.legacy_state_policy_sha256_v1() != baseline
    with monkeypatch.context() as scoped:
        scoped.setattr(
            policy_module, "host_provisioning_policy_sha256_v1",
            lambda: "sha256:" + "f" * 64,
        )
        assert legacy.legacy_state_policy_sha256_v1() != baseline


def test_modules_are_pure_small_and_functions_are_bounded() -> None:
    assert not hasattr(policy_module, "os")
    for module in (request_module, policy_module, journal_module):
        source = inspect.getsource(module)
        assert len(source.splitlines()) <= 400
        for value in vars(module).values():
            if inspect.isfunction(value) and value.__module__ == module.__name__:
                assert len(inspect.getsource(value).splitlines()) <= 40
