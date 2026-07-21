"""Vista unificata dei task di aging notturni.

Aggrega gli ultimi audit log dei 4 ager indipendenti del sistema:
- `apply_ager` (mnestoma decay/demote/proto purge) — events table
- `apply_executor_ager` (active→deprecated→archived)
  — `~/.local/share/metnos/aging/executor_ager_<ts>.jsonl`
- `introvertiva_propose` — `~/.local/share/metnos/introvertiva/candidates_*_<ts>.jsonl`
- `introvertiva_apply` — `~/.local/share/metnos/introvertiva/auto_applied_<ts>.jsonl`
- `proposals_cleanup` (ADR 0096) — `~/.local/share/metnos/lifecycle/proposals_cleanup_<ts>.jsonl`

NON unifica i moduli (vedi pros/cons in ADR 0097): aggrega solo i
report. Costo: tre file letti, niente DB scan.

Output via `output_format` (ADR 0095): markdown deterministico, niente LLM.
"""
from __future__ import annotations

import json
import os as _os
import sys as _sys
import time
from pathlib import Path

_RUNTIME = _os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in _sys.path:
    _sys.path.insert(0, _RUNTIME)
from messages import get as _msg
import config as _C  # §7.11

AGING_DIR = _C.PATH_USER_DATA / "aging"
INTROVERTIVA_DIR = _C.PATH_USER_DATA / "introvertiva"
LIFECYCLE_DIR = _C.PATH_USER_DATA / "lifecycle"


def _latest(dir_path: Path, pattern: str, *, since_ts: float | None = None) -> Path | None:
    if not dir_path.exists():
        return None
    matches = []
    for p in dir_path.glob(pattern):
        if "_archived" in p.parts:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if since_ts is not None and mt < since_ts:
            continue
        matches.append((mt, p))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _read_jsonl_first(p: Path) -> dict | None:
    try:
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if ln:
                return json.loads(ln)
    except Exception:
        return None
    return None


def _count_jsonl_records(p: Path) -> int:
    try:
        return sum(1 for ln in p.read_text().splitlines() if ln.strip())
    except Exception:
        return 0


def collect_summary(*, window_hours: int = 24) -> dict:
    """Raccoglie l'ultimo report per ognuno degli ager nella finestra.

    Ritorna dict strutturato con sezioni `executor_ager`, `introvertiva_apply`,
    `introvertiva_propose`, `proposals_cleanup`. Ogni sezione ha:
        - `path`: file letto (str | None)
        - `ts`: epoch dell'esecuzione (float | None)
        - `data`: contenuto del report (dict | None)
        - `note`: stringa diagnostica se manca/vecchio.
    """
    cutoff = time.time() - window_hours * 3600
    out: dict = {}

    # executor_ager
    p = _latest(AGING_DIR, "executor_ager_*.jsonl", since_ts=cutoff)
    out["executor_ager"] = _section_from_file(p, _read_jsonl_first)

    # introvertiva_apply (auto_applied)
    p = _latest(INTROVERTIVA_DIR, "auto_applied_*.jsonl", since_ts=cutoff)
    if p is not None:
        n = _count_jsonl_records(p)
        out["introvertiva_apply"] = {
            "path": str(p), "ts": p.stat().st_mtime,
            "data": {"applied_count": n}, "note": None,
        }
    else:
        out["introvertiva_apply"] = {
            "path": None, "ts": None, "data": None,
            "note": _msg("MSG_LIFECYCLE_NO_AUTO_APPLIED", hours=window_hours),
        }

    # introvertiva_propose: la "produzione" e' un set di candidates_<kind>_<ts>.jsonl;
    # raccogliamo i piu' recenti di ogni kind.
    propose: dict[str, dict] = {}
    for kind in ("dedupe", "generalize", "specialize"):
        p = _latest(INTROVERTIVA_DIR, f"candidates_{kind}_*.jsonl",
                     since_ts=cutoff)
        if p is None:
            propose[kind] = {"records": 0, "path": None}
        else:
            propose[kind] = {
                "records": _count_jsonl_records(p),
                "path": str(p),
                "ts": p.stat().st_mtime,
            }
    out["introvertiva_propose"] = {
        "path": None,
        "ts": None,
        "data": propose,
        "note": None,
    }

    # proposals_cleanup
    p = _latest(LIFECYCLE_DIR, "proposals_cleanup_*.jsonl", since_ts=cutoff)
    out["proposals_cleanup"] = _section_from_file(p, _read_jsonl_first)

    return out


def _section_from_file(p: Path | None, reader) -> dict:
    if p is None:
        return {"path": None, "ts": None, "data": None,
                "note": _msg("MSG_LIFECYCLE_NO_RECENT_AUDIT")}
    return {
        "path": str(p),
        "ts": p.stat().st_mtime,
        "data": reader(p),
        "note": None,
    }


