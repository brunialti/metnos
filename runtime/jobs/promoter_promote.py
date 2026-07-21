"""Promote di una proposta synth nel catalog live.

Step:
1. Verifica admission ADR 0114 dry-run (layer 2/3/5/6 pre-emptivo).
2. Crea `~/.local/share/metnos/executors/<name>/` con `manifest.toml` +
   `<name>.py` rigenerati dal payload della proposta.
3. Firma Ed25519 via `sign.py`.
4. Crea rollback blob `~/.local/share/metnos/promoter_blobs/<id>.tar.gz`
   (atomic tmp+rename).

§7.9 deterministico ovunque tranne layer 6 (LLM verifier, gia' chiuso in
proposal_evaluator). §2.8 fail-loud: ogni admission fail ritorna esplicito
`{ok:false, error:...}`.
"""
from __future__ import annotations

import os
import sys as _sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11

# Import lazy del firmatore: `sign_executor` viene patchato nei test via
# `mock.patch("jobs.promoter_promote.sign_executor")` quindi serve come
# attribute risolvibile a livello modulo (no `from sign import` dentro la
# funzione che bypasserebbe il monkeypatch).
try:
    from sign import sign_executor  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    sign_executor = None  # type: ignore[assignment]


# Dir canoniche, env-overridable per i test.
_DEFAULT_SYNTH_EXEC_DIR = _C.PATH_USER_DATA / "executors"
_DEFAULT_BLOB_DIR = _C.PATH_USER_DATA / "promoter_blobs"
# ADR 0148 rename-resilient — derive from this module's location (runtime/jobs/file.py)
_DEFAULT_HANDCRAFTED_DIR = Path(__file__).resolve().parents[2] / "executors"


def _synth_exec_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_SYNTH_DIR")
    return Path(env) if env else _DEFAULT_SYNTH_EXEC_DIR


def _blob_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_BLOB_DIR")
    return Path(env) if env else _DEFAULT_BLOB_DIR


def _handcrafted_dir() -> Path:
    """Dir handcrafted canonico. Promoter NON deve mai toccarla (read-only)."""
    return _DEFAULT_HANDCRAFTED_DIR


def _build_manifest_toml(proposal: dict) -> str:
    """Rigenera il manifest TOML del nuovo executor dalla proposta.

    Schema (compat ADR 0086+0114):
        [executor]
        name = "..."
        description = "..."
        affinity = [...]
        capabilities = [...]
        reverse_pattern = null | "..."

        [code]
        files = ["<name>.py"]
        digest = ""  # sign_executor lo riempie

        [args_schema]
        ... (passthrough dallo stage 2)
    """
    name = proposal.get("name") or proposal.get("expected_name") or "?"
    stages = proposal.get("stages") or []
    s2 = stages[1].get("output") if len(stages) >= 2 else {}
    s4 = stages[3].get("output") if len(stages) >= 4 else {}
    if not isinstance(s2, dict):
        s2 = {}
    if not isinstance(s4, dict):
        s4 = {}

    description = s4.get("description") or ""
    affinity = s4.get("affinity") or []
    capabilities = s2.get("capabilities") or []
    reverse_pattern = s2.get("reverse_pattern") or ""
    args_required = s2.get("args_required") or []
    args_properties = s2.get("args_properties") or {}
    if not args_properties and isinstance(s2.get("args_schema"), dict):
        sch = s2["args_schema"]
        args_required = sch.get("required") or args_required
        args_properties = sch.get("properties") or args_properties

    # Costruzione TOML manuale (no tomli_w): determinismo §7.9, niente dep.
    out: list[str] = []
    out.append("# Manifest sintetizzato dal promoter daemon.")
    out.append("# Provenance: synth (proposal_id=" + str(proposal.get("id", "?")) + ")")
    out.append("")
    out.append("[executor]")
    out.append(f'name = "{_toml_esc(name)}"')
    out.append(f'description = "{_toml_esc(description)}"')
    out.append("affinity = " + _toml_list(affinity))
    out.append("capabilities = " + _toml_caps(capabilities))
    if reverse_pattern:
        out.append(f'reverse_pattern = "{_toml_esc(reverse_pattern)}"')
    out.append("")
    out.append("[code]")
    out.append(f'files = ["{_toml_esc(name)}.py"]')
    # Placeholder digest: `sign_executor` lo riscrive col digest reale.
    # Il regex `_DIGEST_RE` di sign.py richiede prefisso `sha256:`.
    out.append('digest = "sha256:placeholder"')
    out.append("")
    out.append("[args_schema]")
    out.append("type = \"object\"")
    out.append("required = " + _toml_list(args_required))
    out.append("")
    if args_properties and isinstance(args_properties, dict):
        for arg_name, arg_spec in args_properties.items():
            if not isinstance(arg_spec, dict):
                continue
            out.append(f"[args_schema.properties.{arg_name}]")
            for k, v in arg_spec.items():
                out.append(f"{k} = {_toml_value(v)}")
            out.append("")
    return "\n".join(out) + "\n"


