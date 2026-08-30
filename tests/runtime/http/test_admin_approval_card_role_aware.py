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

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


@pytest.fixture
def isolated_admin_i18n(tmp_path, monkeypatch):
    """A complete catalog copy that mutation tests may safely corrupt."""
    import i18n

    db_path = tmp_path / "i18n.sqlite"
    shutil.copy2(
        Path(__file__).resolve().parents[3] / "install/data/i18n_seed.sqlite",
        db_path,
    )
    monkeypatch.setattr(i18n, "DB_PATH", db_path)
    monkeypatch.setattr(i18n, "_conn", None)
    monkeypatch.setattr(i18n, "_conn_owner_thread_id", None)
    monkeypatch.setattr(i18n, "_conn_path", None)
    monkeypatch.setattr(i18n, "_thread_connections", __import__("threading").local())
    return i18n


class TestExplainCommandDangers:

    def test_rm_explained(self):
        from system.admin import _explain_command_dangers
        msg = _explain_command_dangers(["rm", "-rf", "/tmp/x"], None)
        assert "cancella" in msg.lower() or "deletes" in msg.lower()
        assert "ricorsiv" in msg.lower() or "recursive" in msg.lower()
        assert "forzant" in msg.lower() or "force" in msg.lower()

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

    @pytest.mark.parametrize(
        ("lang", "danger_fragment", "host_fragment", "guest_fragment"),
        (
            ("it", "Cancella", "Stai per autorizzare", "non è autorizzato"),
            ("en", "Permanently deletes", "You are about to authorize",
             "is not authorized"),
        ),
    )
    def test_danger_and_card_warning_follow_instance_language(
            self, monkeypatch, lang, danger_fragment, host_fragment,
            guest_fragment):
        import i18n
        from system.admin import _build_approval_card

        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", lang)
        host = _build_approval_card(
            ["rm", "-rf", "/tmp/x"], "sig", False, "irreversible",
            None, "test", "host", severity="irreversible",
        )
        guest = _build_approval_card(
            ["rm", "-rf", "/tmp/x"], "sig", False, "irreversible",
            None, "test", "guest_lucia", severity="irreversible",
        )

        assert danger_fragment in host["danger_summary"]
        assert host_fragment in host["warning"]
        assert guest_fragment in guest["warning"]
        assert host["danger_summary"] in host["warning"]
        assert guest["danger_summary"] in guest["warning"]

    def test_unmaterialized_non_italian_language_fails_safe_to_bootstrap(
            self, monkeypatch):
        import i18n
        from system.admin import _build_approval_card

        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")
        card = _build_approval_card(
            ["rm", "-rf", "/tmp/x"], "sig", False, "irreversible",
            None, "test", "host", severity="irreversible",
        )

        assert card["actor_role"] == "admin"
        assert card["options"] == [
            "approve_once", "approve_and_whitelist",
            "reject_once", "block_forever",
        ]
        assert "You are about to authorize" in card["warning"]
        assert "Permanently deletes" in card["danger_summary"]
        assert "Stai per autorizzare" not in card["warning"]
        assert "<missing:" not in card["warning"]

    def test_every_binary_message_key_is_ready_in_seeded_languages(self):
        import i18n
        from system.admin import _DANGER_MESSAGE_KEY_BY_BINARY

        keys = set(_DANGER_MESSAGE_KEY_BY_BINARY.values()) | {
            "MSG_SYSTEM_ADMIN_DANGER_EMPTY",
            "MSG_SYSTEM_ADMIN_DANGER_UNKNOWN_BINARY",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_FORCE",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_RECURSIVE",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_ROOT",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_IRREVERSIBLE",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_DANGEROUS",
            "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST",
            "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_GUEST",
        }
        for key in keys:
            for lang in ("it", "en"):
                resource = i18n.resource_for_language(
                    key, lang, fallback=False, ready_only=True,
                )
                assert resource is not None, f"missing ready {key}[{lang}]"

    @pytest.mark.parametrize("wrapper", ["sudo", "doas", "pkexec"])
    def test_privilege_wrapper_uses_canonical_target(self, monkeypatch, wrapper):
        import i18n
        from system.admin import _build_approval_card, _check_forbidden_argv

        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
        card = _build_approval_card(
            [wrapper, "-u", "root", "rm", "-rf", "/tmp/x"], "sig", True,
            "irreversible", None, "test", "host", severity="irreversible",
        )

        assert "Permanently deletes" in card["danger_summary"]
        assert f"`{wrapper}` is not recognized" not in card["danger_summary"]
        assert "root" in card["danger_summary"]
        denied, reason = _check_forbidden_argv(
            [wrapper, "-u", "root", "rm", "-rf", "/"],
        )
        assert denied is True and "Law 1" in reason

    @pytest.mark.parametrize(
        "argv",
        [
            ["dd", "of=/dev/mapper/vg-root"],
            ["wipefs", "/dev/disk/by-id/example"],
            ["mkfs.ext4", "/dev/md0"],
            ["sgdisk", "--zap-all", "/dev/vda"],
            ["parted", "/dev/xvda", "mklabel", "gpt"],
            ["rm", "-rf", "//"],
            ["rm", "-rf", "/tmp/.."],
            ["rm", "-rf", "/dev/.."],
        ],
    )
    def test_law1_denies_dev_families_and_normalized_root_aliases(self, argv):
        from system.admin import _check_forbidden_argv
        denied, reason = _check_forbidden_argv(argv)
        assert denied is True
        assert "Law 1" in reason

    def test_existing_symlink_target_fails_closed(self, tmp_path):
        from system.admin import _check_forbidden_argv
        destination = tmp_path / "destination"
        destination.write_text("x")
        link = tmp_path / "ambiguous"
        link.symlink_to(destination)
        denied, reason = _check_forbidden_argv(["rm", str(link)])
        assert denied is True
        assert "ambiguous target" in reason

    def test_existing_block_device_target_fails_closed(
            self, tmp_path, monkeypatch):
        import stat
        import system.admin as admin
        target = tmp_path / "virtual-block"
        real_lstat = admin.os.lstat

        def block_at_target(path):
            if Path(path) == target:
                return type("S", (), {"st_mode": stat.S_IFBLK})()
            return real_lstat(path)

        monkeypatch.setattr(admin.os, "lstat", block_at_target)
        denied, reason = admin._check_forbidden_argv(
            ["rm", str(target)],
        )
        assert denied is True
        assert "block device" in reason

    @pytest.mark.parametrize(
        "binary", ["mkfs.ext4", "mkfs.xfs", "mkfs.vfat", "sgdisk"],
    )
    def test_destructive_variants_use_specific_danger_message(
            self, monkeypatch, binary):
        import i18n
        from system.admin import _build_approval_card
        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
        card = _build_approval_card(
            [binary, "/tmp/nonexistent"], "sig", False, "irreversible",
            None, "test", "host", severity="irreversible",
        )
        assert "not recognized" not in card["danger_summary"]


