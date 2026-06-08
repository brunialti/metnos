#!/usr/bin/env python3
"""set_persons — enrolla una persona nel registro nominale per ricerche per nome.

ADR 0086 (indici di dominio) + ADR 0090 (engine UI dichiarativo) + persons
registry (PR1/PR2). Storage in `~/.local/share/metnos/persons.sqlite` via
`runtime/persons_registry.PersonsRegistry`.

Pipeline:
    1. Per ogni path in `paths`: open → sha256 byte → ArcFace detect_faces.
    2. 0 volti  -> errors[]
    3. 1 volto  -> enroll diretto.
    4. >1 volto -> se face_choices[path] noto, usa quello; altrimenti
                   accumula in pending_choices.
    5. Se pending_choices non vuoto -> ritorna decision="needs_inputs"
       con get_inputs payload (un step `choice` per ogni path multi-volto).
    6. Altrimenti enroll() per ogni path risolto.

Determinismo §7.9: niente LLM. Detection ML lazy via face_embedding.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg
from persons_registry import (
    PersonsRegistry,
    WARN_EXAMPLES_PER_PERSON,
    slugify,
)


# Test isolation via env vars (8/5/2026): vedi runtime/config.py.
# `_persons_db_path()` ritorna None se METNOS_USER_DATA NON e' settato,
# cosi' PersonsRegistry usa il suo DEFAULT_DB_PATH (che i test legacy
# monkeypatchano direttamente sul modulo `persons_registry`).
def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _read_image_bytes(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bbox_to_tuple(bbox) -> tuple[int, int, int, int]:
    """ArcFace bbox e' (x, y, w, h) come tuple di float; storage vuole int."""
    x, y, w, h = bbox
    return (int(x), int(y), int(w), int(h))


def _detect_faces_for_path(path: Path):
    """Ritorna `list[dict]` con bbox + embedding per ogni volto rilevato.

    Lazy import di face_embedding: se i modelli non ci sono o l'engine
    fallisce, ritorna [] e segnala via errors[] al caller.
    """
    try:
        from face_embedding import get_face_engine
    except ImportError:
        return None, "face_embedding module non importabile"
    engine = get_face_engine()
    if not engine.available:
        return None, "FaceEngine model pack non installato"
    try:
        faces = engine.detect_faces(path)
    except Exception as e:  # noqa: BLE001 (propaga qualunque crash come error)
        return None, f"detect_faces crash: {type(e).__name__}: {e}"
    return faces, None


