"""Manutenzione cleanup di `synt_proposals/` e `introvertiva/`.

Problema (Roberto, 7/5/2026): 77 file in `synt_proposals/` + 25 in
`introvertiva/`. Volume tale da impedire la review umana → si rischia
di perdere candidati validi nel rumore.

Strategia (ADR 0096):
- **synt_proposals/**: log di run synth, non decisioni. Archiviare
  quelli relativi a executor gia' nel catalog OR vecchi >30gg.
- **introvertiva/**: candidate file per audit periodico. Dedup per
  signature (kind, src, dst) tenendo il piu' recente; archive stale.
- **legacy_orphan auto-decay**: mnest che riferisce un src non piu' nel
  catalog (verb rimosso/rinominato) → decay automatico (no review utente).

NIENTE LLM (§7.9). Tutto deterministico.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import config as _C  # §7.11

SYNT_PROPOSALS_DIR = _C.PATH_USER_DATA / "synt_proposals"
INTROVERTIVA_DIR = _C.PATH_USER_DATA / "introvertiva"


# ─── synt_proposals/ ─────────────────────────────────────────────────────


def _proposal_age_days(p: Path, now: float | None = None) -> float:
    """Eta' in giorni di una proposal. Usa mtime; il filename ha
    epoch ma e' meno affidabile (puo' essere riusato)."""
    if now is None:
        now = time.time()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return 0.0
    return max(0.0, (now - mtime) / 86400.0)


def archive_aged_synth_proposals(*, max_age_days: int = 30,
                                   archive_synthesized_in_catalog: bool = True,
                                   dry_run: bool = False) -> dict:
    """Sposta in `synt_proposals/_archived/<YYYY>/<MM>/` le proposal:
    1. con `final_state="synthesized"` E `name` gia' nel catalog (storia
       superflua); regola attiva solo se `archive_synthesized_in_catalog`.
    2. con eta' > `max_age_days` indipendentemente dallo stato.

    Ritorna dict con `{archived, kept, errors, archived_paths}`.

    Niente delete: sempre move. L'archive resta esplorabile.
    """
    if not SYNT_PROPOSALS_DIR.exists():
        return {"archived": 0, "kept": 0, "errors": [], "archived_paths": []}

    catalog_names: set[str] = set()
    if archive_synthesized_in_catalog:
        try:
            from loader import load_catalog
            catalog_names = {e.name for e in load_catalog()}
        except Exception:
            # fallback: scan dir handcrafted + synth installed
            catalog_names = _fallback_catalog_names()

    now = time.time()
    archived: list[Path] = []
    errors: list[str] = []
    kept = 0
    for p in sorted(SYNT_PROPOSALS_DIR.glob("*.json")):
        if "_archived" in p.parts:
            continue
        try:
            doc = json.loads(p.read_text())
        except Exception as ex:
            errors.append(f"{p.name}: parse: {ex}")
            kept += 1
            continue
        age_days = _proposal_age_days(p, now)
        name = doc.get("name") or doc.get("expected_name")
        final_state = doc.get("final_state")

        should_archive = False
        if archive_synthesized_in_catalog and final_state == "synthesized" \
                and name and name in catalog_names:
            should_archive = True
        elif age_days > max_age_days:
            should_archive = True

        if not should_archive:
            kept += 1
            continue

        if dry_run:
            archived.append(p)
            continue

        # Determina dest dir per anno/mese da ts_start o mtime
        ts = doc.get("ts_start") or p.stat().st_mtime
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        dest_dir = SYNT_PROPOSALS_DIR / "_archived" / f"{dt.year:04d}" / f"{dt.month:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        try:
            shutil.move(str(p), str(dest))
            archived.append(dest)
        except Exception as ex:
            errors.append(f"{p.name}: move: {ex}")
            kept += 1
    return {
        "archived": len(archived),
        "kept": kept,
        "errors": errors,
        "archived_paths": [str(p) for p in archived],
    }


def _fallback_catalog_names() -> set[str]:
    out: set[str] = set()
    import config as _C  # ADR 0148 rename-resilient
    for d in (_C.PATH_EXECUTORS,
              _C.PATH_USER_DATA / "executors"):
        if not d.is_dir():
            continue
        for sub in d.iterdir():
            if sub.is_dir() and (sub / "manifest.toml").exists():
                out.add(sub.name)
    return out


# ─── introvertiva/ candidates ────────────────────────────────────────────


def _candidate_signature(rec: dict, kind_from_file: str = "") -> tuple:
    """Signature usata per dedup. Schema-aware:

    - dedupe (legacy_orphan): (kind, src_executor, dst_executor)
    - specialize: (kind, executor, arg_name, dominant_value, proposed_name)
    - generalize: (kind, tuple(pattern))
    - default: tutti i campi non-numerici ordinati

    Due record con stessa signature sono equivalenti. La "cost function"
    sceglie il record con `uses`/`total_uses` piu' alto; tiebreak su
    `weight`/`avg_weight`/`dominance`.
    """
    kind = rec.get("kind") or kind_from_file or ""
    if kind in ("legacy_orphan", "dedupe") or rec.get("src_executor"):
        return (
            kind or "dedupe",
            rec.get("src_executor", ""),
            rec.get("dst_executor", ""),
            rec.get("proposed_name", ""),
        )
    if "proposed_name" in rec and "executor" in rec:
        # specialize
        return (
            kind or "specialize",
            rec.get("executor", ""),
            rec.get("arg_name", ""),
            str(rec.get("dominant_value", "")),
            rec.get("proposed_name", ""),
        )
    if "pattern" in rec and isinstance(rec.get("pattern"), list):
        # generalize
        return (kind or "generalize", tuple(rec["pattern"]))
    # Fallback: campi stringa concatenati
    return (kind or "unknown",) + tuple(
        f"{k}={v}" for k, v in sorted(rec.items())
        if isinstance(v, str)
    )


def _candidate_cost(rec: dict) -> tuple[int, float]:
    """Cost: (uses_max, weight_max). Maggiore = vincitore."""
    uses = (rec.get("uses") or rec.get("total_uses") or
              rec.get("distinct_intents") or 0)
    weight = (rec.get("weight") or rec.get("avg_weight") or
              rec.get("dominance") or rec.get("score") or 0.0)
    return (int(uses), float(weight))


def _kind_from_filename(p: Path) -> str:
    """Estrae kind dal nome file: candidates_<kind>_<ts>.jsonl."""
    name = p.name
    if not name.startswith("candidates_"):
        return ""
    rest = name[len("candidates_"):]
    parts = rest.split("_")
    if len(parts) >= 2:
        return parts[0]  # dedupe / generalize / specialize
    return ""


def keep_latest_n_per_kind(*, n: int = 3, dry_run: bool = False) -> dict:
    """Per ogni `kind` (dedupe/generalize/specialize) tiene i `n` file
    `candidates_<kind>_*.jsonl` piu' recenti; archivia gli altri.

    Razionale: ogni run di `task_introvertiva_propose` emette uno snapshot
    completo dei candidati attivi. Tre snapshot consecutivi bastano a
    capire stabilita'/trend. Snapshot piu' vecchi → cold storage.
    """
    if not INTROVERTIVA_DIR.exists():
        return {"archived": 0, "kept": 0, "errors": []}
    by_kind: dict[str, list[Path]] = {}
    for p in INTROVERTIVA_DIR.glob("candidates_*.jsonl"):
        if "_archived" in p.parts:
            continue
        kind = _kind_from_filename(p)
        by_kind.setdefault(kind, []).append(p)
    archived: list[Path] = []
    errors: list[str] = []
    kept = 0
    for kind, files in by_kind.items():
        # piu' recenti prima
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for p in files[:n]:
            kept += 1
        for p in files[n:]:
            if dry_run:
                archived.append(p)
                continue
            try:
                _archive_jsonl(p)
                archived.append(p)
            except Exception as ex:
                errors.append(f"{p.name}: {ex}")
    return {
        "archived": len(archived),
        "kept": kept,
        "by_kind": {k: len(v) for k, v in by_kind.items()},
        "errors": errors,
    }


def dedupe_introvertiva_candidates(*, retention_days: int = 7,
                                     dry_run: bool = False) -> dict:
    """Per ogni file `candidates_<op>_<ts>.jsonl` in `INTROVERTIVA_DIR`:
    leggi i record, dedup per signature mantenendo il piu' "performante"
    (uses massimo, ts piu' recente come tiebreak), riscrivi.

    File vecchi >`retention_days` archiviati come per le proposal.
    """
    if not INTROVERTIVA_DIR.exists():
        return {"deduped": 0, "removed_records": 0, "archived": 0, "errors": []}

    now = time.time()
    deduped_files = 0
    removed_total = 0
    archived = 0
    errors: list[str] = []
    for p in sorted(INTROVERTIVA_DIR.glob("candidates_*.jsonl")):
        age_days = _proposal_age_days(p, now)
        if age_days > retention_days:
            if not dry_run:
                _archive_jsonl(p)
            archived += 1
            continue

        try:
            lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
        except Exception as ex:
            errors.append(f"{p.name}: read: {ex}")
            continue
        if not lines:
            continue

        records: list[dict] = []
        for ln in lines:
            try:
                records.append(json.loads(ln))
            except Exception:
                continue

        # Dedup: per ogni signature, scegli il "vincitore"
        kind_hint = _kind_from_filename(p)
        winners: dict[tuple, dict] = {}
        for rec in records:
            sig = _candidate_signature(rec, kind_from_file=kind_hint)
            if sig not in winners:
                winners[sig] = rec
                continue
            # Cost: (uses, weight) lex compare
            if _candidate_cost(rec) > _candidate_cost(winners[sig]):
                winners[sig] = rec

        removed = len(records) - len(winners)
        if removed > 0:
            removed_total += removed
            deduped_files += 1
            if not dry_run:
                p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                         for r in winners.values()) + "\n")
    return {
        "deduped_files": deduped_files,
        "removed_records": removed_total,
        "archived": archived,
        "errors": errors,
    }


def _archive_jsonl(p: Path) -> None:
    dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    dest_dir = INTROVERTIVA_DIR / "_archived" / f"{dt.year:04d}" / f"{dt.month:02d}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest_dir / p.name))


# ─── auto-decay legacy_orphan mnests ─────────────────────────────────────


def auto_decay_legacy_orphan_mnests(*, dry_run: bool = False) -> dict:
    """Decade automaticamente i mnest `state='active'` in cui src e/o dst
    NON sono piu' nel catalog.

    Caso paradigmatico: `fetch_urls -> write_files`. Il verbo `fetch_urls`
    e' stato rimosso 3/5/2026 (ridotto a `get_urls`). Il mnest residuo non
    e' piu' azionabile. Niente review umana necessaria.

    Operazione: imposta `state='superseded'`. Niente DELETE — restano per
    audit trail.
    """
    try:
        from mnestoma import Mnestoma
        from loader import load_catalog
    except Exception as ex:
        return {"decayed": 0, "errors": [f"import: {ex}"]}
    cat = load_catalog()
    catalog_names = {e.name for e in cat}
    mn = Mnestoma()
    decayed: list[str] = []
    errors: list[str] = []
    rows = list(mn.conn.execute(
        "SELECT id, src_executor, dst_executor, uses, weight FROM mnests "
        "WHERE state = 'active'"
    ))
    for r in rows:
        src_orphan = r["src_executor"] not in catalog_names
        dst_orphan = r["dst_executor"] not in catalog_names
        if not (src_orphan or dst_orphan):
            continue
        if dry_run:
            decayed.append(r["id"])
            continue
        try:
            # Schema mnests non ha superseded_at/_reason: scriviamo il
            # motivo del decay nel campo `tags` (TEXT) come prefisso noto.
            tag = (
                f"superseded_legacy_orphan: src='{r['src_executor']}' "
                f"dst='{r['dst_executor']}' at={int(time.time())}"
            )
            mn.conn.execute(
                "UPDATE mnests SET state='superseded', tags=? WHERE id=?",
                (tag, r["id"]),
            )
            decayed.append(r["id"])
        except Exception as ex:
            errors.append(f"{r['id']}: {ex}")
    if not dry_run:
        mn.conn.commit()
    return {"decayed": len(decayed), "ids": decayed, "errors": errors}


# ─── orchestrator ────────────────────────────────────────────────────────


def run_cleanup(*, dry_run: bool = False, keep_n_snapshots: int = 3,
                  write_audit: bool = True) -> dict:
    """Esegue tutte le fasi e ritorna un report unico.

    Da chiamare manualmente o via scheduler builtin
    (`task_proposals_cleanup`, daily@06:00 in `make_default_scheduler`).

    Quando `write_audit=True` (e non dry_run), persiste il report in
    `~/.local/share/metnos/lifecycle/proposals_cleanup_<ts>.jsonl` per
    consumo da `lifecycle_summary` (ADR 0096 §lifecycle).
    """
    report: dict = {}
    report["synth_proposals"] = archive_aged_synth_proposals(dry_run=dry_run)
    report["introvertiva_dedup"] = dedupe_introvertiva_candidates(dry_run=dry_run)
    report["introvertiva_snapshots"] = keep_latest_n_per_kind(
        n=keep_n_snapshots, dry_run=dry_run,
    )
    report["legacy_orphan_mnests"] = auto_decay_legacy_orphan_mnests(dry_run=dry_run)
    if write_audit and not dry_run:
        try:
            audit_dir = _C.PATH_USER_DATA / "lifecycle"
            audit_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            (audit_dir / f"proposals_cleanup_{ts}.jsonl").write_text(
                json.dumps(report, ensure_ascii=False) + "\n"
            )
        except Exception:  # audit fail = non bloccante
            pass
    return report


__all__ = [
    "archive_aged_synth_proposals",
    "dedupe_introvertiva_candidates",
    "keep_latest_n_per_kind",
    "auto_decay_legacy_orphan_mnests",
    "run_cleanup",
]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rep = run_cleanup(dry_run=args.dry_run)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
