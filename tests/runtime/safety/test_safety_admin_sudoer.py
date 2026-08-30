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

Run with `python3 -m pytest tests/runtime/safety/test_safety_admin_sudoer.py -v`.
The tests use a temporary safety DB to avoid contaminating the user state.
"""
from __future__ import annotations

import sys
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

# Make sure runtime is on the path
_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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
        sig = compute_signature(["ls", "-la", "/home/user/Documents"])
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

    @pytest.mark.parametrize(
        "argv",
        [
            ["sudo", "--unknown", "rm", "/tmp/x"],
            ["doas", "PATH=/tmp", "rm", "/tmp/x"],
            ["pkexec", "sudo", "rm", "/tmp/x"],
            ["sudo", "-u"],
            ["/tmp/sudo", "true"],
            ["sudo", "/tmp/systemctl", "status", "nginx"],
            ["env", "PATH=/tmp", "rm", "-rf", "/"],
        ],
    )
    def test_privilege_wrapper_grammar_fails_closed(self, argv):
        from safety.canonicalize import ArgvValidationError, validate_argv
        with pytest.raises(ArgvValidationError):
            validate_argv(argv)

    @pytest.mark.parametrize(
        ("argv", "command"),
        [
            (["sudo", "--", "rm", "/tmp/x"], ("rm", "/tmp/x")),
            (["sudo", "--user", "root", "rm", "/tmp/x"], ("rm", "/tmp/x")),
            (["doas", "-u", "root", "rm", "/tmp/x"], ("rm", "/tmp/x")),
            (["pkexec", "--user=root", "rm", "/tmp/x"], ("rm", "/tmp/x")),
        ],
    )
    def test_privilege_wrapper_closed_forms_resolve_one_target(self, argv, command):
        from safety.canonicalize import validate_argv
        assert validate_argv(argv).command_argv == command

    def test_absolute_paths_are_normalized_in_snapshot_and_signature(self):
        from safety.canonicalize import validate_argv
        validated = validate_argv(["rm", "-rf", "//tmp/dir/../.."])
        assert validated.argv == ("rm", "-rf", "/")
        assert validated.signature.target_kind == "fs:root"

    @pytest.mark.parametrize("token", ["line\nfeed", "bidi\u202etext"])
    def test_control_and_format_characters_are_rejected(self, token):
        from safety.canonicalize import ArgvValidationError, validate_argv
        with pytest.raises(ArgvValidationError) as caught:
            validate_argv(["echo", token])
        assert caught.value.code == "ERR_ARGV_CONTROL"

    def test_display_is_nfkc_bounded_json_and_markup_inert(self):
        from safety.canonicalize import render_argv_for_display, validate_argv
        validated = validate_argv(["echo", "Ａ`value"])
        rendered = render_argv_for_display(validated)
        assert rendered.startswith('["echo","A')
        assert "`" not in rendered
        assert r"\u0060" in rendered
        long_rendered = render_argv_for_display(["x" * 5000])
        assert len(long_rendered) < 256
        assert "sha256:" in long_rendered


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
            assert "comandi diretti" in d.reason.lower()

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
        assert d.card_payload["argv_rendered"].startswith('["cowsay"')

    def test_guest_never_executes_a_whitelisted_admin_command(self, seeded_db):
        from system.admin import _decide_for_argv
        # systemctl:status:unit is seeded whitelist, but a guest receives only
        # its non-executing card options.
        decision = _decide_for_argv(
            ["systemctl", "status", "nginx"],
            intent_text="status", actor="guest_x",
        )
        assert decision.kind == "ask_user"
        assert decision.card_payload["options"] == [
            "run_externally", "request_admin_whitelist", "reject_once",
        ]

    def test_planner_wrapper_unknown_option_is_structurally_rejected(self):
        import system.admin as admin
        result = admin.invoke(
            intent="run as root",
            command_proposed="sudo --preserve-env true",
        )
        assert result["decision"] == "reject"
        assert result["error_class"] == "invalid_args"
        assert result["error_code"] == "ERR_ARGV_WRAPPER_OPTION"

    def test_user_approves_unknown_is_one_shot_without_graylist(self, seeded_db):
        from system.admin import decide, apply_user_decision
        from safety.storage import SafetyStore
        mock_llm = lambda _p: '{"kind":"translated","argv":["cowsay","hi"]}'
        d = decide("cowsay", llm_call=mock_llm)
        assert d.kind == "ask_user"
        d2 = apply_user_decision(decision=d, user_choice="approve")
        assert d2.kind == "execute_silent"
        assert d2.age_class == "one_shot"
        # A one-time approval must not silently authorize future argv sharing
        # the same coarse signature.
        store = SafetyStore()
        row = store.find_by_signature(d.signature)
        assert row is None
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

    def test_target_becoming_symlink_is_blocked_by_immediate_revalidation(
            self, temp_db, tmp_path, monkeypatch):
        import system.admin as admin
        import system.sudoer as sudoer
        target = str(tmp_path / "future")
        real_lstat = admin.os.lstat
        reads = 0

        def changing_lstat(path):
            nonlocal reads
            if str(path) == target:
                reads += 1
                if reads == 1:
                    raise FileNotFoundError(path)
                return type("S", (), {"st_mode": stat.S_IFLNK})()
            return real_lstat(path)

        monkeypatch.setattr(admin.os, "lstat", changing_lstat)
        monkeypatch.setattr(
            sudoer.subprocess, "run",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("subprocess must not run")
            ),
        )
        res = sudoer.execute(argv=["rm", target])
        assert res.status == "blocked_at_fire"
        assert reads == 2

    def test_sudoer_fires_the_exact_validated_argv(self, temp_db, monkeypatch):
        from types import SimpleNamespace
        from safety.canonicalize import validate_argv
        import system.sudoer as sudoer
        validated = validate_argv(["sudo", "-n", "true"])
        seen = []
        monkeypatch.setattr(
            sudoer.subprocess, "run",
            lambda argv, **_kwargs: (
                seen.append(list(argv)) or
                SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            ),
        )
        result = sudoer.execute(validated_argv=validated)
        assert result.ok is True
        assert seen == [["sudo", "-n", "true"]]

    def test_sudoer_rejects_forged_validated_product(self, temp_db, monkeypatch):
        from dataclasses import replace
        from safety.canonicalize import Signature, validate_argv
        import system.sudoer as sudoer
        validated = validate_argv(["true"])
        forged = replace(
            validated,
            signature=Signature("false", "*", "*"),
        )
        monkeypatch.setattr(
            sudoer.subprocess, "run",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("forged validated product must not execute")
            ),
        )
        result = sudoer.execute(validated_argv=forged)
        assert result.ok is False
        assert result.status == "error"
        assert result.audit["error_code"] == "ERR_ARGV_SNAPSHOT_MISMATCH"


class TestAdminConsentCapability:
    @pytest.fixture(autouse=True)
    def _isolated_consent(self, tmp_path, monkeypatch):
        import system.admin as admin
        monkeypatch.setattr(admin._C, "PATH_USER_DATA", tmp_path)

    def test_same_signature_different_argv_does_not_consume_capability(self):
        import system.admin as admin
        from safety.canonicalize import validate_argv
        approved = validate_argv(["echo", "alpha"])
        mutant = validate_argv(["echo", "beta"])
        assert approved.signature == mutant.signature
        token = admin._sign_consent_token(approved, "host")
        assert admin._verify_consent_token(token, mutant, "host") is False
        assert admin._verify_consent_token(token, approved, "host") is True
        assert admin._verify_consent_token(token, approved, "host") is False

    def test_concurrent_replay_has_exactly_one_winner(self):
        import system.admin as admin
        from safety.canonicalize import validate_argv
        approved = validate_argv(["echo", "once"])
        token = admin._sign_consent_token(approved, "host")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _i: admin._verify_consent_token(token, approved, "host"),
                range(8),
            ))
        assert results.count(True) == 1

    def test_guest_cannot_receive_or_consume_admin_capability(self):
        import system.admin as admin
        from safety.canonicalize import validate_argv
        approved = validate_argv(["echo", "guest"])
        with pytest.raises(PermissionError):
            admin._sign_consent_token(approved, "guest_x")
        token = admin._sign_consent_token(approved, "host")
        assert admin._verify_consent_token(token, approved, "guest_x") is False

    def test_consent_key_io_failure_is_a_typed_fail_closed_envelope(
            self, temp_db, monkeypatch):
        import system.admin as admin

        monkeypatch.setattr(admin, "_executor_name_in_argv", lambda _argv: None)
        monkeypatch.setattr(
            admin, "_detect_credentials_placeholder", lambda _argv: (None, {}),
        )

        def ask(argv, **kwargs):
            validated = kwargs["_validated_snapshot"]
            return admin.AdminDecision(
                kind="ask_user", argv=list(validated.argv),
                signature=str(validated.signature),
                card_payload={
                    "type": "approval_card", "actor_role": "admin",
                    "options": list(admin._ADMIN_APPROVAL_OPTIONS),
                },
                validated_argv=validated,
            )

        monkeypatch.setattr(admin, "_decide_for_argv", ask)
        monkeypatch.setattr(
            admin, "_consent_key",
            lambda: (_ for _ in ()).throw(OSError("read-only key store")),
        )
        result = admin._invoke_impl(
            intent="run tool", command_proposed="unknown_consent_tool",
        )
        assert result["ok"] is False
        assert result["approval_required"] is False
        assert result["consent_token"] is None
        assert result["error_class"] == "dependency_unavailable"
        assert result["error_code"] == "ERR_ADMIN_CONSENT_STORE_UNAVAILABLE"

    def test_consent_db_io_failure_during_verification_is_typed_fail_closed(
            self, temp_db, monkeypatch):
        import system.admin as admin
        from safety.canonicalize import validate_argv

        validated = validate_argv(["unknown_consent_tool"])
        token = admin._sign_consent_token(validated, "host")
        monkeypatch.setattr(admin, "_executor_name_in_argv", lambda _argv: None)
        monkeypatch.setattr(
            admin, "_detect_credentials_placeholder", lambda _argv: (None, {}),
        )

        def ask(argv, **kwargs):
            snapshot = kwargs["_validated_snapshot"]
            return admin.AdminDecision(
                kind="ask_user", argv=list(snapshot.argv),
                signature=str(snapshot.signature),
                card_payload={
                    "type": "approval_card", "actor_role": "admin",
                    "options": list(admin._ADMIN_APPROVAL_OPTIONS),
                },
                validated_argv=snapshot,
            )

        monkeypatch.setattr(admin, "_decide_for_argv", ask)
        monkeypatch.setattr(
            admin, "_open_consent_db",
            lambda: (_ for _ in ()).throw(
                admin.sqlite3.OperationalError("database unavailable")
            ),
        )
        result = admin._invoke_impl(
            intent="run tool", command_proposed="unknown_consent_tool",
            actor_consent_token=token,
        )
        assert result["ok"] is False
        assert result["approval_required"] is False
        assert result["consent_token"] is None
        assert result["error_class"] == "dependency_unavailable"
        assert result["error_code"] == "ERR_ADMIN_CONSENT_STORE_UNAVAILABLE"

    def test_guest_approval_card_contains_no_token(self, temp_db):
        import system.admin as admin
        result = admin._invoke_impl(
            intent="run unknown", command_proposed="unknown_guest_xyz",
            actor="guest_x",
        )
        assert result["decision"] == "approval_required"
        assert result["consent_token"] is None

    def test_error_typing_never_parses_localized_summary(self):
        import system.admin as admin
        result = admin._standardize_result({
            "ok": False, "summary": "dependency unavailable esecuzione fallita",
            "audit": {"gate": "argv_validation_rejected",
                      "error_code": "ERR_ARGV_WRAPPER_OPTION"},
        })
        assert result["error_class"] == "invalid_args"
        assert result["error_code"] == "ERR_ARGV_WRAPPER_OPTION"

    def test_admin_passes_the_same_validated_object_to_sudoer(self, monkeypatch):
        from types import SimpleNamespace
        import loader
        import system.admin as admin
        from safety.canonicalize import validate_argv
        validated = validate_argv(["echo", "exact"])
        decision = admin.AdminDecision(
            kind="execute_silent", argv=list(validated.argv),
            signature=str(validated.signature), validated_argv=validated,
        )
        seen = {}

        def fake_invoke(*_args, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                ok=True, status="executed", exit_code=0, stdout="", stderr="",
                duration_ms=1,
            )

        monkeypatch.setattr(loader, "invoke_verb_unique", fake_invoke)
        result = admin._spawn_via_sudoer(
            decision=decision, intent_text="echo", actor="host",
        )
        assert result["ok"] is True
        assert seen["validated_argv"] is validated
        assert "argv" not in seen

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
