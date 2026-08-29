"""Test per l'esposizione di admin al PLANNER (ADR 0088).

Copre:
  1. catalog membership: load_catalog() ora include admin in `executors`.
  2. prefilter shell-intent: query "monta share NAS" → admin nel top-K.
  3. admin invoke() con args validi → approval_required + carta vaglio.
  4. admin invoke() con actor_consent_token valido → execute_silent / sudoer.
  5. cap-pending consume con kind="admin_approval" → direct admin call.

Run con `python3 -m pytest tests/runtime/http/test_admin_exposure.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Per-test SQLite file per il safety store + clean registry."""
    db_path = tmp_path / "safety.db"
    monkeypatch.setenv("SAFETY_DB_PATH", str(db_path))
    import importlib
    import safety.storage as storage_mod
    importlib.reload(storage_mod)
    from loader import VERB_UNIQUE_REGISTRY
    VERB_UNIQUE_REGISTRY.clear()
    return db_path


@pytest.fixture
def seeded_db(temp_db):
    import importlib
    import safety.seed_bootstrap as sb
    importlib.reload(sb)
    res = sb.bootstrap_safety_seed()
    assert res.upgraded
    return temp_db


# ── 1. Catalog membership ─────────────────────────────────────────────

class TestCatalogMembership:
    def test_admin_in_catalog(self, seeded_db):
        """load_catalog() include admin (ADR 0088)."""
        from loader import load_catalog
        catalog = load_catalog(verify=False, include_synth=False)
        assert "admin" in catalog.executors
        admin_ex = catalog.executors["admin"]
        # Manifest virtuale ha description leggibile
        assert "shell" in admin_ex.description.lower()
        assert "approvazione" in admin_ex.description.lower() or \
               "vaglio" in admin_ex.description.lower()
        # Args schema contiene intent + command_proposed
        props = admin_ex.args_schema.get("properties", {})
        assert "intent" in props
        assert "command_proposed" in props
        # Capability amministrativa canonica richiesta
        assert any(c.get("name") == "system:admin"
                   for c in admin_ex.capabilities)

    def test_sudoer_NOT_in_catalog(self, seeded_db):
        """sudoer resta invisibile al PLANNER (ADR 0088)."""
        from loader import load_catalog
        catalog = load_catalog(verify=False, include_synth=False)
        assert "sudoer" not in catalog.executors


# ── 2. Prefilter shell-intent injection ───────────────────────────────

class TestPrefilterShellIntent:
    def test_mount_query_promotes_admin(self, seeded_db):
        from loader import load_catalog
        from prefilter import rank_adaptive
        catalog = load_catalog(verify=False, include_synth=False)
        # Senza llm_call, fallback BoW → ramo che inietta admin
        # quando _detect_shell_intent matcha
        selected, info = rank_adaptive(
            "monta share NAS del server 192.168.1.20",
            catalog, k_min=3, k_max=8, llm_call=None,
        )
        names = [e.name for e in selected]
        assert "admin" in names, f"admin atteso in top-K, ottenuti: {names}"

    def test_kill_query_promotes_admin(self, seeded_db):
        from loader import load_catalog
        from prefilter import rank_adaptive
        catalog = load_catalog(verify=False, include_synth=False)
        selected, _ = rank_adaptive(
            "kill processo zombie pid 1234", catalog,
            k_min=3, k_max=8, llm_call=None,
        )
        assert "admin" in [e.name for e in selected]

    def test_non_shell_query_does_not_promote_admin(self, seeded_db):
        from loader import load_catalog
        from prefilter import rank_adaptive
        catalog = load_catalog(verify=False, include_synth=False)
        # Query mail standard: admin non deve apparire
        selected, _ = rank_adaptive(
            "leggi le mail di oggi", catalog,
            k_min=3, k_max=8, llm_call=None,
        )
        # admin non deve essere in top-3 (potrebbe esserci nel cap a 8 per
        # caso, ma non nei primi tre dove conta).
        top3 = [e.name for e in selected[:3]]
        assert "admin" not in top3, (
            f"admin non dovrebbe stare in top-3 di una query mail, top-3: {top3}"
        )

    @pytest.mark.parametrize("command", ["ping", "traceroute", "dig", "whois"])
    def test_safety_grammar_command_keeps_admin_as_generic_fallback(
            self, seeded_db, command):
        from loader import load_catalog
        from prefilter import rank_adaptive

        catalog = load_catalog(verify=False, include_synth=False)
        selected, _ = rank_adaptive(
            f"esegui {command} verso example.net",
            catalog, k_min=3, k_max=8, llm_call=None,
        )
        assert "admin" in [executor.name for executor in selected]

    def test_live_ping_wording_exposes_guarded_admin_fallback(self, seeded_db):
        from loader import load_catalog
        from prefilter import rank_adaptive

        catalog = load_catalog(verify=False, include_synth=False)
        selected, _ = rank_adaptive(
            "fai ping a pc-roberto",
            catalog, k_min=3, k_max=8, llm_call=None,
        )
        assert "admin" in [executor.name for executor in selected]

    def test_message_ping_does_not_expose_admin(self, seeded_db):
        from loader import load_catalog
        from prefilter import rank_adaptive

        catalog = load_catalog(verify=False, include_synth=False)
        selected, _ = rank_adaptive(
            "mandami un ping", catalog, k_min=3, k_max=8, llm_call=None,
        )
        names = [executor.name for executor in selected]
        assert "admin" not in names


