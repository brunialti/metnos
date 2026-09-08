"""Fresh-state and authority tests for terminal legacy inspection."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

import contract_cutover_guard as cutover_guard
import executor_birth_legacy_state_journal as journal
from executor_birth_legacy_state_policy import LegacyStateDispositionV1
from install import executor_birth_legacy_state_inspection as inspection


def _install_seams(
    monkeypatch, *, current_sha256: str, disposition,
    inventory_disposition=LegacyStateDispositionV1.exact_service,
):
    digest = "sha256:" + "a" * 64
    events = []
    request = SimpleNamespace(
        distribution_sha256=digest,
        request_id=digest,
        state_root=PurePosixPath("/var/lib/metnos/legacy"),
    )
    latest = SimpleNamespace(
        state=journal.LegacyStateV1.LEGACY_STATE_READY,
        request_id=digest,
        record_sha256=digest,
        ready_sha256=digest,
        inventory_disposition=inventory_disposition,
    )

    class Chain:
        root_fd = 7

        @staticmethod
        def attest_metadata(_expected):
            events.append("filesystem-attestation")

    @contextmanager
    def roots(_request, *, include_state):
        yield Chain() if include_state else None, Chain(), 8

    monkeypatch.setattr(
        inspection, "_require_maintenance_session_v1",
        lambda _: events.append("maintenance"),
    )
    monkeypatch.setattr(inspection, "require_canonical_legacy_state_request_v1", lambda x: x)
    monkeypatch.setattr(inspection, "build_legacy_state_request_v1", lambda *_: request)
    monkeypatch.setattr(inspection, "_expected_v1", lambda *_: {})
    monkeypatch.setattr(inspection, "_inspection_roots_v1", roots)
    monkeypatch.setattr(inspection, "require_legacy_journal_lock_bound_v1", lambda *_: None)
    monkeypatch.setattr(
        inspection, "LegacyStateJournalStoreV1",
        lambda _fd: SimpleNamespace(inspect_records=lambda: (b"0",) * 4),
    )
    monkeypatch.setattr(inspection.journal, "decode_legacy_state_chain_v1", lambda _: (latest,) * 4)
    monkeypatch.setattr(
        inspection, "observe_legacy_state_v1",
        lambda _request: SimpleNamespace(observation_sha256=current_sha256),
    )
    monkeypatch.setattr(inspection, "classify_legacy_state_v1", lambda *_: disposition)
    return request, latest, events


@pytest.mark.parametrize(
    ("current", "disposition"),
    [
        ("sha256:" + "b" * 64, LegacyStateDispositionV1.exact_service),
        ("sha256:" + "a" * 64, LegacyStateDispositionV1.fresh),
    ],
)
def test_terminal_inspection_rejects_fresh_state_drift(
    monkeypatch, current, disposition,
) -> None:
    request, latest, _events = _install_seams(
        monkeypatch, current_sha256=current, disposition=disposition,
    )
    with pytest.raises(inspection.LegacyStateInspectionError, match="state changed"):
        inspection.inspect_ready_legacy_state_live_v1(
            request, object(), latest.record_sha256, object(),
        )


def test_live_inspection_accepts_matching_exact_service(
    monkeypatch,
) -> None:
    digest = "sha256:" + "a" * 64
    request, latest, events = _install_seams(
        monkeypatch, current_sha256=digest,
        disposition=LegacyStateDispositionV1.exact_service,
    )
    assert inspection.inspect_ready_legacy_state_live_v1(
        request, object(), latest.record_sha256, object(),
    ) is latest
    assert events == [
        "maintenance", "maintenance",
        "filesystem-attestation", "filesystem-attestation",
        "maintenance",
        "filesystem-attestation", "filesystem-attestation",
        "maintenance",
    ]


def test_live_inspection_rejects_fresh_state_after_ready(monkeypatch) -> None:
    digest = "sha256:" + "a" * 64
    request, latest, _events = _install_seams(
        monkeypatch, current_sha256=digest,
        disposition=LegacyStateDispositionV1.fresh,
        inventory_disposition=LegacyStateDispositionV1.fresh,
    )
    with pytest.raises(inspection.LegacyStateInspectionError):
        inspection.inspect_ready_legacy_state_live_v1(
            request, object(), latest.record_sha256, object(),
        )


def test_history_inspection_does_not_reopen_changed_service_state(
    monkeypatch,
) -> None:
    digest = "sha256:" + "a" * 64
    request, latest, events = _install_seams(
        monkeypatch, current_sha256="sha256:" + "b" * 64,
        disposition=LegacyStateDispositionV1.invalid,
    )
    monkeypatch.setattr(
        inspection, "observe_legacy_state_v1",
        lambda _request: pytest.fail("history reopened mutable service state"),
    )
    assert inspection.inspect_terminal_legacy_state_history_v1(
        request, object(), latest.record_sha256, object(),
    ) is latest
    assert events == [
        "maintenance", "maintenance", "filesystem-attestation",
        "maintenance", "filesystem-attestation", "maintenance",
    ]


def test_callable_lookalike_is_rejected_before_inspection_io(monkeypatch) -> None:
    monkeypatch.setattr(
        inspection, "_inspection_roots_v1",
        lambda *_: pytest.fail("invalid maintenance reached filesystem"),
    )
    with pytest.raises(cutover_guard.ContractCutoverGuardError) as denied:
        inspection.inspect_ready_legacy_state_live_v1(
            object(), object(), "sha256:" + "a" * 64, lambda: True,
        )
    assert denied.value.code == "cutover_session_invalid"
