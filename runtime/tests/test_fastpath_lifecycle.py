"""test_fastpath_lifecycle.py — ciclo di vita dei fastpath L0 (11/6/2026).

Copre il mandato auto-produzione + aging + morte:
  1. LOOP — turno completato con successo dal piano pieno auto-crea il
     fastpath; la query identica successiva è HIT (proposer NON richiamato).
  2. PERTINENZA — framework query-specific servito SOLO via hash 0a,
     mai via cosine 0b (query simile ma semanticamente diversa).
  3. AGING — prune deterministico: mai-riusato oltre grazia, stale, cap LRU.
  4. MORTE — tool del piano non più nel catalog (C1) o executor che
     implementa direttamente l'intent del fastpath (C2) → rimozione.

Mock everywhere — no live LLM, no live executor, no BGE-M3.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Intent, Framework, StepSpec
from engine import fastpath as eng_fastpath
from engine import dispatch as eng_dispatch


def _pack(vec):
    return b"".join(struct.pack("<f", float(x)) for x in vec)


def _fw(*tools, args_map=None, final="fatto"):
    """Framework di comodo: steps dai nomi tool + final_answer."""
    args_map = args_map or {}
    steps = [StepSpec(tool=t, args=dict(args_map.get(t, {}))) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps, final_message=final)


class _FastpathDbCase(unittest.TestCase):
    """DB fastpath isolato per-test (stesso pattern di test_engine_v2)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"

    def tearDown(self):
        eng_fastpath._db_path = self._orig


# ── 1. Auto-produzione ─────────────────────────────────────────────────────

class TestAutoRecord(_FastpathDbCase):
    def test_record_success_sets_origin_auto_and_intent(self):
        intent = Intent(verb="get", object="numbers")
        fp_id = eng_fastpath.record_success(
            "che ore sono", _fw("get_now"), intent=intent)
        self.assertGreater(fp_id, 0)
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "auto")
        self.assertEqual(rows[0]["intent_verb"], "get")
        self.assertEqual(rows[0]["intent_object"], "numbers")
        self.assertTrue(rows[0]["created_at"])

    def test_record_skips_final_only(self):
        fw = Framework(steps=[StepSpec(tool="final_answer", args={})],
                       final_message="ciao")
        self.assertEqual(eng_fastpath.record_success("ciao", fw), 0)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_record_skips_context_dependent_tools(self):
        # undo_last_turn / get_inputs: replay semanticamente scorretto
        self.assertEqual(
            eng_fastpath.record_success("annulla", _fw("undo_last_turn")), 0)
        self.assertEqual(
            eng_fastpath.record_success("chiedimi", _fw("get_inputs")), 0)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_record_idempotent_refreshes_framework(self):
        q = "conta i file in tmp"
        eng_fastpath.record_success(q, _fw("find_files"))
        eng_fastpath.record_success(q, _fw("list_files"))  # piano aggiornato
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)
        hit = eng_fastpath.lookup(q)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.framework.steps[0].tool, "list_files")

    def test_refresh_returns_real_fp_id(self):
        # Sull'upsert-UPDATE lastrowid NON è la riga aggiornata: il refresh
        # deve ritornare lo STESSO fp_id del primo record (telemetria §2.8).
        q = "conta i file in tmp"
        id1 = eng_fastpath.record_success(q, _fw("find_files"))
        id2 = eng_fastpath.record_success(q, _fw("list_files"))
        self.assertGreater(id1, 0)
        self.assertEqual(id1, id2)

    def test_refresh_heals_null_embedding(self):
        # Primo record con BGE-M3 giù (embed=None) → embedding NULL; il
        # refresh con embedder vivo RIPARA; un refresh successivo con
        # embedder di nuovo giù NON cancella l'embedding valido.
        q = "conta i file della cartella tmp"

        def _emb_count():
            c = eng_fastpath._conn()
            n = c.execute("SELECT COUNT(*) FROM fastpaths "
                          "WHERE embedding IS NOT NULL").fetchone()[0]
            c.close()
            return n

        with mock.patch("engine.cluster.embed", new=lambda q: None):
            eng_fastpath.record_success(q, _fw("find_files"))
            self.assertEqual(_emb_count(), 0)
        with mock.patch("engine.cluster.embed",
                        new=lambda q: _pack([1.0, 0.0])):
            eng_fastpath.record_success(q, _fw("find_files"))
            self.assertEqual(_emb_count(), 1)  # riparato
        with mock.patch("engine.cluster.embed", new=lambda q: None):
            eng_fastpath.record_success(q, _fw("find_files"))
            self.assertEqual(_emb_count(), 1)  # conservato

    def test_query_specific_flag_persisted(self):
        fw = _fw("find_images",
                 args_map={"find_images": {"query_text": "tramonto"}})
        eng_fastpath.record_success("cerca foto tramonto", fw)
        rows = eng_fastpath.list_all()
        self.assertTrue(rows[0]["query_specific"])


# ── 1ter. Migrazione schema v1 (era-approvazione) ──────────────────────────

