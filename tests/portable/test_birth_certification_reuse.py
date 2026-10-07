"""Signed inert two-installation fixtures; no real installation is certified."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import base64
import hashlib
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import birth_certification_evidence as ledger
from install import birth_certification_reuse as reuse
from executor_birth_certification_authority import (
    encode_certification_registry_v1, decode_certification_registry_v1,
)
from executor_birth_lifecycle import CERTIFICATION_DOMAIN, ACTIVATION_POLICY, LifecycleError
from executor_birth_authority_files import OwnershipAuthorityError
from test_certification_shared import make_cases

native = pytest.mark.skipif(not sys.platform.startswith("linux"),
                           reason="managed trust-file custody is Linux-only")


def digest(label):
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def context(label):
    return dict(installation_id=digest(label + "/installation"), head_id=digest(label + "/head"),
                closed_build_id=digest(label + "/build"), migration_id=digest(label + "/migration"),
                source_closure=digest("same reviewed sources"), conditions=dict(platform="linux",
                    architecture="x86_64", native_preflight="verified", migration="completed",
                    service_policy=digest("same effective service policy")))


def test_conditions_source_closure_covers_all_authenticated_product_files(tmp_path):
    import test_executor_birth_distribution_manifest as fixture
    distribution = fixture.distribution
    private, key_id, registry = fixture._authority(distribution.PURPOSE)
    required = ["install/certification/" + name for name in (
        "focused.py", "oracle.py", "postconditions.py", "native_probe.py",
        "observations.py", "schemas.json", "http_capture.py")]
    required += ["install/birth_certification_reuse.py", "runtime/llm_workloads.py"]

    def closure(label, *, changed=None, missing=None):
        root = tmp_path / label

        def files_mutate(files, path):
            for name in required + ["prompts/inert.txt", "executors/inert.py", "docs/inert.md"]:
                if name != missing:
                    fixture._add_declared_file(files, path, name, "runtime_code"
                        if name.endswith(".py") else "public_document", b"INERT = 1\n")
            if changed:
                fixture._replace_declared_file(files, path, changed, b"INERT = 2\n")

        _, encoded, signature = fixture._manifest(root, private, key_id, files_mutate=files_mutate)
        verified = distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=fixture._test_environment(root))
        return reuse.source_closure_v1(verified)

    expected = closure("original")
    assert closure("another_installation") == expected
    assert closure("deployment", changed="deployment/admin/preflight.py") == expected
    assert closure("documentation", changed="docs/inert.md") == expected
    for index, path in enumerate(("runtime/llm_workloads.py", "prompts/inert.txt",
                                 "executors/inert.py", "install/certification/oracle.py")):
        assert closure(f"changed-{index}", changed=path) != expected
    with pytest.raises(reuse.ReuseError, match="source closure incomplete"):
        closure("missing", missing="install/certification/http_capture.py")
    with pytest.raises(reuse.ReuseError, match="unauthenticated distribution"):
        reuse.source_closure_v1(SimpleNamespace(files=[]))


def sign(document, private, domain=reuse.EXPORT_DOMAIN_V1):
    document = {key: value for key, value in document.items() if key != "signature"}
    return reuse.canonical({**document, "signature": base64.b64encode(
        private.sign(domain + reuse.canonical(document))).decode("ascii")})


@contextmanager
def evidence(*, native=False, mutate=None, queued_subjects=None):
    connection = sqlite3.connect(":memory:")
    for statement in ledger._DDL:
        connection.execute(statement)
    owner = ledger._Evidence(connection)
    owner.census(scope_id=digest("inert census"), sources=(b"inert source observation",),
                 review=b"inert review", findings={})
    try:
        if native:
            manifest, cases, cycles = make_cases(queued_subjects=queued_subjects)
            if mutate:
                mutate(manifest, cases, cycles)
            origin = context("origin")
            owner.profile(base=dict(installation_id=origin["installation_id"], head_id=origin["head_id"],
                source_id=origin["source_closure"], catalog_id=digest("catalog"), harness_id=digest("harness")),
                manifest=reuse.canonical(manifest), cases=reuse.canonical(cases))
            for cycle in cycles:
                started = owner.start_cycle().pending_cycle
                owner.finish_cycle(start=started, results=reuse.canonical([item["result"] for item in cycle]),
                    turns={item["case"]["case_id"]: {captured["native"]["turn_id"]: reuse.canonical(item)
                        for captured in item["inputs"]["captures"]} for item in cycle})
        yield owner
    finally:
        connection.close()


@pytest.fixture
def signed_export():
    private = Ed25519PrivateKey.generate()
    registry = encode_certification_registry_v1(private.public_key())
    public = decode_certification_registry_v1(registry)
    with evidence(native=True) as owner:
        document, artifacts = reuse.build_export_v1(owner, context("origin"), public.key_id)
    return sign(document, private), artifacts, public, private, registry


def test_positive_two_installations_replay_full_signed_profile(signed_export):
    signed, artifacts, public, _, _ = signed_export
    result = reuse.verify_export_v1(signed, artifacts, public, context("destination"))
    assert result["origin"]["installation_id"] != context("destination")["installation_id"]
    assert len(result["cycles"]) == 2 and len(result["events"]) == 6
    assert reuse.decode_bundle_v1(reuse.encode_bundle_v1(signed, artifacts)) == (signed, artifacts)
    with evidence() as owner:
        owner.import_operational(exported=signed, artifacts=artifacts,
                                 destination=reuse.canonical(context("destination")), predecessor=None)
        assert owner.frontier.profile is None and owner.frontier.consecutive_successes == ()
        assert owner.frontier.operational_import is not None
        assert owner.frontier.event_count == 2


def test_signed_replay_preserves_distinct_frozen_queue_subjects():
    private = Ed25519PrivateKey.generate()
    public = decode_certification_registry_v1(encode_certification_registry_v1(private.public_key()))
    with evidence(native=True, queued_subjects={
        "1": "builtin:inert_long_first/manifest.toml",
        "2": "builtin:inert_long_second/manifest.toml",
    }) as owner:
        document, artifacts = reuse.build_export_v1(owner, context("origin"), public.key_id)
    result = reuse.verify_export_v1(sign(document, private), artifacts, public, context("destination"))
    assert len(result["cycles"]) == 2


@pytest.mark.parametrize("damage", ["signature", "key", "revoked", "purpose", "policy", "missing",
                                   "altered", "extra", "size", "event", "source", "platform", "model"])
def test_refuse_signed_export_tamper_or_changed_obligation(signed_export, damage):
    signed, artifacts, public, private, _ = signed_export
    destination = context("destination")
    document = json.loads(signed)
    artifacts = dict(artifacts)
    if damage == "signature":
        document["origin"]["head_id"] = digest("changed")
        signed = reuse.canonical(document)
    elif damage == "key":
        public = replace(public, public_key=Ed25519PrivateKey.generate().public_key())
    elif damage == "revoked":
        public = replace(public, status="revoked")
    elif damage in {"purpose", "policy"}:
        document["purpose" if damage == "purpose" else "policy_id"] = "another purpose"
        signed = sign(document, private)
    elif damage == "missing":
        artifacts.pop(next(iter(artifacts)))
    elif damage == "altered":
        artifacts[next(iter(artifacts))] = b"altered"
    elif damage == "extra":
        artifacts[digest("extra")] = b"extra"
    elif damage == "size":
        document["artifacts"][next(iter(artifacts))] += 1
        signed = sign(document, private)
    elif damage == "event":
        document["events"].append(document["events"][0])
        signed = sign(document, private)
    elif damage == "source":
        destination["source_closure"] = digest("changed code")
    else:
        destination["conditions"]["platform" if damage == "platform" else "service_policy"] = (
            "windows" if damage == "platform" else digest("changed model"))
    with pytest.raises(ValueError):
        reuse.verify_export_v1(signed, artifacts, public, destination)


@pytest.mark.parametrize("damage", ["one-cycle", "same-subject", "same-job", "overlap", "pass-flag", "action"])
def test_refuse_incomplete_or_false_native_history(damage):
    def mutate(manifest, _cases, cycles):
        if damage == "one-cycle":
            cycles.pop()
        elif damage == "same-subject":
            manifest["cycle_subjects"]["2"] = manifest["cycle_subjects"]["1"]
        elif damage == "same-job":
            for snapshot in cycles[1][7]["inputs"]["native_evidence"]["restarted_attempt"][:2]:
                snapshot["workload"]["id"] = "inert-job-1"
        elif damage == "overlap":
            cycles[1][0]["inputs"]["started_at"] = 1
        elif damage == "pass-flag":
            cycles[0][0]["inputs"]["native_evidence"]["birth_reused"][2]["publication"] = None
        else:
            cycles[0][7]["inputs"]["native_evidence"].pop("service_restarted")
    with evidence(native=True, mutate=mutate) as owner, pytest.raises(ValueError):
        reuse.build_export_v1(owner, context("origin"), "inert-key")


@native
def test_trust_unknown_origin_is_not_created_by_import(tmp_path, signed_export):
    _, _, _, _, registry = signed_export
    origin = context("origin")["installation_id"]
    tmp_path.chmod(0o755)
    with pytest.raises(OwnershipAuthorityError):
        reuse._read_trust(tmp_path, origin, root_owned=False)
    assert not list(tmp_path.iterdir())
    accepted = reuse._write_trust(tmp_path, origin, registry, root_owned=False)
    public, _ = reuse._read_trust(tmp_path, origin, root_owned=False)
    assert accepted["key_id"] == public.key_id and public.status == "active"
    revoked = encode_certification_registry_v1(public.public_key, status="revoked")
    reuse._write_trust(tmp_path, origin, revoked, root_owned=False)
    with pytest.raises(reuse.ReuseError, match="revocation is final"):
        reuse._write_trust(tmp_path, origin, registry, root_owned=False)
    other = encode_certification_registry_v1(Ed25519PrivateKey.generate().public_key())
    with pytest.raises(reuse.ReuseError, match="key replacement"):
        reuse._write_trust(tmp_path, origin, other, root_owned=False)


@native
def test_trust_symlink_and_wrong_mode_refused(tmp_path, signed_export):
    _, _, _, _, registry = signed_export
    origin = context("origin")["installation_id"]
    tmp_path.chmod(0o755)
    reuse._write_trust(tmp_path, origin, registry, root_owned=False)
    path = reuse._trust_file(tmp_path, origin)
    path.chmod(0o666)
    with pytest.raises(OwnershipAuthorityError):
        reuse._read_trust(tmp_path, origin, root_owned=False)
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(OwnershipAuthorityError):
        reuse._write_trust(tmp_path, origin, registry, root_owned=False)


def predecessor(owner, destination, private, public):
    qualification = dict(qualification_id=digest("inert qualification"), evidence_head=owner.frontier.head)
    certificate = sign(dict(schema_version=1, purpose="f5_activation_v1", policy_id=ACTIVATION_POLICY,
        **{key: destination[key] for key in ("installation_id", "head_id", "closed_build_id", "migration_id")},
        qualification_id=qualification["qualification_id"], key_id=public.key_id), private, CERTIFICATION_DOMAIN)
    owner.prepare_certificate(certificate=certificate, qualification=reuse.canonical(qualification))
    return certificate


@pytest.mark.parametrize("damage", [None, "migration", "source", "missing-predecessor", "same-head", "finding", "revoked"])
def test_continuation_retains_predecessor_and_refuses_new_obligations(signed_export, damage):
    signed, artifacts, public, private, _ = signed_export
    destination = context("destination")
    with evidence() as owner:
        owner.import_operational(exported=signed, artifacts=artifacts,
            destination=reuse.canonical(destination), predecessor=None)
        certificate = predecessor(owner, destination, private, public)
        successor = dict(destination, head_id=digest("new head"), closed_build_id=digest("new build"))
        if damage in {"migration", "source"}:
            successor["migration_id" if damage == "migration" else "source_closure"] = digest("changed")
        elif damage == "missing-predecessor":
            value = json.loads(certificate)
            value["qualification_id"] = digest("unarchived")
            certificate = sign(value, private, CERTIFICATION_DOMAIN)
        elif damage == "same-head":
            successor = destination
        elif damage == "finding":
            owner.open_defect("inert-defect", b"inert real finding")
        elif damage == "revoked":
            public = replace(public, status="revoked")
        if damage:
            with pytest.raises((ValueError, LifecycleError)):
                reuse._continue(owner, successor, certificate, public, public)
        else:
            proposal = reuse._continue(owner, successor, certificate, public, public)
            before = owner.frontier.head
            owner.import_operational(**proposal)
            assert proposal["predecessor"] == certificate and owner.frontier.head != before
            event = owner.event(owner.frontier.operational_import)["payload"]
            assert owner._artifact(event["predecessor"]) == certificate
            assert json.loads(owner._artifact(event["destination"])) == successor
            with pytest.raises(reuse.ReuseError, match="continuation required"):
                reuse._check_import_history(owner, json.loads(signed), successor)


def test_continuation_cannot_reimport_cycles_after_a_finding(signed_export):
    signed, artifacts, _, _, _ = signed_export
    with evidence() as owner:
        destination = context("destination")
        owner.import_operational(exported=signed, artifacts=artifacts,
                                 destination=reuse.canonical(destination), predecessor=None)
        opened = owner.open_defect("defect", b"finding").head
        owner.close_defect("defect", opening=opened, repair=b"fix", verification=b"test", review=b"review")
        with pytest.raises(reuse.ReuseError, match="invalidated"):
            reuse._check_import_history(owner, json.loads(signed), destination)


@pytest.mark.parametrize("race", [None, "destination", "trust"])
def test_positive_import_rereads_frontiers_before_append(monkeypatch, signed_export, race):
    import install.birth_certification_issuer as issuer
    signed, artifacts, public, _, _ = signed_export
    destination = context("destination")
    contexts = iter([destination, dict(destination, head_id=digest("raced")) if race == "destination" else destination])
    trust = iter([(public, b"trust"), (public, b"raced" if race == "trust" else b"trust")])
    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    monkeypatch.setattr(reuse, "_root_owned_chain", lambda _root: None)
    monkeypatch.setattr(reuse, "observe_destination_v1", lambda: next(contexts))
    monkeypatch.setattr(reuse, "_read_trust", lambda *_a: next(trust))
    with evidence() as owner:
        @contextmanager
        def administrative():
            yield owner
        monkeypatch.setattr(ledger, "administrative_evidence_v1", administrative)
        if race:
            with pytest.raises(reuse.ReuseError, match="frontier changed"):
                reuse.import_evidence_v1(reuse.encode_bundle_v1(signed, artifacts))
            assert owner.frontier.operational_import is None
        else:
            result = reuse.import_evidence_v1(reuse.encode_bundle_v1(signed, artifacts))
            assert result["destination"] == destination
            assert owner.frontier.operational_import == result["import_id"]


def test_conditions_policy_excludes_secrets_but_changes_with_model(monkeypatch):
    import config
    import llm_router
    spec = dict(model="inert-model", temperature=0, api_key="secret", base_url="local-alias", id_slot=1)
    monkeypatch.setattr(llm_router, "resolved_tier_spec", lambda *_a, **_kw: spec)
    monkeypatch.setattr(config, "INSTANCE_LANG", "it")
    first = reuse._service_conditions_v1()
    spec.update(api_key="other-secret", base_url="other-alias", id_slot=2)
    assert reuse._service_conditions_v1() == first
    spec["model"] = "different-model"
    assert reuse._service_conditions_v1() != first


@pytest.mark.parametrize("race", [None, "pid", "environment"])
def test_conditions_use_current_unprivileged_service_and_refuse_races(monkeypatch, race):
    import install.birth_lifecycle_migration as migration
    account = SimpleNamespace(record=SimpleNamespace(name="inert-service", uid=555, gid=556, home="/inert-home"),
                              supplementary_gids=(557,), assert_unchanged=lambda _current: None)
    pids = iter([123, 123, 124 if race == "pid" else 123])
    live = dict(METNOS_LLM_TIERS_CONFIG="/service-config", METNOS_LLM_SEED="314", UNRELATED_SECRET="hidden")
    environments = iter([live, dict(live, METNOS_LLM_SEED="changed") if race == "environment" else live])
    monkeypatch.setattr(migration, "resolve_posix_account_snapshot_v1", lambda _name: account)
    monkeypatch.setattr(migration, "_service_main_pid", lambda: next(pids))
    monkeypatch.setattr(migration, "_service_environment", lambda *_a, **kw: next(environments) if kw == {"conditions": True} else pytest.fail("historic conditions"))
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", "/root-must-not-leak")
    def child(argv, **kwargs):
        assert argv[1:3] == ["-I", "-B"]
        assert kwargs["user"] == 555 and kwargs["group"] == 556 and kwargs["extra_groups"] == (557,)
        assert kwargs["env"]["METNOS_LLM_TIERS_CONFIG"] == "/service-config"
        assert kwargs["env"]["METNOS_LLM_SEED"] == "314"
        assert "UNRELATED_SECRET" not in kwargs["env"]
        assert json.loads(kwargs["input"]) == {"operation": "conditions"}
        return SimpleNamespace(returncode=0, stdout=reuse.canonical({"service_policy": digest("policy")}))
    monkeypatch.setattr(migration.subprocess, "run", child)
    if race:
        with pytest.raises(migration.LifecycleCutoverError, match="service changed"):
            migration._in_service_child("conditions")
    else:
        assert migration._in_service_child("conditions") == {"service_policy": digest("policy")}
