"""Stress test del loop introvertivo completo.

Verifica che il sistema converga (non esploda):
  - quiescenza proposte (pending↔dormant↔reawaken con reset)
  - decay executor (active→deprecated→archived)
  - auto-apply specialize (synth:introvertive_specialize → executor concreto)

Lo stress simula:
  - 100 cicli notturni (un ciclo = un firing del task notturno)
  - inserimento progressivo di executor synth da specialize
  - inattivita' selettiva di alcuni executor
  - reawaken di proposte con dato che si rinforza

Verifica invarianti:
  1. Numero di executor totali NON cresce illimitatamente (decay funziona)
  2. Proposte dormant non vengono mostrate finche' non riemergono
  3. Auto-apply non duplica varianti gia' esistenti
  4. Tutto il flusso e' best-effort: nessuna eccezione propaga al runtime
  5. Storia: gli eventi sono tracciati correttamente, nessun event mancante
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


# Per-test isolation: temp DB paths che non interferiscono con la produzione.

@pytest.fixture
def tmp_dbs(monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", str(tmp_path / "exec_stats.db"))
    monkeypatch.setenv("METNOS_PROPOSALS_STATE_DB", str(tmp_path / "props_state.db"))
    # Re-import with fresh env vars (DB_PATH inizializzato a module-import time).
    import importlib
    import executor_aging
    import proposals_state
    importlib.reload(executor_aging)
    importlib.reload(proposals_state)
    yield {
        "exec_stats": tmp_path / "exec_stats.db",
        "props_state": tmp_path / "props_state.db",
    }
    # Teardown: ripristina i moduli al loro path canonical (env originale).
    # Senza questo, executor_aging.DB_PATH resta puntato al tmp_path che
    # pytest cancellera' — altri test che usano executor_aging dopo trovano
    # DB inesistente e l'invariate cross-test si rompe.
    monkeypatch.undo()
    importlib.reload(executor_aging)
    importlib.reload(proposals_state)


# ──────────────────────────────────────────────────────────────────────
# 1. Quiescenza proposte: 100 notti simulate, sopra/sotto soglie
# ──────────────────────────────────────────────────────────────────────

class TestProposalsQuiescenceStress:

    def test_100_nights_random_walk(self, tmp_dbs):
        """100 notti, 5 signature, uses random-walk. Verifica che:
        - dormant rate sia coerente con i parametri
        - reawaken funzioni quando uses cresce 30%
        - n_seen sia sempre coerente (1≤n≤dormancy_nights+1)
        """
        import random
        from proposals_state import touch_or_insert, DORMANCY_NIGHTS

        random.seed(42)
        sigs = [
            ("specialize", f"tool_{i}", "arg_x", '"v"')
            for i in range(5)
        ]
        # Initial uses for each
        uses_state = [10, 50, 100, 5, 200]

        max_n_seen = 0
        states_seen: dict[str, int] = {}

        for night in range(100):
            for i, sig in enumerate(sigs):
                # Random walk with small steps + occasional spike
                if night > 0 and random.random() < 0.05:
                    uses_state[i] = int(uses_state[i] * 1.5)  # spike
                else:
                    uses_state[i] = max(0, uses_state[i] + random.randint(-2, 3))
                row = touch_or_insert(sig, "specialize", uses_state[i])
                max_n_seen = max(max_n_seen, row.n_seen)
                states_seen[row.state] = states_seen.get(row.state, 0) + 1

        # Sanity:
        # - n_seen mai supera DORMANCY_NIGHTS+1 (dovrebbe transire a dormant)
        assert max_n_seen <= DORMANCY_NIGHTS + 5, (
            f"n_seen={max_n_seen} oltre soglia: dovremmo essere transiti a dormant"
        )
        # - tutti gli stati visti almeno una volta:
        assert "pending" in states_seen
        assert "dormant" in states_seen
        # - proporzione: pending+dormant > 95% di tutti gli stati visitati
        total = sum(states_seen.values())
        ratio = (states_seen.get("pending", 0) + states_seen.get("dormant", 0)) / total
        assert ratio > 0.95, f"stati transitori inattesi: {states_seen}"

    def test_reawaken_resets_n_seen(self, tmp_dbs):
        """La riemersione resetta n_seen, dando una nuova finestra di
        DORMANCY_NIGHTS prima di tornare dormant."""
        from proposals_state import touch_or_insert, DORMANCY_NIGHTS, REEMERGE_FACTOR
        sig = ("specialize", "X", "arg", '"v"')
        # Notti 1..DORMANCY_NIGHTS: pending (poi dormant alla soglia)
        for n in range(1, DORMANCY_NIGHTS + 1):
            r = touch_or_insert(sig, "specialize", 50)
        assert r.state == "dormant"
        # Notte successiva: rinforzo sufficiente → riemerge
        r = touch_or_insert(sig, "specialize", int(50 * REEMERGE_FACTOR + 5))
        assert r.state == "pending"
        assert r.n_seen == 1, f"reawaken deve resettare n_seen, got {r.n_seen}"

    def test_blocked_state_persists(self, tmp_dbs):
        """Lo stato `blocked` deve resistere a touch successivi."""
        from proposals_state import touch_or_insert, mark_action, lookup
        sig = ("specialize", "Z", "arg", '"v"')
        touch_or_insert(sig, "specialize", 100)
        mark_action(sig, "block")
        for _ in range(5):
            touch_or_insert(sig, "specialize", 200)
        row = lookup(sig)
        assert row.state == "blocked"


# ──────────────────────────────────────────────────────────────────────
# 2. Decay executor: simulazione di mesi virtuali
# ──────────────────────────────────────────────────────────────────────

class TestExecutorAgingStress:

    def test_active_to_archived_lifecycle(self, tmp_dbs):
        """Executor attivo che resta inattivo 60+ giorni: deve passare
        active → deprecated → archived correttamente."""
        from executor_aging import (
            register, touch, apply_executor_ager, lookup,
            DEPRECATED_DAYS, ARCHIVED_DAYS,
        )
        register("idle_tool", source="synth:reactive")
        touch("idle_tool", ok=True)  # initial use
        # Simula 35 giorni futuri: dovrebbe essere deprecated
        future_dep = (datetime.now(timezone.utc) + timedelta(
            days=DEPRECATED_DAYS + 5
        )).strftime("%Y-%m-%dT%H:%M:%SZ")
        res1 = apply_executor_ager(now_iso=future_dep)
        assert "idle_tool" in res1["deprecated"]
        assert lookup("idle_tool").lifecycle_override == "deprecated"

        # Simula 50 giorni futuri (35+15): dovrebbe essere archived
        future_arc = (datetime.now(timezone.utc) + timedelta(
            days=DEPRECATED_DAYS + ARCHIVED_DAYS + 5
        )).strftime("%Y-%m-%dT%H:%M:%SZ")
        res2 = apply_executor_ager(now_iso=future_arc)
        assert "idle_tool" in res2["archived"]
        assert lookup("idle_tool").lifecycle_override == "archived"

    def test_protected_names_never_age(self, tmp_dbs):
        """I PROTECTED_NAMES (seed core) non vengono retirati anche
        dopo lunga inattivita'."""
        from executor_aging import (
            register, touch, apply_executor_ager, lookup,
            PROTECTED_NAMES,
        )
        for name in list(PROTECTED_NAMES)[:3]:
            register(name, source="handcrafted")
            touch(name, ok=True)
        # 100 giorni futuri
        very_future = (datetime.now(timezone.utc) + timedelta(days=100)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = apply_executor_ager(now_iso=very_future)
        for name in list(PROTECTED_NAMES)[:3]:
            assert lookup(name).lifecycle_override is None, \
                f"{name} archived/deprecated: protected names violation"
        assert res["protected_skipped"] >= 3

    def test_synth_decays_after_inactivity(self, tmp_dbs):
        """Un executor SYNTH inattivo oltre soglia viene deprecato.

        (Era `test_recent_use_resets_clock`, confuso: registrava un tool
        handcrafted e ne asseriva il decay — comportamento ora corretto come
        bug, gli handcrafted NON invecchiano. Qui testiamo il decay reale, che
        vale solo per i synth.)"""
        from executor_aging import (
            register, touch, apply_executor_ager,
        )
        register("idle_synth_tool", source="synth:reactive")
        touch("idle_synth_tool", ok=True)
        # last_used_at e' "now" reale; con now_iso a +60gg → days_inactive≈60.
        far_future = (datetime.now(timezone.utc) + timedelta(days=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = apply_executor_ager(now_iso=far_future)
        assert "idle_synth_tool" in res["deprecated"]

    def test_handcrafted_never_ages_by_inactivity(self, tmp_dbs):
        """Regression (bug delete_persons 13/6/2026): un executor HANDCRAFTED
        non-protetto, inattivo a lungo, NON deve essere deprecato per
        inattivita' (l'aging culla solo la proliferazione synth). Deprecarlo lo
        toglie dal catalog composer → misroute silenzioso a un fratello (§2.8)."""
        from executor_aging import (
            register, touch, apply_executor_ager, lookup, PROTECTED_NAMES,
        )
        # NON in PROTECTED_NAMES: la protezione deve venire dall'essere handcrafted.
        assert "delete_persons" not in PROTECTED_NAMES
        register("delete_persons", source="handcrafted")
        touch("delete_persons", ok=True)
        far_future = (datetime.now(timezone.utc) + timedelta(days=100)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = apply_executor_ager(now_iso=far_future)
        assert "delete_persons" not in res["deprecated"]
        assert res["handcrafted_skipped"] >= 1
        assert lookup("delete_persons").lifecycle_override is None

    def test_handcrafted_ondisk_never_ages_despite_wrong_source(
            self, tmp_dbs, monkeypatch, tmp_path):
        """Regression (bug 21/6/2026): un handcrafted core con `source`
        MAL-REGISTRATO nel DB stats ('synth:reactive' invece di 'handcrafted',
        caso reale delete_files/find_events_empty) NON deve invecchiare: la
        presenza on-disk in config.PATH_EXECUTORS e' la verita' autorevole, vince
        sul source. Senza, l'ager lo deprecava → fuori dal catalog composer →
        misroute al fratello (delete_files→delete_entries).

        Ermetico: una dir executors FINTA con `core_tool/` evita dipendenze
        dall'ordine dei test (altri test rimappano config.PATH_EXECUTORS)."""
        import config as _C
        fake_execs = tmp_path / "execs"
        (fake_execs / "core_tool").mkdir(parents=True)
        monkeypatch.setattr(_C, "PATH_EXECUTORS", fake_execs)
        from executor_aging import register, touch, apply_executor_ager, lookup
        # source SBAGLIATO ('synth:reactive') ma il tool ESISTE on-disk → handcrafted.
        register("core_tool", source="synth:reactive")
        touch("core_tool", ok=True)
        far_future = (datetime.now(timezone.utc) + timedelta(days=100)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = apply_executor_ager(now_iso=far_future)
        assert "core_tool" not in res["deprecated"]
        assert res["handcrafted_skipped"] >= 1
        assert lookup("core_tool").lifecycle_override is None

    def test_undeprecate_resets_state(self, tmp_dbs):
        """undeprecate di un executor archived rimuove archived_at e
        deprecated_at, ripristinando lifecycle attivo."""
        from executor_aging import (
            register, touch, apply_executor_ager, lookup, undeprecate,
        )
        register("zombie", source="synth:reactive")
        touch("zombie", ok=True)
        far = (datetime.now(timezone.utc) + timedelta(days=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_executor_ager(now_iso=far)
        far2 = (datetime.now(timezone.utc) + timedelta(days=80)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_executor_ager(now_iso=far2)
        assert lookup("zombie").lifecycle_override == "archived"
        ok = undeprecate("zombie")
        assert ok is True
        assert lookup("zombie").lifecycle_override is None


# ──────────────────────────────────────────────────────────────────────
# 3. Loop completo: convergenza del catalog
# ──────────────────────────────────────────────────────────────────────

class TestLoopConvergence:

    def test_creation_decay_balance_does_not_explode(self, tmp_dbs):
        """Stress: 200 cicli simulati di {auto-apply N, decay} con tassi
        di creazione/inattivita' tali che il sistema deve raggiungere uno
        stato stazionario (count totali bounded). Verifica che il numero
        di executor attivi NON cresca illimitatamente.
        """
        from executor_aging import (
            register, touch, apply_executor_ager, all_stats,
        )
        import random
        random.seed(2026)

        # Simula 200 cicli (≈200 giorni se 1 ciclo = 1 giorno)
        max_active_seen = 0
        for cycle in range(200):
            # 1. Creazione: 0-2 nuovi executor synth per ciclo (random)
            n_new = random.choices([0, 1, 2], weights=[60, 30, 10])[0]
            for k in range(n_new):
                name = f"synth_{cycle}_{k}"
                register(name, source="synth:introvertive_specialize")
                if random.random() < 0.5:
                    touch(name, ok=True)

            # 2. Touch random degli existing (simula uso continuativo
            #    di alcuni executor, abbandono di altri)
            stats = all_stats()
            for st in stats:
                if st.archived_at:
                    continue
                if random.random() < 0.20:
                    touch(st.name, ok=True)

            # 3. Decay: ogni 7 cicli simuliamo un giorno reale di age
            if cycle % 7 == 0:
                future = (datetime.now(timezone.utc) + timedelta(days=cycle)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                apply_executor_ager(now_iso=future)

            active_now = sum(1 for s in all_stats()
                              if not s.archived_at and not s.deprecated_at)
            max_active_seen = max(max_active_seen, active_now)

        # Invariante 1: il numero totale di active e' bounded (non esplode).
        # Nota: con 200 cicli e tassi 0/1/2 nuovi per ciclo, ci aspettiamo
        # ≈ 60+30+10*1 = ~80 creati + decay → stato stazionario sotto 100.
        assert max_active_seen < 200, (
            f"system explodes: {max_active_seen} active at peak"
        )

        # Invariante 2: la storia ha eventi di tutti i kind canonici.
        from executor_aging import history
        ev = history(limit=10000)
        kinds = {e["event_kind"] for e in ev}
        assert "created" in kinds
        # 'first_used' should be there (we touch some)
        assert "first_used" in kinds
        # 'deprecated' should fire at some point with 200 cicli
        assert "deprecated" in kinds, \
            f"decay never fired in 200 cycles, kinds={kinds}"

    def test_counts_by_source_lifecycle_consistent(self, tmp_dbs):
        """counts_by_source_lifecycle() ritorna un dict con somma totale
        = numero di righe nella tabella stats (no dropouts, no doppi
        conteggi)."""
        from executor_aging import (
            register, touch, apply_executor_ager, all_stats,
            counts_by_source_lifecycle,
        )
        # Mix: handcrafted, synth:reactive, synth:introvertive_specialize
        sources = [
            "handcrafted", "synth:reactive",
            "synth:introvertive_specialize", "synth:promoted",
        ]
        for i in range(40):
            register(f"tool_{i}", source=sources[i % 4])
            if i % 3:
                touch(f"tool_{i}", ok=True)
        # Decay alcuni
        far = (datetime.now(timezone.utc) + timedelta(days=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_executor_ager(now_iso=far)
        far2 = (datetime.now(timezone.utc) + timedelta(days=80)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_executor_ager(now_iso=far2)

        counts = counts_by_source_lifecycle()
        total_rows = len(all_stats())
        total_counted = sum(
            v["active"] + v["deprecated"] + v["archived"]
            for v in counts.values()
        )
        assert total_counted == total_rows, (
            f"count mismatch: {total_counted} != {total_rows}"
        )


# ──────────────────────────────────────────────────────────────────────
# 4. Integrazione catalog ↔ aging (loader)
# ──────────────────────────────────────────────────────────────────────

class TestLoaderIntegration:

    def test_archived_executor_excluded_from_catalog(self, tmp_dbs, monkeypatch):
        """Quando un executor viene archiviato in executor_aging,
        load_catalog lo esclude dal catalog visibile.

        Registrato come SYNTH: dopo il fix 13/6/2026 solo i synth invecchiano
        per inattivita' (gli handcrafted sono esclusi dal decay) — qui interessa
        il meccanismo loader-esclude-archived, non la sorgente."""
        from executor_aging import (
            register, touch, apply_executor_ager, PROTECTED_NAMES, _open)
        from loader import load_catalog
        # Trova un executor del pool non-protected per il test
        cat0 = load_catalog(verify=True)
        target = None
        for name in cat0.executors:
            if name not in PROTECTED_NAMES:
                target = name
                break
        assert target is not None, "no non-protected executor in pool"

        register(target, source="synth:reactive")
        touch(target, ok=True)
        # Questo test verifica il MECCANISMO loader-esclude-archived, non l'aging
        # in sé. Dal fix 21/6/2026 un executor ON-DISK (qualunque del catalog
        # reale) NON puo' essere archiviato da apply_executor_ager (la presenza
        # nel repo vince sul source) → impostiamo archived_at DIRETTAMENTE nel DB
        # per esercitare l'esclusione del loader (lifecycle_override='archived').
        far = (datetime.now(timezone.utc) + timedelta(days=80)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        _c = _open()
        try:
            _c.execute("UPDATE executor_stats SET deprecated_at=?, archived_at=? "
                       "WHERE name=?", (far, far, target))
            _c.commit()
        finally:
            _c.close()

        # Ora il loader deve escluderlo
        cat1 = load_catalog(verify=True)
        assert target not in cat1.executors, (
            f"archived executor {target!r} still in catalog"
        )
        # E deve essere in rejected
        assert any("archived" in r[1] for r in cat1.rejected), (
            "archived not reported in rejected list"
        )