class TestApprovalCardCatalogAtomicity:

    def _card(self):
        from system.admin import _build_approval_card
        return _build_approval_card(
            ["rm", "-rf", "/tmp/x"], "sig", False, "irreversible",
            None, "test", "host", severity="irreversible",
        )

    @pytest.mark.parametrize(
        "mutation", ["missing", "pending", "hash", "placeholder"],
    )
    def test_incomplete_or_malformed_family_fails_closed(
            self, isolated_admin_i18n, monkeypatch, mutation):
        i18n = isolated_admin_i18n
        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
        conn = i18n._open()
        key = "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST"
        if mutation == "missing":
            conn.execute("DELETE FROM i18n WHERE key=? AND lang='it'", (key,))
        elif mutation == "pending":
            conn.execute(
                "UPDATE i18n SET needs_translation=1 WHERE key=? AND lang='it'",
                (key,),
            )
        elif mutation == "hash":
            conn.execute(
                "UPDATE i18n SET text=text || 'x' WHERE key=? AND lang='it'",
                (key,),
            )
        else:
            text = "Attenzione: {danger_summary.__class__}"
            version = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            conn.execute(
                "UPDATE i18n SET text=?,version_hash=? "
                "WHERE key=? AND lang='it'",
                (text, version, key),
            )
        conn.commit()

        card = self._card()

        assert card["error_code"] == (
            "ERR_SYSTEM_ADMIN_APPROVAL_CATALOG_UNAVAILABLE"
        )
        assert card["options"] == []
        assert not ({"approve_once", "approve_and_whitelist"}
                    & set(card["options"]))

    def test_failed_card_cannot_be_approved_or_write_safety_store(
            self, isolated_admin_i18n, monkeypatch):
        import system.admin as admin

        i18n = isolated_admin_i18n
        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
        conn = i18n._open()
        conn.execute(
            "UPDATE i18n SET needs_translation=1 "
            "WHERE key='MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST' AND lang='it'"
        )
        conn.commit()
        card = self._card()
        decision = admin.AdminDecision(
            kind="ask_user", argv=["rm", "-rf", "/tmp/x"],
            signature="rm:fr:fs:tmp", severity="irreversible",
            card_payload=card,
        )

        class _MustNotOpen:
            def __init__(self):
                raise AssertionError("SafetyStore must not open")

        monkeypatch.setattr(admin, "SafetyStore", _MustNotOpen)
        result = admin.apply_user_decision(
            decision=decision, user_choice="approve_and_whitelist", actor="host",
        )

        assert result.kind == "reject"
        assert result.audit["error_code"] == card["error_code"]

    def test_planner_caller_does_not_issue_consent_for_failed_card(
            self, monkeypatch):
        import system.admin as admin

        card = {
            "type": "approval_card", "options": [],
            "error_code": "ERR_SYSTEM_ADMIN_APPROVAL_CATALOG_UNAVAILABLE",
        }
        denied = admin.AdminDecision(
            kind="ask_user", argv=["tool"], signature="tool:*:*",
            card_payload=card,
        )
        monkeypatch.setattr(admin, "_decide_for_argv", lambda *_a, **_k: denied)
        monkeypatch.setattr(
            admin, "_sign_consent_token",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("consent token must not be issued")
            ),
        )

        result = admin._invoke_impl(
            intent="run tool", command_proposed="tool",
        )

        assert result["ok"] is False
        assert result["approval_required"] is False
        assert result["consent_token"] is None
        assert result["error_code"] == card["error_code"]

    def test_complete_third_language_requires_english_source_provenance(
            self, isolated_admin_i18n, monkeypatch):
        i18n = isolated_admin_i18n
        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "zz")
        conn = i18n._open()
        keys = [
            "MSG_SYSTEM_ADMIN_DANGER_RM",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_FORCE",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_RECURSIVE",
            "MSG_SYSTEM_ADMIN_DANGER_NOTE_IRREVERSIBLE",
            "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST",
        ]
        rows = conn.execute(
            "SELECT key,text,version_hash FROM i18n WHERE lang='en' AND key IN "
            "(" + ",".join("?" for _ in keys) + ")",
            keys,
        ).fetchall()
        for key, text, source_version in rows:
            conn.execute(
                "INSERT INTO i18n(key,lang,text,needs_translation,source_lang,"
                "version_hash,source_text_hash) VALUES(?, 'zz', ?, 0, 'en', ?, ?)",
                (key, text, source_version, source_version),
            )
        conn.commit()

        assert "error_code" not in self._card()
        conn.execute(
            "UPDATE i18n SET source_lang='it' "
            "WHERE key=? AND lang='zz'",
            ("MSG_SYSTEM_ADMIN_DANGER_NOTE_FORCE",),
        )
        conn.commit()
        assert self._card()["error_code"] == (
            "ERR_SYSTEM_ADMIN_APPROVAL_CATALOG_UNAVAILABLE"
        )


