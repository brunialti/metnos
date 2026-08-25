"""change_applier_extend — patch in-place del manifest TOML per estendere
un executor esistente con un nuovo arg (ADR 0158, Fase 2.2).

Strategia conservativa (no parser TOML lossy):
  - Append della nuova sezione `[args.properties.<arg>]` in fondo al
    manifest. TOML 1.0 ammette sezioni nuove in qualsiasi posizione
    purche' non duplichino sezioni esistenti.
  - Backup del manifest pre-modifica in
    `~/.local/share/metnos/rollback_blobs/<sha8>-<executor>.toml`.
  - Pubblicazione esclusivamente tramite il confine operativo Birth F4.

Limitazioni MVP:
  - Solo aggiunta di arg string/boolean/array semplici (no nested).
  - Niente aggiunta a `args.required` (default invece di required).
  - Niente live reload del catalog (next service restart o reload manuale).

Idempotente: se il manifest contiene gia' la sezione `[args.properties.<arg>]`,
short-circuit con `already_extended`.
"""
from __future__ import annotations

import hashlib
import contextlib
import shutil
import stat
import tempfile
import time
import tomllib
from pathlib import Path, PurePosixPath

import config as C
from change_intents import ChangeIntent
from executor_birth_intent import (
    BirthIntent, require_birth_intent_adapter, submit_birth_intent,
)


# Tipo arg → snippet TOML
_TYPE_TEMPLATES = {
    "string": """
[args.properties.{arg}]
type        = "string"
default     = ""

[args.properties.{arg}.description]
it = "{desc_it}"
en = "{desc_en}"
""",
    "boolean": """
[args.properties.{arg}]
type        = "boolean"
default     = false

[args.properties.{arg}.description]
it = "{desc_it}"
en = "{desc_en}"
""",
    "array": """
[args.properties.{arg}]
type        = "array"

[args.properties.{arg}.description]
it = "{desc_it}"
en = "{desc_en}"
""",
    "integer": """
[args.properties.{arg}]
type        = "integer"
default     = 0

[args.properties.{arg}.description]
it = "{desc_it}"
en = "{desc_en}"
""",
}


def _resolve_executor_dir(name: str) -> Path | None:
    """Ricerca manifest_dir per executor `name` in:
      - executors/<name>/  (handcrafted)
      - ~/.local/share/metnos/executors/<name>/  (synthesized)
    """
    candidates = [
        C.PATH_EXECUTORS / name,
        C.PATH_SYNTH_EXECUTORS / name,
    ]
    for cand in candidates:
        if (cand / "manifest.toml").is_file():
            return cand
    return None


def _run_birth(intent: BirthIntent):
    result = submit_birth_intent(intent)
    if (getattr(result, "error_code", "invalid_result") is not None
            or getattr(result, "publication", None) is None):
        detail = getattr(result, "error_code", "invalid_result")
        raise RuntimeError(f"birth rejected: {detail}")
    return result


def _contract_id(manifest_path: Path):
    from manifest_inventory import (
        inventory_authoring_manifests, manifest_ref_for_source_path,
    )
    return manifest_ref_for_source_path(
        inventory_authoring_manifests(), manifest_path,
    ).contract_id


