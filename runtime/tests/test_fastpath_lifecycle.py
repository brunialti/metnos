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
        eng_fastpath._DB_INIT_DONE = False

    def tearDown(self):
        eng_fastpath._db_path = self._orig
        eng_fastpath._DB_INIT_DONE = False


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


if __name__ == "__main__":
    unittest.main()