def test_both_production_callers_propagate_classified_severity(monkeypatch):
    import json
    import system.admin as admin

    seen = []

    class _EmptyStore:
        def find_by_kind(self, _kind):
            return []

        def close(self):
            pass

    def _capture_card(*args, **kwargs):
        seen.append(kwargs["severity"])
        return {"options": ["reject_once"]}

    monkeypatch.setattr(admin, "SafetyStore", _EmptyStore)
    monkeypatch.setattr(admin, "_build_approval_card", _capture_card)
    legacy = admin.decide(
        "remove temp files",
        llm_call=lambda _prompt: json.dumps({
            "kind": "translated", "argv": ["rm", "-rf", "/tmp/x"],
        }),
    )
    planner = admin._decide_for_argv(
        ["rm", "-rf", "/tmp/x"], intent_text="remove temp files",
    )

    assert legacy.severity == planner.severity == "irreversible"
    assert seen == ["irreversible", "irreversible"]


class TestAdminUserOutputsEnglish:

    @pytest.fixture(autouse=True)
    def _english_instance(self, monkeypatch):
        import i18n
        monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")

    def test_decision_gates_and_wait_are_english(self):
        import system.admin as admin

        literal = admin.decide("sudo rm -rf /tmp/x")
        waits = []
        unknown = admin.decide(
            "do something", emit_wait=waits.append,
            llm_call=lambda _prompt: '{"kind":"unknown"}',
        )
        malformed = admin.decide(
            "do something", llm_call=lambda _prompt: "not-json",
        )
        empty = admin._decide_for_argv([], intent_text="nothing")
        forbidden = admin._decide_for_argv(
            ["rm", "-rf", "/"], intent_text="remove root",
        )

        assert "direct commands" in literal.reason
        assert "do not know" in unknown.reason
        assert "checking whether" in waits[0]
        assert "could not understand" in malformed.reason
        assert "is empty" in empty.reason
        assert "Forbidden:" in forbidden.reason
        assert "destructive" in forbidden.reason
        assert not any(
            fragment in " ".join(
                [literal.reason, unknown.reason, *waits, malformed.reason,
                 empty.reason]
            ).lower()
            for fragment in ("non posso", "non so", "sto verificando", "vuoto o")
        )

    def test_decision_replies_and_renderers_are_english(self):
        import system.admin as admin

        decision = admin.AdminDecision(
            kind="ask_user", argv=["tool", "--flag"], signature="tool:flag:*",
            requires_sudo=True, reversibility="unknown", card_payload={
                "actor_role": "admin",
                "options": list(admin._ADMIN_APPROVAL_OPTIONS),
            },
        )
        guest_decision = admin.AdminDecision(
            kind="ask_user", argv=["tool", "--flag"], signature="tool:flag:*",
            requires_sudo=True, reversibility="unknown", card_payload={
                "actor_role": "guest",
                "options": list(admin._GUEST_APPROVAL_OPTIONS),
            },
        )
        rejected = admin.apply_user_decision(
            decision=decision, user_choice="reject_once", actor="host",
        )
        external = admin.apply_user_decision(
            decision=guest_decision, user_choice="run_externally", actor="guest_x",
        )
        card_summary = admin._format_card_summary(
            decision, intent_text="run a tool",
        )
        credentials = admin._format_credentials_required(
            "cifs_host", {"binding": "cifs", "host": "host"},
        )
        cli = admin._format_cli_instructions(
            "cifs_host", {"binding": "cifs", "host": "host"},
        )

        assert "rejected for this time" in rejected.reason
        assert "manually outside Metnos" in external.reason
        assert "I propose" in card_summary and "requires sudo" in card_summary
        assert "Credentials are required" in credentials
        assert "from a terminal" in cli

    def test_planner_guards_and_credentials_card_are_english(self, monkeypatch):
        import credentials
        import system.admin as admin

        unresolved = admin._invoke_impl(
            intent="mount <server>", command_proposed="mount <server>",
        )
        monkeypatch.setattr(credentials, "list_domains", lambda: [])
        needs = admin._invoke_impl(
            intent="mount the share",
            command_proposed=(
                "sudo mount -t cifs //203.0.113.253/Share /tmp/share "
                "-o credentials=${METNOS_CIFS_CREDS}"
            ),
        )

        assert "unresolved placeholders" in unresolved["summary"]
        payload = needs["needs_inputs"]
        assert payload["title"].startswith("Credentials for")
        assert "will be encrypted" in payload["description"]
        assert payload["dialog"][0]["prompt"] == "Username:"

    @pytest.mark.parametrize("ok", [True, False])
    def test_sudoer_terminal_summary_is_english(self, monkeypatch, ok):
        from types import SimpleNamespace
        import loader
        import system.admin as admin

        monkeypatch.setattr(
            loader, "invoke_verb_unique",
            lambda *_args, **_kwargs: SimpleNamespace(
                ok=ok, status="executed" if ok else "failed", exit_code=0 if ok else 2,
                stdout="done" if ok else "", stderr="bad" if not ok else "",
                duration_ms=1,
            ),
        )
        result = admin._spawn_via_sudoer(
            decision=admin.AdminDecision(
                kind="execute_silent", argv=["tool"], signature="tool:*:*",
            ),
            intent_text="run", actor="host",
        )

        expected = "Executed:" if ok else "Execution failed:"
        assert expected in result["summary"]


