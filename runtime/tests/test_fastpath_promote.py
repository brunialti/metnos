"""test_fastpath_promote.py — promozione fastpath L0 → executor synt (11/6/2026).

Copre il mandato promozione:
  1. PROVENIENZA — l'executor promosso porta i fp_id/hash di origine; la
     morte C2 diventa ESATTA per provenienza (non più solo name-based).
  2. DETECTION — cluster semantici (shape+intent) con gating conservativo:
     i candidati deboli (mono-step, <3 distinti, poco usati, giovani) sono
     RIFIUTATI (test anti-esplosione).
  3. TIER 1 — proposta emessa nel canale introvertiva (proposals_state),
     dedupe per sig_key + vs catalog + vs generalize pendenti.
  4. TIER 2 — auto-promozione dietro flag (OFF default), floor alto,
     hard cap 1/notte, attraverso la pipeline synt (handle_synth_request).

Mock everywhere — no live LLM, no live executor, no BGE-M3.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Framework, Intent, StepSpec
from engine import fastpath as eng_fastpath
from engine import fastpath_promote as promote
import proposals_state as ps


def _fw(*tools, args_map=None, final="fatto"):
    args_map = args_map or {}
    steps = [StepSpec(tool=t, args=dict(args_map.get(t, {}))) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps, final_message=final)


_NOW = 1_780_000_000.0  # epoch fisso (§7.9)
_AGING = dict(stale_days=30, grace_days=14, max_rows=500, now_ts=_NOW)


def _iso_days_ago(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(_NOW - days * 86400))


class _FastpathDbCase(unittest.TestCase):
    """DB fastpath isolato per-test (stesso pattern di test_fastpath_lifecycle)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"
        eng_fastpath._DB_INIT_DONE = False

    def tearDown(self):
        eng_fastpath._db_path = self._orig
        eng_fastpath._DB_INIT_DONE = False

    def _seed(self, query: str, fw: Framework, *, intent=None,
              created_days_ago: float = 0.0, n_uses: int = 0,
              used_days_ago: float | None = 1.0) -> tuple[int, str]:
        """record_success + retrodatazione/usi. Ritorna (fp_id, canonical_hash)."""
        fp_id = eng_fastpath.record_success(query, fw, intent=intent)
        self.assertGreater(fp_id, 0)
        c = eng_fastpath._conn()
        c.execute(
            "UPDATE fastpaths SET created_at = ?, n_uses = ?, last_used = ? "
            "WHERE id = ?",
            (_iso_days_ago(created_days_ago), n_uses,
             _iso_days_ago(used_days_ago) if used_days_ago is not None
             else None, fp_id))
        chash = c.execute("SELECT canonical_hash FROM fastpaths WHERE id = ?",
                          (fp_id,)).fetchone()[0]
        c.commit()
        c.close()
        return fp_id, chash


# ── 1. Provenienza → morte C2 esatta ───────────────────────────────────────

