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

Run con `python3 -m pytest tests/runtime/http/test_admin_catalog_guard.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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

    def test_command_grammar_exposes_admin_without_per_command_hint(self):
        from prefilter import _detect_command_grammar_intent

        # A future command added to the canonical grammar must become
        # discoverable without editing the prefilter's natural-language list.
        assert _detect_command_grammar_intent(
            "esegui futurectl status", command_names={"futurectl"}
        ) is True
        assert _detect_command_grammar_intent(
            "esegui futurectl status", command_names={"otherctl"}
        ) is False

    def test_command_grammar_requires_structural_invocation_signal(self):
        from prefilter import _detect_command_grammar_intent

        names = {
            "date", "file", "free", "head", "last", "sort", "top",
            "true", "who", "yes", "ping", "traceroute", "futurectl",
        }
        for query in (
            "date of the meeting",
            "date of the meeting at 192.168.1.137",
            "show file report",
            "file report for https://example.net",
            "free time tomorrow",
            "host of example.net",
            "head of the department",
            "I use date format ISO",
            "last 7 days",
            "please use the attached file",
            "sort these names",
            "top ten films",
            "Use it. Who is Roberto?",
            "use the file",
            "fammi un file PDF",
            "is this true",
            "who is Roberto",
            "yes, send it",
            "mandami un ping",
            "non eseguire ping a pc-roberto",
            "senza eseguire ping a pc-roberto",
            "do not run ping example.net",
            "never execute ping example.net",
            "evita di eseguire ping example.net",
            "avoid execute ping example.net",
            "invece di eseguire ping example.net",
            "instead of executing ping example.net",
            "instead of execute ping example.net",
            "instead of run ping example.net",
            "rather than execute ping example.net, describe it",
            "rather than run ping example.net, describe it",
            "execute. ping example.net",
            "fai; ping a pc-roberto",
            "ping example.net, but do not execute it",
            "ping example.net; non eseguirlo",
            "ping example.net; non farlo",
            "ping example.net, but avoid executing it",
            "ping example.net, but avoid running it",
            "ping example.net is not what I want",
            "ping example.net; do not do it because date is slow",
            "ping example.net; non farlo perché file è lento",
            "ping example.net; do not do it because you can execute date",
            "ping example.net; non farlo perché puoi eseguire date",
            "don't execute ping example.net",
            "don’t execute ping example.net",
            "can't execute ping example.net",
            "cannot execute ping example.net",
            "ping example.net; don't do it",
            "ping example.net; don’t do it",
            "didn't run ping example.net",
            "didn’t execute ping example.net",
            "I didn't ask you to execute ping example.net",
            "haven't run ping example.net",
            "hasn't execute ping example.net",
        ):
            assert _detect_command_grammar_intent(
                query, command_names=names,
            ) is False, query

        for query in (
            "fai ping a pc-roberto",
            "puoi eseguire ping example.net",
            "esegui futurectl status",
            "ping -c 1 192.168.1.137",
            "futurectl --status",
            "run ping example.net, but do not execute date",
            "run ping example.net, but do not execute traceroute",
            "do not execute mount, execute ping example.net",
            "do not execute mount: execute ping example.net",
            "do not execute mount, then execute ping example.net",
            "non eseguire mount, poi esegui ping example.net",
            "do not execute mount, and execute ping example.net",
            "non eseguire mount, e esegui ping example.net",
        ):
            assert _detect_command_grammar_intent(
                query, command_names=names,
            ) is True, query

    def test_command_assertion_guard_fails_closed(self, monkeypatch):
        import detection_lexicon
        from prefilter import _detect_command_grammar_intent

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("lexicon unavailable")

        monkeypatch.setattr(detection_lexicon, "asserted_at", unavailable)
        assert _detect_command_grammar_intent(
            "fai ping example.net", command_names={"ping"},
        ) is False

    def test_stated_quantity_keeps_the_registered_command_discoverable(self):
        from prefilter import _detect_command_grammar_intent

        for query in (
            "fai 2 ping a example.net",
            "esegui 7 ping example.net",
            "run 3 ping example.net",
            "do not execute mount, then execute 2 ping example.net",
        ):
            assert _detect_command_grammar_intent(query), query
        for query in (
            "non eseguire 2 ping example.net",
            "do not run 3 ping example.net",
            "avoid executing 3 ping example.net",
            "run 3 ping example.net; do not do it",
            "run ping example.net; do not run 3 ping example.net",
            "fai 2 file PDF",
            "fai 2 date per il calendario",
            "run. 3 ping example.net",
            "fai 2 3 ping example.net",
        ):
            assert not _detect_command_grammar_intent(query), query

    def test_quantity_support_follows_the_central_grammar(self, monkeypatch):
        import safety.canonicalize as grammar
        from prefilter import _detect_command_grammar_intent

        query = "esegui 3 futurectl example.net"
        assert not _detect_command_grammar_intent(query, command_names={"futurectl"})
        monkeypatch.setattr(grammar, "_OPTION_NUMERIC_VALUES", {"futurectl": {"-n": 1.0}})
        assert _detect_command_grammar_intent(query, command_names={"futurectl"})

    def test_time_intent_word_boundary(self):
        from prefilter import _detect_time_intent
        assert _detect_time_intent("che ora è") is True
        assert _detect_time_intent("what time is it") is True
        assert _detect_time_intent("ora corrente") is True
        # word "ora" come avverbio (now) non triggera se non nelle frasi note
        assert _detect_time_intent("ora ti spiego") is False

    def test_shell_intent_comes_from_translatable_registry(
            self, monkeypatch, tmp_path):
        """RM-0005: no language table may remain in the prefilter."""
        import sqlite3
        import detection_lexicon as dl
        import prefilter

        conn = sqlite3.connect(str(tmp_path / "shell-intent.sqlite"))
        conn.executescript(dl._SCHEMA)
        conn.commit()
        monkeypatch.setattr(dl, "_conn", conn)
        monkeypatch.setattr(dl, "_seeded", True)
        monkeypatch.setattr(dl, "current_lang", lambda: "fr")
        dl._invalidate()
        try:
            for concept, it, en, fr in (
                ("syntax.negation", ["non"], ["not"], ["ne pas"]),
                ("syntax.inhibition", ["evita"], ["avoid"], ["évitez"]),
                ("syntax.contrast", ["ma"], ["but"], ["mais"]),
                ("syntax.command_invocation", ["esegui"], ["run"], ["exécutez"]),
                ("syntax.negative_coordination", ["o"], ["or"], ["ou"]),
                ("syntax.sequence", ["poi"], ["then"], ["puis"]),
            ):
                dl.register(
                    concept, "phrases", it=it, en=en, match_mode="word",
                    review_policy="manual",
                )
                dl.set_payload(
                    concept, "fr", fr, kind="phrases", match_mode="word",
                    source_lang="en",
                )
            dl.register(
                "admin.shell_intent", "phrases", match_mode="word",
                it=["riavvia"], en=["restart"],
            )
            dl.set_payload(
                "admin.shell_intent", "fr", ["redémarre"],
                kind="phrases", match_mode="word", source_lang="en",
            )
            assert prefilter._detect_shell_intent(
                "redémarre le service metnos") is True
            for query in (
                "ne pas redémarre le service metnos",
                "évitez redémarre le service metnos",
            ):
                assert prefilter._detect_shell_intent(query) is False
            assert not hasattr(prefilter, "_SHELL_INTENT_HINTS")
            assert not hasattr(prefilter, "_SHELL_INTENT_RE")
        finally:
            dl._invalidate()

    def test_shell_intent_honors_later_revocation(self):
        from prefilter import _detect_shell_intent

        for query in (
            "esegui ping example.net; non farlo",
            "esegui ping example.net, ma non eseguirlo",
            "restart the service, but do not execute it",
            "riavvia il servizio, ma non farlo",
            "restart service; do not do it because date is slow",
            "riavvia il servizio; non farlo perché file è lento",
            "restart service; do not do it because you can execute date",
            "mount share, but do not execute mount",
            "systemctl restart nginx, but do not execute systemctl",
            "don't restart service",
            "don’t restart service",
            "restart service; don't do it",
            "restart service; don’t do it",
            "didn't restart service",
            "didn’t restart service",
            "hasn't restarted service",
            "haven't restarted service",
            "restart service; didn't do it",
            "mount share and systemctl status, but do not execute mount or systemctl",
            "mount share and systemctl status, but do not execute mount and systemctl",
            "systemctl status and mount share, but do not execute systemctl or mount",
            "esegui mount e systemctl, ma non eseguire mount o systemctl",
            "mount share and systemctl status, but do not execute mount, or systemctl",
            "mount share and systemctl status, but do not execute mount, systemctl",
            "systemctl status and mount share, but do not execute systemctl, or mount",
            "esegui mount e systemctl, ma non eseguire mount, o systemctl",
            "mount share and systemctl status, but execute neither mount nor systemctl",
            "mount share and systemctl status, but neither execute mount nor systemctl",
            "mount share and systemctl status, but do not execute: mount or systemctl",
            "mount share and systemctl status, but do not execute: mount, systemctl",
            "esegui mount e systemctl, ma non eseguire: mount o systemctl",
            "mount share and systemctl status, but execute neither: mount nor systemctl",
            "mount share, but do not execute mount, although you can execute systemctl",
            "mount share, but do not execute mount, since you can execute systemctl",
            "do not execute systemctl, execute the sentence systemctl is forbidden",
            "do not execute systemctl, execute a harmless example mentioning systemctl",
            "non eseguire systemctl, esegui la frase systemctl è vietato",
            "do not restart service, execute the report text that mentions systemctl",
        ):
            assert _detect_shell_intent(query) is False, query

        assert _detect_shell_intent(
            "mount share, but do not execute date") is True
        assert _detect_shell_intent(
            "mount share and systemctl status, but do not execute mount",
        ) is True
        assert _detect_shell_intent(
            "mount share and systemctl status, but do not execute mount: execute systemctl",
        ) is True


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

    def test_negated_shell_request_does_not_inject_admin(self):
        from prefilter import rank_adaptive
        from loader import load_catalog

        catalog = list(load_catalog(verify=False, include_synth=False))
        for query in (
            "non esegui ping",
            "do not restart the service",
            "non riavviare il servizio",
            "avoid restart the service",
            "restart the service, but do not execute it",
            "riavvia il servizio, ma non farlo",
        ):
            top, _ = rank_adaptive(
                query, catalog, k_min=5, k_max=8, llm_call=None,
            )
            assert "admin" not in [executor.name for executor in top[:3]], query


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


class TestPlaceholderGuard:
    """`admin.invoke(command_proposed=<...<ph>...>)` → reject deterministico.

    Bug iter 4/5 (24/5/2026, chat_quality «monta share \\\\<ip>\\Public»):
    PLANNER ha propagato `<ip>` letterale come placeholder nel command_proposed.
    Senza guard, il LLM classifier ritornava `kind=unknown` ciclico → loop_break
    con messaggio criptico. Fix §7.3: detection regex deterministica PRIMA del
    LLM call → reject con summary specifico che istruisce l'utente.
    """

    def test_reject_command_with_ip_placeholder(self):
        from system.admin import invoke
        res = invoke(
            intent="monta cifs share",
            command_proposed=("sudo mount -t cifs //<ip>/Public/media "
                              "/home/user/Immagini "
                              "-o credentials=${METNOS_CIFS_CREDS}"),
            actor="host",
        )
        assert res["ok"] is False
        assert res["decision"] == "reject"
        assert res.get("error_class") == "invalid_args"
        assert res.get("error_code") == "ERR_ARG_UNRESOLVED_PLACEHOLDER"
        assert "<ip>" in res["summary"]
        assert "segnaposto" in res["summary"].lower()
        assert res["audit"]["gate"] == "placeholder_rejected"
        assert "ip" in res["audit"]["placeholders"]

    def test_multiple_placeholders_listed(self):
        from system.admin import invoke
        res = invoke(
            intent="esegui comando",
            command_proposed="sudo cp /data/<user>/<dir>/file.txt /backup/",
            actor="host",
        )
        assert res["decision"] == "reject"
        phs = res["audit"]["placeholders"]
        assert "user" in phs and "dir" in phs

    def test_placeholder_in_intent_only(self):
        """Anche se command_proposed e' pulito, un placeholder in `intent`
        indica che il PLANNER ha propagato un user-text non risolto."""
        from system.admin import invoke
        res = invoke(
            intent="connessione al server <host>",
            command_proposed="echo ok",
            actor="host",
        )
        assert res["decision"] == "reject"
        assert "host" in res["audit"]["placeholders"]

    def test_clean_command_falls_through(self):
        """Niente placeholder → guard non scatta, flow normale."""
        from system.admin import invoke
        res = invoke(
            intent="montare share NAS",
            command_proposed="sudo mount -t cifs //192.168.1.20/Public /mnt/nas -o credentials=${METNOS_CIFS_CREDS}",
            actor="host",
        )
        # non e' rejection da placeholder guard.
        if res.get("decision") == "reject":
            assert res.get("audit", {}).get("gate") != "placeholder_rejected"

    def test_env_placeholder_does_not_trigger(self):
        """`${VAR}` (env-style) NON e' un placeholder utente — i CIFS creds
        usano questo pattern come marker per la sostituzione runtime."""
        from system.admin import invoke
        res = invoke(
            intent="mount share",
            command_proposed=("sudo mount -t cifs //host/share /mnt "
                              "-o credentials=${METNOS_CIFS_CREDS}"),
            actor="host",
        )
        if res.get("decision") == "reject":
            assert res.get("audit", {}).get("gate") != "placeholder_rejected"

    def test_helper_strips_path_to_basename_for_catalog_match(self):
        """Path che basename'a in nome executor del catalog → guard scatta.
        I path relativi eseguibili vengono negati prima, al confine argv."""
        from system.admin import _executor_name_in_argv
        assert _executor_name_in_argv(["/usr/bin/get_now"]) == "get_now"
        assert _executor_name_in_argv(["./get_now"]) is None
