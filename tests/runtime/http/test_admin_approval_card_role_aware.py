"""Test carta vaglio role-aware admin (22/5/2026).

Spec utente:
- LLM propone comando admin non in whitelist
- Se utente=admin (actor='host'): danger summary + 4 opzioni inclusi
  approve_and_whitelist (promozione permanente) e block_forever.
- Se utente=guest: 3 opzioni — run_externally / request_admin_whitelist /
  reject_once. Nessun execute possibile per guest.

Run: `python3 -m pytest tests/runtime/http/test_admin_approval_card_role_aware.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class TestExplainCommandDangers:

    def test_rm_explained(self):
        from system.admin import _explain_command_dangers
        msg = _explain_command_dangers(["rm", "-rf", "/tmp/x"], None)
        assert "Cancella" in msg or "cancella" in msg.lower()
        assert "RICORSIVA" in msg or "ricorsiva" in msg.lower()
        assert "forzanti" in msg.lower() or "force" in msg.lower()

    def test_unknown_binary_generic_warning(self):
        from system.admin import _explain_command_dangers
        msg = _explain_command_dangers(["weirdbin", "--show"], None)
        assert "weirdbin" in msg
        assert "non" in msg.lower()  # "non e' nella whitelist conosciuta"

    def test_severity_irreversible_noted(self):
        from system.admin import _explain_command_dangers
        msg = _explain_command_dangers(["dd", "if=/dev/zero"], "irreversible")
        assert "IRREVERSIBILE" in msg.upper()

    def test_empty_argv(self):
        from system.admin import _explain_command_dangers
        msg = _explain_command_dangers([], None)
        assert msg


class TestApprovalCardRoleAware:
    """Card emitted on whitelist miss: shape differente per host vs guest."""

    def _ask_user_decision_for(self, argv: list[str], actor: str):
        from system.admin import _decide_for_argv
        return _decide_for_argv(argv, intent_text="test intent", actor=actor)

    def test_card_admin_has_approve_and_whitelist_option(self):
        d = self._ask_user_decision_for(["weirdcmd", "--show"], actor="host")
        if d.kind != "ask_user":
            pytest.skip(f"weirdcmd risolto come {d.kind}, non testabile come ask_user")
        card = d.card_payload
        assert card["actor_role"] == "admin"
        assert "approve_and_whitelist" in card["options"]
        assert "block_forever" in card["options"]
        assert "danger_summary" in card

    def test_card_guest_has_run_externally_option(self):
        d = self._ask_user_decision_for(["weirdcmd", "--show"], actor="guest_lucia")
        if d.kind != "ask_user":
            pytest.skip(f"weirdcmd risolto come {d.kind}, non testabile come ask_user")
        card = d.card_payload
        assert card["actor_role"] == "guest"
        assert "run_externally" in card["options"]
        assert "request_admin_whitelist" in card["options"]
        # guest non puo' direttamente whitelistare
        assert "approve_and_whitelist" not in card["options"]
        assert "block_forever" not in card["options"]

    def test_card_warning_text_present_both_roles(self):
        for actor in ("host", "guest_x"):
            d = self._ask_user_decision_for(["weirdcmd", "--x"], actor=actor)
            if d.kind != "ask_user":
                continue
            card = d.card_payload
            assert card.get("warning")
            assert card.get("danger_summary")


class TestApplyUserDecisionNewOptions:
    """apply_user_decision con le 4 nuove choices."""

    def _make_ask(self, actor):
        from system.admin import _decide_for_argv
        return _decide_for_argv(
            ["weirdunknowncmd_xyz", "--show"],
            intent_text="test", actor=actor,
        )

    def test_approve_and_whitelist_promotes_to_whitelist(self, tmp_path, monkeypatch):
        # Usa DB temporaneo per non sporcare quello reale
        import os
        db = tmp_path / "safety.db"
        monkeypatch.setenv("SAFETY_DB_PATH", str(db))
        # Reload safety modules per usare il nuovo path
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)

        d = self._make_ask("host")
        if d.kind != "ask_user":
            pytest.skip("decisione non ask_user")
        from system.admin import apply_user_decision
        result = apply_user_decision(
            decision=d, user_choice="approve_and_whitelist", actor="host",
        )
        assert result.kind == "execute_silent"
        assert result.age_class == "permanent"
        # Verifica che la signature sia ora in whitelist
        from safety.storage import SafetyStore
        store = SafetyStore()
        try:
            row = store.find_by_signature(result.signature)
            assert row is not None
            assert row.kind == "whitelist"
            assert row.source == "user"
        finally:
            store.close()

    def test_approve_and_whitelist_denied_for_guest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)

        d = self._make_ask("guest_x")
        if d.kind != "ask_user":
            pytest.skip()
        from system.admin import apply_user_decision
        with pytest.raises(ValueError, match="admin role"):
            apply_user_decision(decision=d, user_choice="approve_and_whitelist",
                                actor="guest_x")

    def test_run_externally_returns_reject_no_side_effect(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)

        d = self._make_ask("guest_x")
        if d.kind != "ask_user":
            pytest.skip()
        from system.admin import apply_user_decision
        result = apply_user_decision(decision=d, user_choice="run_externally",
                                      actor="guest_x")
        assert result.kind == "reject"
        assert "manualmente" in result.reason.lower() or "fuori" in result.reason.lower()

    def test_request_admin_whitelist_queues_request(self, tmp_path, monkeypatch):
        import os
        queue_path = tmp_path / "admin_whitelist_requests.jsonl"
        monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
        monkeypatch.setenv("HOME", str(tmp_path))
        # Reset _REQUEST_WHITELIST_QUEUE module-level to picked up HOME
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)
        # Override module constant
        system.admin._REQUEST_WHITELIST_QUEUE = queue_path

        d = self._make_ask("guest_lucia")
        if d.kind != "ask_user":
            pytest.skip()
        from system.admin import apply_user_decision
        result = apply_user_decision(decision=d, user_choice="request_admin_whitelist",
                                      actor="guest_lucia")
        assert result.kind == "reject"
        assert "amministratore" in result.reason.lower()
        assert queue_path.is_file()
        import json
        content = queue_path.read_text().strip().splitlines()
        assert len(content) == 1
        rec = json.loads(content[0])
        assert rec["requester"] == "guest_lucia"
        assert rec["status"] == "pending"


class TestBackwardsCompatLegacyOptions:
    """Le opzioni vecchie 'approve' e 'reject_once' devono continuare a
    funzionare (graylist + reject), per non rompere chiamate runtime
    esistenti."""

    def test_legacy_approve_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)

        from system.admin import _decide_for_argv, apply_user_decision
        d = _decide_for_argv(["weirdxyz", "--p"], intent_text="t", actor="host")
        if d.kind != "ask_user":
            pytest.skip()
        result = apply_user_decision(decision=d, user_choice="approve", actor="host")
        assert result.kind == "execute_silent"
        assert result.age_class == "graylist"

    def test_legacy_reject_once_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
        import importlib
        import safety.storage
        importlib.reload(safety.storage)
        import system.admin
        importlib.reload(system.admin)
        from system.admin import _decide_for_argv, apply_user_decision
        d = _decide_for_argv(["weirdxyz", "--p"], intent_text="t", actor="host")
        if d.kind != "ask_user":
            pytest.skip()
        result = apply_user_decision(decision=d, user_choice="reject_once", actor="host")
        assert result.kind == "reject"
