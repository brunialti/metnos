"""Unit + integration tests for the safety stack and admin/sudoer chain.

Covers:
  - safety.canonicalize: argv → signature for representative cases.
  - safety.storage:      CRUD + idempotent seed bootstrap + source='user'
                          preservation.
  - safety.secret_slot:  fill / consume / zero invariants.
  - verb_unique loader:  five invariants of ADR 0069 (vocab collision,
                          unauthorised caller, double-registration).
  - system.admin:   four-act flow with mocked LLM (gate, translated,
                          unknown, blacklist hit, whitelist hit, ask user).
  - system.sudoer:  re-validation at fire time + sanity-check
                          additivity + secret-slot lifecycle.

Run with `python3 -m pytest runtime/tests/test_safety_admin_sudoer.py -v`.
The tests use a temporary safety DB to avoid contaminating the user state.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sure runtime is on the path
_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Use a per-test SQLite file for the safety store."""
    db_path = tmp_path / "safety.db"
    monkeypatch.setenv("SAFETY_DB_PATH", str(db_path))
    # Re-import storage with new env var.
    import importlib
    import safety.storage as storage_mod
    importlib.reload(storage_mod)
    return db_path


@pytest.fixture
def seeded_db(temp_db):
    """Apply the v1 seed before each test that needs it."""
    import importlib
    import safety.seed_bootstrap as sb
    importlib.reload(sb)
    res = sb.bootstrap_safety_seed()
    assert res.upgraded
    return temp_db


# ── canonicalize tests ────────────────────────────────────────────────

class TestCanonicalize:
    def test_basic_ls(self):
        from safety.canonicalize import compute_signature
        sig = compute_signature(["ls", "-la", "/home/roberto/Documents"])
        assert str(sig) == "ls:al:fs:user"

    def test_rm_root_is_forbidden_signature(self):
        from safety.canonicalize import compute_signature
        sig = compute_signature(["rm", "-rf", "/"])
        assert str(sig) == "rm:fr:fs:root"

    def test_sudo_is_stripped(self):
        from safety.canonicalize import compute_signature, has_sudo_wrapper
        argv = ["sudo", "systemctl", "restart", "nginx"]
        sig = compute_signature(argv)
        assert sig.binary == "systemctl"
        assert sig.subcommand_or_flag == "restart"
        assert sig.target_kind == "unit"
        assert has_sudo_wrapper(argv) is True

    def test_dd_block_device_via_key_value(self):
        from safety.canonicalize import compute_signature
        sig = compute_signature(["dd", "if=/dev/zero", "of=/dev/sda"])
        assert sig.target_kind == "block_device"

    def test_journalctl_unit_hint(self):
        from safety.canonicalize import compute_signature
        sig = compute_signature(["journalctl", "-u", "metnos.service"])
        assert sig.binary == "journalctl"
        assert sig.subcommand_or_flag == "u"
        assert sig.target_kind == "unit"

    def test_apt_install_pkg(self):
        from safety.canonicalize import compute_signature
        sig = compute_signature(["apt", "install", "-y", "htop"])
        assert sig.target_kind == "pkg"

    def test_signature_match_wildcard(self):
        from safety.canonicalize import compute_signature, signature_matches
        sig = compute_signature(["systemctl", "restart", "nginx.service"])
        assert signature_matches(sig, "systemctl:restart:*")
        assert signature_matches(sig, "systemctl:*:*")
        assert not signature_matches(sig, "systemctl:status:*")
        assert not signature_matches(sig, "apt:install:*")


# ── storage + bootstrap tests ─────────────────────────────────────────