class TestProvenanceDeath(_FastpathDbCase):
    """L'executor promosso porta i fp_id di origine: quando entra nel
    catalog, i fastpath di provenienza muoiono ESATTAMENTE (anche dove il
    match name-based C2 era un falso-negativo)."""

    def test_promoted_executor_kills_member_exact(self):
        # Composizione: il piano USA find_images (famiglia dell'intent) →
        # name-C2 immune per costruzione. La provenienza chiude il buco.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite al mare",
            _fw("find_persons", "find_images"), intent=intent)
        n = eng_fastpath.record_promotion(
            "find_images_similar", [(fp_id, chash)], tier=2)
        self.assertEqual(n, 1)
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        self.assertEqual(rep["dead_superseded"], 0)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_promotion_inert_until_executor_in_catalog(self):
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite al mare",
            _fw("find_persons", "find_images"), intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_promoted_in_plan_immune(self):
        # Il fastpath NUOVO che usa l'executor promosso NON deve morire
        # (si ri-creerebbe in loop): immunità se il piano lo contiene.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite al mare", _fw("find_images_similar"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_provenance_survives_recreation_via_hash(self):
        # Il fastpath muore (prune/delete) e si RI-CREA con fp_id nuovo:
        # stessa query → stesso canonical_hash → provenienza ancora valida.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite al mare",
            _fw("find_persons", "find_images"), intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        eng_fastpath.delete(fp_id)
        fp_id2, chash2 = self._seed(
            "foto di ospite al mare",
            _fw("find_persons", "find_images"), intent=intent)
        self.assertNotEqual(fp_id, fp_id2)
        self.assertEqual(chash, chash2)
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_provenance_exact_only_members_die(self):
        # Esattezza: muore SOLO il membro registrato, non il vicino con
        # stesso shape ma fuori dalla provenienza (e intent diverso).
        intent = Intent(verb="find", object="images")
        member_id, member_hash = self._seed(
            "foto di ospite al mare",
            _fw("find_persons", "find_images"), intent=intent)
        outsider_id, _ = self._seed(
            "cerca documenti di marco",
            _fw("find_persons", "find_files"))  # intent assente
        eng_fastpath.record_promotion("find_images_similar",
                                      [(member_id, member_hash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images", "find_files",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        ids = {r["id"] for r in eng_fastpath.list_all()}
        self.assertEqual(ids, {outsider_id})

    def test_record_promotion_idempotent(self):
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite", _fw("find_persons", "find_images"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)], tier=1)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)], tier=2)
        rows = eng_fastpath.list_promotions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], 2)  # refresh aggiorna il tier

    def test_no_catalog_no_provenance_death(self):
        # catalog_names=None → nessuna morte, nemmeno per provenienza (§2.8).
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di ospite", _fw("find_persons", "find_images"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(catalog_names=None, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)


# ── 2-4. Detection + Tier 1 + Tier 2 ───────────────────────────────────────

# Catalog di comodo: contiene i tool delle catene seminate, NON gli stem
# degli intent (così i candidati sono 'free' se non dichiarato altrimenti).
_CATALOG = {"find_persons", "find_images", "find_files", "filter_entries",
            "compress_files_zip", "read_messages", "send_messages",
            "get_now"}


class _PromoteCase(_FastpathDbCase):
    """Isola ANCHE proposals_state.db (oltre al DB fastpath)."""

    def setUp(self):
        super().setUp()
        self._ps_orig = ps.DB_PATH
        ps.DB_PATH = Path(self.tmp) / "proposals_state.db"

    def tearDown(self):
        ps.DB_PATH = self._ps_orig
        super().tearDown()

    def _seed_cluster(self, n: int, base_query: str, chain: tuple[str, ...],
                      intent: Intent, *, uses_each: int = 7,
                      age_days: float = 45.0) -> list[tuple[int, str]]:
        """N fastpath DISTINTI (query diverse) con stessa shape+intent."""
        members = []
        for i in range(n):
            members.append(self._seed(
                f"{base_query} variante {i}", _fw(*chain), intent=intent,
                created_days_ago=age_days - i,  # il più vecchio fissa l'età
                n_uses=uses_each))
        return members

    def _ps_row(self, sig_key):
        conn = ps._open()
        try:
            return conn.execute(
                "SELECT * FROM proposals_state WHERE sig_key = ?",
                (ps._canonical(sig_key),)).fetchone()
        finally:
            conn.close()

    def _run(self, **kw):
        kw.setdefault("catalog_names", _CATALOG)
        kw.setdefault("now_ts", _NOW)
        return promote.run_nightly(**kw)


class TestDetectionGating(_PromoteCase):
    """ANTI-ESPLOSIONE: i candidati deboli sono rifiutati, con conteggi."""

    def test_rejects_mono_step(self):
        # 5 fastpath mono-step pesantissimi: l'executor c'è già, il valore
        # L0 è saltare l'LLM, non il piano → MAI candidati.
        for i in range(5):
            self._seed(f"che ore sono {i}", _fw("get_now"),
                       intent=Intent(verb="get", object="numbers"),
                       created_days_ago=60, n_uses=100)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["mono_step"], 5)

    def test_rejects_small_cluster(self):
        # 2 fastpath distinti (< 3): cluster troppo piccolo.
        self._seed_cluster(2, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=50, age_days=60)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["too_few_members"], 1)

    def test_rejects_low_usage(self):
        # 3 distinti ma 2 usi l'uno (6 < 15): poco usati.
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=2, age_days=60)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["low_usage"], 1)

    def test_rejects_young_cluster(self):
        # 3 distinti, 60 usi, ma il più vecchio ha 10 giorni (< 30).
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=20, age_days=10)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["too_young"], 1)

    def test_rejects_non_promotable_chain(self):
        # La catena contiene un meta-tool del runtime: mai wrappabile.
        self._seed_cluster(3, "monta la share",
                           ("find_files", "admin"),
                           Intent(verb="read", object="files"),
                           uses_each=20, age_days=60)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["non_promotable_tool"], 1)

    def test_rejects_missing_intent(self):
        for i in range(3):
            self._seed(f"richiesta opaca {i}",
                       _fw("find_files", "filter_entries"),
                       created_days_ago=60, n_uses=20)  # intent assente
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["no_intent"], 3)

    def test_rejects_already_covered(self):
        # La famiglia dell'intent è nel catalog ma NON nella catena: la
        # morte C2 name-based poterà questi fastpath — proporre = duplicato.
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=20, age_days=60)
        det = promote.detect_candidates(
            catalog_names=_CATALOG | {"compress_images"}, now_ts=_NOW)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["rejected"]["already_covered"], 1)

    def test_accepts_valid_cluster_free(self):
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=7, age_days=45)  # 21 usi ≥ 15
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(len(det["candidates"]), 1)
        cand = det["candidates"][0]
        self.assertEqual(cand["expected_name"], "compress_images")
        self.assertEqual(cand["kind"], "free")
        self.assertEqual(cand["n_distinct"], 3)
        self.assertEqual(cand["cum_uses"], 21)
        self.assertEqual(cand["chain"],
                         ["find_images", "compress_files_zip"])

    def test_composition_flagged(self):
        # La catena USA già la famiglia dell'intent (find_images): macro di
        # tool esistenti → kind='composition' (solo tier 1, nome umano).
        self._seed_cluster(3, "foto delle persone",
                           ("find_persons", "find_images"),
                           Intent(verb="find", object="images"),
                           uses_each=7, age_days=45)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual(len(det["candidates"]), 1)
        self.assertEqual(det["candidates"][0]["kind"], "composition")

    def test_population_mista_solo_il_forte_passa(self):
        # Popolazione eterogenea (12 fastpath): SOLO il cluster forte passa.
        # 5 mono-step
        for i in range(5):
            self._seed(f"che ore sono {i}", _fw("get_now"),
                       intent=Intent(verb="get", object="numbers"),
                       created_days_ago=60, n_uses=100)
        # cluster di 2 (piccolo)
        self._seed_cluster(2, "manda le mail",
                           ("read_messages", "send_messages"),
                           Intent(verb="send", object="messages"),
                           uses_each=30, age_days=60)
        # cluster giovane
        self._seed_cluster(3, "filtra i log",
                           ("find_files", "filter_entries"),
                           Intent(verb="filter", object="files"),
                           uses_each=20, age_days=5)
        # cluster valido
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=7, age_days=45)
        det = promote.detect_candidates(catalog_names=_CATALOG, now_ts=_NOW)
        self.assertEqual([c["expected_name"] for c in det["candidates"]],
                         ["compress_images"])
        self.assertEqual(det["rejected"]["mono_step"], 5)
        self.assertEqual(det["rejected"]["too_few_members"], 1)
        self.assertEqual(det["rejected"]["too_young"], 1)
        self.assertEqual(det["scanned"], 13)