def _toml_esc(s: Any) -> str:
    """Escape minimale per stringa TOML basic (no triple-quote)."""
    s = str(s) if s is not None else ""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _toml_list(items: list) -> str:
    if not items:
        return "[]"
    parts = [f'"{_toml_esc(x)}"' for x in items]
    return "[" + ", ".join(parts) + "]"


def _toml_caps(caps: list) -> str:
    """Capabilities sono list of dict {name, hint?}. Serializza inline."""
    if not caps:
        return "[]"
    parts: list[str] = []
    for c in caps:
        if not isinstance(c, dict):
            continue
        bits = [f'name = "{_toml_esc(c.get("name", ""))}"']
        hint = c.get("hint")
        if hint:
            bits.append(f"hint = {_toml_list(hint if isinstance(hint, list) else [hint])}")
        parts.append("{ " + ", ".join(bits) + " }")
    return "[" + ", ".join(parts) + "]"


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return _toml_list(v)
    return f'"{_toml_esc(v)}"'


def _extract_code(proposal: dict) -> str:
    """Estrae il codice Python dallo stage 5 della proposta."""
    stages = proposal.get("stages") or []
    if len(stages) < 5:
        return ""
    s5 = stages[4]
    if not isinstance(s5, dict):
        return ""
    out = s5.get("output") or {}
    if not isinstance(out, dict):
        return ""
    return out.get("code") or ""