def test_system_admin_seed_uses_english_as_source_for_italian():
    import re
    import sqlite3

    source = (Path(__file__).resolve().parents[3] / "runtime/system/admin.py").read_text()
    referenced = set(re.findall(r'\bMSG_SYSTEM_ADMIN_[A-Z_]+\b', source))
    seed = Path(__file__).resolve().parents[3] / "install/data/i18n_seed.sqlite"
    conn = sqlite3.connect(seed)
    try:
        rows = conn.execute(
            "SELECT i.key,i.source_lang,i.source_text_hash,e.version_hash "
            "FROM i18n i JOIN i18n e ON e.key=i.key AND e.lang='en' "
            "WHERE i.key GLOB 'MSG_SYSTEM_ADMIN_*' AND i.lang='it'"
        ).fetchall()
        total = conn.execute(
            "SELECT count(DISTINCT key) FROM i18n "
            "WHERE key GLOB 'MSG_SYSTEM_ADMIN_*'"
        ).fetchone()[0]
        seeded = {
            key for (key,) in conn.execute(
                "SELECT key FROM i18n WHERE key GLOB 'MSG_SYSTEM_ADMIN_*' "
                "GROUP BY key HAVING count(*)=2 AND sum(needs_translation)=0"
            )
        }
    finally:
        conn.close()

    assert len(rows) == total
    assert referenced <= seeded
    assert all(source_lang == "en" and source_hash == en_hash
               for _, source_lang, source_hash, en_hash in rows)


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

    @pytest.mark.parametrize("mutation", ["missing_role", "wrong_role", "options"])
    def test_card_identity_and_exact_server_choices_are_mandatory(
            self, monkeypatch, mutation):
        import system.admin as admin

        decision = self._ask_user_decision_for(
            ["weird_card_command_xyz", "--show"], actor="host",
        )
        assert decision.kind == "ask_user"
        card = dict(decision.card_payload)
        if mutation == "missing_role":
            card.pop("actor_role")
        elif mutation == "wrong_role":
            card["actor_role"] = "guest"
        else:
            card["options"] = [*card["options"], "run_externally"]
        decision.card_payload = card

        class _MustNotOpen:
            def __init__(self):
                raise AssertionError("malformed card must not reach SafetyStore")

        monkeypatch.setattr(admin, "SafetyStore", _MustNotOpen)
        result = admin.apply_user_decision(
            decision=decision, user_choice="approve_once", actor="host",
        )
        assert result.kind == "reject"
        assert result.audit["error_code"] == "ERR_ADMIN_APPROVAL_CARD_MISMATCH"


