from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from i18n_materializer import InventoryItem, materialize, sha256_text
from i18n_pipeline import (
    CandidateValidationError,
    _translate_item,
    _validate_common,
    promote_candidates,
    publish_versioned_contract_candidates,
    reconcile_published_contract_registry,
    review_semantics,
    translate_pending,
)
from i18n_registry import CandidateConflict, LocalizationRegistry
from manifest_rules import DESC_MAX
from contract_store import (
    ContractRetirement,
    LocalizationPatch,
    current_manifest,
    publish_localization,
)
from test_i18n_materializer import _fixture, _versioned_fixture


def _translator(text: str, _source: str, target: str, _context: str) -> str:
    replacements = {
        "Read a file": "Lees een bestand",
        "File path": "Bestandspad",
        "Hello": "Hallo",
        "Guide": "Gids",
        "Rule": "Regel",
        "run": "uitvoeren",
    }
    out = text
    for source, translated in replacements.items():
        out = out.replace(source, translated)
    return out.replace("lang: en", f"lang: {target}")


def _logical_hash(text: str) -> str:
    return "sha256:" + sha256_text(text)


def _localization_patch(
    snapshot,
    *,
    selector: str,
    candidate: str,
    source_language: str = "en",
    target_language: str = "nl",
) -> LocalizationPatch:
    descriptions = snapshot.parsed
    for component in selector.split("."):
        descriptions = descriptions[component]
    previous = descriptions.get(target_language)
    return LocalizationPatch(
        selector=selector,
        source_hash=_logical_hash(descriptions[source_language]),
        previous_target_hash=(
            _logical_hash(previous) if isinstance(previous, str) else None
        ),
        candidate_text=candidate,
        candidate_hash=_logical_hash(candidate),
    )


def _versioned_workflow(tmp_path: Path, target: str = "nl"):
    (
        paths,
        ref,
        private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
    ) = _versioned_fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize(
        target,
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
    )
    return (
        paths,
        ref,
        private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
        registry,
    )


def _exclude_contracts_from_legacy_fixture(registry: LocalizationRegistry) -> None:
    """Keep legacy-layout tests focused on non-contract publication.

    Contract publication deliberately has no legacy writer after RM-0007; its
    complete path is exercised by the versioned-store tests below.
    """
    connection = sqlite3.connect(registry.path)
    try:
        connection.execute(
            "DELETE FROM localization_resources WHERE layer='contract'",
        )
        connection.commit()
    finally:
        connection.close()


def test_translate_validate_review_and_promote_all_nonmanual_layers(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    report = translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    assert report.failed == 0
    assert report.translated >= 7
    candidate = paths.prompts / "nl" / "_pending" / "planner/core.j2.candidate"
    assert "{{ value }}" in candidate.read_text()

    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )
    with pytest.raises(CandidateValidationError, match="legacy contract publication"):
        promote_candidates("nl", registry=registry, paths=paths)
    _exclude_contracts_from_legacy_fixture(registry)
    promoted = promote_candidates("nl", registry=registry, paths=paths)
    assert promoted.errors == {}
    assert (paths.prompts / "nl" / "planner/core.j2").is_file()
    assert (paths.docs / "nl" / "guide.html").read_text() == "<h1>Gids</h1>"

    manifest = (tmp_path / "executors/sample/manifest.toml").read_text()
    assert 'nl = "SCOPO: Lees een bestand.' not in manifest
    assert 'schema_inline="{ok: bool}"' in manifest
    conn = sqlite3.connect(paths.detection_db)
    mapping = json.loads(conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept='action.run' AND lang='nl'"
    ).fetchone()[0])
    assert mapping == ["uitvoeren"]
    assert conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept='confirm.yes' AND lang='nl'"
    ).fetchone()[0] is None
    conn.close()


