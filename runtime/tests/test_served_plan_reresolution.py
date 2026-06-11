"""Ri-risoluzione degli slot query-specific sui piani SERVITI (12/6/2026).

Bug live 11/6/2026 (faglie 1+2 del routing mail): il fallback intent-hash di
L1 autopath serviva il champion `read_messages__v1.0.0` (nato da «controlla
le mail di metnos» → account='metnos_system', niente finestra) a QUALSIASI
query read|messages, riusando gli arg della query d'ORIGINE verbatim; L0 ha
poi cachato il piano sbagliato per «controlla tutte le mie mailbox ultime
24 ore». Il champion e' un template di STRUTTURA: gli slot query-specific
(account mail, time_window) si ri-riempiono dalla query ATTUALE.

Due punte della stessa lama (engine/executor.resolve_query_canonical_args):
  1. ESECUZIONE — Executor.run applica la catena a ogni step, qualunque
     layer abbia servito il piano (L0/L1/L3): la risposta e' corretta anche
     col piano stantio in cache.
  2. RECORD — dispatch._maybe_record_fastpath canonicalizza il piano PRIMA
     di registrarlo: lo store L0 riflette cio' che esegue (§2.8) e
     query_specific viene calcolato sul piano vero (finestra relativa
     literal → 0a-only).

Mock everywhere — no live LLM, no IMAP, no BGE-M3.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Intent, Framework, StepSpec, RunResult
from engine.executor import Executor, resolve_query_canonical_args
from engine import fastpath as eng_fastpath
from engine import dispatch as eng_dispatch
from engine import cluster as eng_cluster

_QUERY_BUG = "controlla tutte le mie mailbox ultime 24 ore"

_RM_SCHEMA = {"properties": {"account": {"type": "string"},
                             "folder": {"type": "string"},
                             "max_results": {"type": "integer"},
                             "time_window": {"type": "string"}},
              "required": []}

_KNOWN = ["metnos_system", "knowcastle", "mykleos"]


def _champion_framework() -> Framework:
    """Il piano del champion L1 reale (arg della query d'origine)."""
    return Framework(
        steps=[StepSpec(tool="read_messages",
                        args={"account": "metnos_system", "folder": "INBOX",
                              "max_results": 20}),
               StepSpec(tool="final_answer", args={})],
        final_message="${step1.@count}")


def _catalog():
    return [SimpleNamespace(name="read_messages", args_schema=_RM_SCHEMA,
                            affinity=[])]


class _KnownAccountsMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        import mail_client
        p = mock.patch.object(mail_client, "list_known_accounts",
                              lambda: list(_KNOWN))
        p.start()
        self.addCleanup(p.stop)


# ── 1. Catena resolver (unita') ────────────────────────────────────────────

class TestResolveQueryCanonicalArgs(_KnownAccountsMixin):
    def test_bug_query_riempe_account_e_finestra(self):
        out = resolve_query_canonical_args(
            "read_messages",
            {"account": "metnos_system", "folder": "INBOX", "max_results": 20},
            _QUERY_BUG, args_schema=_RM_SCHEMA)
        self.assertEqual(out["account"], "all")
        self.assertEqual(out["time_window"], "last-24h")
        self.assertEqual(out["folder"], "INBOX")  # struttura preservata

    def test_account_nominato_vince_sul_leak(self):
        out = resolve_query_canonical_args(
            "read_messages", {"account": "metnos_system"},
            "controlla la mail di knowcastle", args_schema=_RM_SCHEMA)
        self.assertEqual(out["account"], "knowcastle")
        self.assertNotIn("time_window", out)

    def test_query_neutra_e_noop(self):
        args = {"account": "metnos_system", "folder": "INBOX"}
        out = resolve_query_canonical_args(
            "read_messages", args, "controlla la posta",
            args_schema=_RM_SCHEMA)
        self.assertEqual(out, args)  # nessuna finestra spuria, account fermo


# ── 2. ESECUZIONE: il piano servito (stantio) esegue corretto ──────────────

class TestExecutorReresolution(_KnownAccountsMixin):
    def _run(self, query: str):
        captured = []

        def probe(tool, args):
            captured.append((tool, {k: v for k, v in args.items()
                                    if not k.startswith("_")}))
            return {"ok": True, "entries": [], "ok_count": 0}

        ex = Executor(invoke_executor=probe, catalog=_catalog())
        run = ex.run(_champion_framework(), query=query,
                     runtime_ctx={"actor": "host", "lang": "it"})
        self.assertEqual(run.final_kind, "answer")
        return dict(captured[0][1])

    def test_piano_champion_servito_alla_query_bug(self):
        args = self._run(_QUERY_BUG)
        self.assertEqual(args["account"], "all")
        self.assertEqual(args["time_window"], "last-24h")

    def test_guard_account_nominato(self):
        args = self._run("controlla la mail di knowcastle")
        self.assertEqual(args["account"], "knowcastle")
        self.assertNotIn("time_window", args)

    def test_guard_senza_tempo_nessuna_finestra_spuria(self):
        args = self._run("controlla la posta")
        self.assertEqual(args["account"], "metnos_system")
        self.assertNotIn("time_window", args)

    def test_replay_0a_stessa_query_deterministico(self):
        # Stessa query 2 volte → stessi arg risolti (Bug B chiuso a monte).
        self.assertEqual(self._run(_QUERY_BUG), self._run(_QUERY_BUG))


# ── 3. RECORD: lo store L0 riflette cio' che esegue (§2.8) ─────────────────

class TestRecordCanonicalization(_KnownAccountsMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"
        self.addCleanup(self._restore)
        # BGE-M3 fuori dal test: 0a-only basta per l'assert.
        p = mock.patch.object(eng_cluster, "embed", lambda q: None)
        p.start()
        self.addCleanup(p.stop)

    def _restore(self):
        eng_fastpath._db_path = self._orig

    def test_hit_l1_registra_il_piano_canonicalizzato(self):
        run = RunResult(final_kind="answer")
        eng_dispatch._maybe_record_fastpath(
            _QUERY_BUG, Intent(verb="read", object="messages"),
            _champion_framework(), run, origin="autopath",
            catalog=_catalog())
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "autopath")
        # Piano onesto: account='all' + finestra estratta dalla query.
        hit = eng_fastpath.lookup(_QUERY_BUG)
        self.assertIsNotNone(hit)
        args = hit.framework.steps[0].args
        self.assertEqual(args["account"], "all")
        self.assertEqual(args["time_window"], "last-24h")
        # Finestra relativa literal → 0a-only per costruzione.
        self.assertTrue(rows[0]["query_specific"])

    def test_query_neutra_registra_il_piano_originale(self):
        run = RunResult(final_kind="answer")
        eng_dispatch._maybe_record_fastpath(
            "controlla la posta", Intent(verb="read", object="messages"),
            _champion_framework(), run, origin="autopath",
            catalog=_catalog())
        hit = eng_fastpath.lookup("controlla la posta")
        self.assertIsNotNone(hit)
        args = hit.framework.steps[0].args
        self.assertEqual(args, {"account": "metnos_system",
                                "folder": "INBOX", "max_results": 20})


if __name__ == "__main__":
    unittest.main()