class TestTier1Proposal(_PromoteCase):
    """La proposta è emessa nel canale introvertiva (proposals_state),
    dedupe per sig_key + vs generalize, cap nuove emissioni, provenienza."""

    def _valid_cluster(self, **kw):
        return self._seed_cluster(
            3, kw.pop("base", "comprimi le foto"),
            kw.pop("chain", ("find_images", "compress_files_zip")),
            kw.pop("intent", Intent(verb="compress", object="images")),
            uses_each=kw.pop("uses_each", 7),
            age_days=kw.pop("age_days", 45))

    def test_emits_proposal_in_backlog(self):
        members = self._valid_cluster()
        rep = self._run()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["emitted"], ["compress_images"])
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        row = self._ps_row(sig)
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "fastpath_promote")
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["last_uses"], 21)
        # Provenienza registrata per il candidato 'free' (tier 1).
        promos = eng_fastpath.list_promotions()
        self.assertEqual(len(promos), 3)
        self.assertEqual({p["executor_name"] for p in promos},
                         {"compress_images"})
        self.assertEqual({p["fp_id"] for p in promos},
                         {m[0] for m in members})

    def test_second_night_refreshes_not_duplicates(self):
        self._valid_cluster()
        rep1 = self._run()
        rep2 = self._run()
        self.assertEqual(rep1["emitted"], ["compress_images"])
        self.assertEqual(rep2["emitted"], [])
        self.assertEqual(rep2["refreshed"], ["compress_images"])
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        row = self._ps_row(sig)
        self.assertEqual(row["n_seen"], 2)
        conn = ps._open()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM proposals_state").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1)

    def test_no_catalog_no_emission(self):
        self._valid_cluster()
        rep = self._run(catalog_names=None)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["reason"], "catalog_unavailable")
        conn = ps._open()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM proposals_state").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_cap_new_emissions_per_night(self):
        # 4 cluster validi, cap 3 nuove/notte: la 4ª (per usi) è rinviata,
        # la notte dopo entra (le prime 3 sono refresh, budget libero).
        specs = [
            ("comprimi le foto", ("find_images", "compress_files_zip"),
             Intent(verb="compress", object="images"), 20),
            ("manda il riassunto", ("read_messages", "send_messages"),
             Intent(verb="send", object="messages"), 15),
            ("filtra i log", ("find_files", "filter_entries"),
             Intent(verb="filter", object="files"), 10),
            ("descrivi le persone", ("find_persons", "find_files"),
             Intent(verb="describe", object="persons"), 5),
        ]
        for base, chain, intent, uses in specs:
            self._seed_cluster(3, base, chain, intent,
                               uses_each=uses, age_days=45)
        rep1 = self._run()
        self.assertEqual(len(rep1["emitted"]), 3)
        self.assertEqual(rep1["deferred_cap"], ["describe_persons"])
        rep2 = self._run()
        self.assertEqual(rep2["emitted"], ["describe_persons"])
        self.assertEqual(len(rep2["refreshed"]), 3)

    def test_dedupe_vs_pending_generalize(self):
        # Una GENERALIZE introvertiva pendente sulla stessa catena: la
        # nostra proposta sarebbe rumore doppio → skip.
        self._valid_cluster()
        ps.touch_or_insert(
            ["generalize", ["find_images", "compress_files_zip"]],
            "generalize", 10)
        rep = self._run()
        self.assertEqual(rep["emitted"], [])
        self.assertEqual(rep["rejected"]["pending_generalize"], 1)
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        self.assertIsNone(self._ps_row(sig))

    def test_blocked_proposal_not_resurrected(self):
        self._valid_cluster()
        self._run()
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        ps.mark_action(sig, "block")
        rep = self._run()
        self.assertEqual(rep["emitted"], [])
        row = self._ps_row(sig)
        self.assertEqual(row["state"], "blocked")

    def test_composition_no_provenance(self):
        # Composizione: il nome finale richiede qualifier umano → proposta
        # SÌ, provenienza automatica NO (documentato nel modulo).
        self._seed_cluster(3, "foto delle persone",
                           ("find_persons", "find_images"),
                           Intent(verb="find", object="images"),
                           uses_each=7, age_days=45)
        rep = self._run()
        self.assertEqual(rep["emitted"], ["find_images"])
        self.assertEqual(eng_fastpath.list_promotions(), [])
        self.assertEqual(rep["provenance_rows"], 0)