class TestStorage:
    def test_seed_bootstrap_is_idempotent(self, temp_db):
        from safety.seed_bootstrap import bootstrap_safety_seed
        r1 = bootstrap_safety_seed()
        r2 = bootstrap_safety_seed()
        assert r1.upgraded is True
        assert r2.upgraded is False
        assert r1.applied > 0
        assert r2.applied == 0

    def test_user_curation_preserved_across_bootstrap(self, temp_db):
        from safety.seed_bootstrap import bootstrap_safety_seed
        from safety.storage import SafetyStore
        bootstrap_safety_seed()  # v1
        # User overrides one of the seed entries
        store = SafetyStore()
        store.upsert_user(
            "ls:*:fs:user", "blacklist",
            severity="dangerous", reason="user disabled ls listing",
        )
        store.close()
        # Imagine a v2 seed: simulate by lowering safety_meta and re-applying
        # the same v1 — the entry must NOT be overwritten (source='user').
        store2 = SafetyStore()
        store2.conn.execute("DELETE FROM safety_meta")
        store2.close()
        r = bootstrap_safety_seed()
        assert "ls:*:fs:user" in r.skipped_signatures
        # And the row is still 'user' / 'blacklist'
        store3 = SafetyStore()
        row = store3.find_by_signature("ls:*:fs:user")
        assert row.source == "user"
        assert row.kind == "blacklist"
        store3.close()

    def test_record_use_increments(self, temp_db):
        from safety.storage import SafetyStore
        store = SafetyStore()
        store.upsert_user(
            "demo:*:*", "graylist",
            severity="reversible", reason="test",
        )
        u1 = store.record_use("demo:*:*")
        u2 = store.record_use("demo:*:*")
        assert u1 == 1 and u2 == 2
        store.close()

    def test_promotion_candidates(self, temp_db):
        from safety.storage import SafetyStore
        store = SafetyStore()
        store.upsert_user(
            "candidate:*:*", "graylist",
            severity="reversible", reason="test",
        )
        for _ in range(6):
            store.record_use("candidate:*:*")
        cands = store.find_promotion_candidates(min_uses=5)
        assert any(c.signature == "candidate:*:*" for c in cands)
        store.close()


# ── secret_slot tests ─────────────────────────────────────────────────

class TestSecretSlot:
    def test_fill_then_consume_zeros_buffer(self):
        from safety.secret_slot import SecretSlot
        slot = SecretSlot()
        slot.fill(b"hunter2")
        assert slot.is_filled and not slot.is_consumed
        with slot.consume() as secret:
            assert secret == b"hunter2"
        assert slot.is_consumed
        assert not slot.is_filled

    def test_double_fill_raises(self):
        from safety.secret_slot import SecretSlot, SecretSlotError
        slot = SecretSlot()
        slot.fill(b"a")
        with pytest.raises(SecretSlotError):
            slot.fill(b"b")

    def test_double_consume_raises(self):
        from safety.secret_slot import SecretSlot, SecretSlotError
        slot = SecretSlot()
        slot.fill(b"a")
        with slot.consume() as _:
            pass
        with pytest.raises(SecretSlotError):
            with slot.consume() as _:
                pass

    def test_consume_without_fill_raises(self):
        from safety.secret_slot import SecretSlot, SecretSlotError
        slot = SecretSlot()
        with pytest.raises(SecretSlotError):
            with slot.consume() as _:
                pass


# ── verb_unique loader tests (five invariants) ────────────────────────

class TestVerbUniqueLoader:
    def setup_method(self, _):
        # Each test starts with a clean registry.
        from loader import VERB_UNIQUE_REGISTRY
        VERB_UNIQUE_REGISTRY.clear()

    def test_register_admin_and_sudoer(self):
        from loader import boot_register_verb_unique_builtins, VERB_UNIQUE_REGISTRY
        verbs = boot_register_verb_unique_builtins()
        assert "admin" in verbs
        assert "sudoer" in verbs
        assert VERB_UNIQUE_REGISTRY["admin"]["module"].VERB == "admin"

    def test_unauthorised_caller_is_refused(self, temp_db):
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique
        boot_register_verb_unique_builtins()
        mock_llm = lambda _p: '{"kind": "translated", "argv": ["ls"]}'
        with pytest.raises(PermissionError):
            invoke_verb_unique(
                "admin", caller="evil.module",
                user_text="ciao", llm_call=mock_llm,
            )

    def test_vocab_collision_is_refused(self):
        from loader import register_verb_unique_builtin, VerbUniqueViolation

        class Bad:
            __name__ = "bad"
            NOT_IN_VOCAB = True
            EXPOSE_TO_PLANNER = False
            AUTHORISED_CALLERS = ("x",)
            VERB = "find"  # in vocab.ACTIONS

        with pytest.raises(VerbUniqueViolation):
            register_verb_unique_builtin(Bad)

    def test_missing_attribute_is_refused(self):
        from loader import register_verb_unique_builtin, VerbUniqueViolation

        class Missing:
            __name__ = "missing"
            NOT_IN_VOCAB = True
            EXPOSE_TO_PLANNER = False
            # AUTHORISED_CALLERS missing
            VERB = "alpha"

        with pytest.raises(VerbUniqueViolation):
            register_verb_unique_builtin(Missing)

    def test_double_registration_with_different_module_refused(self):
        from loader import register_verb_unique_builtin, VerbUniqueViolation

        class A:
            __name__ = "a"
            NOT_IN_VOCAB = True
            EXPOSE_TO_PLANNER = False
            AUTHORISED_CALLERS = ("x",)
            VERB = "alpha_unique_test"

        class B:
            __name__ = "b"
            NOT_IN_VOCAB = True
            EXPOSE_TO_PLANNER = False
            AUTHORISED_CALLERS = ("x",)
            VERB = "alpha_unique_test"

        register_verb_unique_builtin(A)
        with pytest.raises(VerbUniqueViolation):
            register_verb_unique_builtin(B)