def test_placeholder_loss_fails_and_never_creates_live_prompt(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("sv", registry=registry, paths=paths)

    def broken(text: str, *_args: str) -> str:
        return text.replace("{{ value }}", "värde")

    report = translate_pending(
        "sv", registry=registry, paths=paths, translator=broken,
    )
    assert "prompt:planner/core.j2" in report.errors
    assert not (paths.prompts / "sv" / "planner/core.j2").exists()


def test_common_validation_normalizes_only_jinja_placeholder_padding():
    _validate_common(
        "Use {{ value }} twice: {{ value }}",
        "Gebruik {{value}} tweemaal: {{value}}",
    )

    with pytest.raises(CandidateValidationError, match="jinja invariants changed"):
        _validate_common(
            "Use {{ value | trim }}",
            "Gebruik {{value|trim}}",
        )
    with pytest.raises(CandidateValidationError, match="jinja invariants changed"):
        _validate_common(
            "Use {{ value }} twice: {{ value }}",
            "Gebruik {{value}}",
        )


def test_contract_translation_exposes_structured_lint_findings():
    source = (
        'SCOPO: Read a file. PATTERN: sample(path="/tmp/example"). '
        "NON: other operations. OUT: {ok}."
    )
    translated = (
        'SCOPO: Lees een bestand. PATTERN: sample(source="/tmp/example"). '
        "NON: andere bewerkingen. OUT: {ok}."
    )
    item = InventoryItem(
        resource_id="contract:sample:description",
        layer="contract",
        source_lang="en",
        source_hash=sha256_text(source),
        source_text=source,
        metadata={"selector": "description"},
    )

    with pytest.raises(CandidateValidationError) as caught:
        _translate_item(
            item,
            "nl",
            lambda _text, _source, _target, _context: translated,
        )

    assert str(caught.value) == "contract translation invariants changed: pattern_atoms"
    assert tuple(
        (finding.check, finding.scope, finding.languages)
        for finding in caught.value.findings
    ) == (("pattern_atoms", "parity", ("en", "nl")),)


def test_contract_warning_is_reported_and_does_not_block_publication(
    tmp_path: Path,
):
    (
        paths,
        ref,
        private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)

    def verbose_translation(
        text: str,
        source: str,
        target: str,
        context: str,
    ) -> str:
        translated = _translator(text, source, target, context)
        if context == "contract:read_files:description":
            extra = max(0, DESC_MAX + 1 - len(translated))
            padding = ("uitvoerige toelichting " * (extra // 22 + 1))[:extra]
            translated = translated.replace(
                "SCOPO: ",
                f"SCOPO: {padding} ",
                1,
            )
        return translated

    translated = translate_pending(
        "nl",
        registry=registry,
        paths=paths,
        translator=verbose_translation,
        contract_snapshot_provider=snapshot_provider,
    )

    resource = "contract:read_files:description"
    assert translated.failed == 0
    description_warning = next(
        finding for finding in translated.warnings[resource]
        if finding.check == "description_length"
    )
    assert description_warning.severity == "warn"
    assert description_warning.scope == "local"
    assert description_warning.resource == "description"
    assert description_warning.languages == ("nl",)
    assert description_warning.evidence["length"] > description_warning.evidence["limit"]

    review_semantics(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )
    published = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=partial(
            publish_localization,
            private_key=private_key,
            trusted_publics=trusted,
            store_root=store,
        ),
    )
    assert published.errors == {}
    assert current_manifest(
        ref,
        trusted_publics=trusted,
        store_root=store,
    ).generation_id != initial.current_generation_id


def test_contract_lint_failure_never_creates_candidate_artifact(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)

    def broken(text: str, _source: str, _target: str, context: str) -> str:
        if context == "contract:sample:description":
            return text.replace("sample(path=", "sample(source=")
        return text

    report = translate_pending(
        "nl", registry=registry, paths=paths, translator=broken,
    )

    resource = "contract:sample:description"
    record = next(row for row in registry.resources("nl") if row.resource_id == resource)
    assert "pattern_atoms" in report.errors[resource]
    assert record.status == "failed"
    assert record.artifact_path is None


def test_prompt_loader_does_not_consume_pending_candidate(tmp_path: Path, monkeypatch):
    import prompt_loader
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "sample.j2").write_text("fallback", encoding="utf-8")
    (tmp_path / "nl" / "_pending").mkdir(parents=True)
    (tmp_path / "nl" / "_pending" / "sample.j2.candidate").write_text("candidate", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "_BASE", tmp_path)
    prompt_loader._envs.clear()
    assert prompt_loader.get("sample", "nl") == "fallback"


def test_legacy_contract_promotion_is_rejected_without_touching_source(tmp_path: Path):
    paths = _fixture(tmp_path)
    manifest = tmp_path / "executors/sample/manifest.toml"
    signature = manifest.with_name("manifest.toml.sig")
    before = manifest.read_bytes()
    signature.write_bytes(b"original-signature")
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )

    with pytest.raises(CandidateValidationError, match="legacy contract publication"):
        promote_candidates("nl", registry=registry, paths=paths)
    assert manifest.read_bytes() == before
    assert signature.read_bytes() == b"original-signature"
    assert all(
        row.status == "translated"
        for row in registry.resources("nl") if row.layer == "contract"
    )