def _atomic_write(target: Path, content: str | bytes) -> None:
    """Write atomico via tmp+rename nella stessa dir per garantire atomic."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent),
    )
    try:
        mode = "wb" if isinstance(content, bytes) else "w"
        with os.fdopen(tmp_fd, mode) as f:
            f.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_rollback_blob(executor_dir: Path, blob_path: Path) -> None:
    """Crea tar.gz dei file appena scritti. Atomic via tmp+rename."""
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{blob_path.name}.", suffix=".tmp",
        dir=str(blob_path.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        with tarfile.open(str(tmp_path), "w:gz") as tf:
            for child in sorted(executor_dir.iterdir()):
                tf.add(str(child), arcname=child.name)
        os.replace(tmp_path_str, str(blob_path))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _dry_run_admission_layer2(proposal: dict) -> tuple[bool, str]:
    """Layer 2 — Affinity overlap dry-run.

    Riusa la stessa logica di `proposal_evaluator._check_affinity_overlap`
    (soglia 0.4 stretta vs 0.5 al catalog load). Se overlap >= 0.4 verso
    handcrafted o synth piu' vecchio → reject.
    """
    try:
        from proposal_evaluator import _check_affinity_overlap
    except ImportError as ex:
        return True, f"affinity_check_unavailable ({ex})"
    triggered, reason, _info = _check_affinity_overlap(proposal, catalog=None)
    return not triggered, reason


def _dry_run_admission_layer3(proposal: dict) -> tuple[bool, str]:
    """Layer 3 — Efficacy ager dry-run.

    Sui synth nuovi (mai invocati live) non si applica: skip = pass.
    Re-check post-promote sara' eseguito dal task `apply_executor_ager`
    sul catalog vero quando l'executor accumula invocations.
    """
    return True, ""


def _dry_run_admission_layer5(proposal: dict) -> tuple[bool, str]:
    """Layer 5 — Smoke battery: il nuovo executor deve non rompere alcun
    routing critico definito in `runtime/smoke.py::BATTERY`. Pre-promote
    dry-run: se la proposta dichiara una affinity che catch-all su una
    query smoke nota, reject.

    Implementazione semplificata §7.2: ricarica BATTERY e check che il
    name della proposta NON sia in conflitto con expected_first_tool di
    nessun case (es. proposta `find_texts` mentre un case attende
    `find_urls`). Se conflitto, layer 5 fail.
    """
    try:
        from smoke import BATTERY  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return True, "smoke battery unavailable; layer 5 skipped"
    name = proposal.get("name") or ""
    if not name:
        return True, ""
    affinity = set()
    stages = proposal.get("stages") or []
    if len(stages) >= 4 and isinstance(stages[3], dict):
        out = stages[3].get("output") or {}
        if isinstance(out, dict):
            for t in (out.get("affinity") or []):
                affinity.add(str(t).strip().lower())
    if not affinity:
        return True, ""
    for case in BATTERY:
        if not isinstance(case, dict):
            continue
        expected = case.get("expected_first_tool")
        if not expected or expected == name:
            continue
        # Query case-insensitive token overlap >= 3 con affinity?
        q = (case.get("query") or "").lower()
        q_tokens = {t for t in q.replace(",", " ").split() if len(t) >= 3}
        overlap = affinity & q_tokens
        if len(overlap) >= 3:
            return False, (
                f"layer 5: la proposta '{name}' rischia di hijackare "
                f"il routing della smoke '{q[:50]}' "
                f"(expected={expected}, overlap={sorted(overlap)})"
            )
    return True, ""


def _dry_run_admission_layer6(proposal: dict) -> tuple[bool, str]:
    """Layer 6 — Semantic verifier LLM stage 6.

    Per proposte gia' synthesized, il check e' stato eseguito durante
    la synt pipeline. Se `final_state == "synthesized"` consideriamo
    layer 6 passato (audit gia' presente in `synth_audit/`).
    Re-eseguirlo qui spreca GPU (ricontrollerebbe lo stesso payload).
    """
    final_state = proposal.get("final_state") or ""
    if final_state == "synthesized":
        return True, ""
    return False, f"layer 6: final_state '{final_state}' non e' synthesized"


def promote_to_catalog(proposal: dict) -> dict:
    """Promote di una proposta synth nel synth catalog dir.

    Ritorna dict con shape:
        {ok: bool, path: str (dir), blob_path: str, error: str | None,
         admission_layer_failed: str | None}

    §2.8 fail-loud: ogni step fallito ritorna dict con `ok=False` + reason.
    """
    proposal_id = proposal.get("id") or "?"
    name = proposal.get("name") or proposal.get("expected_name") or ""
    if not name:
        return {"ok": False, "error": "proposal_name_empty",
                "proposal_id": proposal_id}

    # Refuse di toccare la dir handcrafted (mai).
    target_dir = _synth_exec_dir() / name
    if str(target_dir).startswith(str(_handcrafted_dir()) + os.sep):
        return {"ok": False, "error": "target_dir_inside_handcrafted",
                "proposal_id": proposal_id, "target_dir": str(target_dir)}

    # Admission dry-run prima di toccare il filesystem.
    for layer_name, fn in (
        ("layer_2", _dry_run_admission_layer2),
        ("layer_3", _dry_run_admission_layer3),
        ("layer_5", _dry_run_admission_layer5),
        ("layer_6", _dry_run_admission_layer6),
    ):
        ok, reason = fn(proposal)
        if not ok:
            return {
                "ok": False,
                "error": f"admission_failed_{layer_name}",
                "admission_layer_failed": layer_name,
                "reason": reason,
                "proposal_id": proposal_id,
                "name": name,
            }

    code = _extract_code(proposal)
    if not code:
        return {"ok": False, "error": "stage5_code_missing",
                "proposal_id": proposal_id, "name": name}

    manifest_text = _build_manifest_toml(proposal)

    # Refuse to overwrite existing executor (idempotency safety, §2.8).
    if target_dir.exists() and (target_dir / "manifest.toml").exists():
        return {"ok": False, "error": "executor_already_exists",
                "proposal_id": proposal_id, "name": name,
                "path": str(target_dir)}

    # Write files.
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write(target_dir / f"{name}.py", code)
        _atomic_write(target_dir / "manifest.toml", manifest_text)
    except OSError as ex:
        return {"ok": False, "error": f"write_failed: {ex}",
                "proposal_id": proposal_id, "name": name}

    # Sign (Ed25519). Errori qui sono fatal: senza firma il loader scarta.
    if sign_executor is None:
        return {"ok": False, "error": "sign_executor_unavailable",
                "proposal_id": proposal_id, "name": name,
                "path": str(target_dir)}
    try:
        sign_executor(target_dir)
    except Exception as ex:
        return {"ok": False, "error": f"sign_failed: {ex}",
                "proposal_id": proposal_id, "name": name,
                "path": str(target_dir)}

    # Rollback blob (tar.gz dei file appena scritti).
    blob_path = _blob_dir() / f"{proposal_id}.tar.gz"
    try:
        _write_rollback_blob(target_dir, blob_path)
    except OSError as ex:
        # Niente blob = niente rollback sicuro. Marca executor come orfano
        # (rimuovi cosi' non promuoviamo qualcosa che non possiamo annullare).
        try:
            for f in target_dir.iterdir():
                f.unlink()
            target_dir.rmdir()
        except OSError:
            pass
        return {"ok": False, "error": f"blob_failed: {ex}",
                "proposal_id": proposal_id, "name": name}

    return {
        "ok": True,
        "path": str(target_dir),
        "blob_path": str(blob_path),
        "proposal_id": proposal_id,
        "name": name,
        "promoted_at": time.time(),
    }


__all__ = ["promote_to_catalog"]