# ── admin four-act flow tests ────────────────────────────────────────

class TestAdminFlow:
    def test_pre1_gate_rejects_literal_shell(self, seeded_db):
        from system.admin import decide
        # Various literal-shell shapes
        for text in [
            "sudo systemctl restart nginx",
            "rm -rf /tmp/foo",
            "ls /etc; cat /etc/passwd",
            "df -h && du -sh",
            "echo $(whoami)",
        ]:
            d = decide(text, llm_call=lambda _: '{"kind":"translated","argv":["true"]}')
            assert d.kind == "reject"
            assert "non accetto comandi diretti" in d.reason.lower()

    def test_blacklist_hit_rejects(self, seeded_db):
        from system.admin import decide
        # The seed has rm:fr:fs:root in blacklist as forbidden.
        mock_llm = lambda _p: '{"kind":"translated","argv":["rm","-rf","/"]}'
        d = decide("svuota la radice", llm_call=mock_llm)
        assert d.kind == "reject"
        # Either forbidden (raw argv check) or blacklist hit triggered.
        assert d.audit.get("safety") in ("forbidden_hit", "blacklist_hit")

    def test_whitelist_hit_silent_execute(self, seeded_db):
        from system.admin import decide
        mock_llm = lambda _p: '{"kind":"translated","argv":["systemctl","status","nginx"]}'
        d = decide("vedi se nginx gira", llm_call=mock_llm)
        assert d.kind == "execute_silent"
        assert d.signature == "systemctl:status:unit"

    def test_unknown_signature_asks_user(self, seeded_db):
        from system.admin import decide
        # Pick a binary not in seed (e.g. cowsay)
        mock_llm = lambda _p: '{"kind":"translated","argv":["cowsay","hello"]}'
        d = decide("fammi un cowsay", llm_call=mock_llm)
        assert d.kind == "ask_user"
        assert d.card_payload is not None
        assert d.card_payload["argv_rendered"].startswith("cowsay")

    def test_user_approves_unknown_creates_graylist(self, seeded_db):
        from system.admin import decide, apply_user_decision
        from safety.storage import SafetyStore
        mock_llm = lambda _p: '{"kind":"translated","argv":["cowsay","hi"]}'
        d = decide("cowsay", llm_call=mock_llm)
        assert d.kind == "ask_user"
        d2 = apply_user_decision(decision=d, user_choice="approve")
        assert d2.kind == "execute_silent"
        # Verify the graylist row was created
        store = SafetyStore()
        row = store.find_by_signature(d.signature)
        assert row is not None
        assert row.kind == "graylist"
        assert row.source == "user"
        store.close()

    def test_user_blocks_forever_creates_blacklist(self, seeded_db):
        from system.admin import decide, apply_user_decision
        from safety.storage import SafetyStore
        mock_llm = lambda _p: '{"kind":"translated","argv":["fortune"]}'
        d = decide("fortune", llm_call=mock_llm)
        assert d.kind == "ask_user"
        d2 = apply_user_decision(decision=d, user_choice="block_forever")
        assert d2.kind == "reject"
        store = SafetyStore()
        row = store.find_by_signature(d.signature)
        assert row.kind == "blacklist"
        assert row.source == "user"
        store.close()

    def test_user_reject_once_no_side_effect(self, seeded_db):
        from system.admin import decide, apply_user_decision
        from safety.storage import SafetyStore
        mock_llm = lambda _p: '{"kind":"translated","argv":["xeyes"]}'
        d = decide("xeyes", llm_call=mock_llm)
        assert d.kind == "ask_user"
        d2 = apply_user_decision(decision=d, user_choice="reject_once")
        assert d2.kind == "reject"
        # No DB row should have been created
        store = SafetyStore()
        row = store.find_by_signature(d.signature)
        assert row is None
        store.close()

    def test_llm_unknown_kind_rejects(self, seeded_db):
        from system.admin import decide
        mock_llm = lambda _p: '{"kind":"unknown","reason":"ambiguous"}'
        d = decide("non si capisce", llm_call=mock_llm)
        assert d.kind == "reject"

    def test_llm_impossible_kind_rejects(self, seeded_db):
        from system.admin import decide
        mock_llm = lambda _p: '{"kind":"impossible","reason":"ssh interactive"}'
        d = decide("aprimi una sessione ssh", llm_call=mock_llm)
        assert d.kind == "reject"


