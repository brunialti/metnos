#!/usr/bin/env python3
"""stress_synt.py — stress test mnestoma + synt MVP.

Dimensioni testate:
- D-mn-scale:       scaling del mnestoma per N mnest (insert + top_k + walk)
- D-mn-decay:       ager su N mnest (decay massivo)
- D-synt-compose:   compose su grafo grande (latency vs depth)
- D-synt-suggest:   suggest su molti proto-mnest

Lo scopo NON è benchmarkare in millisecondi, ma:
- verificare che il sistema regge le scale (no crash, no timeout assurdi)
- identificare cliff non lineari prima della prossima fase
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _setup_env() -> tuple[str, dict]:
    base = tempfile.mkdtemp(prefix="metnos_stress_")
    env = {
        "MNESTOMA_DB_PATH": f"{base}/mn.sqlite",
        "SYNT_AUDIT_DIR": f"{base}/audit",
        "SYNT_LOCK_PATH": f"{base}/locks.json",
    }
    for k, v in env.items():
        os.environ[k] = v
    return base, env


def _human_n(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def stress_mnestoma_scale(sizes: list[int]) -> None:
    print("\n[D-mn-scale] insert + top_k + walk per N mnest")
    print(f"{'N':>8} {'insert_s':>10} {'topk_us':>10} {'walk_d2_us':>12} {'walk_d3_us':>12} {'db_kb':>8}")
    for N in sizes:
        base, _ = _setup_env()
        try:
            from mnestoma import Mnestoma
            m = Mnestoma()
            t0 = time.perf_counter()
            # crea grafo a "stelle uscenti": hub_i -> leaf_j
            HUBS = max(1, int(N ** 0.5))
            for i in range(N):
                hub = f"hub_{i % HUBS}"
                leaf = f"leaf_{i}"
                m.record_passing(hub, "1.0.0", leaf, "1.0.0")
            t_insert = time.perf_counter() - t0

            # top_k uscente da uno hub (ha ~N/HUBS uscenti)
            t1 = time.perf_counter()
            for _ in range(50):
                m.top_k_outgoing("hub_0", k=10)
            t_topk = (time.perf_counter() - t1) / 50

            # walk depth 2 da hub_0
            t2 = time.perf_counter()
            for _ in range(20):
                m.walk("hub_0", max_depth=2)
            t_walk2 = (time.perf_counter() - t2) / 20

            # walk depth 3
            t3 = time.perf_counter()
            for _ in range(10):
                m.walk("hub_0", max_depth=3)
            t_walk3 = (time.perf_counter() - t3) / 10

            db_kb = Path(os.environ["MNESTOMA_DB_PATH"]).stat().st_size / 1024
            print(f"{_human_n(N):>8} {t_insert:>10.2f} {t_topk*1e6:>10.0f} "
                  f"{t_walk2*1e6:>12.0f} {t_walk3*1e6:>12.0f} {db_kb:>8.0f}")
            m.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


def stress_mnestoma_decay(sizes: list[int]) -> None:
    print("\n[D-mn-decay] apply_ager() su N mnest active (decadimento massivo)")
    print(f"{'N':>8} {'insert_s':>10} {'ager_s':>10} {'decayed':>10} {'demoted':>10}")
    for N in sizes:
        base, _ = _setup_env()
        try:
            from mnestoma import Mnestoma
            m = Mnestoma()
            t0 = time.perf_counter()
            for i in range(N):
                m.record_passing(f"a_{i % 100}", "1", f"b_{i}", "1")
            t_insert = time.perf_counter() - t0
            # forza ts vecchi su tutti gli active
            m.conn.execute(
                "UPDATE mnests SET ts_first='2024-01-01T00:00:00Z',"
                " ts_last='2024-01-02T00:00:00Z' WHERE state='active'",
            )
            t1 = time.perf_counter()
            stats = m.apply_ager()
            t_ager = time.perf_counter() - t1
            print(f"{_human_n(N):>8} {t_insert:>10.2f} {t_ager:>10.2f} "
                  f"{stats['decayed']:>10} {stats['demoted_to_decaying']:>10}")
            m.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


def stress_synt_compose_big(sizes: list[int]) -> None:
    print("\n[D-synt-compose] compose su grafo a catena lunga + branching")
    print(f"{'N':>8} {'build_s':>10} {'compose_us':>12} {'chain_len':>10} {'state':>12}")
    for N in sizes:
        base, _ = _setup_env()
        try:
            from mnestoma import Mnestoma, build_desired_signature
            from synt import Synt, make_request
            m = Mnestoma()
            # Catena profonda: n0 -> n1 -> n2 -> ... -> n_{N-1}
            # Branching: ogni nodo ha 2 archi laterali a leaf di rumore
            t0 = time.perf_counter()
            for i in range(N - 1):
                m.record_passing(f"n{i}", "1", f"n{i+1}", "1")
                m.record_passing(f"n{i}", "1", f"noise_{i}_a", "1")
                m.record_passing(f"n{i}", "1", f"noise_{i}_b", "1")
            t_build = time.perf_counter() - t0
            sig = build_desired_signature("target_x", {}, "stress")
            proto = m.record_passing("n0", "1", "target_x",
                                     dst_exists=False, desired_signature=sig)
            s = Synt(mnestoma=m)

            # Cerca catena fino a n_{min(N-1, 5)}
            target = f"n{min(N-1, 5)}"
            req = make_request("stress", proto_mnest=proto, capability_hint=[target])
            t1 = time.perf_counter()
            prop = s.react(req)
            t_compose = (time.perf_counter() - t1) * 1e6
            chain_len = len(prop.artefact.get("chain", [])) if prop.artefact else 0
            print(f"{_human_n(N):>8} {t_build:>10.2f} {t_compose:>12.0f} "
                  f"{chain_len:>10} {prop.state:>12}")
            m.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


def stress_synt_many_protos(counts: list[int]) -> None:
    print("\n[D-synt-suggest] suggest su K proto-mnest (con catena nota)")
    print(f"{'K':>8} {'build_s':>10} {'react_avg_us':>14} {'composed':>10} {'abandoned':>10}")
    for K in counts:
        base, _ = _setup_env()
        try:
            from mnestoma import Mnestoma, build_desired_signature
            from synt import Synt, make_request
            m = Mnestoma()
            # Backbone composto: src -> mid -> dst_real
            # Per ogni proto-mnest creo una richiesta src -> wanted_k che si risolve
            # via catena nel mid -> dst_real
            t0 = time.perf_counter()
            m.record_passing("src", "1", "mid", "1")
            m.record_passing("mid", "1", "dst_real", "1")
            protos = []
            for k in range(K):
                sig = build_desired_signature(f"wanted_{k}", {}, "x")
                pid = m.record_passing(
                    "src", "1", f"wanted_{k}", dst_exists=False, desired_signature=sig,
                )
                protos.append(pid)
            t_build = time.perf_counter() - t0

            s = Synt(mnestoma=m)
            composed = abandoned = 0
            t1 = time.perf_counter()
            for pid in protos:
                req = make_request(f"intent_{pid}", proto_mnest=pid,
                                   capability_hint=["dst_real"])
                prop = s.react(req)
                if prop.state == "composed":
                    composed += 1
                else:
                    abandoned += 1
            t_react = (time.perf_counter() - t1) / max(1, K) * 1e6
            print(f"{K:>8} {t_build:>10.2f} {t_react:>14.0f} {composed:>10} {abandoned:>10}")
            m.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Stress test mnestoma + synt")
    ap.add_argument("--only", choices=("scale", "decay", "compose", "suggest"),
                    help="esegui solo una dimensione")
    ap.add_argument("--scale-sizes", type=int, nargs="+",
                    default=[100, 1000, 10000])
    ap.add_argument("--decay-sizes", type=int, nargs="+",
                    default=[100, 1000, 10000])
    ap.add_argument("--compose-sizes", type=int, nargs="+",
                    default=[10, 100, 500])
    ap.add_argument("--suggest-counts", type=int, nargs="+",
                    default=[10, 100])
    args = ap.parse_args()

    if not args.only or args.only == "scale":
        stress_mnestoma_scale(args.scale_sizes)
    if not args.only or args.only == "decay":
        stress_mnestoma_decay(args.decay_sizes)
    if not args.only or args.only == "compose":
        stress_synt_compose_big(args.compose_sizes)
    if not args.only or args.only == "suggest":
        stress_synt_many_protos(args.suggest_counts)


if __name__ == "__main__":
    main()