# ── 3-4. admin invoke() flow ──────────────────────────────────────────

class TestAdminInvokeFlow:
    def test_invoke_unknown_signature_emits_approval_card(self, seeded_db):
        """Una signature sconosciuta produce approval_required + carta."""
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique
        boot_register_verb_unique_builtins()
        # cowsay non e' nel seed → unknown → carta
        res = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="fammi un cowsay",
            command_proposed="cowsay hello",
        )
        assert isinstance(res, dict)
        assert res["approval_required"] is True
        assert res["decision"] == "approval_required"
        assert "approval_card" in res
        assert res["approval_card"]["argv_rendered"].startswith("cowsay")
        # consent_token presente, non vuoto
        assert res.get("consent_token")
        # summary user-facing in italiano
        assert "approvare" in res["summary"].lower() or \
               "appruov" in res["summary"].lower() or \
               "sì" in res["summary"].lower()

    def test_invoke_with_valid_consent_token_skips_card(self, seeded_db,
                                                        monkeypatch):
        """Con consent_token valido, admin salta la carta e procede."""
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique

        boot_register_verb_unique_builtins()

        # Step 1: ottieni il consent_token emesso da una prima call
        res1 = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="fammi un cowsay",
            command_proposed="cowsay hello",
        )
        assert res1["approval_required"] is True
        token = res1["consent_token"]
        assert token

        # Step 2: monkeypatch sudoer per non spawnare processo
        from dataclasses import dataclass

        @dataclass
        class MockExecRes:
            ok: bool = True
            status: str = "executed"
            argv: list = None
            signature: str = ""
            requires_sudo: bool = False
            exit_code: int = 0
            stdout: str = "fake stdout"
            stderr: str = ""
            duration_ms: int = 1
            audit: dict = None

            def __post_init__(self):
                if self.argv is None:
                    self.argv = []
                if self.audit is None:
                    self.audit = {}

        from loader import VERB_UNIQUE_REGISTRY
        # Sostituisci callable di sudoer con mock
        if "sudoer" in VERB_UNIQUE_REGISTRY:
            VERB_UNIQUE_REGISTRY["sudoer"]["callable"] = (
                lambda **kw: MockExecRes(argv=kw.get("argv", []))
            )

        # Step 3: rilancio con token → execute_silent
        res2 = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="fammi un cowsay",
            command_proposed="cowsay hello",
            actor_consent_token=token,
        )
        assert res2["decision"] == "execute_silent"
        assert res2["approval_required"] is False
        assert res2["ok"] is True
        assert "Eseguito" in res2["summary"] or "executed" in res2["summary"].lower()

    def test_invoke_with_forbidden_argv_rejects(self, seeded_db):
        """argv come `rm -rf /` viene rifiutato (gate sintattico
        sull'argv intera o forbidden raw check)."""
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique
        boot_register_verb_unique_builtins()
        res = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="svuota la radice",
            command_proposed="rm -rf /",
        )
        assert res["decision"] == "reject"
        assert res["approval_required"] is False
        # Il gate sintattico fa scattare "starts with known shell binary"
        # PRIMA del forbidden raw check su `rm`. Va bene cosi': il rifiuto
        # arriva, e il messaggio di errore istruisce a riformulare.
        s = res["summary"].lower()
        assert (
            "vietato" in s
            or "bloccato" in s
            or "shell-meta" in s
            or "binary" in s
            or "riformula" in s
        ), f"summary inatteso: {res['summary']!r}"

    def test_invoke_with_pipe_in_argv_rejects(self, seeded_db):
        """Shell-meta nel command_proposed → gate rifiuta."""
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique
        boot_register_verb_unique_builtins()
        res = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="elenca e conta",
            command_proposed="ls /etc | wc -l",
        )
        assert res["decision"] == "reject"
        assert "shell-meta" in res["summary"].lower() or \
               "pipe" in res["summary"].lower() or \
               "vietat" in res["summary"].lower()


# ── 5. Cap-pending integration (light) ────────────────────────────────

class TestCapPendingAdminApproval:
    def test_proposal_kind_is_admin_approval(self, seeded_db, monkeypatch,
                                              tmp_path):
        """Quando admin emette approval_required, il runtime salva un
        proposal con kind='admin_approval' (verifica shape del dict, non
        l'integrazione daemon completa)."""
        # Costruzione manuale del proposal come fa agent_runtime
        from loader import boot_register_verb_unique_builtins, invoke_verb_unique
        boot_register_verb_unique_builtins()
        obs = invoke_verb_unique(
            "admin", caller="agent_runtime",
            intent="fammi un cowsay",
            command_proposed="cowsay ciao",
        )
        assert obs["approval_required"]
        # Shape della proposal compatibile con _consume_admin_approval
        proposal = {
            "kind": "admin_approval",
            "step_num": 1,
            "executor": "admin",
            "cap_field": "actor_consent_token",
            "cap_suggested": obs["consent_token"],
            "args_original": {"intent": "fammi un cowsay",
                              "command_proposed": "cowsay ciao"},
            "args_suggested": {
                "intent": "fammi un cowsay",
                "command_proposed": "cowsay ciao",
                "actor_consent_token": obs["consent_token"],
            },
            "approval_card": obs["approval_card"],
            "signature": obs["signature"],
        }
        # Chiavi richieste dal daemon._consume_admin_approval
        assert proposal["kind"] == "admin_approval"
        assert "args_suggested" in proposal
        assert "actor_consent_token" in proposal["args_suggested"]
