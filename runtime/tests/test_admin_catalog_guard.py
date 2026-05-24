"""Test anti-regressione bug 1f82a766 (12/5/2026): admin invocato dal
PLANNER con `command_proposed` = nome di un executor del catalog (es.
"get_now"). Il bug produceva FileNotFoundError perche' sudoer faceva
`subprocess.run(["get_now"])` senza che `get_now` sia un binario shell.

Due livelli di difesa, testati qui:

  1. **Prefilter shell-intent word-boundary** (`runtime/prefilter.py`):
     la query «...mandami una email di conferma» NON deve triggerare
     shell-intent injection di admin (il match naive `"ferma"` matchava
     `con-ferma`).

  2. **Admin catalog-name guard** (`runtime/system/admin.py`):
     se `argv[0]` (saltando wrapper sudo) e' un executor del catalog,
     admin emette decision="reject" con messaggio chiaro PRIMA di
     emettere la carta vaglio o spawnare sudoer.

Run con `python3 -m pytest runtime/tests/test_admin_catalog_guard.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Layer 1: prefilter word-boundary shell-intent detection ───────────

class TestShellIntentWordBoundary:
    """`_detect_shell_intent` deve usare word-boundary, non substring naive."""

    def test_conferma_does_not_trigger_admin(self):
        """Caso bug live: "email di conferma" matchava "ferma" come substring."""
        from prefilter import _detect_shell_intent
        q = (
            "fissa un appuntamento mercoledi mattina dopo le 9 per un ora "
            "se c'è posto e mandami una email di conferma"
        ).lower()
        assert _detect_shell_intent(q) is False

    def test_legitimate_shell_words_still_trigger(self):
        from prefilter import _detect_shell_intent
        cases_true = [
            "monta share NAS",
            "monta cifs //host/share",
            "riavvia il servizio metnos-http",
            "uccidi processo 1234",
            "systemctl restart nginx",
            "apt install foo",
            "ferma il daemon",
            "fermare il processo",
            "comando shell",
            "shell command",
            "avviare il backup",
            "chmod 600 /etc/passwd",
            "stop the service",
        ]
        for q in cases_true:
            assert _detect_shell_intent(q.lower()) is True, q

    def test_false_positives_avoided(self):
        from prefilter import _detect_shell_intent
        cases_false = [
            "conferma la prenotazione",
            "afferma di sapere",
            "fermata autobus dove si trova",
            "killer instinct",
            "skilled developer",
            "shared folder con il team",
            "startup script da rivedere",
            "stops the train",
            "informa il cliente",
        ]
        for q in cases_false:
            actual = _detect_shell_intent(q.lower())
            assert actual is False, f"false-positive on: {q!r}"

    def test_time_intent_word_boundary(self):
        from prefilter import _detect_time_intent
        assert _detect_time_intent("che ora è") is True
        assert _detect_time_intent("what time is it") is True
        assert _detect_time_intent("ora corrente") is True
        # word "ora" come avverbio (now) non triggera se non nelle frasi note
        assert _detect_time_intent("ora ti spiego") is False


class TestPrefilterIntegrationAppointment:
    """End-to-end: la query bug NON deve far comparire admin nel top-K."""

    def test_appointment_query_no_admin_injection_bow(self):
        from prefilter import rank_adaptive
        from loader import load_catalog
        catalog = list(load_catalog(verify=False, include_synth=False))
        q = (
            "fissa un appuntamento mercoledi mattina dopo le 9 per un ora "
            "se c'è posto e mandami una email di conferma"
        )
        top, _ = rank_adaptive(q, catalog, k_min=5, k_max=8)
        names = [e.name for e in top]
        assert "admin" not in names, (
            f"admin spurio nel top-K (fix prefilter word-boundary regredito): {names}"
        )

    def test_appointment_query_with_intent_no_admin(self):
        from prefilter import rank_with_intent
        from loader import load_catalog
        # synth incluso: lo scenario reale ha tutto il catalog (handcrafted
        # + synth + imported skills) caricato; con `events` come object il
        # rank_with_intent richiede la presenza di executor coerenti.
        catalog = list(load_catalog(verify=False, include_synth=True))
        q = (
            "fissa un appuntamento mercoledi mattina dopo le 9 per un ora "
            "se c'è posto e mandami una email di conferma"
        )
        # post ADR 0128: l'intent semantico per «fissa appuntamento» e' verb=create
        # (era set_events, ora create_events).
        intent = {"verb": "create", "object": "events"}
        top = rank_with_intent(q, catalog, intent, k=8)
        assert top is not None, "rank_with_intent ha rifiutato events (catalog incompleto?)"
        names = [e.name for e in top]
        assert "admin" not in names
        assert "create_events" in names


# ── Layer 2: admin catalog-name guard ─────────────────────────────────

class TestAdminCatalogGuard:
    """`admin.invoke(command_proposed=<executor_name>)` → reject chirurgico."""

    def test_reject_get_now_as_command(self):
        """Bug 1f82a766: PLANNER ha invocato admin(command_proposed='get_now')."""
        from system.admin import invoke
        res = invoke(
            intent="ottenere la data corrente",
            command_proposed="get_now",
            actor="host",
        )
        assert res["ok"] is False
        assert res["decision"] == "reject"
        assert "get_now" in res["summary"]
        assert "executor del catalogo" in res["summary"]
        # niente carta vaglio
        assert res["approval_required"] is False
        assert res["approval_card"] is None
        assert res["audit"]["gate"] == "catalog_name_rejected"
        assert res["audit"]["catalog_name"] == "get_now"

    def test_reject_set_events_as_command(self):
        # Test name unchanged for git diff readability; post ADR 0128
        # l'executor canonical e' `create_events` (set_events rinominato).
        from system.admin import invoke
        res = invoke(
            intent="creare un evento",
            command_proposed="create_events arg1 arg2",
            actor="host",
        )
        assert res["decision"] == "reject"
        assert "create_events" in res["summary"]

    def test_reject_executor_under_sudo_wrapper(self):
        """sudo get_now → ancora reject (skip wrapper, vede executor name)."""
        from system.admin import invoke
        res = invoke(
            intent="forzare con sudo",
            command_proposed="sudo get_now",
            actor="host",
        )
        assert res["decision"] == "reject"
        assert res["audit"]["catalog_name"] == "get_now"

    def test_reject_under_sudo_S_flag(self):
        from system.admin import invoke
        res = invoke(
            intent="forzare con sudo -S",
            command_proposed="sudo -S get_now",
            actor="host",
        )
        assert res["decision"] == "reject"
        assert res["audit"]["catalog_name"] == "get_now"

    def test_legitimate_mount_still_works(self):
        """`admin(command_proposed="mount -t cifs ...")` → NON e' un nome
        executor → continua il flow normale (approval card o execute_silent)."""
        from system.admin import invoke
        res = invoke(
            intent="montare share NAS",
            command_proposed="sudo mount -t cifs //host/share /mnt/nas -o credentials=${METNOS_CIFS_CREDS},uid=1000",
            actor="host",
        )
        # `mount` non e' un executor → admin passa al flow normale.
        # Puo' essere `approval_required` (no credentials) o `needs_inputs`
        # se il placeholder e' presente e il dominio non e' salvato.
        assert res["decision"] in (
            "approval_required", "execute_silent", "needs_inputs", "reject"
        )
        # In ogni caso, non e' una rejection da catalog-name guard.
        if res.get("decision") == "reject":
            assert res.get("audit", {}).get("gate") != "catalog_name_rejected"

    def test_admin_name_not_self_rejected(self):
        """`argv[0]=admin` non innesca il guard (admin e' verb_unique builtin,
        non un executor "normale" nel senso del guard). Il gate sintattico
        lo gestisce separatamente."""
        from system.admin import _executor_name_in_argv
        assert _executor_name_in_argv(["admin"]) is None
        assert _executor_name_in_argv(["sudoer"]) is None

    def test_helper_handles_empty_argv(self):
        from system.admin import _executor_name_in_argv
        assert _executor_name_in_argv([]) is None
        assert _executor_name_in_argv([""]) is None

    def test_helper_handles_absolute_path(self):
        """Path assoluti (es. /bin/mount) NON triggerano il guard:
        sono comandi shell con path letterale, non nomi di executor."""
        from system.admin import _executor_name_in_argv
        # `/bin/mount` finisce in `mount` come basename: mount non e'
        # un executor del catalog → None.
        assert _executor_name_in_argv(["/bin/mount", "-t", "cifs"]) is None

    def test_helper_strips_path_to_basename_for_catalog_match(self):
        """Path che basename'a in nome executor del catalog → guard scatta.
        Defesa contro tentativi di bypass tipo "/usr/bin/get_now"."""
        from system.admin import _executor_name_in_argv
        assert _executor_name_in_argv(["/usr/bin/get_now"]) == "get_now"
        assert _executor_name_in_argv(["./get_now"]) == "get_now"