# DDL v1 ESATTO del DB live di produzione (~/.local/share/metnos/
# fastpaths.sqlite, creato dall'era-approvazione): approved_at TEXT NOT NULL
# che record_success non valorizza → IntegrityError → 0 righe (bug 11/6/2026).
_V1_DDL = """
CREATE TABLE fastpaths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_text TEXT NOT NULL,
    canonical_hash TEXT NOT NULL UNIQUE,
    embedding BLOB,
    framework_json TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT NOT NULL,
    n_uses INTEGER NOT NULL DEFAULT 0,
    last_used TEXT
);
CREATE INDEX fp_hash ON fastpaths(canonical_hash);
CREATE INDEX fp_uses ON fastpaths(n_uses DESC);
"""

# Colonne v2 appese da una _migrate_schema PRE-fix (ADD-only): è lo stato
# REALE del DB live al momento del bug — vestigia NOT NULL ancora presenti.
_V1_PARTIAL_ALTERS = (
    "ALTER TABLE fastpaths ADD COLUMN origin TEXT NOT NULL DEFAULT 'auto'",
    "ALTER TABLE fastpaths ADD COLUMN intent_verb TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE fastpaths ADD COLUMN intent_object TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE fastpaths ADD COLUMN query_specific INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE fastpaths ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
)


class TestSchemaV1Migration(_FastpathDbCase):
    """Il DB di prod ha lo schema v1 (approved_at NOT NULL): la migrazione
    deve renderlo canonico (DROP vestigia), preservare le righe, ed essere
    idempotente — e record/lookup devono funzionare SU QUEL DB."""

    def _create_v1_db(self, *, partial_v2: bool = False,
                      seed_row: bool = False):
        import sqlite3 as _sq
        p = eng_fastpath._db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        c = _sq.connect(str(p))
        c.executescript(_V1_DDL)
        if partial_v2:
            for stmt in _V1_PARTIAL_ALTERS:
                c.execute(stmt)
        if seed_row:
            c.execute(
                "INSERT INTO fastpaths(canonical_text, canonical_hash, "
                "framework_json, approved_by, approved_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("vecchia query", "deadbeef00000000",
                 '{"steps": [{"tool": "get_now", "args": {}}], '
                 '"fillers": {}, "final_message": "x"}',
                 "roberto", "2026-05-20T10:00:00Z"))
        c.commit()
        c.close()

    def _cols(self) -> set:
        c = eng_fastpath._conn()
        cols = {r[1] for r in c.execute("PRAGMA table_info(fastpaths)")}
        c.close()
        return cols

    def test_record_succeeds_on_pure_v1_schema(self):
        self._create_v1_db()
        fp_id = eng_fastpath.record_success(
            "controlla posta ultime 24 ore",
            _fw("read_messages", "describe_entries"),
            intent=Intent(verb="read", object="messages"))
        self.assertGreater(fp_id, 0)
        hit = eng_fastpath.lookup("controlla posta ultime 24 ore")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.match_kind, "hash")
        self.assertEqual(hit.framework.steps[0].tool, "read_messages")

    def test_record_succeeds_on_live_partial_v2_schema(self):
        # Lo stato ESATTO del DB live al bug: v1 + colonne v2 già appese,
        # approved_at NOT NULL ancora presente.
        self._create_v1_db(partial_v2=True)
        fp_id = eng_fastpath.record_success(
            "controlla posta ultime 24 ore",
            _fw("read_messages", "describe_entries"),
            intent=Intent(verb="read", object="messages"))
        self.assertGreater(fp_id, 0)
        self.assertIsNotNone(
            eng_fastpath.lookup("controlla posta ultime 24 ore"))

    def test_migration_drops_vestigia_preserves_rows(self):
        self._create_v1_db(seed_row=True)
        eng_fastpath._conn().close()  # innesca la migrazione
        cols = self._cols()
        self.assertNotIn("approved_at", cols)
        self.assertNotIn("approved_by", cols)
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_text"], "vecchia query")
        # created_at backfillata dall'approved_at v1 (età reale preservata)
        self.assertEqual(rows[0]["created_at"], "2026-05-20T10:00:00Z")

    def test_migration_idempotent(self):
        self._create_v1_db(seed_row=True)
        for _ in range(3):
            eng_fastpath._conn().close()
        cols = self._cols()
        self.assertNotIn("approved_at", cols)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_dispatch_records_and_hits_on_v1_schema(self):
        # End-to-end sullo scenario di prod: schema VECCHIO, turno-successo
        # → REGISTRA; query ripetuta → COLPISCE (match_source=fastpath).
        self._create_v1_db(partial_v2=True)
        fw = _fw("get_now")
        fake = _FakeProposer(fw)
        catalog = [SimpleNamespace(
            name="get_now",
            args_schema={"type": "object", "properties": {}})]
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r1 = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t1")
            self.assertEqual(r1.match_source, "engine")
            self.assertEqual(len(eng_fastpath.list_all()), 1)  # REGISTRATO
            r2 = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t2")
            self.assertEqual(r2.match_source, "fastpath")  # COLPITO
            self.assertEqual(fake.calls, 1)  # proposer mai richiamato


# ── 2. Pertinenza 0a/0b ────────────────────────────────────────────────────

