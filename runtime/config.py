#!/usr/bin/env python3
"""config — costanti e path centralizzati di Metnos.

Fonte UNICA di:
  - paths assoluti (root install, workspace, user data, state, config)
  - costanti di tuning (cap, soglie, decay, timeout) con rationale inline
  - env override (METNOS_*) per deploy diversi

Pattern: ogni modulo importa `from config import C`. Niente magic number
nei moduli applicativi; niente path hardcoded sparsi.

Convenzioni:
  - C.PATH_*           Path assoluti (Path objects, non str)
  - C.DB_*             percorsi DB sqlite (sotto user state/data)
  - C.CAP_*            limiti runtime (loop break, max items)
  - C.WEIGHT_*         pesi e soglie mnestoma (decay, reinforce, archive)
  - C.TIMEOUT_*        timeout in secondi (task, request, push)
  - C.DEFAULT_*        default dei tier (lang, channel, llm tier)

Env override (tutti opzionali):
  METNOS_HOME           override root install (default <install_root>)
  METNOS_USER_DATA      override ~/.local/share/metnos
  METNOS_USER_STATE     override ~/.local/state/metnos
  METNOS_USER_CONFIG    override ~/.config/metnos
  METNOS_LANG           bootstrap lingua d'istanza (default 'it'); una
                        richiesta firmata valida diventa poi autorevole
  METNOS_CAP_STEPS      max step per turno (default 30)
  METNOS_LOG_LEVEL      logger root (DEBUG|INFO|WARNING|ERROR; default INFO)
  METNOS_LOG_FILE       path file log (default journal-only)
  METNOS_INDEX_ROOT     override <USER_DATA>/index (storage indici di dominio)
  METNOS_DRY_RUN        "1" → executor write short-circuitano (no side effects)
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else default


def env_int(name: str, default: int) -> int:
    """Parse an integer environment override with a stable fallback.

    Domain-specific bounds remain at the call site: a token budget may clamp
    while a manifest limit must reject non-positive values. Parsing malformed
    values has one implementation.
    """
    v = os.environ.get(name)
    try:
        return int(v) if v else default
    except (ValueError, TypeError):
        return default


# Compatibility for constants and older imports; new modules use ``env_int``.
_env_int = env_int


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Root paths -----------------------------------------------------------

# Install root (codice + executor canonici + decisions).
# Default auto-derived da `Path(__file__).resolve().parents[1]` — questo file
# vive in <PATH_ROOT>/runtime/config.py, quindi `parents[1]` ricava la root
# senza ipotesi sul nome del path. Cambiare il filesystem layout (es.
# rinomina <install_root> → /opt/metnos) non richiede nessuna config: il codice
# si trova da solo.
# Override esplicito via env METNOS_INSTALL_ROOT (preferito, ADR 0148);
# l'alias METNOS_HOME e' deprecato — viene letto solo se METNOS_INSTALL_ROOT
# non e' settato, per back-compat finche' tutti gli script downstream non
# sono passati al nuovo nome.
_AUTO_ROOT         = Path(__file__).resolve().parents[1]
PATH_ROOT          = _env_path(
    "METNOS_INSTALL_ROOT",
    _env_path("METNOS_HOME", _AUTO_ROOT),
)
PATH_RUNTIME       = PATH_ROOT / "runtime"
PATH_EXECUTORS     = PATH_ROOT / "executors"
PATH_WORKSPACE     = _env_path("METNOS_WORKSPACE", PATH_ROOT / "workspace")
PATH_DECISIONS     = PATH_ROOT / "decisions"
PATH_DOCS          = PATH_ROOT / "docs"

def _home() -> Path:
    """Home robusta (specchio di `path_alias._home`, §7.2 senza import inverso):
    `Path.home()` solleva RuntimeError nella sandbox del device (env
    HOME/USERPROFILE strippati) e config è nel bundle shim — un crash a
    MODULE-LOAD ammazzerebbe ogni executor files sul device (visto live 5/7,
    PC Windows reale). Fallback onesto: env nativi → drive di sistema → cwd.
    Sul server Path.home() funziona sempre: comportamento invariato."""
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        for var in ("HOME", "USERPROFILE"):
            v = os.environ.get(var)
            if v and v.strip():
                return Path(v)
        updrive = (os.environ.get("HOMEDRIVE", "")
                   + os.environ.get("HOMEPATH", "")).strip()
        if updrive:
            return Path(updrive)
        drive = os.environ.get("SystemDrive") or ""
        return Path(drive + os.sep) if drive else Path.cwd()


# User XDG paths (per-utente, scrivibili senza sudo)
PATH_USER_DATA     = _env_path("METNOS_USER_DATA",
                                _home() / ".local" / "share" / "metnos")
PATH_USER_STATE    = _env_path("METNOS_USER_STATE",
                                _home() / ".local" / "state" / "metnos")
PATH_USER_CONFIG   = _env_path("METNOS_USER_CONFIG",
                                _home() / ".config" / "metnos")
PATH_USER_CACHE    = _env_path(
    "METNOS_USER_CACHE",
    Path(os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache")) / "metnos",
)

# Signed, instance-wide localization request (RM-0005 F0).  This file is the
# persistent authority once present and valid; METNOS_LANG remains the safe
# bootstrap source for existing installations that predate the request.
PATH_LOCALIZATION_REQUEST = (
    PATH_USER_STATE / "i18n" / "localization_request.json"
)
LOCALIZATION_REQUEST_SCHEMA = "metnos.localization-request/1"
LOCALIZATION_STATES = frozenset({"active", "bootstrap_english"})
BOOTSTRAP_LANGUAGE = "en"


def normalize_language_tag(value: str | None) -> str:
    """Validate and normalize a structural BCP-47 language tag.

    The grammar is locale-neutral: it does not contain a language allowlist.
    Storage uses lowercase because BCP-47 comparison is case-insensitive and
    the SQLite catalogs and filesystem registries use the same stable form.
    Empty, private-use-only, duplicate, or malformed tags return ``""`` so a
    bad administrative value can be rejected without preventing boot.
    """

    candidate = str(value or "").strip().replace("_", "-")
    if not candidate or not candidate.isascii():
        return ""
    parts = candidate.split("-")
    if any(not re.fullmatch(r"[A-Za-z0-9]{1,8}", part) for part in parts):
        return ""
    if parts[0].casefold() == "x":
        # A private-use token does not identify the language whose corpus must
        # be materialized, so it is ambiguous for an instance locale.
        return ""

    language = parts[0]
    if not language.isalpha() or not 2 <= len(language) <= 8:
        return ""
    offset = 1

    # Up to three extlang subtags may follow a two- or three-letter primary.
    if len(language) in {2, 3}:
        extlangs = 0
        while (
            offset < len(parts)
            and len(parts[offset]) == 3
            and parts[offset].isalpha()
            and extlangs < 3
        ):
            offset += 1
            extlangs += 1

    if (
        offset < len(parts)
        and len(parts[offset]) == 4
        and parts[offset].isalpha()
    ):
        offset += 1
    if offset < len(parts) and (
        (len(parts[offset]) == 2 and parts[offset].isalpha())
        or (len(parts[offset]) == 3 and parts[offset].isdigit())
    ):
        offset += 1

    variants: set[str] = set()
    while offset < len(parts) and (
        5 <= len(parts[offset]) <= 8
        or (len(parts[offset]) == 4 and parts[offset][0].isdigit())
    ):
        variant = parts[offset].casefold()
        if variant in variants:
            return ""
        variants.add(variant)
        offset += 1

    singletons: set[str] = set()
    while (
        offset < len(parts)
        and len(parts[offset]) == 1
        and parts[offset].casefold() != "x"
    ):
        singleton = parts[offset].casefold()
        if singleton in singletons:
            return ""
        singletons.add(singleton)
        offset += 1
        extension_start = offset
        while offset < len(parts) and 2 <= len(parts[offset]) <= 8:
            offset += 1
        if offset == extension_start:
            return ""

    if offset < len(parts) and parts[offset].casefold() == "x":
        offset += 1
        if offset == len(parts):
            return ""
        offset = len(parts)
    if offset != len(parts):
        return ""
    return "-".join(part.casefold() for part in parts)


@dataclass(frozen=True, slots=True)
class LocalizationRequest:
    instance_lang: str
    requested_lang: str | None
    state: str
    requested_at: str
    corpus_version: str


def _canonical_json_bytes(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def localization_corpus_version() -> str:
    """Content identity of the released localization corpus.

    F0 needs a reproducible version before the F2 resource registry exists.
    The inventory is layer-based, not language- or provider-specific: prompt
    resources, deterministic catalogs, executor contracts and public sources.
    """

    candidates: set[Path] = set()
    roots_and_names = (
        (PATH_RUNTIME / "prompts", {".j2", ".yaml"}),
        (PATH_EXECUTORS, {"manifest.toml", "manifest.lang_state.json"}),
        (PATH_RUNTIME / "builtin_executor_contracts",
         {"manifest.toml", "manifest.lang_state.json"}),
        (PATH_DOCS, {".html"}),
    )
    for root, accepted in roots_and_names:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_symlink() and path.is_file() and (
                path.name in accepted or path.suffix in accepted
            ):
                candidates.add(path)
    for path in (
        PATH_ROOT / "install" / "data" / "i18n_seed.sqlite",
        PATH_RUNTIME / "detection_lexicon_seed.py",
    ):
        if path.is_file():
            candidates.add(path)

    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(PATH_ROOT).as_posix().encode("utf-8")
            content = path.read_bytes()
        except (OSError, ValueError):
            continue
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def _validated_localization_payload(value: object) -> LocalizationRequest | None:
    if not isinstance(value, dict) or set(value) != {
        "schema", "instance_lang", "requested_lang", "state",
        "requested_at", "corpus_version",
    }:
        return None
    if value.get("schema") != LOCALIZATION_REQUEST_SCHEMA:
        return None
    instance = normalize_language_tag(value.get("instance_lang"))
    requested_raw = value.get("requested_lang")
    requested = (
        normalize_language_tag(requested_raw)
        if requested_raw is not None else None
    )
    state = value.get("state")
    requested_at = value.get("requested_at")
    corpus_version = value.get("corpus_version")
    if (
        not instance
        or (requested_raw is not None and not requested)
        or state not in LOCALIZATION_STATES
        or not isinstance(requested_at, str)
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", requested_at,
        )
        or not isinstance(corpus_version, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", corpus_version)
    ):
        return None
    if state == "bootstrap_english" and (
        instance != BOOTSTRAP_LANGUAGE
        or requested is None
        or requested == instance
    ):
        return None
    if state == "active" and requested not in (None, instance):
        return None
    return LocalizationRequest(
        instance_lang=instance,
        requested_lang=requested,
        state=state,
        requested_at=requested_at,
        corpus_version=corpus_version,
    )


def read_localization_request(
    path: Path | None = None,
) -> tuple[LocalizationRequest | None, str | None]:
    """Read and verify the signed instance localization request.

    Returns ``(request, error_code)`` and never prevents boot. Missing state is
    distinct from an invalid or tampered document so diagnostics stay honest.
    """

    target = Path(path or PATH_LOCALIZATION_REQUEST)
    if not target.exists():
        return None, "missing"
    try:
        if target.is_symlink() or target.stat().st_size > 16_384:
            return None, "invalid_file"
        document = json.loads(target.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or set(document) != {"payload", "signature"}
        ):
            return None, "invalid_document"
        payload = document["payload"]
        signature = document["signature"]
        request = _validated_localization_payload(payload)
        if (
            request is None
            or not isinstance(signature, dict)
            or signature.get("algorithm") != "ed25519"
            or signature.get("key_id") != "author"
        ):
            return None, "invalid_document"
        encoded = signature.get("value")
        if not isinstance(encoded, str):
            return None, "invalid_signature"
        padding = "=" * (-len(encoded) % 4)
        signature_bytes = base64.urlsafe_b64decode(encoded + padding)
        public_bytes = (
            PATH_USER_CONFIG / "keys" / "author_pub.bin"
        ).read_bytes()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        public = Ed25519PublicKey.from_public_bytes(public_bytes)
        public.verify(signature_bytes, _canonical_json_bytes(payload))
        return request, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, "invalid_document"
    except Exception:
        return None, "invalid_signature"


def write_localization_request(
    *,
    instance_lang: str,
    requested_lang: str | None,
    state: str,
    path: Path | None = None,
    corpus_version: str | None = None,
) -> tuple[LocalizationRequest, bool]:
    """Atomically persist an idempotent request signed by the installation."""

    instance = normalize_language_tag(instance_lang)
    requested = (
        normalize_language_tag(requested_lang)
        if requested_lang is not None else None
    )
    version = corpus_version or localization_corpus_version()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    probe = _validated_localization_payload({
        "schema": LOCALIZATION_REQUEST_SCHEMA,
        "instance_lang": instance,
        "requested_lang": requested,
        "state": state,
        "requested_at": now,
        "corpus_version": version,
    })
    if probe is None:
        raise ValueError("invalid localization request")

    target = Path(path or PATH_LOCALIZATION_REQUEST)
    existing, _error = read_localization_request(target)
    if existing is not None:
        same_request = (
            existing.instance_lang == probe.instance_lang
            and existing.requested_lang == probe.requested_lang
            and existing.state == probe.state
        )
        if same_request and existing.corpus_version == probe.corpus_version:
            return existing, False
        if same_request:
            probe = LocalizationRequest(
                instance_lang=probe.instance_lang,
                requested_lang=probe.requested_lang,
                state=probe.state,
                requested_at=existing.requested_at,
                corpus_version=probe.corpus_version,
            )

    payload = {
        "schema": LOCALIZATION_REQUEST_SCHEMA,
        "instance_lang": probe.instance_lang,
        "requested_lang": probe.requested_lang,
        "state": probe.state,
        "requested_at": probe.requested_at,
        "corpus_version": probe.corpus_version,
    }
    private_path = PATH_USER_CONFIG / "keys" / "author_priv.bin"
    public_path = PATH_USER_CONFIG / "keys" / "author_pub.bin"
    if not private_path.is_file() or not public_path.is_file():
        raise FileNotFoundError("installation signing key unavailable")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    private = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
    signature_bytes = private.sign(_canonical_json_bytes(payload))
    public = Ed25519PublicKey.from_public_bytes(public_path.read_bytes())
    public.verify(signature_bytes, _canonical_json_bytes(payload))
    encoded = base64.urlsafe_b64encode(signature_bytes).rstrip(b"=").decode(
        "ascii"
    )
    document = {
        "payload": payload,
        "signature": {
            "algorithm": "ed25519",
            "key_id": "author",
            "value": encoded,
        },
    }
    write_private_text(
        target,
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    verified, error = read_localization_request(target)
    if verified != probe or error is not None:
        raise OSError("localization request post-write verification failed")
    return verified, True


def _resolve_instance_localization() -> tuple[
    str, str | None, str, str | None,
]:
    request, error = read_localization_request()
    if request is not None:
        return (
            request.instance_lang,
            request.requested_lang,
            request.state,
            None,
        )
    configured = normalize_language_tag(os.environ.get("METNOS_LANG"))
    if configured:
        return configured, None, "active", None if error == "missing" else error
    diagnostic = error if error != "missing" else "invalid_instance_language"
    # Preserve the historical locale for installations that have neither a
    # valid signed request nor a valid bootstrap environment. A requested,
    # unready locale still uses BOOTSTRAP_LANGUAGE through its signed state.
    return "it", None, "fallback_invalid", diagnostic

# Synth executors (synth on-the-fly, ADR 0066)
PATH_SYNTH_EXECUTORS = PATH_USER_DATA / "executors"
# Skill imported (ADR 0123; ADR 0160 rename _imports/ → skills/).
# Loader scansiona ENTRAMBI: i write nuovi vanno in PATH_SKILLS_USER (new),
# le installazioni legacy in PATH_SKILLS_USER_LEGACY restano leggibili.
PATH_SKILLS_USER         = PATH_USER_DATA / "executors" / "skills"
PATH_SKILLS_USER_LEGACY  = PATH_USER_DATA / "executors" / "_imports"
PATH_SKILLS_BUILTIN      = PATH_EXECUTORS / "skills"
# Audit log dir (introvertiva, vaglio, synt; ADR 0067)
PATH_AUDIT         = PATH_USER_DATA / "introvertiva"
# History turns (TurnLog jsonl daily files)
PATH_TURNS         = PATH_USER_DATA / "turns"
# Cost tracking (LLM provider costs)
PATH_COST          = PATH_USER_DATA / "cost"
# Index root (indici di dominio: image/scene, image/persons, image/gps, ...).
# Override via METNOS_INDEX_ROOT per isolare i test E2E dry-run dallo storage
# di produzione (8/5/2026: il bug "47 sha8 orfane con base_path=/tmp/..." nasce
# proprio da test che scrivevano sotto ~/.local/share/metnos/index/ globale).
PATH_INDEX_ROOT    = _env_path("METNOS_INDEX_ROOT", PATH_USER_DATA / "index")
# Index image (storage canonico ADR 0086 + 0113)
PATH_INDEX_IMAGE   = PATH_INDEX_ROOT / "image"

# Dry-run globale: se True, gli executor "write" (create/delete/move/write/
# change/send/set) short-circuitano la parte distruttiva e ritornano un
# payload `{ok, dry_run:true, would_*}` senza side-effect. Read-only invariati.
DRY_RUN            = _env_str("METNOS_DRY_RUN", "0") == "1"

# --- Database paths ------------------------------------------------------

# Mnestoma (mnest grafo + events)
DB_MNESTOMA        = PATH_WORKSPACE / ".mnestoma" / "mnest.sqlite"
# Scheduler (system tasks + recurring user tasks state)
DB_SCHEDULER       = PATH_WORKSPACE / ".scheduler" / "state.sqlite"
# i18n testi multilingua
DB_I18N            = PATH_USER_DATA / "i18n.sqlite"
# Lessici di detection NL multilingua (gemello di i18n, lato INPUT)
DB_DETECTION       = PATH_USER_DATA / "detection.sqlite"
# Scratchpad (handle observation grandi, ADR 0050)
DB_SCRATCHPAD      = PATH_USER_DATA / "scratchpad.db"
# Pairings (multi-device, ADR 0035)
DB_PAIRINGS        = PATH_USER_STATE / "pairings.db"
# Recurring user tasks (registered via PLANNER create_tasks)
DB_RECURRING_TASKS = PATH_USER_STATE / "recurring_tasks.db"
# Approvals (autonomy_level + grant pending)
DB_APPROVALS       = PATH_USER_STATE / "approvals.db"
# Devices (multi-device pairing extensions)
DB_DEVICES         = PATH_USER_STATE / "devices.db"
# Policy (autonomy_level matrix)
DB_POLICY          = PATH_USER_STATE / "policy.db"
# Observability (run history, dashboard data)
DB_OBSERVABILITY   = PATH_USER_STATE / "observability.db"
# Durable-workload kernel (ADR 0213).  The package remains dormant until a
# later lifecycle phase installs its worker; merely importing config never
# creates this directory or opens the database.
PATH_DURABLE_WORKLOADS = PATH_USER_STATE / "durable_workloads"
DB_DURABLE_WORKLOADS = PATH_DURABLE_WORKLOADS / "state.sqlite3"
PATH_DURABLE_ARTIFACTS = PATH_USER_DATA / "durable_workloads"
# Multi-tool fast-path memoization (ADR 0150): canonical_query → tools sequence
# memoizzata, TTL N giorni di attivita' effettiva.
DB_MULTI_TOOL_PATHS = PATH_USER_DATA / "multi_tool_paths.sqlite"
# Change intents (ADR 0158): single source of truth per il ciclo di vita
# proposed → accepted → applied → observed → finalized (o rolled_back).
# Sostituisce 9 storage frammentati (telos jsonl, introvertiva sqlite,
# synt jsonl, multi_tool sqlite, canonical_query_log, executor_history, ...).
DB_CHANGE_INTENTS  = PATH_USER_STATE / "change_intents.sqlite"
# Audit JSONL (append-only, no schema; non-DB ma simile)
LOG_LOCATIONS_JSONL = PATH_USER_DATA / "locations.jsonl"

# --- Tuning costanti ------------------------------------------------------

# Loop / step cap (agent_runtime). Override via METNOS_CAP_STEPS.
# Razionale: 30 step e' soglia oltre cui il PLANNER molto raramente converge
# senza loop_break. Cap sotto = utenti smart query bloccati.
CAP_STEPS              = _env_int("METNOS_CAP_STEPS", 30)
# Stesso executor in fila prima di marcare loop. 10 = soglia conservativa
# pre-cap-expand prompt.
CAP_SAME_EXECUTOR      = _env_int("METNOS_CAP_SAME_EXECUTOR", 10)
# Observation > soglia → offload a scratchpad invece di passare inline.
CAP_OBSERVATION_BYTES  = _env_int("METNOS_OBS_BYTES", 4096)

# --- Mnestoma tuning ------------------------------------------------------

# Reinforce per ogni passing osservato. 0.15/passing → mnest a uses=4
# raggiunge weight ~0.7 (sopra soglia synth_trigger).
WEIGHT_REINFORCE       = 0.15
# Weight iniziale di un nuovo mnest (mai osservato prima).
WEIGHT_BOOTSTRAP       = 0.30
# Decay esponenziale per giorno. 0.018/giorno → dimezzamento ~38 giorni.
# Razionale: pattern visto 1 mese fa pesa meta' di uno visto oggi.
WEIGHT_DECAY_LAMBDA    = 0.018
# Sotto questa soglia, mnest viene "decayed" (state=decaying invece active).
WEIGHT_DECAY_THRESHOLD = 0.20
# Sotto questa soglia + age, mnest archiviato (state=archived).
WEIGHT_ARCHIVE_THRESHOLD = 0.05
# Eta' minima in giorni per archive (evita archive di mnest nuovi che
# decadono brevemente per inattivita' temporanea).
ARCHIVE_AGE_DAYS       = 90
# Proto-mnest mai promossi sotto soglia → purgati.
PROTO_PURGE_THRESHOLD  = 0.05

# --- Synth trigger --------------------------------------------------------

# Numero uses minimo per synth-trigger (mnest deve essere visto N volte).
SYNTH_TRIGGER_USES     = 3
# Weight minimo (oltre il quale il mnest e' ritenuto "stabile").
SYNTH_TRIGGER_WEIGHT   = 0.30

# --- Timeout (secondi) ----------------------------------------------------

# Default task fire timeout (scheduler). Oltre → status='timeout'.
TIMEOUT_TASK_S         = 300
# Push canale (Telegram send) per fire ricorrente.
TIMEOUT_PUSH_S         = 30
# Approval pending TTL (cap_pending dialog).
TIMEOUT_APPROVAL_S     = 600
# Location request timeout (utente non risponde a prompt 📍).
TIMEOUT_LOCATION_S     = 300
# Scratchpad TTL (1 ora).
TIMEOUT_SCRATCHPAD_S   = 3600

# --- Default app ---------------------------------------------------------

INSTANCE_LANG, REQUESTED_LANG, LOCALIZATION_STATE, LOCALIZATION_ERROR = (
    _resolve_instance_localization()
)
DEFAULT_LANG           = INSTANCE_LANG
DEFAULT_TIMEZONE       = _env_str("METNOS_TZ", "Europe/Rome")
DEFAULT_CHANNEL        = "telegram"
DEFAULT_ACTOR          = "host"

# --- Recurring tasks quota -----------------------------------------------

MAX_TASKS_PER_ACTOR    = _env_int("METNOS_MAX_TASKS_PER_ACTOR", 50)

# --- Geo provider --------------------------------------------------------

# Chain provider (CSV): prima primary, poi fallback. Es. "google,photon".
GEO_PROVIDERS_CHAIN    = _env_str("METNOS_GEO_PROVIDERS", "google,photon")

# --- Logging ------------------------------------------------------------

LOG_LEVEL              = _env_str("METNOS_LOG_LEVEL", "INFO").upper()
LOG_FILE               = _env_path("METNOS_LOG_FILE",
                                     PATH_USER_STATE / "metnos.log")
LOG_FORMAT             = "%(asctime)s %(name)s %(levelname)s %(message)s"
LOG_DATE_FORMAT        = "%Y-%m-%dT%H:%M:%S"

# --- Helper ensure dirs ---------------------------------------------------

def ensure_private_dir(path: Path) -> Path:
    """Create/repair one account-private directory without following links."""

    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"private directory cannot be a symlink: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        # Windows ACLs are enforced by the client sandbox instead of POSIX mode.
        pass
    return path


def ensure_private_file(path: Path) -> Path:
    """Repair an existing sensitive file to owner-only POSIX permissions."""

    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"private file cannot be a symlink: {path}")
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def write_private_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a private text file with mode 0600."""

    path = Path(path)
    ensure_private_dir(path.parent)
    if path.is_symlink():
        raise ValueError(f"private file cannot be a symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ensure_private_file(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_private_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> bool:
    """Create one private file without replacing a concurrent writer.

    A crash can leave an incomplete file, which every security-sensitive
    reader must reject.  This is preferable to overwriting configuration that
    another installer or administrator created between an existence check and
    the write.
    """

    path = Path(path)
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    try:
        os.fchmod(descriptor, 0o600)
    except (AttributeError, OSError):
        pass
    with os.fdopen(descriptor, "w", encoding=encoding) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    ensure_private_file(path)
    # A failed write intentionally leaves a fail-closed partial file. Removing
    # it here could unlink a replacement installed after a platform failure.
    return True


def append_private_bytes(path: Path, data: bytes) -> None:
    """Append one complete record to a private file under an advisory lock."""

    path = Path(path)
    ensure_private_dir(path.parent)
    if path.is_symlink():
        raise ValueError(f"private file cannot be a symlink: {path}")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    lock_module = None
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            ensure_private_file(path)
        try:
            import fcntl as lock_module  # Unix server; absent on Windows shim.
            lock_module.flock(descriptor, lock_module.LOCK_EX)
        except ImportError:
            lock_module = None
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        if lock_module is not None:
            lock_module.flock(descriptor, lock_module.LOCK_UN)
        os.close(descriptor)

def ensure_dirs() -> None:
    """Crea i path user (idempotente). Non crea PATH_ROOT (deve esistere
    a deploy time)."""
    for p in (PATH_USER_DATA, PATH_USER_STATE, PATH_USER_CONFIG,
              PATH_SYNTH_EXECUTORS, PATH_AUDIT, PATH_TURNS, PATH_COST,
              DB_PAIRINGS.parent, DB_RECURRING_TASKS.parent,
              DB_MNESTOMA.parent, DB_SCHEDULER.parent):
        ensure_private_dir(p)

    # Repair known record/database formats left with an old umask-dependent
    # mode. Root directories are already 0700; file repair adds defence in
    # depth if a parent is later loosened by an administrator.
    for root in (PATH_USER_DATA, PATH_USER_STATE, PATH_USER_CONFIG):
        for pattern in ("*.db", "*.sqlite", "*.jsonl", "*.key", "*.sig"):
            for candidate in root.glob(pattern):
                if candidate.is_file():
                    ensure_private_file(candidate)
    for private_tree in (
            PATH_TURNS,
            PATH_USER_DATA / "credentials",
            PATH_USER_STATE / "location_pending",
            PATH_USER_STATE / "dialog_pending",
            PATH_USER_CONFIG / "keys"):
        if private_tree.exists() and not private_tree.is_symlink():
            ensure_private_dir(private_tree)
            for candidate in private_tree.rglob("*"):
                if candidate.is_file() and not candidate.is_symlink():
                    ensure_private_file(candidate)


# Auto-ensure al primo import (idempotente, low cost).
ensure_dirs()


# --- Backward-compat aliases (deprecabili gradualmente) ------------------

# Mnestoma.py compatibility (vecchi import). Deprecabili dopo refactor.
REINFORCE_DELTA              = WEIGHT_REINFORCE
BOOTSTRAP_WEIGHT             = WEIGHT_BOOTSTRAP
DECAY_LAMBDA_DEFAULT         = WEIGHT_DECAY_LAMBDA
DECAY_THRESHOLD              = WEIGHT_DECAY_THRESHOLD
ARCHIVE_THRESHOLD            = WEIGHT_ARCHIVE_THRESHOLD