class TestApproveHook(_PromoteCase):
    """L'approve umano di una proposta fastpath_promote scrive il marker
    synt_pending (stesso canale accept→synth delle proposte introspettive,
    consumato da telos_synth_consumer → handle_synth_request)."""

    def setUp(self):
        super().setUp()
        import proposal_actions as pa
        self._pa = pa
        self._dir_patch = mock.patch.object(
            pa, "SYNT_PENDING_DIR", Path(self.tmp) / "synt_pending")
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        super().tearDown()

    def _markers(self):
        d = Path(self.tmp) / "synt_pending"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def test_approve_free_writes_consumable_marker(self):
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=7, age_days=45)
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        out = promote.on_proposal_approved(ps._canonical(sig))
        self.assertEqual(out["kind"], "synt_pending")
        self.assertTrue(out["created"])
        markers = self._markers()
        self.assertEqual(len(markers), 1)
        import json as _json
        payload = _json.loads(markers[0].read_text())
        # Contratto del consumer: sig + expected_name + intent non vuoti.
        self.assertEqual(payload["expected_name"], "compress_images")
        self.assertTrue(payload["sig"])
        self.assertIn("find_images → compress_files_zip",
                      payload["intent"])
        # I sample sono il testo CANONICO del fastpath (normalize_query).
        self.assertIn("comprimi foto", payload["intent"])
        self.assertEqual(payload["kind"], "synt_request")

    def test_approve_idempotent_per_signature(self):
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        out1 = promote.on_proposal_approved(ps._canonical(sig))
        out2 = promote.on_proposal_approved(ps._canonical(sig))
        self.assertTrue(out1["created"])
        self.assertFalse(out2["created"])
        self.assertEqual(len(self._markers()), 1)

    def test_approve_composition_noop(self):
        sig = promote.sig_key_for(
            "find_images", ["find_persons", "find_images"])
        out = promote.on_proposal_approved(ps._canonical(sig))
        self.assertEqual(out["kind"], "noop")
        self.assertEqual(out["reason"], "composition_requires_human_naming")
        self.assertEqual(self._markers(), [])

    def test_approve_garbage_sig_noop(self):
        self.assertEqual(
            promote.on_proposal_approved("non-json")["kind"], "noop")
        self.assertEqual(
            promote.on_proposal_approved(
                ps._canonical(["generalize", ["a", "b"]]))["kind"], "noop")
        self.assertEqual(self._markers(), [])

    def test_unified_hub_accept_triggers_hook(self):
        # Percorso REALE dell'admin: emissione notturna → accept nell'hub
        # unificato → mark_action + marker synt_pending.
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=7, age_days=45)
        self._run()
        import proposals_unified as pu
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        prop_id = pu._intr_prop_id(ps._canonical(sig))
        rec = pu.apply_decision_unified(prop_id, "introvertiva", "accept",
                                        by="test")
        self.assertEqual(rec["native_state"], "applied")
        self.assertEqual(rec["operative_effect"]["kind"], "synt_pending")
        self.assertEqual(len(self._markers()), 1)

    def test_unified_target_extraction(self):
        import proposals_unified as pu
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        self.assertEqual(
            pu._intr_target_from_sigkey(ps._canonical(sig)),
            "compress_images")

    def test_describe_proposal_renders_name_and_chain(self):
        from proposals_unified import _describe_proposal
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        with mock.patch("messages.get",
                        side_effect=lambda code, **kw: f"{code}|{kw}"):
            desc = _describe_proposal("fastpath_promote",
                                      ps._canonical(sig))
        self.assertIn("MSG_PROP_FASTPATH_PROMOTE", desc)
        self.assertIn("compress_images", desc)
        self.assertIn("find_images → compress_files_zip", desc)


