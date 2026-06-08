#!/usr/bin/env python3
"""delete_persons — rimuove persone dal registro nominale.

Pattern §2.1 (vettoriale per costruzione): accetta sia singolare (`name`)
che plurale (`names`/`entries` da from_step) che `all=true` per purge.
Output sempre `results: list`.

§2.3 reversibile (module.reverse): prima di cancellare, ogni persona viene
esportata in un blob (riga + esempi + embedding); `reverse()` la reinserisce
verbatim. Undo onesto solo per i record con backup riuscito.

Disambiguation pattern (ADR 0090): se almeno un nome matcha piu' slug,
ritorna `decision="needs_inputs"` con un dialogo `choice_with_preview`
per slug ambiguo. I non-ambigui vengono cancellati subito; gli ambigui
attendono la scelta.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg
from persons_registry import PersonsRegistry


def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _backup_dir() -> Path:
    """Dir blob di backup-persona del turno (§2.3, come delete_files)."""
    history = os.environ.get("METNOS_HISTORY_DIR")
    if not history:
        import config as _C  # §7.11
        history = str(_C.PATH_USER_DATA / "_history")
    turn_id = os.environ.get("METNOS_TURN_ID") or "no_turn"
    return Path(history) / turn_id / "persons_backup"


def _delete_with_backup(reg, slug: str, display: str) -> dict:
    """Esporta la persona (riga+esempi+biometria) come blob PRIMA di
    cancellarla, poi cancella. Il blob path va nel result → `reverse()` lo
    rilegge per ripristinare (undo §2.3). Backup best-effort: se fallisce, la
    cancellazione procede ma il result non porta backup_path (undo onesto §2.8).
    """
    backup_path = None
    try:
        dump = reg.export_person(slug)
        if dump is not None:
            bdir = _backup_dir()
            bdir.mkdir(parents=True, exist_ok=True)
            bp = bdir / f"{slug}.json"
            bp.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
            backup_path = str(bp)
    except OSError:
        backup_path = None
    out = reg.delete(slug)
    row = {"slug": slug, "name": display,
           "removed_examples": out["removed_examples"]}
    if backup_path:
        row["backup_path"] = backup_path
    return row


def reverse(plan, results):
    """Undo §2.3 (module.reverse): ripristina le persone cancellate dai blob di
    backup. Reversibile solo per i result con `backup_path` (export riuscito);
    senza backup → quella persona non e' ribaltabile (conta come fail onesto).
    """
    res = results or {}
    rows = res.get("results") or []
    reg = PersonsRegistry(db_path=_persons_db_path())
    out, failed = [], []
    try:
        for r in rows:
            if not isinstance(r, dict):
                continue
            bp = r.get("backup_path")
            if not bp:
                failed.append({"slug": r.get("slug"),
                               "error": "no backup_path: non ripristinabile"})
                continue
            try:
                dump = json.loads(Path(bp).read_text(encoding="utf-8"))
                rr = reg.restore_person(dump)
                if rr.get("restored"):
                    out.append({"slug": rr["slug"],
                                "restored_examples": rr.get("restored_examples", 0)})
                else:
                    # slug gia' presente = gia' ripristinato/mai cancellato: noop ok
                    out.append({"slug": rr["slug"], "restored_examples": 0,
                                "note": rr.get("reason")})
            except (OSError, ValueError, json.JSONDecodeError) as e:
                failed.append({"slug": r.get("slug"), "error": str(e)})
    finally:
        reg.close()
    return {"ok": len(failed) == 0, "ok_count": len(out),
            "fail_count": len(failed), "results": out, "failed": failed}


def _coalesce_targets(args) -> tuple[list[str], str | None]:
    """Estrae la lista di nomi target da args.

    Sorgenti accettate (priorita' in ordine):
      1. `all=true` → sentinel ["__ALL__"], caller espande via list_all.
      2. `names: list[str]` (plurale canonico §2.1).
      3. `name: str` (singolare, len-1 list, §2.4 compound). Ha priorita'
         su `entries` perche' il PLANNER spesso passa entrambi quando
         pipeline da find_files (errato) → delete_persons; il `name`
         esplicito e' l'intent reale (10/5/2026 fix).
      4. `entries: list[dict]` (da from_step di get_persons): estrae
         `.name` (o `.slug` come fallback). Solo se nessuno dei
         precedenti e' presente.
    """
    if bool(args.get("all")):
        return ["__ALL__"], None

    names = args.get("names")
    if names is not None:
        if not isinstance(names, list):
            return [], "names must be a list of strings"
        out: list[str] = []
        for v in names:
            if not isinstance(v, str) or not v.strip():
                return [], "each name must be a non-empty string"
            out.append(v.strip())
        if not out:
            return [], "names must be non-empty"
        return out, None

    name = args.get("name")
    if isinstance(name, str) and name.strip():
        return [name.strip()], None

    entries = args.get("entries")
    if entries is not None:
        if not isinstance(entries, list):
            return [], "entries must be a list of dicts"
        out2: list[str] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            v = e.get("name") or e.get("slug")
            if isinstance(v, str) and v.strip():
                out2.append(v.strip())
        if not out2:
            return [], "no usable name/slug in entries (use `name` for a single person)"
        return out2, None

    if name is None:
        return [], "must provide one of: names, entries, name, all=true"
    if not isinstance(name, str):
        return [], "name must be a string"
    return [], "name must be non-empty"


def _build_preview_options(reg: PersonsRegistry, slugs: list[str]) -> list[dict]:
    options_pv: list[dict] = []
    for s in slugs:
        entry = reg.get(s)
        if not entry:
            continue
        display = entry.get("name") or s
        n = int(entry.get("n_examples") or 0)
        examples = entry.get("examples") or []
        if not examples:
            continue
        ex0 = examples[0]
        img_path = ex0.get("image_path")
        face_box = ex0.get("face_box")
        if not isinstance(img_path, str) or not img_path:
            continue
        if isinstance(face_box, (list, tuple)) and len(face_box) == 4:
            fb_str = ",".join(str(int(v)) for v in face_box)
            preview = f"{img_path}#bbox={fb_str}"
        else:
            preview = img_path
        options_pv.append({
            "value": s,
            "label": f"{display} ({n} esempi)",
            "preview_image_path": preview,
        })
    return options_pv


def invoke(args):
    chosen_slug = args.get("chosen_slug")
    chosen_slugs = args.get("chosen_slugs")
    multistep_keys = [
        k for k in args.keys()
        if isinstance(k, str) and k.startswith("chosen_slug__")
    ]
    has_post_disambig = bool(
        chosen_slug or chosen_slugs or multistep_keys
    )

    if has_post_disambig:
        targets, err = [], None
    else:
        targets, err = _coalesce_targets(args)
        if err is not None:
            return {"ok": False, "error": err}

    if bool(args.get("dry_run")) or _is_dry_run():
        reg_ro = PersonsRegistry(db_path=_persons_db_path())
        try:
            if targets == ["__ALL__"]:
                all_entries = reg_ro.list_all()
                would_remove = [
                    {"slug": e["slug"], "name": e.get("name", e["slug"]),
                     "n_examples": int(e.get("n_examples") or 0)}
                    for e in all_entries
                ]
            else:
                would_remove = []
                for nm in targets:
                    for s in reg_ro.resolve_name(nm):
                        e = reg_ro.get(s)
                        if e is None:
                            continue
                        would_remove.append({
                            "slug": s, "name": e.get("name", s),
                            "n_examples": int(e.get("n_examples") or 0),
                        })
        finally:
            reg_ro.close()
        return {
            "ok": True, "dry_run": True,
            "would_remove": would_remove,
            "n_candidates": len(would_remove),
        }

    reg = PersonsRegistry(db_path=_persons_db_path())
    try:
        results: list[dict] = []
        unknown_names: list[str] = []
        ambiguous_dialogs: list[dict] = []

        # Resume callback post-disambig: cancella SOLO gli slug scelti.
        post_disambig: list[str] = []
        if isinstance(chosen_slugs, list):
            post_disambig.extend(
                str(s) for s in chosen_slugs if isinstance(s, str) and s
            )
        if isinstance(chosen_slug, str) and chosen_slug:
            post_disambig.append(chosen_slug)
        # Slugs scelti via dialog multi-step (var name: chosen_slug__<nm>).
        for k, v in args.items():
            if (isinstance(k, str) and k.startswith("chosen_slug__")
                    and isinstance(v, str) and v):
                post_disambig.append(v)
        if post_disambig:
            for cs in post_disambig:
                entry = reg.get(cs)
                if entry is None:
                    return {
                        "ok": False,
                        "error": _msg("ERR_SLUG_NOT_FOUND", slug=cs),
                    }
                results.append(_delete_with_backup(reg, cs, entry["name"]))
            return {
                "ok": True, "results": results,
                "n_removed": len(results),
                "removed_examples_total": sum(
                    r["removed_examples"] for r in results
                ),
            }

        if targets == ["__ALL__"]:
            all_entries = reg.list_all()
            if not all_entries:
                return {
                    "ok": True, "results": [],
                    "n_removed": 0, "removed_examples_total": 0,
                    "final_message_hint": _msg("MSG_PERSONS_LIST_EMPTY"),
                }
            for e in all_entries:
                slug = e["slug"]
                display = e.get("name") or slug
                results.append(_delete_with_backup(reg, slug, display))
            return {
                "ok": True, "results": results,
                "n_removed": len(results),
                "removed_examples_total": sum(
                    r["removed_examples"] for r in results
                ),
            }

        for nm in targets:
            slugs = reg.resolve_name(nm)
            if not slugs:
                unknown_names.append(nm)
                continue
            if len(slugs) == 1:
                entry = reg.get(slugs[0])
                display = entry["name"] if entry else slugs[0]
                results.append(_delete_with_backup(reg, slugs[0], display))
                continue
            options_pv = _build_preview_options(reg, slugs)
            ambiguous_dialogs.append({
                "var": f"chosen_slug__{nm}",
                "prompt": _msg("MSG_PERSONS_PICK_VISUAL", name=nm),
                "schema": {"kind": "choice_with_preview",
                            "options": options_pv},
            })

        if ambiguous_dialogs:
            ambiguous_names = [d["var"].split("__", 1)[1] for d in ambiguous_dialogs]
            payload = {
                "title": _msg(
                    "MSG_PERSONS_AMBIGUOUS_NAME",
                    name=", ".join(ambiguous_names),
                ),
                "dialog": ambiguous_dialogs,
                "fmt": "auto",
                "on_complete": {
                    "type": "resume_executor_with_values",
                    "executor": "delete_persons",
                    "args_base": {},
                },
            }
            return {
                "ok": True,
                "decision": "needs_inputs",
                "needs_inputs": payload,
                "results": results,
                "n_removed": len(results),
                "removed_examples_total": sum(
                    r["removed_examples"] for r in results
                ),
                "ambiguous": True,
                "unknown_names": unknown_names,
                "final_message_hint": payload["title"],
            }

        if unknown_names and not results:
            return {
                "ok": False, "results": [],
                "error": "unknown_name",
                "unknown_names": unknown_names,
                "final_message_hint": _msg(
                    "MSG_PERSONS_UNKNOWN_NAME",
                    name=", ".join(unknown_names),
                ),
            }

        return {
            "ok": True, "results": results,
            "n_removed": len(results),
            "removed_examples_total": sum(
                r["removed_examples"] for r in results
            ),
            "unknown_names": unknown_names,
        }
    finally:
        reg.close()


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