class TestPertinence(_FastpathDbCase):
    """0b cosine NON deve servire piani query-specific (literal di UN'ALTRA
    query); restano servibili via hash 0a (query identica)."""

    def _fake_embed(self, table):
        def _emb(q):
            return table.get(q)
        return _emb

    def test_query_specific_not_served_by_cosine(self):
        va = _pack([1.0, 0.0])
        vb = _pack([0.95, 0.312249899])  # cosine(va, vb) = 0.95 ≥ soglia 0.92
        table = {"cerca le foto di silvia": va,
                 "cerca le foto di marco": vb}
        fw = _fw("find_persons",
                 args_map={"find_persons": {"name": "silvia"}})
        with mock.patch("engine.cluster.embed", new=self._fake_embed(table)):
            eng_fastpath.record_success("cerca le foto di silvia", fw)
            # Query SIMILE ma semanticamente diversa: NO hit (0b filtrato)
            self.assertIsNone(eng_fastpath.lookup("cerca le foto di marco"))
            # Query IDENTICA: hit via hash 0a (args giusti per costruzione)
            hit = eng_fastpath.lookup("cerca le foto di silvia")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.match_kind, "hash")

    def test_generic_framework_served_by_cosine(self):
        va = _pack([1.0, 0.0])
        vb = _pack([0.95, 0.312249899])
        table = {"conta i file della cartella tmp": va,
                 "contami i file dentro tmp": vb}
        fw = _fw("find_files", args_map={"find_files": {"base_path": "/tmp"}})
        with mock.patch("engine.cluster.embed", new=self._fake_embed(table)):
            eng_fastpath.record_success("conta i file della cartella tmp", fw)
            hit = eng_fastpath.lookup("contami i file dentro tmp")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.match_kind, "cosine")
            self.assertGreaterEqual(hit.similarity, 0.92)


# ── 1bis. LOOP end-to-end via dispatch.run_turn ────────────────────────────

class _FakeProposer:
    def __init__(self, fw):
        self.fw = fw
        self.calls = 0

    def propose(self, **kw):
        self.calls += 1
        return self.fw


class TestDispatchLoop(_FastpathDbCase):
    """Turno-successo (piano pieno) → auto-crea L0 → query identica → HIT
    senza richiamare il proposer."""

    def test_success_turn_autocreates_then_hits(self):
        fw = _fw("get_now")
        fake = _FakeProposer(fw)
        catalog = [SimpleNamespace(
            name="get_now",
            args_schema={"type": "object", "properties": {}})]
        intent = Intent()  # incompleto → niente prefilter/autopath
        invoked = []

        def invoke(name, args):
            invoked.append(name)
            return {"ok": True, "iso": "2026-06-11T10:00:00Z"}

        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r1 = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=intent, catalog=catalog,
                invoke_executor_cb=invoke, turn_id="t1")
            self.assertEqual(r1.match_source, "engine")
            self.assertEqual(r1.final_kind, "answer")
            self.assertEqual(fake.calls, 1)
            rows = eng_fastpath.list_all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["origin"], "auto")

            r2 = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=intent, catalog=catalog,
                invoke_executor_cb=invoke, turn_id="t2")
            self.assertEqual(r2.match_source, "fastpath")
            self.assertEqual(r2.final_kind, "answer")
            self.assertEqual(fake.calls, 1)  # proposer NON richiamato
            self.assertEqual(invoked, ["get_now", "get_now"])  # piano eseguito 2×
            # Telemetria: l'hit ha toccato n_uses/last_used
            rows = eng_fastpath.list_all()
            self.assertEqual(rows[0]["n_uses"], 1)
            self.assertTrue(rows[0]["last_used"])

    def test_record_failure_logged_at_warning(self):
        # §2.8: il record è best-effort ma il suo fallimento NON è silenzioso
        # — a debug era invisibile in prod (INFO) e ha nascosto la
        # causa-radice 0-righe (IntegrityError approved_at).
        fw = _fw("get_now")
        fake = _FakeProposer(fw)
        catalog = [SimpleNamespace(
            name="get_now",
            args_schema={"type": "object", "properties": {}})]
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch.object(eng_dispatch._fp, "record_success",
                               side_effect=RuntimeError("boom")), \
             self.assertLogs("engine.dispatch", level="WARNING") as cm:
            r = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t1")
        self.assertEqual(r.final_kind, "answer")  # il turno NON si blocca
        self.assertTrue(any("record_success fallita" in m
                            for m in cm.output))

    def test_failed_turn_does_not_autocreate(self):
        fw = _fw("get_now")
        fake = _FakeProposer(fw)
        catalog = [SimpleNamespace(
            name="get_now",
            args_schema={"type": "object", "properties": {}})]
        intent = Intent()

        def invoke(name, args):
            return {"ok": False, "error": "boom"}

        fake_recovery = SimpleNamespace(recover=lambda **kw: None)
        fake_terminator = SimpleNamespace(
            explain=lambda **kw: SimpleNamespace(final_text="ko"))
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.recovery.get_recovery",
                        return_value=fake_recovery), \
             mock.patch("engine.terminator.get_terminator",
                        return_value=fake_terminator), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r1 = eng_dispatch.run_turn(
                query="che ore sono adesso", intent=intent, catalog=catalog,
                invoke_executor_cb=invoke, turn_id="t1")
            self.assertNotEqual(r1.match_source, "fastpath")
            self.assertEqual(eng_fastpath.list_all(), [])