class _FakeSynth:
    """Stub del canale synt: registra le chiamate, risponde a copione."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {
            "ok": True, "installed": True, "synthesized": True,
            "proposed_name": "compress_images", "elapsed_s": 1.0}

    def __call__(self, args, *, user_query, **kw):
        self.calls.append({"args": args, "user_query": user_query})
        return dict(self.result)


class TestTier2Autopromote(_PromoteCase):
    """Auto-promozione: flag OFF default; floor alto; cap 1/notte;
    pipeline synt riusata; decisioni umane MAI scavalcate."""

    def _huge_cluster(self, base="comprimi le foto",
                      chain=("find_images", "compress_files_zip"),
                      intent=None, uses_each=12):
        # 5 membri × 12 usi = 60 ≥ 50; età 45g.
        return self._seed_cluster(
            5, base, chain,
            intent or Intent(verb="compress", object="images"),
            uses_each=uses_each, age_days=45)

    def _run_nights(self, n, **kw):
        rep = None
        for _ in range(n):
            rep = self._run(**kw)
        return rep

    def _with_synth(self, fake):
        return mock.patch.dict(
            sys.modules, {"synth_request": SimpleNamespace(
                handle_synth_request=fake)})

    _FLAG_ON = {"METNOS_FASTPATH_AUTOPROMOTE": "1"}

    def test_flag_off_by_default_no_synth(self):
        self._huge_cluster()
        fake = _FakeSynth()
        with self._with_synth(fake):
            rep = self._run_nights(4)
        self.assertFalse(rep["tier2"]["enabled"])
        self.assertEqual(fake.calls, [])

    def test_flag_on_floor_met_triggers_synt_once(self):
        members = self._huge_cluster()
        fake = _FakeSynth()
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            # Notti 1-2: n_seen < 3 → shape non ancora stabile, no synth.
            rep2 = self._run_nights(2)
            self.assertIsNone(rep2["tier2"]["attempted"])
            self.assertEqual(fake.calls, [])
            # Notte 3: n_seen=3 → floor completo → UNA chiamata synt.
            rep3 = self._run()
        self.assertEqual(rep3["tier2"]["attempted"], "compress_images")
        self.assertTrue(rep3["tier2"]["ok"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["args"]["expected_name"],
                         "compress_images")
        self.assertIn("find_images → compress_files_zip",
                      fake.calls[0]["args"]["intent"])
        # Provenienza tier 2 sotto il nome FINALE + proposta applied.
        promos = [p for p in eng_fastpath.list_promotions()
                  if p["tier"] == 2]
        self.assertEqual(len(promos), 5)
        self.assertEqual({p["fp_id"] for p in promos},
                         {m[0] for m in members})
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        self.assertEqual(self._ps_row(sig)["state"], "applied")

    def test_below_floor_no_synth_even_with_flag(self):
        # Passa il tier 1 (3 membri, 21 usi) ma NON il floor tier 2
        # (≥5 membri, ≥50 usi): l'auto non parte mai.
        self._seed_cluster(3, "comprimi le foto",
                           ("find_images", "compress_files_zip"),
                           Intent(verb="compress", object="images"),
                           uses_each=7, age_days=45)
        fake = _FakeSynth()
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            rep = self._run_nights(4)
        self.assertTrue(rep["tier2"]["enabled"])
        self.assertIsNone(rep["tier2"]["attempted"])
        self.assertEqual(rep["tier2"]["eligible"], [])
        self.assertEqual(fake.calls, [])

    def test_hard_cap_one_per_night(self):
        # DUE cluster 'free' sopra il floor: synt chiamato UNA volta sola
        # (il più usato), l'altro resta proposta tier 1.
        self._huge_cluster(uses_each=20)  # 100 usi
        self._huge_cluster(base="estrai gli eventi dalle mail",
                           chain=("read_messages", "filter_entries"),
                           intent=Intent(verb="extract", object="entries"),
                           uses_each=12)  # 60 usi
        fake = _FakeSynth()
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            rep = self._run_nights(3)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(rep["tier2"]["attempted"], "compress_images")
        self.assertEqual(set(rep["tier2"]["eligible"]),
                         {"compress_images", "extract_entries"})

    def test_human_block_stops_auto(self):
        self._huge_cluster()
        fake = _FakeSynth()
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            self._run_nights(2)
            sig = promote.sig_key_for(
                "compress_images",
                ["find_images", "compress_files_zip"])
            ps.mark_action(sig, "block")
            rep = self._run_nights(2)
        self.assertIsNone(rep["tier2"]["attempted"])
        self.assertEqual(fake.calls, [])

    def test_synth_failure_keeps_proposal_pending(self):
        self._huge_cluster()
        fake = _FakeSynth(result={"ok": False, "synthesized": False,
                                  "abandoned": True, "reason": "stage5"})
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            rep = self._run_nights(3)
        self.assertEqual(rep["tier2"]["attempted"], "compress_images")
        self.assertFalse(rep["tier2"]["ok"])
        self.assertEqual(len(fake.calls), 1)
        sig = promote.sig_key_for(
            "compress_images", ["find_images", "compress_files_zip"])
        self.assertNotEqual(self._ps_row(sig)["state"], "applied")
        self.assertEqual([p for p in eng_fastpath.list_promotions()
                          if p["tier"] == 2], [])

    def test_composition_never_autopromoted(self):
        # Composizione sopra ogni soglia numerica: l'auto NON parte mai
        # (nome finale = scelta umana).
        self._seed_cluster(5, "foto delle persone",
                           ("find_persons", "find_images"),
                           Intent(verb="find", object="images"),
                           uses_each=20, age_days=45)
        fake = _FakeSynth()
        with mock.patch.dict(os.environ, self._FLAG_ON), \
             self._with_synth(fake):
            rep = self._run_nights(4)
        self.assertIsNone(rep["tier2"]["attempted"])
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
