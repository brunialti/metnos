#!/usr/bin/env python3
"""Paired held-out measurement for the c10/c11 derived-field cuts.

Each invocation runs a fresh c8 control and exactly one candidate on the same
120 queries in the same order.  Production data is read only; the script writes
only its result JSON beside itself.

    python3 prova_specchi.py c10
    python3 prova_specchi.py c11
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/metnos")
sys.path.insert(0, "/opt/metnos/runtime")

LAB = pathlib.Path("/opt/metnos/internal/tools/request_analysis_lab")
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

BUDGET = 4000
CUTS = {
    "c10": (
        "input_from_predicate_id",
        "predicate_id",
        "predicate_anchor_token_id",
    ),
    "c11": (
        "input_from_predicate_id",
        "predicate_id",
        "predicate_anchor_token_id",
        "role",
    ),
}
USAGE: list[dict] = []


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def install_transport_probe() -> None:
    """Set the output cap and retain server token accounting."""
    real_request = urllib.request.Request
    real_urlopen = urllib.request.urlopen

    class BudgetedRequest(real_request):
        def __init__(self, url, data=None, headers=None, **kwargs):
            if data:
                body = json.loads(data)
                if "max_tokens" in body:
                    body["max_tokens"] = BUDGET
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            super().__init__(url, data=data, headers=headers or {}, **kwargs)

    def capturing_urlopen(request, *args, **kwargs):
        payload = real_urlopen(request, *args, **kwargs).read()
        try:
            body = json.loads(payload)
            USAGE.append(
                {
                    "completion_tokens": (body.get("usage") or {}).get(
                        "completion_tokens"
                    ),
                    "finish_reason": (body.get("choices") or [{}])[0].get(
                        "finish_reason"
                    ),
                }
            )
        except Exception:  # noqa: BLE001
            USAGE.append({})

        class Replay:
            def read(self, *_args):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Replay()

    urllib.request.Request = BudgetedRequest
    urllib.request.urlopen = capturing_urlopen


def make_arm(tag: str, cuts: tuple[str, ...]):
    module = load(
        LAB / "unified_query_bench_v23_checkpoint.py", f"specchi_{tag}"
    )
    import patch_corrente

    applied = [
        ("contratto_ruolo_allineato_al_validatore", patch_corrente._contract(module)),
        ("tie_break_rimosso", patch_corrente._drop_tie_break(module)),
    ]
    if cuts:
        import uscita_specchi

        applied.append(("specchi_tolti", uscita_specchi.install(module, cuts)))

    names = [name for name, fired in applied if fired]
    expected = 2 + bool(cuts)
    if len(names) != expected:
        raise AssertionError(f"not every requested patch fired: {applied}")

    # Exercise the patch before spending GPU time.  This also proves that every
    # requested field disappeared only from the model-facing predicate schema.
    item = module.schema("v23lite")["properties"]["predicates"]["items"]
    still_present = [name for name in cuts if name in item["properties"]]
    if still_present:
        raise AssertionError(f"fields still present after cut: {still_present}")
    return module, names


def observe(module, query: str) -> dict:
    before = len(USAGE)
    started = time.perf_counter()
    try:
        frame, info = module.folded_call(query, prompt_variant="v23lite")
        row = {
            "valid": bool(info.get("valid")),
            "reason": str(info.get("reason") or ""),
            "routes": [
                f"{predicate.get('verb')}/{predicate.get('object')}"
                for predicate in frame.get("predicates") or []
            ],
            "roles": [
                predicate.get("role") for predicate in frame.get("predicates") or []
            ],
            "frame": frame,
        }
    except Exception as error:  # noqa: BLE001
        row = {
            "valid": False,
            "reason": f"{type(error).__name__}: {error}",
            "routes": [],
            "roles": [],
            "frame": {},
        }
    row["ms"] = (time.perf_counter() - started) * 1000
    usage = USAGE[before] if len(USAGE) > before else {}
    row.update(usage)
    return row


def summary(rows: list[dict]) -> dict:
    latencies = sorted(row["ms"] for row in rows)
    token_counts = sorted(
        row["completion_tokens"]
        for row in rows
        if isinstance(row.get("completion_tokens"), int)
    )
    return {
        "valid": sum(bool(row["valid"]) for row in rows),
        "invalid": len(rows) - sum(bool(row["valid"]) for row in rows),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": latencies[int(len(latencies) * 0.95) - 1],
        "latency_total_ms": sum(latencies),
        "completion_tokens_p50": statistics.median(token_counts)
        if token_counts
        else None,
        "completion_tokens_total": sum(token_counts),
        "truncated": sum(row.get("finish_reason") == "length" for row in rows),
    }


def run_arm(tag: str, module, queries: list[str]) -> list[dict]:
    rows = []
    started = time.perf_counter()
    for index, query in enumerate(queries):
        rows.append(observe(module, query))
        if (index + 1) % 10 == 0:
            valid = sum(bool(row["valid"]) for row in rows)
            print(
                f"  {tag}: {index + 1:3d}/{len(queries)} | valide {valid:3d}",
                flush=True,
            )
    print(f"  {tag}: completato in {time.perf_counter() - started:.1f} s", flush=True)
    return rows


def main() -> int:
    candidate = sys.argv[1] if len(sys.argv) > 1 else ""
    if candidate not in CUTS:
        raise SystemExit("usage: prova_specchi.py c10|c11")

    import prova_cieca

    queries, groups = prova_cieca.sample()
    if len(queries) != 120:
        raise AssertionError(f"expected 120 held-out queries, found {len(queries)}")
    sample_sha256 = hashlib.sha256(
        json.dumps(queries, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    control_module, control_patches = make_arm(f"c8_for_{candidate}", ())
    candidate_module, candidate_patches = make_arm(candidate, CUTS[candidate])
    install_transport_probe()

    print(
        f"specchi {candidate} | controllo c8 nuovo | 2 x {len(queries)} query | "
        f"budget {BUDGET} | campione {sample_sha256}",
        flush=True,
    )
    control_rows = run_arm("c8 controllo", control_module, queries)
    candidate_rows = run_arm(candidate, candidate_module, queries)
    control_summary = summary(control_rows)
    candidate_summary = summary(candidate_rows)

    changed_routes = [
        index
        for index in range(len(queries))
        if control_rows[index]["routes"] != candidate_rows[index]["routes"]
        or control_rows[index]["valid"] != candidate_rows[index]["valid"]
    ]
    healed = [
        index
        for index in range(len(queries))
        if not control_rows[index]["valid"] and candidate_rows[index]["valid"]
    ]
    broken = [
        index
        for index in range(len(queries))
        if control_rows[index]["valid"] and not candidate_rows[index]["valid"]
    ]

    result = {
        "measurement": "derived_predicate_mirror_cut",
        "candidate": candidate,
        "heldout_seed": prova_cieca.HELDOUT_SEED,
        "budget": BUDGET,
        "sample_sha256": sample_sha256,
        "queries": queries,
        "groups": groups,
        "arms": {
            "c8_control": {
                "patches": control_patches,
                "summary": control_summary,
                "rows": control_rows,
            },
            candidate: {
                "patches": candidate_patches,
                "cuts": list(CUTS[candidate]),
                "summary": candidate_summary,
                "rows": candidate_rows,
            },
        },
        "comparison": {
            "changed_routes_or_validity": changed_routes,
            "healed": healed,
            "broken": broken,
        },
    }
    output = HERE / f"prova_specchi_{candidate}.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print("\nriepilogo", flush=True)
    print(f"  c8: {control_summary}", flush=True)
    print(f"  {candidate}: {candidate_summary}", flush=True)
    print(
        f"  divergenze: {len(changed_routes)} | risanate {len(healed)} | "
        f"rotte {len(broken)}",
        flush=True,
    )
    print(f"scritto {output.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