def format_summary(summary: dict, *, window_hours: int = 24) -> str:
    """Renderizza il summary come markdown deterministico via ADR 0095."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from output_format import (format_kv, format_table, format_tldr)

    out_lines: list[str] = []
    out_lines.append(_msg("MSG_LIFECYCLE_TITLE", hours=window_hours))

    # TL;DR aggregato
    n_archived_synth = (summary.get("proposals_cleanup", {}).get("data") or {}) \
        .get("synth_proposals", {}).get("archived", 0)
    n_dec = (summary.get("proposals_cleanup", {}).get("data") or {}) \
        .get("legacy_orphan_mnests", {}).get("decayed", 0)
    n_ex_dep = (summary.get("executor_ager", {}).get("data") or {}) \
        .get("deprecated", []) or []
    n_ex_arch = (summary.get("executor_ager", {}).get("data") or {}) \
        .get("archived", []) or []
    n_intro_apply = (summary.get("introvertiva_apply", {}).get("data") or {}) \
        .get("applied_count", 0)

    bullets = []
    if isinstance(n_ex_dep, list):
        n_ex_dep = len(n_ex_dep)
    if isinstance(n_ex_arch, list):
        n_ex_arch = len(n_ex_arch)
    if n_archived_synth or n_dec or n_ex_dep or n_ex_arch or n_intro_apply:
        bullets.append(_msg("MSG_LIFECYCLE_TLDR_EXECUTOR",
                            dep=n_ex_dep, arch=n_ex_arch))
        bullets.append(_msg("MSG_LIFECYCLE_TLDR_MNEST", n=n_dec))
        bullets.append(_msg("MSG_LIFECYCLE_TLDR_SYNTH", n=n_archived_synth))
        bullets.append(_msg("MSG_LIFECYCLE_TLDR_INTRO", n=n_intro_apply))
        out_lines.append("")
        out_lines.append(format_tldr("; ".join(bullets)))

    # executor_ager
    out_lines.append("")
    out_lines.append(_msg("MSG_LIFECYCLE_SECTION_EXECUTOR"))
    sec = summary.get("executor_ager", {})
    if not sec.get("data"):
        out_lines.append(f"  _{sec.get('note') or _msg('MSG_LIFECYCLE_NO_AUDIT')}_")
    else:
        d = sec["data"]
        rows = [
            [_msg("MSG_LIFECYCLE_ROW_DEPRECATED"),
             str(len(d.get("deprecated") or []))],
            [_msg("MSG_LIFECYCLE_ROW_ARCHIVED"),
             str(len(d.get("archived") or []))],
            [_msg("MSG_LIFECYCLE_ROW_PROTECTED_SKIPPED"),
             str(d.get("protected_skipped", 0))],
            [_msg("MSG_LIFECYCLE_ROW_TOTAL_SEEN"),
             str(d.get("total_seen", 0))],
        ]
        out_lines.append(format_table(
            [_msg("MSG_LIFECYCLE_TABLE_OUTCOME"),
             _msg("MSG_LIFECYCLE_TABLE_N")],
            rows, align=["left", "right"]))

    # introvertiva
    out_lines.append("")
    out_lines.append(_msg("MSG_LIFECYCLE_SECTION_INTRO_PROPOSE"))
    p_sec = summary.get("introvertiva_propose", {}).get("data") or {}
    if p_sec:
        rows = [[k, str(v.get("records", 0))]
                for k, v in sorted(p_sec.items())]
        out_lines.append(format_table(
            [_msg("MSG_LIFECYCLE_TABLE_KIND"),
             _msg("MSG_LIFECYCLE_TABLE_RECORD")],
            rows, align=["left", "right"]))

    out_lines.append("")
    out_lines.append(_msg("MSG_LIFECYCLE_SECTION_INTRO_APPLY"))
    sec = summary.get("introvertiva_apply", {})
    if not sec.get("data"):
        out_lines.append(f"  _{sec.get('note') or _msg('MSG_LIFECYCLE_NO_AUDIT')}_")
    else:
        out_lines.append(format_kv(
            _msg("MSG_LIFECYCLE_KV_APPLIED"),
            sec["data"].get("applied_count", 0)))

    # proposals_cleanup
    out_lines.append("")
    out_lines.append(_msg("MSG_LIFECYCLE_SECTION_PROPOSALS_CLEANUP"))
    sec = summary.get("proposals_cleanup", {})
    if not sec.get("data"):
        out_lines.append(f"  _{sec.get('note') or _msg('MSG_LIFECYCLE_NO_AUDIT')}_")
    else:
        d = sec["data"]
        rows = [
            [_msg("MSG_LIFECYCLE_ROW_SYNTH_ARCHIVED"),
             str(d.get("synth_proposals", {}).get("archived", 0))],
            [_msg("MSG_LIFECYCLE_ROW_DEDUP_REMOVED"),
             str(d.get("introvertiva_dedup", {}).get("removed_records", 0))],
            [_msg("MSG_LIFECYCLE_ROW_SNAPSHOTS_ARCHIVED"),
             str(d.get("introvertiva_snapshots", {}).get("archived", 0))],
            [_msg("MSG_LIFECYCLE_ROW_LEGACY_DECAYED"),
             str(d.get("legacy_orphan_mnests", {}).get("decayed", 0))],
        ]
        out_lines.append(format_table(
            [_msg("MSG_LIFECYCLE_TABLE_OPERATION"),
             _msg("MSG_LIFECYCLE_TABLE_N")],
            rows, align=["left", "right"]))

    return "\n".join(out_lines)


def run_summary(*, window_hours: int = 24) -> dict:
    """API per il task scheduler. Raccoglie + formatta + persiste in
    `~/.local/share/metnos/lifecycle/lifecycle_summary_<ts>.jsonl`.
    """
    summary = collect_summary(window_hours=window_hours)
    rendered = format_summary(summary, window_hours=window_hours)
    LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    audit_path = LIFECYCLE_DIR / f"lifecycle_summary_{ts}.jsonl"
    audit_path.write_text(json.dumps({
        "ts": ts, "window_hours": window_hours,
        "summary": summary, "rendered": rendered,
    }, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "audit_path": str(audit_path),
        "rendered": rendered,
        "window_hours": window_hours,
    }


__all__ = ["collect_summary", "format_summary", "run_summary"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--json", action="store_true",
                        help="output JSON invece di markdown")
    args = parser.parse_args()
    s = collect_summary(window_hours=args.window_hours)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_summary(s, window_hours=args.window_hours))