def invoke(args):
    name = args.get("name") or ""
    paths = args.get("paths") or []
    mode = args.get("mode", "add") or "add"
    face_choices = args.get("face_choices") or {}

    # Validation deterministica (§7.9)
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="name")}
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="paths")}
    if mode not in ("add", "replace"):
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="mode", allowed="add | replace")}

    # Slug derivation: validazione precoce (rifiuta nomi non slugifiable)
    try:
        slug_check = slugify(name)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="name", reason=str(e))}

    # Dry-run short-circuit: NIENTE write su persons.sqlite. Ritorna preview
    # delle enroll che AVREMMO fatto basandosi su file path validi (no detect).
    if bool(args.get("dry_run")) or _is_dry_run():
        would_enroll: list[dict] = []
        skipped: list[dict] = []
        for p in paths:
            ps = os.path.expanduser(str(p))
            pp = Path(ps)
            if not pp.exists():
                skipped.append({"path": ps, "reason": _msg("ERR_PATH_NOT_FOUND", path=ps)})
                continue
            would_enroll.append({
                "name": name,
                "slug": slug_check,
                "path": ps,
                "embedding_dim": 512,
            })
        return {
            "ok": True,
            "dry_run": True,
            "would_enroll": would_enroll,
            "skipped": skipped,
            "name": name,
            "slug": slug_check,
            "n_paths": len(paths),
            "mode": mode,
        }

    reg = PersonsRegistry(db_path=_persons_db_path())
    try:
        results: list[dict] = []
        errors: list[dict] = []
        pending_choices: list[dict] = []
        # Per build del get_inputs payload: ogni step ha un `var` univoco.
        # Usiamo l'indice del path nella lista paths come var key.
        # Caller risponde con face_choices = {path_str: face_idx_int}.

        for idx, p in enumerate(paths):
            ps = os.path.expanduser(str(p))
            pp = Path(ps)
            if not pp.exists():
                errors.append({"path": ps, "error": _msg("ERR_PATH_NOT_FOUND", path=ps)})
                continue
            try:
                data = _read_image_bytes(pp)
            except OSError as e:
                errors.append({"path": ps, "error": f"read error: {e}"})
                continue
            sha = _sha256_hex(data)

            faces, err = _detect_faces_for_path(pp)
            if err is not None:
                errors.append({"path": ps, "error": err})
                continue
            if not faces:
                errors.append({
                    "path": ps,
                    "error": "no_face_detected",
                    "message": _msg("MSG_PERSONS_NO_FACE", path=ps),
                })
                continue
            if len(faces) == 1:
                f = faces[0]
                results.append({
                    "path": ps, "sha256": sha, "face_idx": 0,
                    "bbox": _bbox_to_tuple(f["bbox"]),
                    "embedding": f["embedding"],
                })
                continue
            # >1 volto: se face_choices ha gia' la scelta, usa quella
            chosen = face_choices.get(ps)
            if chosen is None:
                # Anche per indice numerico (str dict): get_inputs ritorna
                # i values come {var: value} ma var sara' "p<idx>" (stabile).
                chosen = face_choices.get(f"p{idx}")
            if chosen is not None:
                try:
                    fi = int(chosen)
                except (ValueError, TypeError):
                    errors.append({
                        "path": ps,
                        "error": _msg("ERR_ARG_INVALID", arg="face_choices", reason=str(chosen)),
                    })
                    continue
                if fi < 0 or fi >= len(faces):
                    errors.append({
                        "path": ps,
                        "error": _msg("ERR_ARG_INVALID", arg="face_choices", reason=str(fi)),
                    })
                    continue
                f = faces[fi]
                results.append({
                    "path": ps, "sha256": sha, "face_idx": fi,
                    "bbox": _bbox_to_tuple(f["bbox"]),
                    "embedding": f["embedding"],
                })
                continue
            # Pending: accumula per il dialog
            pending_choices.append({
                "path": ps, "var": f"p{idx}",
                "n_faces": len(faces),
                "faces": [
                    {"face_idx": i, "bbox": _bbox_to_tuple(f["bbox"]),
                     "score": float(f.get("score", 0.0))}
                    for i, f in enumerate(faces)
                ],
            })

        # Caso (5): pending_choices non vuoto -> needs_inputs con preview
        # (PR5): ogni opzione mostra la miniatura del volto detected,
        # cosi' l'utente vede DIRETTAMENTE il volto invece di leggere
        # bbox numerici astratti.
        if pending_choices:
            dialog = []
            for pc in pending_choices:
                bn = pc["path"].rsplit("/", 1)[-1]
                opts_pv: list[dict] = []
                for f in pc["faces"]:
                    bx, by, bw, bh = f["bbox"]
                    preview = f"{pc['path']}#bbox={bx},{by},{bw},{bh}"
                    opts_pv.append({
                        "value": int(f["face_idx"]),
                        "label": f"Volto {int(f['face_idx'])+1} "
                                 f"({bw}x{bh} px, score {float(f['score']):.2f})",
                        "preview_image_path": preview,
                    })
                dialog.append({
                    "var": pc["var"],
                    "prompt": f"Scegli quale volto in {bn} è '{name}':",
                    "schema": {"kind": "choice_with_preview",
                                "options": opts_pv,
                                "context_image_path": pc["path"]},
                })
            payload = {
                "title": _msg("MSG_PERSONS_NEEDS_FACE_CHOICE", name=name),
                "dialog": dialog,
                "fmt": "auto",
                "on_complete": {
                    "type": "resume_executor_with_values",
                    "executor": "set_persons",
                    "args_base": {
                        "name": name,
                        "paths": [str(p) for p in paths],
                        "mode": mode,
                        # face_choices verra' popolato dal merge dei values
                        # raccolti dal dialog (key=var, value=label scelto).
                        # Il caller deve mappare label->idx; per semplicita'
                        # accettiamo direttamente l'idx nel value (il choice
                        # adapter di get_inputs ritorna l'indice 0-based del
                        # label scelto in `_idx`-suffix var; o il label stesso).
                        "face_choices": dict(face_choices),
                    },
                    "merge_into": "face_choices",
                },
            }
            return {
                "ok": True,
                "decision": "needs_inputs",
                "needs_inputs": payload,
                "results": [],
                "errors": errors,
                "pending_choices": pending_choices,
                "final_message_hint": payload["title"],
            }

        # Enroll fase deterministica
        enrolled: list[dict] = []
        n_examples_after = 0
        for r in results:
            try:
                out = reg.enroll(
                    name=name,
                    image_path=r["path"],
                    face_box=r["bbox"],
                    embedding=r["embedding"],
                    sha256=r["sha256"],
                    mode=mode,
                )
            except (ValueError, RuntimeError) as e:
                errors.append({"path": r["path"], "error": f"enroll: {e}"})
                continue
            enrolled.append({
                "path": r["path"],
                "slug": out["slug"],
                "name": out["name"],
                "face_box": list(r["bbox"]),
                "added": out["added"],
            })
            n_examples_after = out["n_examples"]
            # mode="replace" applicato solo al primo enroll: i successivi
            # sono accumulativi (the design guide §7.9, semantica registry PR1).
            mode = "add"

        out_dict: dict = {
            "ok": True,
            "results": enrolled,
            "errors": errors,
            "n_examples_after": n_examples_after,
        }
        if n_examples_after >= WARN_EXAMPLES_PER_PERSON:
            out_dict["warn"] = "examples_>=50"
            out_dict["final_message_hint"] = _msg(
                "MSG_PERSONS_EXAMPLES_LIMIT", name=name,
            )
        return out_dict
    finally:
        reg.close()


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    # Embeddings non vanno in stdout: non sono serializzabili JSON e non
    # servono al caller (sono persistiti nel DB).
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