# ── sudoer execution + re-validation tests ──────────────────────────

class TestSudoerExecution:
    def test_execute_simple_command(self, seeded_db):
        from system.sudoer import execute
        # `true` exits 0; we're testing the spawn + audit path.
        res = execute(argv=["true"], reversibility="reversible")
        assert res.status == "executed"
        assert res.exit_code == 0
        assert res.audit["signature"] == "true:*:*"

    def test_revalidation_blocks_at_fire(self, seeded_db):
        from system.sudoer import execute
        from safety.storage import SafetyStore
        # Add a runtime blacklist entry that didn't exist when admin
        # validated.
        store = SafetyStore()
        store.upsert_user(
            "true:*:*", "blacklist",
            severity="dangerous", reason="banned at runtime",
        )
        store.close()
        res = execute(argv=["true"], reversibility="reversible")
        assert res.status == "blocked_at_fire"
        assert res.notify_user is not None
        assert "regola che lo blocca" in res.notify_user

    def test_forbidden_argv_blocked_at_fire(self, seeded_db):
        from system.sudoer import execute
        res = execute(argv=["rm", "-rf", "/"], reversibility="irreversible")
        assert res.status == "blocked_at_fire"
        assert "Law 1" in res.audit.get("block_reason", "")

    def test_sanity_check_urgent_blocks(self, seeded_db):
        from system.sudoer import execute

        def mock_sanity(prompt, fmt):
            return '{"smell":"urgent_review","reason":"disco al 95%"}'

        res = execute(
            argv=["true"],
            reversibility="irreversible",  # forces sanity invocation
            scheduler_delay_minutes=10,
            sanity_llm_call=mock_sanity,
        )
        assert res.status == "blocked_at_fire"
        assert "disco al 95%" in (res.notify_user or "")

    def test_sanity_check_ok_lets_pass(self, seeded_db):
        from system.sudoer import execute

        def mock_sanity(prompt, fmt):
            return '{"smell":"ok"}'

        res = execute(
            argv=["true"],
            reversibility="irreversible",
            scheduler_delay_minutes=10,
            sanity_llm_call=mock_sanity,
        )
        assert res.status == "executed"
        assert res.exit_code == 0


# ── End-to-end chain admin → sudoer ──────────────────────────────────

class TestAdminToSudoerChain:
    def test_whitelist_silent_chain(self, seeded_db):
        """Whitelist hit: admin returns execute_silent, sudoer runs the cmd."""
        from system.admin import decide
        from system.sudoer import execute
        # `date` is in seed whitelist (date:*:*).
        mock_llm = lambda _p: '{"kind":"translated","argv":["date"]}'
        d = decide("che ora e'?", llm_call=mock_llm)
        assert d.kind == "execute_silent"
        res = execute(argv=d.argv, reversibility=d.reversibility or "unknown")
        assert res.status == "executed"
        assert res.exit_code == 0

    def test_user_approval_then_chain_run(self, seeded_db):
        """Unknown signature: admin asks; user approves; sudoer runs."""
        from system.admin import decide, apply_user_decision
        from system.sudoer import execute
        mock_llm = lambda _p: '{"kind":"translated","argv":["echo","hello"]}'
        d = decide("dimmi ciao", llm_call=mock_llm)
        # echo isn't in seed → ask_user
        if d.kind != "ask_user":
            # echo was added to a list mid-test; skip
            pytest.skip("echo present in seed")
        d2 = apply_user_decision(decision=d, user_choice="approve")
        assert d2.kind == "execute_silent"
        res = execute(argv=d2.argv, reversibility=d2.reversibility or "unknown")
        assert res.status == "executed"
        assert "hello" in res.stdout
