"""change_applier_extend — patch in-place del manifest TOML per estendere
un executor esistente con un nuovo arg (ADR 0158, Fase 2.2).

Strategia conservativa (no parser TOML lossy):
  - Append della nuova sezione `[args.properties.<arg>]` in fondo al
    manifest. TOML 1.0 ammette sezioni nuove in qualsiasi posizione
    purche' non duplichino sezioni esistenti.
  - Backup del manifest pre-modifica in
    `~/.local/share/metnos/rollback_blobs/<sha8>-<executor>.toml`.
  - Pubblicazione tramite il confine canonico
    `sign.publish_authoring_update(manifest_dir)`.

Limitazioni MVP:
  - Solo aggiunta di arg string/boolean/array semplici (no nested).
  - Niente aggiunta a `args.required` (default invece di required).
  - Niente live reload del catalog (next service restart o reload manuale).

Idempotente: se il manifest contiene gia' la sezione `[args.properties.<arg>]`,
short-circuit con `already_extended`.
"""
from __future__ import annotations

import hashlib
import time
import tomllib
from pathlib import Path

import config as C
from change_intents import ChangeIntent


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
    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    layout = resolve_manifest_layout()
    manifest_path = mdir / "manifest.toml"
    text = manifest_path.read_text(encoding="utf-8")

    # Idempotenza: verifica se sezione gia' presente
    marker = f"[args.properties.{arg_name}]"
    if marker in text:
        if layout is ManifestLayout.STORE_ONLY:
            # Un tentativo precedente puo' avere committato la generazione ma
            # fallito durante la riconciliazione finale. Il retry deve
            # riattraversare il publisher, non fermarsi sul file authoring.
            from sign import publish_authoring_update
            try:
                publish_authoring_update(mdir)
            except Exception as exc:
                raise RuntimeError(
                    f"publication outcome requires retry: {exc}; source retained"
                ) from exc
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
    manifest_path.write_text(new_text, encoding="utf-8")

    # Verifica TOML parsabile (rollback se rotto)
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        manifest_path.write_text(text, encoding="utf-8")
        raise RuntimeError(f"post-extend manifest TOML invalid: {exc}; rolled back")

    # Rendi viva la modifica attraverso l'unico confine dipendente dal layout:
    # firma nel layout legacy, pubblicazione immutabile dopo il cutover.
    try:
        from sign import publish_authoring_update
        digest, sig_path, _publication = publish_authoring_update(mdir)
    except Exception as exc:
        if layout is ManifestLayout.AUTHORING:
            # Nel layout legacy la firma e' l'ultimo passo e il ripristino
            # conserva il contratto precedentemente ammesso.
            manifest_path.write_text(text, encoding="utf-8")
            raise RuntimeError(
                f"re-sign failed: {exc}; manifest restored"
            ) from exc
        # Dopo l'ingresso nel publisher un'eccezione puo' seguire il commit.
        # Non riscrivere la sorgente: il retry idempotente ripara mirror e
        # registro senza creare divergenza con la generazione viva.
        raise RuntimeError(
            f"publication outcome requires retry: {exc}; source retained"
        ) from exc

    return {
        "executor_name": target,
        "manifest_path": str(manifest_path),
        "rollback_blob_path": str(rollback_path),
        "arg_added": arg_name,
        "arg_type": arg_type,
        "new_digest": digest,
        "sig_path": str(sig_path),
        "applied_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "diff_summary": f"+ args.properties.{arg_name} ({arg_type})",
    }