def test_semantic_review_model_calls_are_bounded(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    calls: list[str] = []
    reviewed = review_semantics(
        "nl", registry=registry, paths=paths, limit=1,
        judge=lambda _source, _target, resource: not calls.append(resource),
    )
    assert len(calls) == 1
    assert sum(reviewed.values()) == 1


def test_versioned_artifact_and_isolated_publication_are_bound_and_dormant(
    tmp_path: Path,
):
    (
        paths,
        ref,
        private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)
    authoring = {
        name: (ref.manifest_dir / name).read_bytes()
        for name in (
            "manifest.toml",
            "manifest.toml.sig",
            "manifest.lang_state.json",
        )
    }
    translated = translate_pending(
        "nl",
        registry=registry,
        paths=paths,
        translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    assert translated.failed == 0
    contract_records = [
        row for row in registry.resources("nl") if row.layer == "contract"
    ]
    payload = json.loads(Path(contract_records[0].artifact_path).read_text())
    assert set(payload) == {
        "schema", "resource_id", "layer", "contract_id", "selector",
        "expected_generation", "source_lang", "target_lang", "source_hash",
        "previous_target_hash", "candidate_hash", "translation",
    }
    assert payload["schema"] == "metnos.localization-candidate/2"
    assert payload["expected_generation"] == initial.current_generation_id
    assert payload["contract_id"] == str(ref.contract_id)
    assert payload["previous_target_hash"] is None
    assert payload["candidate_hash"] == "sha256:" + sha256_text(payload["translation"])
    assert payload["candidate_hash"].removeprefix("sha256:") != contract_records[0].translation_hash

    review_semantics(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda source, target, resource: bool(source and target and resource),
    )
    outside = tmp_path / "must-not-be-written.toml"
    conn = sqlite3.connect(registry.path)
    rows = conn.execute(
        "SELECT id,metadata_json FROM localization_resources WHERE layer='contract'"
    ).fetchall()
    for row_id, encoded in rows:
        metadata = json.loads(encoded)
        metadata["manifest_path"] = str(outside)
        conn.execute(
            "UPDATE localization_resources SET metadata_json=? WHERE id=?",
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")), row_id),
        )
    conn.commit()
    conn.close()

    with pytest.raises(CandidateValidationError, match="explicit M3 publisher"):
        promote_candidates("nl", registry=registry, paths=paths)

    invalid = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=lambda _ref, **_kwargs: None,
    )
    assert invalid.published_contracts == 0
    assert invalid.errors

    results = []
    real_publisher = partial(
        publish_localization,
        private_key=private_key,
        trusted_publics=trusted,
        store_root=store,
    )

    def recording_publisher(current_ref, **kwargs):
        result = real_publisher(current_ref, **kwargs)
        results.append(result)
        return result

    first = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=recording_publisher,
    )
    assert first.errors == {}
    assert first.published_contracts == 1
    assert first.published_resources == 2
    assert results[-1].repeated is False
    live = current_manifest(ref, trusted_publics=trusted, store_root=store)
    assert live.generation_id != initial.current_generation_id
    assert live.parsed["description"]["nl"].startswith("SCOPO: Lees")
    assert all(
        row.status == "translated" and row.quality == "reviewed"
        for row in registry.resources("nl")
        if row.layer == "contract"
    )
    assert not outside.exists()
    assert authoring == {
        name: (ref.manifest_dir / name).read_bytes()
        for name in authoring
    }

    repeated = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=recording_publisher,
    )
    assert repeated.errors == {}
    assert repeated.published_contracts == 1
    assert results[-1].repeated is True

    published_snapshot = current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    )
    reconciled = reconcile_published_contract_registry(
        published_snapshot,
        registry=registry,
    )
    contract_rows = [
        row for row in registry.resources("nl") if row.layer == "contract"
    ]
    assert len(reconciled) == len(contract_rows) == 2
    assert all(row.status == "admitted" for row in contract_rows)
    assert all(row.basis_id == live.generation_id for row in contract_rows)
    assert all(row.contract_id == str(ref.contract_id) for row in contract_rows)

    retirement = ContractRetirement(
        contract_id=ref.contract_id,
        retirement_id="sha256:" + "f" * 64,
        previous_generation_id=published_snapshot.generation_id,
        actor="test operator",
        reason="uninstall",
        payload_bytes=b"retirement",
        signature_bytes=b"signature",
        signature_hash="sha256:" + "e" * 64,
        signed_by="test",
    )
    assert reconcile_published_contract_registry(
        retirement, registry=registry,
    ) == ()
    assert not any(
        row.layer == "contract" for row in registry.resources("nl")
    )

    # The callback re-admits a contract only when contract_store supplies a
    # manifest revision again (the explicit reactivation boundary).
    restored = reconcile_published_contract_registry(
        published_snapshot, registry=registry,
    )
    assert len(restored) == 2
    assert all(row.status == "admitted" for row in restored)