@pytest.mark.parametrize(
    "argv",
    [
        ["dd", "if=/tmp/source", "of=/tmp/destination"],
        ["mkfs.ext4", "/tmp/image"],
        ["wipefs", "/tmp/image"],
        ["mkswap", "/tmp/image"],
        ["blkdiscard", "/tmp/image"],
        ["fdisk", "--list"],
        ["parted", "--version"],
        ["sgdisk", "--help"],
    ],
)
def test_generic_admin_denies_raw_disk_primitives_unconditionally(argv):
    from system.admin import _check_forbidden_argv
    denied, reason = _check_forbidden_argv(argv)
    assert denied is True
    assert "raw-disk primitive" in reason


@pytest.mark.parametrize(
    "argv", [["mount", "--help"], ["umount", "/tmp/x"], ["ping", "127.0.0.1"]],
)
def test_non_raw_disk_families_keep_their_existing_generic_gate(argv):
    from system.admin import _check_forbidden_argv
    assert _check_forbidden_argv(argv) == (False, None)


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
        with pytest.raises(ValueError, match="not allowed for guest"):
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
    """Legacy labels remain accepted without widening future authorization."""

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
        assert result.age_class == "one_shot"
        from safety.storage import SafetyStore
        store = SafetyStore()
        try:
            assert store.find_by_signature(d.signature) is None
        finally:
            store.close()

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


def test_guest_may_only_submit_options_from_its_card():
    import system.admin as admin
    decision = admin._decide_for_argv(
        ["weird_guest_command_xyz"], intent_text="test", actor="guest_x",
    )
    assert decision.kind == "ask_user"
    for choice in ("approve", "approve_once", "approve_and_whitelist",
                   "block_forever"):
        with pytest.raises(ValueError, match="not allowed for guest"):
            admin.apply_user_decision(
                decision=decision, user_choice=choice, actor="guest_x",
            )