@contextlib.contextmanager
def _stage_candidate(source_root: Path, manifest_bytes: bytes):
    """Build a private, closed candidate without mutating authoring."""
    parsed = tomllib.loads(manifest_bytes.decode("utf-8"))
    files = ((parsed.get("code") or {}).get("files") or [])
    if not isinstance(files, list) or not files:
        raise RuntimeError("candidate has no code.files")
    parent = C.PATH_USER_DATA / "birth_staging"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="change-extend-", dir=parent))
    try:
        payloads = {"manifest.toml": manifest_bytes}
        for relative in ("manifest.lang_state.json", *files):
            pure = PurePosixPath(relative) if isinstance(relative, str) else None
            if (pure is None or not relative or pure.is_absolute()
                    or pure.as_posix() != relative or "\\" in relative
                    or any(part in {"", ".", ".."} for part in pure.parts)):
                raise RuntimeError(f"candidate path invalid: {relative}")
            source = source_root.joinpath(*pure.parts)
            cursor = source_root
            for component in pure.parts[:-1]:
                cursor /= component
                parent_status = cursor.lstat()
                if not stat.S_ISDIR(parent_status.st_mode):
                    raise RuntimeError(f"candidate parent invalid: {relative}")
            status = source.lstat()
            if (not stat.S_ISREG(status.st_mode) or status.st_nlink != 1
                    or source.is_symlink()):
                raise RuntimeError(f"candidate file is not private regular data: {relative}")
            payloads[relative] = source.read_bytes()
        for relative, payload in payloads.items():
            destination = staging / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(payload)
            destination.chmod(0o600)
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def extend_executor_manifest(ci: ChangeIntent) -> dict:
    """Applica extend_executor: append section + rollback_blob + re-sign.

    Body atteso:
      - arg_name: str           — nome del nuovo arg
      - arg_type: str           — string|boolean|array|integer (default "string")
      - desc_it / desc_en: str  — descrizioni (default usa intent_summary)
    """
    body = ci.intent_body or {}
    target = ci.intent_target
    arg_name = body.get("arg_name")
    arg_type = (body.get("arg_type") or "string").lower()
    if not arg_name:
        raise ValueError(f"extend_executor needs arg_name in body — got {body}")
    if arg_type not in _TYPE_TEMPLATES:
        raise ValueError(f"unsupported arg_type={arg_type}; expected {list(_TYPE_TEMPLATES)}")
    if not arg_name.replace("_", "").isalnum():
        raise ValueError(f"arg_name invalid: {arg_name} (must be alnum + underscore)")

    mdir = _resolve_executor_dir(target)
    if mdir is None:
        raise RuntimeError(f"executor manifest dir not found for {target}")
    manifest_path = mdir / "manifest.toml"
    text = manifest_path.read_text(encoding="utf-8")
    # Fail closed before creating blobs or changing authoring. The producer
    # supplies only data; receipt, trust and publisher stay core-owned.
    require_birth_intent_adapter()
    contract_id = _contract_id(manifest_path)

    # Idempotenza: verifica se sezione gia' presente
    marker = f"[args.properties.{arg_name}]"
    if marker in text:
        # Un tentativo precedente puo' avere lasciato la sorgente candidata.
        # Il retry deve riattraversare Birth: la receipt one-use rende un
        # replay della stessa richiesta un rifiuto verificabile.
        with _stage_candidate(mdir, text.encode("utf-8")) as staging:
            _run_birth(BirthIntent(
                candidate_source_root=staging, contract_id=contract_id,
                actor="change_applier",
                reason=f"extend_executor retry change_intent={ci.id}",
                operation="extend",
                approval_refs=(ci.id,),
            ))
        return {
            "executor_name": target,
            "manifest_path": str(manifest_path),
            "already_extended": True,
            "arg_name": arg_name,
        }

    # Verifica che il manifest parsi prima di modificarlo
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"manifest {manifest_path} not valid TOML: {exc}")
    if "args" not in parsed or "properties" not in parsed.get("args", {}):
        raise RuntimeError(f"manifest {manifest_path} has no [args.properties]")

    desc_it = (body.get("desc_it")
               or ci.intent_summary
               or f"Argomento {arg_name} aggiunto (extend automatico).")
    desc_en = (body.get("desc_en")
               or ci.intent_summary
               or f"Argument {arg_name} added (automatic extend).")
    # Escape double quotes nei desc (per evitare TOML break)
    desc_it = desc_it.replace('"', "'")
    desc_en = desc_en.replace('"', "'")

    # Rollback blob (backup pre-modifica)
    rollback_dir = C.PATH_USER_DATA / "rollback_blobs"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    sha8 = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    rollback_path = rollback_dir / f"{sha8}-{target}.toml"
    rollback_path.write_text(text, encoding="utf-8")

    # Append nuova sezione
    snippet = _TYPE_TEMPLATES[arg_type].format(
        arg=arg_name, desc_it=desc_it, desc_en=desc_en,
    )
    new_text = text.rstrip() + "\n" + snippet
    # Verifica TOML parsabile senza scrivere l'authoring.
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"post-extend manifest TOML invalid: {exc}")

    # Rendi viva la modifica esclusivamente attraverso Birth.
    try:
        with _stage_candidate(mdir, new_text.encode("utf-8")) as staging:
            result = _run_birth(BirthIntent(
                candidate_source_root=staging, contract_id=contract_id,
                actor="change_applier",
                reason=f"extend_executor change_intent={ci.id}",
                operation="extend",
                approval_refs=(ci.id,),
            ))
    except Exception as exc:
        # Il chiamante non interpreta una failure tardiva né tenta di riparare
        # il live store: lo staging viene eliminato e l'authoring resta sotto
        # l'esclusiva responsabilità del commit/recovery core-owned.
        raise RuntimeError(
            f"birth publication requires retry: {exc}; source retained"
        ) from exc

    publication = result.publication
    assert publication is not None

    return {
        "executor_name": target,
        "manifest_path": str(manifest_path),
        "rollback_blob_path": str(rollback_path),
        "arg_added": arg_name,
        "arg_type": arg_type,
        "new_generation_id": publication.current_generation_id,
        "birth_request_id": result.request_id,
        "publication_repeated": publication.repeated,
        "applied_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "diff_summary": f"+ args.properties.{arg_name} ({arg_type})",
    }