def test_versioned_translation_and_review_close_basis_races(
    tmp_path: Path,
    monkeypatch,
):
    (
        paths,
        _ref,
        _private_key,
        _trusted,
        _store,
        _initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)
    snapshot = snapshot_provider(_ref)
    changed_provider = lambda _current_ref: replace(
        snapshot,
        generation_id="sha256:" + "b" * 64,
    )
    original_claim = registry.claim
    raced = False

    def racing_claim(resource_id, target_lang):
        nonlocal raced
        if not raced and resource_id.startswith("contract:"):
            raced = True
            materialize(
                "nl",
                registry=registry,
                paths=paths,
                contract_snapshot_provider=changed_provider,
            )
        return original_claim(resource_id, target_lang)

    monkeypatch.setattr(registry, "claim", racing_claim)
    report = translate_pending(
        "nl",
        registry=registry,
        paths=paths,
        translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    assert any("lease changed" in error for error in report.errors.values())
    assert all(
        row.artifact_path is None and row.basis_id == "sha256:" + "b" * 64
        for row in registry.resources("nl")
        if row.layer == "contract"
    )

    # Start again from a clean registry to place the race after the judgment.
    second = LocalizationRegistry(tmp_path / "review-registry.sqlite")
    materialize(
        "nl",
        registry=second,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
    )
    translate_pending(
        "nl",
        registry=second,
        paths=paths,
        translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    changed = False

    def racing_judge(_source, _target, _resource):
        nonlocal changed
        if not changed:
            changed = True
            materialize(
                "nl",
                registry=second,
                paths=paths,
                contract_snapshot_provider=changed_provider,
            )
        return True

    with pytest.raises(CandidateConflict, match="stale"):
        review_semantics(
            "nl",
            registry=second,
            paths=paths,
            contract_snapshot_provider=snapshot_provider,
            judge=racing_judge,
        )
    assert all(
        row.quality is None and row.basis_id == "sha256:" + "b" * 64
        for row in second.resources("nl")
        if row.layer == "contract"
    )


def test_stale_contract_candidates_do_not_move_the_current_pointer(
    tmp_path: Path,
):
    (
        paths,
        ref,
        private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)
    translate_pending(
        "nl",
        registry=registry,
        paths=paths,
        translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    review_semantics(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    winner_text = (
        'SCOPO: Actuele versie. PATTERN: sample(path="/tmp/example"). '
        "NON: andere bewerkingen. OUT: ok=true."
    )
    winner_argument = "Actueel bestandspad { value }"
    winner = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="en",
        target_language="nl",
        patches=(
            _localization_patch(
                base,
                selector="description",
                candidate=winner_text,
            ),
            _localization_patch(
                base,
                selector="args.properties.path.description",
                candidate=winner_argument,
            ),
        ),
        private_key=private_key,
        trusted_publics=trusted,
        store_root=store,
    )

    stale = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=partial(
            publish_localization,
            private_key=private_key,
            trusted_publics=trusted,
            store_root=store,
        ),
    )

    assert stale.published_contracts == 0
    assert any("commit_conflict" in error for error in stale.errors.values())
    assert current_manifest(
        ref,
        trusted_publics=trusted,
        store_root=store,
    ).generation_id == winner.current_generation_id


def test_failed_contract_signature_does_not_move_the_current_pointer(
    tmp_path: Path,
):
    (
        paths,
        ref,
        _private_key,
        trusted,
        store,
        initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)
    translate_pending(
        "nl",
        registry=registry,
        paths=paths,
        translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    review_semantics(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )

    rejected = publish_versioned_contract_candidates(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
        publisher=partial(
            publish_localization,
            private_key=Ed25519PrivateKey.generate(),
            trusted_publics=trusted,
            store_root=store,
        ),
    )

    assert rejected.published_contracts == 0
    assert any("signature_invalid" in error for error in rejected.errors.values())
    assert current_manifest(
        ref,
        trusted_publics=trusted,
        store_root=store,
    ).generation_id == initial.current_generation_id


def test_versioned_publisher_allows_existing_language_subset_but_not_new_gap(
    tmp_path: Path,
):
    (
        paths,
        ref,
        private_key,
        trusted,
        store,
        _initial,
        snapshot_provider,
        registry,
    ) = _versioned_workflow(tmp_path)
    translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
        contract_snapshot_provider=snapshot_provider,
    )
    review_semantics(
        "nl", registry=registry, paths=paths,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )
    publisher = partial(
        publish_localization,
        private_key=private_key,
        trusted_publics=trusted,
        store_root=store,
    )
    assert publish_versioned_contract_candidates(
        "nl", registry=registry, paths=paths,
        contract_snapshot_provider=snapshot_provider, publisher=publisher,
    ).errors == {}

    materialize(
        "nl", registry=registry, paths=paths,
        contract_snapshot_provider=snapshot_provider,
    )

    def updated(text, source, target, context):
        translated = _translator(text, source, target, context)
        return translated + " Bijgewerkt."

    translate_pending(
        "nl", registry=registry, paths=paths, translator=updated, limit=1,
        contract_snapshot_provider=snapshot_provider,
    )
    review_semantics(
        "nl", registry=registry, paths=paths, limit=1,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )
    partial_report = publish_versioned_contract_candidates(
        "nl", registry=registry, paths=paths,
        contract_snapshot_provider=snapshot_provider, publisher=publisher,
    )
    assert partial_report.errors == {}
    assert partial_report.published_resources == 1

    # A fresh language with only one ready surface reaches the store, whose
    # general coverage policy rejects it without changing current.
    gap_registry = LocalizationRegistry(tmp_path / "gap-registry.sqlite")
    materialize(
        "de", registry=gap_registry, paths=paths,
        contract_snapshot_provider=snapshot_provider,
    )
    before = current_manifest(ref, trusted_publics=trusted, store_root=store).generation_id
    translate_pending(
        "de", registry=gap_registry, paths=paths,
        translator=lambda text, *_args: text, limit=1,
        contract_snapshot_provider=snapshot_provider,
    )
    review_semantics(
        "de", registry=gap_registry, paths=paths, limit=1,
        contract_snapshot_provider=snapshot_provider,
        judge=lambda *_args: True,
    )
    gap = publish_versioned_contract_candidates(
        "de", registry=gap_registry, paths=paths,
        contract_snapshot_provider=snapshot_provider, publisher=publisher,
    )
    assert gap.published_contracts == 0
    assert gap.errors
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == before
