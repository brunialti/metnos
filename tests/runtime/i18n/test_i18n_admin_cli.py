from __future__ import annotations

from types import SimpleNamespace

from admin import i18n_cli
from config import LocalizationRequest
from i18n_activation import GateReport
from i18n_materializer import MaterializationReport
from i18n_pipeline import LiveContractContext, live_contract_context
from i18n_registry import CoverageReport, LocalizationRegistry
from manifest_inventory import ManifestLayout


def test_requested_target_is_noop_without_signed_request(monkeypatch):
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (None, "missing"),
    )
    assert i18n_cli._requested_target() is None
    assert i18n_cli._advance_requested(20) == {"status": "no_target"}


def test_requested_target_tracks_pending_and_active_nonbootstrap(monkeypatch):
    pending = SimpleNamespace(
        requested_lang="nl", instance_lang="en", state="bootstrap_english",
    )
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (pending, None),
    )
    assert i18n_cli._requested_target() == "nl"

    active = SimpleNamespace(
        requested_lang=None, instance_lang="pt-br", state="active",
    )
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (active, None),
    )
    assert i18n_cli._requested_target() == "pt-br"


def test_nightly_wrapper_uses_registry_pipeline_without_language_lists():
    script = (i18n_cli._C.PATH_ROOT / "deploy" / "run_prompts_translator.sh").read_text(
        encoding="utf-8",
    )
    assert "advance-requested" in script
    assert "METNOS_LOCALIZATION_CAP_PER_FIRE" in script
    assert "align-prompts" not in script
    assert "prompts/it" not in script


def test_store_only_read_context_never_loads_private_author_key(
    tmp_path, monkeypatch,
) -> None:
    private_reads: list[str] = []
    monkeypatch.setattr(
        "manifest_inventory.resolve_manifest_layout",
        lambda: ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        "sign.list_trusted_publics",
        lambda: [("public-only", object())],
    )
    monkeypatch.setattr(
        "sign.load_private",
        lambda name: private_reads.append(name) or (_ for _ in ()).throw(
            AssertionError("read-only context requested a private key")
        ),
    )

    context = live_contract_context(
        LocalizationRegistry(tmp_path / "registry.sqlite"),
    )

    assert context.store_only
    assert context.snapshot_provider is not None
    assert context.publisher is None
    assert private_reads == []


def test_request_materialize_and_status_share_read_only_live_context(
    monkeypatch, capsys,
) -> None:
    provider = object()
    context_calls: list[bool] = []
    materialized_with: list[object] = []
    gated_with: list[object] = []

    class FakeRegistry:
        def coverage(self, target):
            return CoverageReport(
                target_lang=target,
                total=0,
                ready=0,
                admitted=0,
                by_status={},
                by_layer={},
                missing=(),
                manual_review=(),
            )

        def checks(self, _target):
            return {}

    monkeypatch.setattr(i18n_cli, "LocalizationRegistry", FakeRegistry)
    monkeypatch.setattr(
        i18n_cli,
        "live_contract_context",
        lambda _registry, publication=False: (
            context_calls.append(publication)
            or LiveContractContext(True, provider, None)
        ),
    )
    monkeypatch.setattr(
        i18n_cli._C,
        "localization_corpus_version",
        lambda: "sha256:" + "1" * 64,
    )
    monkeypatch.setattr(
        i18n_cli._C,
        "write_localization_request",
        lambda **_kwargs: (
            LocalizationRequest(
                instance_lang="en",
                requested_lang="nl",
                state="bootstrap_english",
                requested_at="2026-08-25T00:00:00Z",
                corpus_version="sha256:" + "1" * 64,
            ),
            True,
        ),
    )

    def fake_materialize(target, **kwargs):
        materialized_with.append(kwargs["contract_snapshot_provider"])
        return MaterializationReport(
            source_lang="en",
            target_lang=target,
            resources=0,
            by_layer={},
            message_placeholders=0,
            detection_placeholders=0,
            prompt_state_path="",
        )

    def fake_gate(target, **kwargs):
        gated_with.append(kwargs["contract_snapshot_provider"])
        return GateReport(target, True, 0, 0, (), (), {})

    monkeypatch.setattr(i18n_cli, "materialize", fake_materialize)
    monkeypatch.setattr(i18n_cli, "gate", fake_gate)

    assert i18n_cli.main(["request", "nl"]) == 0
    assert i18n_cli.main(["materialize", "nl"]) == 0
    assert i18n_cli.main(["status", "nl"]) == 0
    capsys.readouterr()

    assert context_calls == [False, False, False]
    assert materialized_with == [provider, provider]
    assert gated_with == [provider]