# ── 1quinquies. Copertura: record anche da hit L1/0b (bug live 11/6/2026) ──

class TestRecordFromCacheHits(_FastpathDbCase):
    """Bug live 11/6/2026: «controlla tutte le mie mailbox ultime 24 ore»
    veniva servita dalla skill L1 read_messages e NON registrava MAI il
    fastpath (il ramo autopath di dispatch ritornava senza record) → la
    query esatta ripagava per sempre embed+scan L1 invece dell'hash 0a.
    Classe: OGNI turno-successo la cui query esatta non è in cache 0a
    registra (engine, recovery, hit L1, hit 0b); SOLO l'hit 0a non registra
    (la riga esiste già)."""

    def _catalog(self, *names):
        return [SimpleNamespace(
            name=n, args_schema={"type": "object", "properties": {}})
            for n in names]

    def test_l1_autopath_hit_records_then_l0_takes_over(self):
        from engine.autopath import AutopathHit
        fw = _fw("read_messages", "describe_entries",
                 args_map={"read_messages": {"account": "all",
                                             "folder": "INBOX",
                                             "max_results": 20}})
        intent = Intent(verb="read", object="messages")
        catalog = self._catalog("read_messages", "describe_entries")
        hit = AutopathHit(autopath_id="read_messages__v1.0.0", framework=fw,
                          cluster_id="cl_x", uses=1)
        lookups = []

        def _ap_lookup(query, intent):
            lookups.append(query)
            return hit

        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1",
               "METNOS_AUTOPATH": "1"}
        q = "controlla tutte le mie mailbox ultime 24 ore"
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(eng_dispatch._ap, "lookup", new=_ap_lookup), \
             mock.patch.object(eng_dispatch._ap, "record_observation",
                               return_value="fh"), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r1 = eng_dispatch.run_turn(
                query=q, intent=intent, catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "entries": []},
                turn_id="t1")
            self.assertEqual(r1.match_source, "autopath")
            self.assertEqual(r1.final_kind, "answer")
            rows = eng_fastpath.list_all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["origin"], "autopath")
            self.assertEqual(rows[0]["intent_verb"], "read")
            # Ripetizione identica: ora vince L0 0a, L1 NON interpellato.
            r2 = eng_dispatch.run_turn(
                query=q, intent=intent, catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "entries": []},
                turn_id="t2")
            self.assertEqual(r2.match_source, "fastpath")
            self.assertEqual(len(lookups), 1)  # solo il primo turno

    def test_l1_hit_failed_run_does_not_record(self):
        from engine.autopath import AutopathHit
        fw = _fw("read_messages")
        intent = Intent(verb="read", object="messages")
        catalog = self._catalog("read_messages")
        hit = AutopathHit(autopath_id="s", framework=fw, cluster_id="c", uses=1)
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1",
               "METNOS_AUTOPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(eng_dispatch._ap, "lookup",
                               new=lambda q, i: hit), \
             mock.patch.object(eng_dispatch._ap, "record_observation",
                               return_value="fh"), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r = eng_dispatch.run_turn(
                query="controlla le mailbox", intent=intent, catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": False, "error": "ko"},
                turn_id="t1")
            self.assertEqual(r.final_kind, "error")
            self.assertEqual(eng_fastpath.list_all(), [])

    def test_0b_cosine_hit_promotes_to_own_hash(self):
        va = _pack([1.0, 0.0])
        vb = _pack([0.95, 0.312249899])  # cosine(va, vb) = 0.95 ≥ 0.92
        qa = "conta i file della cartella tmp"
        qb = "contami i file dentro tmp"
        table = {qa: va, qb: vb}
        fw = _fw("find_files", args_map={"find_files": {"base_path": "/tmp"}})
        fake = _FakeProposer(fw)
        catalog = self._catalog("find_files")
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.cluster.embed",
                        new=lambda q: table.get(q)):
            eng_fastpath.record_success(qa, fw)
            r1 = eng_dispatch.run_turn(
                query=qb, intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "entries": []},
                turn_id="t1")
            self.assertEqual(r1.match_source, "fastpath")  # via 0b
            self.assertEqual(fake.calls, 0)  # proposer mai chiamato
            rows = eng_fastpath.list_all()
            self.assertEqual(len(rows), 2)  # promozione: riga propria per qb
            self.assertIn("cosine", {r["origin"] for r in rows})
            # Ripetizione di qb: ora hash 0a diretto (niente scan).
            hit = eng_fastpath.lookup(qb)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.match_kind, "hash")

    def test_0a_hash_hit_does_not_rerecord(self):
        fw = _fw("get_now")
        fake = _FakeProposer(fw)
        catalog = self._catalog("get_now")
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        q = "che ore sono adesso"
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.cluster.embed", new=lambda q: None), \
             mock.patch.object(eng_dispatch._fp, "record_success",
                               wraps=eng_fastpath.record_success) as rec:
            eng_dispatch.run_turn(
                query=q, intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t1")
            self.assertEqual(rec.call_count, 1)  # record dal piano pieno
            eng_dispatch.run_turn(
                query=q, intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t2")
            self.assertEqual(rec.call_count, 1)  # hit 0a: NESSUN re-record


# ── 1sexies. Cacheabilità temporale (misura BGE-M3 12/6/2026) ──────────────

class TestTemporalCacheability(_FastpathDbCase):
    """Pivot temporale «oggi»→«ieri» (cosine 0.9722) supera la soglia 0b
    (0.92) più delle parafrasi legittime (0.946-0.964): nessuna soglia li
    separa. Guard strutturali: finestra RELATIVA pinnata → query-specific
    (0a-only); data ISO ASSOLUTA → non cacheabile (replay stantio)."""

    def test_time_window_literal_is_query_specific(self):
        from engine.executor import is_query_specific
        import json as _json
        fj = _json.dumps(_fw("read_messages", args_map={
            "read_messages": {"time_window": "today"}}).to_dict())
        self.assertTrue(is_query_specific(fj))

    def test_concrete_glob_is_query_specific(self):
        # turn 73476663: pattern="*.py" deriva da «python» nella query → 0a-only.
        # Servirlo via cosine a «quanti file ci sono» dava 448 invece di 980.
        from engine.executor import is_query_specific
        import json as _json
        for pat in ("*.py", "*.md", "*.js"):
            fj = _json.dumps(_fw("find_files_github", args_map={
                "find_files_github": {"repo": "a/b", "pattern": pat,
                                       "count_only": True}}).to_dict())
            self.assertTrue(is_query_specific(fj), pat)

    def test_universal_glob_is_not_query_specific(self):
        # pattern="*" = «tutti i file»: nessuna informazione di query → cosine OK.
        from engine.executor import is_query_specific
        import json as _json
        for pat in ("*", "*.*"):
            fj = _json.dumps(_fw("find_files_github", args_map={
                "find_files_github": {"repo": "a/b", "pattern": pat}}).to_dict())
            self.assertFalse(is_query_specific(fj), pat)

    def test_time_window_plan_not_served_by_cosine(self):
        va = _pack([1.0, 0.0])
        vb = _pack([0.98, 0.198997487])  # cosine ≈ 0.98 (pivot oggi/ieri)
        qa = "riassumi le mail di oggi"
        qb = "riassumi le mail di ieri"
        table = {qa: va, qb: vb}
        fw = _fw("read_messages", "describe_entries", args_map={
            "read_messages": {"time_window": "today"}})
        with mock.patch("engine.cluster.embed", new=lambda q: table.get(q)):
            eng_fastpath.record_success(qa, fw)
            self.assertIsNone(eng_fastpath.lookup(qb))  # 0b BLOCCATO
            hit = eng_fastpath.lookup(qa)               # 0a esatto OK
            self.assertIsNotNone(hit)
            self.assertEqual(hit.match_kind, "hash")

    def test_absolute_iso_literal_not_recorded(self):
        fw = _fw("read_messages", args_map={
            "read_messages": {"since_iso": "2026-06-11"}})
        self.assertEqual(
            eng_fastpath.record_success("mail da ieri", fw), 0)
        # anche annidato in una lista
        fw2 = _fw("read_files", args_map={
            "read_files": {"paths": ["/backup/2026-06-11/x.txt"]}})
        self.assertEqual(
            eng_fastpath.record_success("leggi il backup", fw2), 0)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_placeholder_dates_still_recordable(self):
        # Un placeholder ${stepN.date} NON è un literal congelato.
        fw = _fw("create_events", args_map={
            "create_events": {"start": "${step1.entries.0.start}"}})
        self.assertGreater(
            eng_fastpath.record_success("crea evento dal testo", fw), 0)


# ── 1septies. Valvola feedback ✗ → delete L0 (12/6/2026) ───────────────────

class TestFeedbackValve(_FastpathDbCase):
    def test_delete_by_query_normalizes(self):
        eng_fastpath.record_success("controlla le mailbox", _fw("read_messages"))
        self.assertEqual(len(eng_fastpath.list_all()), 1)
        # Variante con stopword/punteggiatura: stessa canonical_hash
        self.assertEqual(
            eng_fastpath.delete_by_query("controlla le mailbox!"), 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_error_feedback_deletes_l0_row(self):
        import turn_feedback as tf
        q = "controlla tutte le mie mailbox ultime 24 ore"
        eng_fastpath.record_success(q, _fw("read_messages"))
        self.assertEqual(len(eng_fastpath.list_all()), 1)
        fake_turn = {"turn_id": "tx", "user_query": q, "steps": [],
                     "final_kind": "answer"}
        with mock.patch.object(tf, "_load_turn", return_value=fake_turn), \
             mock.patch.object(tf, "FEEDBACK_PATH",
                               Path(self.tmp) / "fb.jsonl"), \
             mock.patch("engine.autopath.record_feedback",
                        return_value={"ok": False}):
            rec = tf.apply_feedback("tx", "error", by="test")
        self.assertEqual(eng_fastpath.list_all(), [])
        self.assertTrue(any(e.get("type") == "fastpath_deleted"
                            for e in rec["effects"]))

    def test_ok_feedback_keeps_l0_row(self):
        import turn_feedback as tf
        q = "controlla le mailbox"
        eng_fastpath.record_success(q, _fw("read_messages"))
        fake_turn = {"turn_id": "ty", "user_query": q, "steps": [],
                     "final_kind": "answer"}
        with mock.patch.object(tf, "_load_turn", return_value=fake_turn), \
             mock.patch.object(tf, "FEEDBACK_PATH",
                               Path(self.tmp) / "fb.jsonl"), \
             mock.patch("engine.autopath.record_feedback",
                        return_value={"ok": False}):
            tf.apply_feedback("ty", "ok", by="test")
        self.assertEqual(len(eng_fastpath.list_all()), 1)


# ── 3. Aging (prune deterministico) ────────────────────────────────────────

_NOW = 1_780_000_000.0  # epoch fisso: prune(now_ts=_NOW) → §7.9 deterministico


def _iso_days_ago(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(_NOW - days * 86400))


class TestAging(_FastpathDbCase):
    def _seed(self, query: str, *, created_days_ago: float,
              used_days_ago: float | None = None) -> int:
        fp_id = eng_fastpath.record_success(query, _fw("get_now"))
        self.assertGreater(fp_id, 0)
        c = eng_fastpath._conn()
        c.execute("UPDATE fastpaths SET created_at = ?, last_used = ? "
                  "WHERE id = ?",
                  (_iso_days_ago(created_days_ago),
                   _iso_days_ago(used_days_ago)
                   if used_days_ago is not None else None,
                   fp_id))
        c.commit()
        c.close()
        return fp_id

    def _ids(self) -> set:
        return {r["id"] for r in eng_fastpath.list_all()}

    def test_never_reused_pruned_after_grace(self):
        old = self._seed("query mai ripetuta", created_days_ago=20)
        fresh = self._seed("query recente", created_days_ago=3)
        rep = eng_fastpath.prune(stale_days=30, grace_days=14,
                                 max_rows=500, now_ts=_NOW)
        self.assertEqual(rep["never_reused_removed"], 1)
        self.assertEqual(self._ids(), {fresh})
        self.assertNotIn(old, self._ids())

    def test_stale_pruned_recent_kept(self):
        stale = self._seed("ricorrenza cessata", created_days_ago=90,
                           used_days_ago=45)
        live = self._seed("ricorrenza viva", created_days_ago=90,
                          used_days_ago=2)
        rep = eng_fastpath.prune(stale_days=30, grace_days=14,
                                 max_rows=500, now_ts=_NOW)
        self.assertEqual(rep["stale_removed"], 1)
        self.assertEqual(self._ids(), {live})
        self.assertNotIn(stale, self._ids())

    def test_cap_lru(self):
        ids = [self._seed(f"query numero {i}", created_days_ago=1,
                          used_days_ago=0.05 * (i + 1))
               for i in range(5)]
        rep = eng_fastpath.prune(stale_days=30, grace_days=14,
                                 max_rows=3, now_ts=_NOW)
        self.assertEqual(rep["cap_removed"], 2)
        # Restano le 3 più recentemente attive (used_days_ago più piccolo)
        self.assertEqual(self._ids(), set(ids[:3]))

    def test_prune_idempotent(self):
        self._seed("query viva", created_days_ago=2, used_days_ago=1)
        rep1 = eng_fastpath.prune(stale_days=30, grace_days=14,
                                  max_rows=500, now_ts=_NOW)
        rep2 = eng_fastpath.prune(stale_days=30, grace_days=14,
                                  max_rows=500, now_ts=_NOW)
        self.assertEqual(rep1["kept"], 1)
        self.assertEqual(rep2["kept"], 1)
        self.assertEqual(rep2["never_reused_removed"], 0)
        self.assertEqual(rep2["stale_removed"], 0)


# ── 4. Morte (executor mancante C1 / equivalente C2) ───────────────────────

class TestDeath(_FastpathDbCase):
    _AGING = dict(stale_days=30, grace_days=14, max_rows=500, now_ts=_NOW)

    def test_c1_missing_tool_pruned(self):
        eng_fastpath.record_success("comando con tool ritirato",
                                    _fw("ghost_tool"))
        rep = eng_fastpath.prune(catalog_names={"get_now", "find_files"},
                                 **self._AGING)
        self.assertEqual(rep["dead_missing_tool"], 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_c2_equivalent_executor_kills(self):
        # Piano N-step per intent (compress, images); poi synt crea
        # compress_images → il fastpath muore (l'executor lo rimpiazza,
        # altrimenti L0 lo oscurerebbe per sempre).
        intent = Intent(verb="compress", object="images")
        eng_fastpath.record_success(
            "comprimi le foto di marzo",
            _fw("find_images", "compress_files_zip"), intent=intent)
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "compress_files_zip",
                           "compress_images"}, **self._AGING)
        self.assertEqual(rep["dead_superseded"], 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_c2_plan_already_in_family_immune(self):
        # Il piano USA già la famiglia verb_object (find_messages): la
        # variante provider (find_messages_google_workspace) NON è un
        # rimpiazzo → nessuna morte.
        intent = Intent(verb="find", object="messages")
        eng_fastpath.record_success(
            "cerca le mail di ieri", _fw("find_messages"), intent=intent)
        rep = eng_fastpath.prune(
            catalog_names={"find_messages", "find_messages_google_workspace"},
            **self._AGING)
        self.assertEqual(rep["dead_superseded"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_c2_without_equivalent_kept(self):
        intent = Intent(verb="compress", object="images")
        eng_fastpath.record_success(
            "comprimi le foto di marzo",
            _fw("find_images", "compress_files_zip"), intent=intent)
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "compress_files_zip"},
            **self._AGING)
        self.assertEqual(rep["dead_superseded"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_no_catalog_no_death(self):
        # catalog_names=None (set completo non ricostruibile): solo aging,
        # MAI morte — meglio nessun kill che falsi kill (§2.8).
        eng_fastpath.record_success("comando con tool ritirato",
                                    _fw("ghost_tool"))
        rep = eng_fastpath.prune(catalog_names=None, **self._AGING)
        self.assertEqual(rep["dead_missing_tool"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_c1_hit_time_guard_self_heals(self):
        # Fastpath stantio che riferisce un tool ritirato: a hit-time viene
        # POTATO (mai eseguito → niente wrong_tool) e il turno cade su L3,
        # che ripianifica col catalog corrente; il successo RI-CREA il
        # fastpath col piano nuovo (self-healing).
        q = "che ore sono adesso"
        eng_fastpath.record_success(q, _fw("ghost_tool"))
        fake = _FakeProposer(_fw("get_now"))
        catalog = [SimpleNamespace(
            name="get_now",
            args_schema={"type": "object", "properties": {}})]
        env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("engine.proposer.get_proposer", return_value=fake), \
             mock.patch("engine.cluster.embed", new=lambda q: None):
            r = eng_dispatch.run_turn(
                query=q, intent=Intent(), catalog=catalog,
                invoke_executor_cb=lambda n, a: {"ok": True, "iso": "x"},
                turn_id="t1")
        self.assertEqual(r.match_source, "engine")  # fall-through, no replay
        self.assertEqual(fake.calls, 1)
        rows = eng_fastpath.list_all()
        self.assertEqual(len(rows), 1)  # ri-creato dal successo
        hit = eng_fastpath.lookup(q)
        self.assertEqual(hit.framework.steps[0].tool, "get_now")


# ── 5. Morte C2 check-prefilter + eredità-punti (mandato #5-followup) ───────

def _ex(name, affinity=()):
    """Oggetto executor minimo per il prefilter (name + affinity)."""
    return SimpleNamespace(name=name, affinity=list(affinity))


class TestDeathPrefilter(_FastpathDbCase):
    """C2 check-prefilter: il name-based ha falsi-negativi su equivalenti
    con NOME DIVERSO (es. sibling verb create per intent write). Il prefilter
    (deterministico, no LLM) sulla query canonica decide: top-1 che
    implementa l'intent + fastpath multi-step fuori famiglia → morte."""

    _AGING = dict(stale_days=30, grace_days=14, max_rows=500, now_ts=_NOW)
    _Q = "salva la lista della spesa in un foglio di calcolo"

    def _seed_multistep(self) -> int:
        # Piano multi-step per intent (write, files): NESSUN tool della
        # famiglia write_files → il name-based non vede l'equivalente
        # create_files_spreadsheet (sibling verb, nome FUORI famiglia).
        intent = Intent(verb="write", object="files")
        fp_id = eng_fastpath.record_success(
            self._Q, _fw("get_urls", "write_texts"), intent=intent)
        self.assertGreater(fp_id, 0)
        return fp_id

    def _catalog_with_equiv(self):
        return [_ex("get_urls"), _ex("write_texts"),
                _ex("create_files_spreadsheet",
                    affinity=["foglio", "calcolo", "spreadsheet", "excel"])]

    def test_different_name_equivalent_kills(self):
        self._seed_multistep()
        cat = self._catalog_with_equiv()
        rep = eng_fastpath.prune(
            catalog_names={e.name for e in cat}, catalog=cat, **self._AGING)
        # Il name-based NON l'ha colto (stem write_files assente)…
        self.assertEqual(rep["dead_superseded"], 0)
        # …il check-prefilter sì: routing → create_files_spreadsheet.
        self.assertEqual(rep["dead_superseded_prefilter"], 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_without_direct_executor_survives(self):
        self._seed_multistep()
        cat = [_ex("get_urls"), _ex("write_texts")]  # niente equivalente
        rep = eng_fastpath.prune(
            catalog_names={e.name for e in cat}, catalog=cat, **self._AGING)
        self.assertEqual(rep["dead_superseded_prefilter"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_single_step_immune(self):
        # Fastpath a UN solo step: il check-prefilter vale solo per i piani
        # multi-step (un singolo executor che ne rimpiazza una CATENA).
        intent = Intent(verb="write", object="files")
        eng_fastpath.record_success(self._Q, _fw("write_texts"),
                                    intent=intent)
        cat = self._catalog_with_equiv()
        rep = eng_fastpath.prune(
            catalog_names={e.name for e in cat}, catalog=cat, **self._AGING)
        self.assertEqual(rep["dead_superseded_prefilter"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_top_in_plan_family_survives_unit(self):
        # Unit su _prefilter_supersedes: se il top-1 del routing appartiene
        # alla famiglia di un tool del piano (variante provider/qualifier),
        # il piano lo usa già → nessun oscuramento, nessuna morte.
        cat = self._catalog_with_equiv()
        self.assertEqual(
            eng_fastpath._prefilter_supersedes(
                self._Q, "write", "files",
                ["get_urls", "create_files_spreadsheet"], cat),
            "")

    def test_injected_top_does_not_kill_unit(self):
        # Unit: top-1 da injection del prefilter (precursor/primary, verbo
        # NON dell'intent né sibling) non "implementa l'intent" → ''.
        # Catalogo senza alcun write_*/create_*: rank torna None o injected.
        cat = [_ex("get_urls"), _ex("find_files")]
        self.assertEqual(
            eng_fastpath._prefilter_supersedes(
                self._Q, "write", "files",
                ["get_urls", "write_texts"], cat),
            "")

    def test_no_catalog_objects_skips_check(self):
        # catalog objects non disponibili (None) → check saltato: meglio
        # nessuna morte che falsi kill (§2.8).
        self.assertEqual(
            eng_fastpath._prefilter_supersedes(
                self._Q, "write", "files", ["get_urls", "write_texts"],
                None),
            "")


class _InheritanceCase(_FastpathDbCase):
    """DB fastpath + DB executor_stats entrambi isolati per-test."""

    def setUp(self):
        super().setUp()
        import executor_aging
        self._ea = executor_aging
        self._ea_patch = mock.patch.object(
            executor_aging, "DB_PATH", Path(self.tmp) / "executor_stats.db")
        self._ea_patch.start()

    def tearDown(self):
        self._ea_patch.stop()
        super().tearDown()

    def _set_uses(self, fp_id: int, n: int) -> None:
        c = eng_fastpath._conn()
        c.execute("UPDATE fastpaths SET n_uses = ? WHERE id = ?", (n, fp_id))
        c.commit()
        c.close()

    def _calls(self, name: str) -> int:
        st = self._ea.lookup(name)
        return st.total_calls if st else 0


class TestInheritance(_InheritanceCase):
    """EREDITÀ-PUNTI: la morte per superamento (name-based / provenienza /
    check-prefilter) trasferisce gli n_uses del fastpath all'erede nel
    deposito executor_stats (executor_aging) — quello che aging/promozione
    leggono."""

    _AGING = dict(stale_days=30, grace_days=14, max_rows=500, now_ts=_NOW)

    def test_name_based_death_inherits_uses(self):
        intent = Intent(verb="compress", object="images")
        fp_id = eng_fastpath.record_success(
            "comprimi le foto di marzo",
            _fw("find_images", "compress_files_zip"), intent=intent)
        self._set_uses(fp_id, 5)
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "compress_files_zip",
                           "compress_images"}, **self._AGING)
        self.assertEqual(rep["dead_superseded"], 1)
        self.assertEqual(rep["inherited_uses"], 5)
        self.assertEqual(self._calls("compress_images"), 5)

    def test_prefilter_death_inherits_uses(self):
        intent = Intent(verb="write", object="files")
        fp_id = eng_fastpath.record_success(
            "salva la lista della spesa in un foglio di calcolo",
            _fw("get_urls", "write_texts"), intent=intent)
        self._set_uses(fp_id, 3)
        cat = [_ex("get_urls"), _ex("write_texts"),
               _ex("create_files_spreadsheet",
                   affinity=["foglio", "calcolo", "spreadsheet"])]
        rep = eng_fastpath.prune(
            catalog_names={e.name for e in cat}, catalog=cat, **self._AGING)
        self.assertEqual(rep["dead_superseded_prefilter"], 1)
        self.assertEqual(rep["inherited_uses"], 3)
        self.assertEqual(self._calls("create_files_spreadsheet"), 3)

    def test_provenance_death_inherits_uses(self):
        intent = Intent(verb="compress", object="images")
        fp_id = eng_fastpath.record_success(
            "comprimi le foto di marzo",
            _fw("find_images", "compress_files_zip"), intent=intent)
        self._set_uses(fp_id, 7)
        eng_fastpath.record_promotion(
            "compress_images_indices", [(fp_id, "")])
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "compress_files_zip",
                           "compress_images_indices"}, **self._AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        self.assertEqual(rep["inherited_uses"], 7)
        self.assertEqual(self._calls("compress_images_indices"), 7)

    def test_missing_tool_death_no_heir(self):
        fp_id = eng_fastpath.record_success(
            "comando con tool ritirato", _fw("ghost_tool"))
        self._set_uses(fp_id, 9)
        rep = eng_fastpath.prune(catalog_names={"get_now"}, **self._AGING)
        self.assertEqual(rep["dead_missing_tool"], 1)
        self.assertEqual(rep["inherited_uses"], 0)  # nessun erede
        self.assertEqual(self._ea.all_stats(), [])  # deposito intatto

    def test_zero_uses_inherits_nothing(self):
        intent = Intent(verb="compress", object="images")
        eng_fastpath.record_success(
            "comprimi le foto di marzo",
            _fw("find_images", "compress_files_zip"), intent=intent)
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "compress_files_zip",
                           "compress_images"}, **self._AGING)
        self.assertEqual(rep["dead_superseded"], 1)
        self.assertEqual(rep["inherited_uses"], 0)
        self.assertEqual(self._ea.all_stats(), [])  # mai-usato → zero punti


if __name__ == "__main__":
    unittest.main()
