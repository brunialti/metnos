#!/usr/bin/env python3
"""
loader.py — scopre, verifica e carica gli executor (Metnos v1.1 POC).

Scansiona una directory di executor (default: <install_root>/executors), per ognuno:
    1. legge il manifest.toml
    2. verifica firma + digest tramite sign.verify_executor()
    3. inserisce nel catalogo (dataclass Executor)
    4. ignora con warning quelli che falliscono verifica

Espone:
    load_catalog(executors_dir) -> Catalog
    Catalog: dict {name: Executor}, con metodi find_by_capability, all_with_affinity
"""
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sign import verify_executor
from executor_metadata import (
    execution_policy as _execution_policy,
    output_schema as _declared_output_schema,
    source_kind as _source_kind,
    standard_state as _standard_state,
    transport_kind as _transport_kind,
)

from logging_setup import get_logger
log = get_logger(__name__)


# ADR 0148: rename-resilient — auto-derived da config.PATH_ROOT che
# usa Path(__file__).resolve().parents[1].
import config as _C  # noqa: E402  (sys.path insert above)
DEFAULT_EXECUTORS_DIR = _C.PATH_EXECUTORS
SYNTHESIZED_EXECUTORS_DIR = _C.PATH_USER_DATA / "executors"
BUILTIN_EXECUTOR_CONTRACTS_DIR = (
    Path(__file__).resolve().parent / "builtin_executor_contracts"
)

# Valori ammessi per il manifest [platforms] (W3.2, executor remoti §16.3
# design doc). Vocabolario chiuso, come §2.2 — nessuna estensione implicita.
ALLOWED_PLATFORMS = {"linux", "windows", "macos"}


def _resolve_lang_text(value, *, where: str, current_lang: str) -> str:
    """Risolve un campo testuale multilingua del manifest (ADR 0092 Phase 4).

    Schema atteso (NUOVO): table TOML `{lang_code: "...", lang_code: "..."}`.
    Schema rifiutato (LEGACY): stringa flat `description = "..."`.

    Pattern latest-wins simmetrico (the design guide §7.3): nessuna lingua canonica.
    Selezione:
      1. Se `value` e' dict → restituisce `value[current_lang]` se presente.
      2. Ripiego §K: EN esplicito se presente; altrimenti prima lingua
         disponibile in ordine alfabetico (deterministic).
      3. Se `value` e' stringa flat → ValueError (legacy schema vietato,
         the design guide §7.1: niente backward-compat).
      4. Se `value` e' dict vuoto → stringa vuota (placeholder valido in
         caso di manifest in fase di costruzione).

    Args:
        value: il campo del manifest (gia' parsed da tomllib).
        where: contesto per il messaggio di errore (es. "find_files.description").
        current_lang: codice lingua corrente (es. "it", "en").

    Returns:
        La stringa risolta nella lingua opportuna, o stringa vuota.
    """
    if isinstance(value, dict):
        if not value:
            return ""
        if current_lang in value and isinstance(value[current_lang], str):
            return value[current_lang]
        # Ripiego nel frattempo (§K, 15/6/2026): EN esplicito prima di tutto —
        # finché la descrizione non è tradotta nella lingua target, il planner
        # legge l'INGLESE (allineato a prompt_loader._FALLBACK_LANG / i18n.
        # FALLBACK_CHAIN), non una lingua qualsiasi in ordine alfabetico.
        en = value.get("en")
        if isinstance(en, str):
            return en
        # Ultima risorsa: prima lingua disponibile in ordine alfabetico.
        for lang in sorted(value.keys()):
            v = value[lang]
            if isinstance(v, str):
                return v
        return ""
    if isinstance(value, str):
        # Schema legacy flat = errore. the design guide §7.1: niente backward-compat.
        raise ValueError(
            f"{where}: schema legacy flat (description = \"...\") rifiutato; "
            f"usa il nuovo schema [description] con sub-keys lingua "
            f"(es. it = \"...\", en = \"...\"). ADR 0092 Phase 4."
        )
    return ""


# ── Verb-unique builtin registry (ADR 0069) ──────────────────────────
#
# Privileged primitives whose verbs are OUTSIDE the closed vocabulary and
# whose execution is restricted to a whitelist of caller modules. They are
# registered explicitly by `register_verb_unique_builtin(module)` and never
# appear in the regular Catalog visible to the PLANNER.

VERB_UNIQUE_REGISTRY: dict[str, dict] = {}


def _normalize_capabilities(raw) -> list[dict]:
    """Normalizza `capabilities` da manifest TOML a list[dict].

    Forma canonica: `[[capabilities]] name="..." hint=[...]` → list[dict].
    Forma erronea ma tollerata: `[capabilities] foo={description=...}` →
    in TOML diventa dict, qui convertito a list[{name, hint}].
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        # Forma dict-malformed: chiavi = nomi capability, valori = metadata.
        out = []
        for k, v in raw.items():
            entry: dict = {"name": k}
            if isinstance(v, dict):
                if "description" in v:
                    entry["hint"] = [v["description"]]
                else:
                    entry.update(v)
            out.append(entry)
        return out
    out = []
    for c in raw:
        if isinstance(c, dict):
            out.append(c)
        elif isinstance(c, str):
            out.append({"name": c})
    return out


def _load_builtin_contract(name: str, module_path: Path) -> tuple[dict, Path, str]:
    """Load and verify the signed contract for one in-process builtin.

    The implementation stays in its runtime module, while the planner-facing
    identity/schema/authority is admitted exactly like a subprocess manifest.
    Missing, stale, unsigned, or mismatched contracts fail closed.
    """
    directory = BUILTIN_EXECUTOR_CONTRACTS_DIR / name
    path = directory / "manifest.toml"
    if not path.is_file():
        raise ValueError(f"builtin contract missing for {name!r}: {path}")
    ok, signature = verify_executor(directory)
    if not ok:
        raise ValueError(
            f"builtin contract signature invalid for {name!r}: "
            f"{signature.get('reason', 'unknown')}"
        )
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    if manifest.get("name") != name:
        raise ValueError(
            f"builtin contract identity mismatch: expected {name!r}, "
            f"got {manifest.get('name')!r}"
        )
    from executor_standard import validate_for_lifecycle
    findings = validate_for_lifecycle(manifest, require_declaration=True)
    if findings:
        detail = "; ".join(f"{item.code}:{item.message}" for item in findings)
        raise ValueError(f"builtin contract nonconformant for {name!r}: {detail}")
    code_paths = {
        (directory / filename).resolve()
        for filename in (manifest.get("code") or {}).get("files", [])
    }
    if module_path.resolve() not in code_paths:
        raise ValueError(
            f"builtin contract code mismatch for {name!r}: "
            f"{module_path.resolve()} is not signed"
        )
    return manifest, path, str(signature.get("signed_by") or "")


def _localized_builtin_contract(name: str, module_path: Path) -> tuple:
    manifest, path, signed_by = _load_builtin_contract(name, module_path)
    try:
        from config import DEFAULT_LANG as current_lang
    except Exception:
        current_lang = "it"
    description = _resolve_lang_text(
        manifest.get("description", {}), where=f"{name}.description",
        current_lang=current_lang,
    )
    args_schema = dict(manifest.get("args") or {})
    localized_props = {}
    for arg_name, original in (args_schema.get("properties") or {}).items():
        spec = dict(original)
        if "description" in spec:
            spec["description"] = _resolve_lang_text(
                spec["description"],
                where=f"{name}.args.properties.{arg_name}.description",
                current_lang=current_lang,
            )
        localized_props[arg_name] = spec
    args_schema["properties"] = localized_props
    return manifest, path, signed_by, description, args_schema


def builtin_contract_executor(name: str, module_path: Path) -> "Executor":
    """Build a catalog Executor from a verified builtin contract."""
    manifest, path, signed_by, description, args_schema = (
        _localized_builtin_contract(name, module_path)
    )
    return Executor(
        name=name,
        version=str(manifest.get("version") or "1.0.0"),
        description=description,
        affinity=list(manifest.get("affinity") or []),
        args_schema=args_schema,
        capabilities=_normalize_capabilities(manifest.get("capabilities")),
        tests=list(manifest.get("tests") or []),
        code_path=module_path,
        manifest_path=path,
        signed_by=signed_by,
        revertible=bool(manifest.get("revertible", False)),
        lifecycle=str(manifest.get("lifecycle") or "active"),
        superseded_by=None,
        reverse_pattern=manifest.get("reverse_pattern"),
        deprecated_at=None,
        deprecation_ttl_hours=24,
        placement=dict(manifest.get("placement") or {}),
        platforms=list(manifest.get("platforms") or ["linux"]),
        digest=str((manifest.get("code") or {}).get("digest") or ""),
        executor_standard=str(manifest.get("executor_standard") or ""),
        standard_state=_standard_state(
            manifest.get("executor_standard"), manifest.get("lifecycle", "active")),
        source="builtin",
        transport="in-process",
        output_schema=_declared_output_schema(manifest),
        execution_policy=_execution_policy(manifest),
        execution_policy_declared=isinstance(manifest.get("execution"), dict),
    )


class VerbUniqueViolation(Exception):
    """Raised when a verb-unique builtin breaks one of the five ADR-0069 invariants."""


def register_verb_unique_builtin(module) -> None:
    """Register a verb-unique builtin module.

    The module MUST expose four module-level constants:

        NOT_IN_VOCAB          (must be True)
        EXPOSE_TO_PLANNER     (bool — see note below)
        AUTHORISED_CALLERS    (non-empty tuple of caller IDs)
        VERB                  (the unique verb, str, NOT in vocab.py::ACTIONS)

    Modificato 4/5/2026 (ADR 0088): l'invariante "EXPOSE_TO_PLANNER must be
    False" della ADR 0069 e' allentata. Singoli verb-unique builtin possono
    optare per la visibilita' al PLANNER esponendo `EXPOSE_TO_PLANNER=True`
    + `MANIFEST_VIRTUAL` (caso `admin`). Il vincolo `NOT_IN_VOCAB=True` resta
    invariato — il verbo deve restare fuori dal vocabolario chiuso degli
    executor handcrafted/synth.

    Sudoer continua a dichiarare `EXPOSE_TO_PLANNER=False` e resta
    invisibile (verbo invocabile solo da admin).

    Le altre quattro invarianti di ADR 0069 sono verificate qui a
    registration time; una violazione solleva VerbUniqueViolation e
    interrompe il boot.
    """
    # Lazy import to avoid circular: vocab is the source of truth.
    from vocab import ACTIONS

    name = getattr(module, "__name__", "<unknown>")
    for attr in ("NOT_IN_VOCAB", "EXPOSE_TO_PLANNER", "AUTHORISED_CALLERS", "VERB"):
        if not hasattr(module, attr):
            raise VerbUniqueViolation(
                f"verb-unique builtin {name!r} missing attribute {attr!r}"
            )

    if module.NOT_IN_VOCAB is not True:
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r} must declare NOT_IN_VOCAB=True"
        )
    # `EXPOSE_TO_PLANNER` accetta True o False (ADR 0088). Il valore va
    # tipizzato: True/False bool, non altro.
    if module.EXPOSE_TO_PLANNER not in (True, False):
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r} EXPOSE_TO_PLANNER must be bool"
        )
    # Se EXPOSE_TO_PLANNER=True, MANIFEST_VIRTUAL e' obbligatorio: il
    # loader lo userà per costruire l'`Executor` dataclass visibile al
    # PLANNER nel catalog.
    if module.EXPOSE_TO_PLANNER is True and not hasattr(module, "MANIFEST_VIRTUAL"):
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r}: EXPOSE_TO_PLANNER=True but "
            "MANIFEST_VIRTUAL is missing (needed to render tool spec)"
        )

    callers = tuple(module.AUTHORISED_CALLERS or ())
    if not callers:
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r} must declare a non-empty AUTHORISED_CALLERS"
        )

    verb = str(module.VERB or "")
    if not verb:
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r} must declare a non-empty VERB"
        )
    if verb in ACTIONS:
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r}: VERB={verb!r} is in vocab.py::ACTIONS "
            "(should be OUTSIDE the closed vocabulary)"
        )
    if verb in VERB_UNIQUE_REGISTRY and VERB_UNIQUE_REGISTRY[verb]["module"] is not module:
        raise VerbUniqueViolation(
            f"verb-unique builtin {name!r}: VERB={verb!r} already registered "
            f"by {VERB_UNIQUE_REGISTRY[verb]['module'].__name__!r}"
        )

    # Callable preference order: invoke (planner-facing entry) > decide
    # (legacy admin flow) > execute (sudoer). admin.py espone sia decide
    # che invoke; per il dispatch dal PLANNER serve invoke.
    fn = (getattr(module, "invoke", None)
          or getattr(module, "decide", None)
          or getattr(module, "execute", None))
    VERB_UNIQUE_REGISTRY[verb] = {
        "verb": verb,
        "module": module,
        "authorised_callers": callers,
        "callable": fn,
        "expose_to_planner": bool(module.EXPOSE_TO_PLANNER),
        "manifest_virtual": getattr(module, "MANIFEST_VIRTUAL", None),
    }


def invoke_verb_unique(verb: str, *, caller: str, **kwargs):
    """Invoke a verb-unique builtin. Refuses if `caller` is not in the
    builtin's `authorised_callers` whitelist (ADR 0069 invariant 3).
    """
    entry = VERB_UNIQUE_REGISTRY.get(verb)
    if entry is None:
        raise KeyError(f"verb-unique builtin not registered: {verb!r}")
    if caller not in entry["authorised_callers"]:
        raise PermissionError(
            f"caller {caller!r} not authorised to invoke verb-unique "
            f"{verb!r}; authorised: {entry['authorised_callers']}"
        )
    fn = entry["callable"]
    if fn is None:
        raise RuntimeError(
            f"verb-unique builtin {verb!r} has no callable entrypoint"
        )
    return fn(**kwargs)


def boot_register_verb_unique_builtins() -> list[str]:
    """Import and register all verb-unique builtins shipped with Metnos.

    Returns the list of registered verbs. Boot fails if any registration
    raises; this is intentional — an inconsistent privileged surface is
    not a recoverable state.

    Idempotente: se i moduli sono già registrati con lo STESSO oggetto,
    skip silenzioso. Permette doppia chiamata (es. test + load_catalog).
    """
    from system import admin as _admin_mod
    from system import sudoer as _sudoer_mod
    if VERB_UNIQUE_REGISTRY.get("admin", {}).get("module") is not _admin_mod:
        register_verb_unique_builtin(_admin_mod)
    if VERB_UNIQUE_REGISTRY.get("sudoer", {}).get("module") is not _sudoer_mod:
        register_verb_unique_builtin(_sudoer_mod)
    return sorted(VERB_UNIQUE_REGISTRY.keys())


def _build_admin_executor_from_manifest_virtual(manifest: dict,
                                                 *, manifest_path: Path) -> "Executor":
    """Costruisce un `Executor` dataclass da `MANIFEST_VIRTUAL` di un
    verb-unique builtin esposto al PLANNER (ADR 0088).

    Il `manifest_path` punta al file `.py` del modulo (non a un manifest
    TOML che NON esiste). `code_path` punta allo stesso file: il runtime
    riconosce l'executor come `is_verb_unique_builtin` e instrada via
    `invoke_verb_unique` invece di subprocess.
    """
    return builtin_contract_executor(manifest["name"], manifest_path)


# --- In-process builtin tool specs registry ------------------------------
#
# Alcuni tool builtin (es. `create_tasks`, `list_tasks`, ...) vivono SOLO
# come dict "tool spec" OpenAI-style in moduli di runtime (es.
# `recurring_tasks.py`) e sono iniettati direttamente nel `tools=[]` del
# PLANNER. Mancano dal catalog `/admin/executors` → coverage check §2.2
# fallisce su OBJECTS coperti solo da builtin in-process (es. `tasks`).
#
# Pattern simmetrico a `_inject_planner_visible_verb_unique`: il modulo
# dichiara un `BUILTIN_INPROC_SPECS` list-of-dict, ogni dict contiene
# `name` + `tool_spec` (OpenAI format) + `manifest_virtual` opzionale.
# Il loader li costruisce in `Executor` dataclass e li aggiunge al
# catalog. Idempotente; collision con executor handcrafted → handcrafted
# vince per costruzione (ADR 0079).

_INPROC_TOOL_MODULE_PATHS: tuple[str, ...] = (
    "recurring_tasks",  # *_tasks builtin scheduler v2
    "skill_admin",      # list_skills / set_skills (admin skill da chat, asse 2)
    "store_entries",    # find/write/delete_entries — skill store generico (16/6)
    "compare_entries",  # compare_entries — distanza semantica universale (17/6)
    "describe_images",  # describe_images — VLM content-describe (upload, 30/6)
    "describe_entries",
    "classify_entries",
    "extract_entries",
)


def _inject_inproc_tool_specs(catalog: "Catalog") -> None:
    """Scopre i `BUILTIN_INPROC_SPECS` dei moduli registrati e inietta i
    relativi `Executor` virtuali nel catalog.

    Ogni entry deve essere un dict con almeno:
      - `name`: nome canonico (es. "create_tasks")
      - `tool_spec`: dict tool spec OpenAI-style (con `function.name`,
        `function.description`, `function.parameters`)
      - `affinity`: lista di keyword IT+EN (opzionale, default [])

    Idempotente: se gia' presente in catalog (handcrafted o synth) → noop.
    """
    for mod_name in _INPROC_TOOL_MODULE_PATHS:
        try:
            mod = __import__(mod_name)
        except ImportError as e:
            log.debug("[loader] inproc-tool module %r not importable: %s",
                       mod_name, e)
            continue
        specs = getattr(mod, "BUILTIN_INPROC_SPECS", None)
        if not specs:
            continue
        mod_path = Path(getattr(mod, "__file__", ""))
        for entry in specs:
            name = entry.get("name") or ""
            if not name:
                continue
            if name in catalog.executors:
                # Handcrafted wins
                continue
            try:
                executor = builtin_contract_executor(name, mod_path)
            except ValueError as exc:
                catalog.rejected.append((str(mod_path), str(exc)))
                continue
            catalog.executors[name] = executor


def _inject_planner_visible_verb_unique(catalog: "Catalog") -> None:
    """Inietta nel catalog i verb-unique builtin che dichiarano
    `EXPOSE_TO_PLANNER=True` (ADR 0088). Idempotente: se gia' presente,
    no-op. Collision con un executor handcrafted del medesimo nome →
    log warning + lascia vincere l'handcrafted (per costruzione, ADR 0079).
    """
    boot_register_verb_unique_builtins()
    for verb, entry in VERB_UNIQUE_REGISTRY.items():
        if not entry.get("expose_to_planner"):
            continue
        manifest = entry.get("manifest_virtual")
        if not isinstance(manifest, dict):
            continue
        name = manifest.get("name") or verb
        if name in catalog.executors:
            existing = catalog.executors[name]
            if str(existing.manifest_path).endswith(".py"):
                # già verb-unique: idempotenza
                continue
            log.warning(
                "[loader] verb-unique builtin %r collides with existing executor "
                "(handcrafted wins by construction, ADR 0079)", name,
            )
            continue
        module = entry["module"]
        manifest_path = Path(getattr(module, "__file__", ""))
        executor = _build_admin_executor_from_manifest_virtual(
            manifest, manifest_path=manifest_path,
        )
        catalog.executors[name] = executor


@dataclass
class Executor:
    name: str
    version: str
    description: str
    affinity: list[str]
    args_schema: dict
    capabilities: list[dict]
    tests: list[dict]
    code_path: Path | None  # None per lifecycle='proposed' (stub senza code)
    manifest_path: Path
    signed_by: str
    revertible: bool = False
    # lifecycle ∈ {'proposed','synthesized','active','deprecated','archived'}.
    # proposed: manifest senza code. synthesized: manifest + code + test verdi, firma sospesa.
    # active: firmato e in pool. deprecated: TTL transitorio prima di archived. archived: off-pool.
    lifecycle: str = "active"
    superseded_by: str | None = None       # nome dell'executor che lo rimpiazza, se deprecato
    reverse_pattern: object = None         # str | list[str] | None
    deprecated_at: float | None = None     # epoch del passaggio a deprecated (per TTL check)
    deprecation_ttl_hours: int = 24        # finestra di disponibilita' di deprecated prima di archive
    timeout_s: int = 30                    # subprocess timeout (manifest override per build lunghi)
    # Dormant: executor caricato e firmato ma SENZA credenziali necessarie
    # (es. *_google_workspace prima del completamento OAuth flow). Resta
    # nel catalogo per introspezione ma viene filtrato dal pool top-K esposto
    # al PLANNER. Ricalcolato a ogni `load_catalog` via skill_credentials.
    dormant: bool = False
    dormant_reason: str = ""
    # Sandbox profile DICHIARATIVO dal manifest `[sandbox]` (mini-version
    # 17/5/2026): network_allowed/fs_read/fs_write/exec_allowed. Oggi non
    # enforced (validato al load, warning se mancante per skill imported).
    # Enforcement bubblewrap arriva con Fase C full a soglia trigger.
    sandbox_profile: dict = field(default_factory=dict)
    # Provenance dal manifest `[provenance]` (ADR 0123): skill_id,
    # imported_from, source_version, source_sha256, imported_at.
    # Usato per audit log skill (runtime/skill_audit.py) e per
    # dormancy check (runtime/skill_credentials.py).
    provenance: dict = field(default_factory=dict)
    # Placement dal manifest `[placement]` (ADR 0034, executor remoti §11
    # design doc): {scope: server|device|any, targets: [...], class: ...}.
    # Vuoto = scope "any" (gira su .33 come oggi). Consumato da
    # placement.choose_placement nel hook di invoke_executor.
    placement: dict = field(default_factory=dict)
    # Planning complexity hint (19/5/2026): suggerisce al planner se questa
    # call beneficia di reasoning LLM (think=True) o se la decisione e' ovvia
    # e think=False e' sufficiente (5-10x speedup sul modello locale - bench
    # 19/5). Valori:
    #   - "low":    decisione ovvia (es. read_files con path esplicito) → think=False
    #   - "medium": default; il planner usa think=True con budget ridotto
    #   - "high":   query complessa (synt, multi-step composto) → think=True full budget
    # Letto dal manifest `[planning] complexity = "low|medium|high"`. Se non
    # dichiarato, fallback automatico in `agent_runtime` basato sul verbo del
    # nome (producer verbs get/read/find/list → low, mutating → medium).
    # NOTA: validato sul modello locale. Per modelli diversi vedi
    # [[metnos_todo_high_think_per_model]].
    complexity: str = ""
    # Piattaforme device supportate (W3.2, executor remoti §16.3 design doc):
    # {"linux","windows","macos"}. Default ["linux"] se il manifest non lo
    # dichiara (tutto il parco esistente e' nato POSIX, §16.0: default onesto,
    # non ottimistico). Consumato da placement.choose_placement per rifiutare
    # un device il cui os_family non e' in questa lista, PRIMA di spedirgli
    # un'invocazione che crasherebbe (modulo mancante, comando POSIX assente).
    platforms: list[str] = field(default_factory=lambda: ["linux"])
    # Digest firmato del codice (manifest [code].digest, ADR 0182): la FIRMA
    # del mondo per la cache-validity — un piano cachato che referenzia questo
    # executor diventa MISS se il digest cambia (re-sign post-edit §7.10).
    # Vuoto per builtin/virtual (cambiano solo col deploy+restart, che azzera
    # la cache in-process; limite onesto documentato in ADR 0182).
    digest: str = ""
    # Executor Standard/catalog identity. ``standard_state='declared'`` means
    # the manifest declared the supported version and passed deterministic
    # loader checks; semantic conformance still requires its review/test gates.
    executor_standard: str = ""
    standard_state: str = "legacy"
    source: str = "handcrafted"
    transport: str = "local-subprocess"
    output_schema: str = ""
    # Scheduler policy normalized by the loader.  Default serial preserves the
    # exact historical execution semantics for every existing executor.
    execution_policy: dict = field(default_factory=_execution_policy)
    execution_policy_declared: bool = False

    def has_capability(self, name_prefix: str) -> bool:
        return any(c.get("name", "").startswith(name_prefix) for c in self.capabilities)

    @property
    def is_imported(self) -> bool:
        """True se executor importato da skill (ha [provenance]) vs builtin."""
        return bool(self.provenance and self.provenance.get("imported_from"))


@dataclass
class Catalog:
    executors: dict[str, Executor] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    def __len__(self):
        return len(self.executors)

    def __iter__(self):
        return iter(self.executors.values())

    def get(self, name: str) -> Executor | None:
        return self.executors.get(name)

    def find_by_capability(self, capability_prefix: str) -> list[Executor]:
        return [e for e in self.executors.values() if e.has_capability(capability_prefix)]

    def all_names(self) -> list[str]:
        return sorted(self.executors.keys())


# Cache di load_catalog (ADR 0099): le 55+ manifest TOML vengono lette ad
# ogni turn. Il catalog cambia raramente (synth + handcrafted edits). Cache
# con invalidazione su mtime max delle dirs sorgente. Hit O(1) vs ~200-500ms
# di cold load. Disabilitato per `verify=False` (path GC sensitive a corse)
# e quando `executors_dir` non e' il default.
_CATALOG_CACHE: dict[str, tuple] = {}  # key → (catalog, mtime_signature)


def _catalog_cache_signature(dirs: list) -> tuple:
    """Firma deterministica per invalidazione cache: max mtime ricorsivo
    di tutti i manifest.toml + .py + .sig nelle dirs + mtime del DB
    executor_aging (lifecycle override puo' cambiare lo stato visibile).

    Costo: ~10-30ms per 55 executor (vs ~200-500ms per il full load).
    """
    sig: list = []
    for d in dirs:
        if not d.exists():
            sig.append((str(d), 0.0, 0))
            continue
        max_mt = 0.0
        n_files = 0
        # Scan minimale: solo manifest.toml + .py + .sig (gli unici che
        # possono cambiare il digest del catalog). Ignora __pycache__.
        for sub in d.iterdir():
            if not sub.is_dir() or sub.name.startswith("__"):
                continue
            for fname in ("manifest.toml", "manifest.toml.sig"):
                fp = sub / fname
                try:
                    mt = fp.stat().st_mtime
                    if mt > max_mt:
                        max_mt = mt
                    n_files += 1
                except OSError:
                    pass
            # Conta i .py nel dir (tipicamente 1, ma gestisci varianti)
            for py in sub.glob("*.py"):
                try:
                    mt = py.stat().st_mtime
                    if mt > max_mt:
                        max_mt = mt
                    n_files += 1
                except OSError:
                    pass
        sig.append((str(d), max_mt, n_files))
    # Aging DB: modifica → lifecycle override map cambia → catalog visibility
    # diversa. Includere il mtime fa SI' che apply_executor_ager invalidi
    # automaticamente la cache (test_archived_executor_excluded_from_catalog).
    try:
        from executor_aging import DB_PATH as _aging_db
        try:
            sig.append(("aging_db", _aging_db.stat().st_mtime))
        except OSError:
            sig.append(("aging_db", 0.0))
    except Exception:
        sig.append(("aging_db", 0.0))
    # Skill state (asse 2): enable/disable di una skill (skill_enabled.json)
    # cambia la dormancy first-party → visibility del catalog diversa. Il mtime
    # nella firma fa SÌ che set_skill_enabled invalidi la cache → gating live.
    try:
        from skill_registry import _state_file as _sf
        try:
            sig.append(("skill_state", _sf().stat().st_mtime))
        except OSError:
            sig.append(("skill_state", 0.0))
    except Exception:
        sig.append(("skill_state", 0.0))
    return tuple(sig)


def load_catalog(executors_dir=DEFAULT_EXECUTORS_DIR, verify=True, *, include_synth=True, include_verb_unique=True) -> Catalog:
    """Scansiona executors_dir + (opzionale) SYNTHESIZED_EXECUTORS_DIR.

    `include_synth=True` (default): carica anche gli executor sintetizzati
    in `~/.local/share/metnos/executors/`. Permette ai nuovi executor di
    essere visti dopo synth-on-the-fly senza modificare il pool seed.

    Lifecycle override (3/5/2026): consulta `executor_stats` SQLite via
    `executor_aging.lifecycle_override_map()`. Se un executor risulta
    `deprecated_at` o `archived_at` non null, il suo lifecycle viene
    sovrascritto. Gli archived sono esclusi dal catalog visibile.

    GC synth in collisione (4/5/2026, ADR 0079): se `verify=True`, dopo il
    load i synth marcati `rejected` per collision con un handcrafted
    vengono spostati (non eliminati) sotto `/tmp/metnos_synth_gc_<ts>/`.
    Backup non distruttivo: la dir resta recoverable, ma esce dal pool
    cosi' che il prossimo load non la veda piu'.

    Cache (ADR 0099, 7/5/2026): hit O(1) quando la firma mtime delle dirs
    non e' cambiata. Cache key = (executors_dir, verify, include_synth).
    Cache miss: full load + store. La firma copre handcrafted + synth +
    .sig: qualunque modifica invalida la cache.
    """
    # Test/dev override: env `METNOS_LOADER_VERIFY=0` disabilita la verify
    # della firma. Use case: server tmp E2E che importa skill al volo via
    # CLI con `--no-sign`. Senza questo override, gli executor importati
    # vengono silenziosamente scartati (digest mismatch) e il PLANNER non
    # li vede mai. NIENTE in produzione.
    if verify and os.environ.get("METNOS_LOADER_VERIFY", "1") == "0":
        verify = False
    # Cache key include lang corrente: descrizioni multilingua risolte al
    # load (ADR 0092) usano `config.DEFAULT_LANG` (modulo, non env). Lo
    # leggiamo qui per ogni call → cache hit corretto sui cambi di lingua
    # in-process (test multilingua, future utenti per-lang).
    try:
        from config import DEFAULT_LANG as _cfg_lang
    except Exception:
        _cfg_lang = ""
    _hidden_env = os.environ.get("METNOS_HIDE_EXECUTORS", "")
    cache_key = f"{executors_dir}|{verify}|{include_synth}|{include_verb_unique}|{_cfg_lang}|{_hidden_env}"
    dirs_for_sig = [Path(executors_dir)]
    if include_synth and SYNTHESIZED_EXECUTORS_DIR.exists():
        dirs_for_sig.append(SYNTHESIZED_EXECUTORS_DIR)
    current_sig = _catalog_cache_signature(dirs_for_sig)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None and cached[1] == current_sig:
        return cached[0]

    catalog = Catalog()
    dirs_to_scan = [Path(executors_dir)]
    if include_synth and SYNTHESIZED_EXECUTORS_DIR.exists():
        dirs_to_scan.append(SYNTHESIZED_EXECUTORS_DIR)

    for i, d in enumerate(dirs_to_scan):
        if not d.exists():
            continue
        _load_dir_into_catalog(d, catalog, verify, is_synthesized=(i > 0))

    # Hidden executors (env-driven): permette di nascondere selettivamente
    # executor dal catalog senza tocco filesystem. Use case principale: test
    # E2E che vogliono forzare il PLANNER a usare un imported skill invece
    # del builtin equivalente (ADR 0136 prefer-builtin default). Universale:
    # qualunque executor `name in METNOS_HIDE_EXECUTORS` viene escluso.
    _hidden_raw = os.environ.get("METNOS_HIDE_EXECUTORS", "")
    if _hidden_raw:
        _hidden_names = {n.strip() for n in _hidden_raw.split(",") if n.strip()}
        for _n in list(catalog.executors.keys()):
            if _n in _hidden_names:
                catalog.executors.pop(_n)

    # Affinity overlap guard (ADR 0114, 8/5/2026): synth con Jaccard >=0.5
    # verso UN handcrafted (o un altro synth piu' vecchio) viene rejected.
    # Audit log JSONL. Esecuzione DOPO load completo, PRIMA di GC.
    _check_affinity_overlap(catalog)

    # GC dei synth rifiutati per collision (ADR 0079, 4/5/2026): prima del
    # ramo executor_aging cosi' i path GC-ati non concorrono piu' agli
    # override.
    if verify and include_synth:
        _gc_collisions(catalog)

    # Inietta verb-unique builtin esposti al PLANNER (ADR 0088, 4/5/2026):
    # `admin` (sì), `sudoer` (no, EXPOSE_TO_PLANNER=False). Si esegue dopo
    # GC per evitare di iniettare e poi GC-are nello stesso load.
    # `include_verb_unique=False` (testing): salta l'iniezione per test che
    # asseriscono cardinalità sull'executors_dir custom (es. carica_*).
    if include_verb_unique:
        try:
            _inject_planner_visible_verb_unique(catalog)
        except Exception as e:
            log.warning("[loader] verb-unique injection failed: %s", e)
        try:
            _inject_inproc_tool_specs(catalog)
        except Exception as e:
            log.warning("[loader] inproc-tool injection failed: %s", e)
        # Registra gli store di PRODUZIONE (attiva i CRUD universali *_entries
        # su di essi via il gate di dormienza). Idempotente, best-effort.
        try:
            from store_bootstrap import register_builtin_stores
            register_builtin_stores()
        except Exception as e:
            log.warning("[loader] builtin-store registration failed: %s", e)

    # Apply lifecycle override from executor_aging stats + register newly
    # discovered executors with their source. Best-effort: if the module
    # isn't available (dev mode) we silently skip the integration.
    try:
        from executor_aging import (
            lifecycle_override_map, register as _exec_register,
        )
        # Register each executor with its source for the history timeline.
        # Synthesized = scanned in SYNTHESIZED_EXECUTORS_DIR (i>0 in scan
        # loop above); we re-derive the source from the manifest path.
        _skills_root = str(SYNTHESIZED_EXECUTORS_DIR / "skills")
        for ex in catalog.executors.values():
            try:
                _mp = str(ex.manifest_path)
                if _skills_root in _mp:
                    # Bundle skill IMPORTATO (github, google-workspace, …): vive
                    # sotto executors/skills/<skill>/ — NON e' un synth REATTIVO.
                    # source='skill' = esente da aging come handcrafted: le skill
                    # NON invecchiano (§reference aging-inactivity-trap; ADR 0170).
                    src = "skill"
                elif str(SYNTHESIZED_EXECUTORS_DIR) in _mp:
                    src = "synth:reactive"  # default; introvertive_specialize
                                            # writes its own register call
                else:
                    src = "handcrafted"
                _exec_register(ex.name, source=src)
            except Exception:
                pass

        overrides = lifecycle_override_map()
        if overrides:
            to_archive = []
            for name, target_state in overrides.items():
                ex = catalog.executors.get(name)
                if ex is None:
                    continue
                if target_state == "archived":
                    to_archive.append(name)
                else:
                    ex.lifecycle = target_state
            for name in to_archive:
                ex = catalog.executors.pop(name, None)
                if ex is not None:
                    catalog.rejected.append(
                        (str(ex.manifest_path),
                         "archived by executor_aging (inactive too long)")
                    )
    except ImportError:
        pass
    except Exception as e:
        log.warning("loader: executor_aging override failed: %s", e)

    # Aggiorna cache (ADR 0099): memorizza catalog + firma corrente.
    _CATALOG_CACHE[cache_key] = (catalog, current_sig)
    return catalog


def invalidate_catalog_cache() -> None:
    """Forza re-load al prossimo `load_catalog()`. Utile dopo `sign.py sign`
    o operazioni che modificano executor manifest in-process (i test per es.).
    """
    _CATALOG_CACHE.clear()


# ── Layer 2 admission guard: affinity overlap (ADR 0114) ──────────────
#
# Famiglie handcrafted canoniche: il pool seed non deve mai essere
# shadow-ato da synth con affinity overlap >= soglia. La lista e' la
# fonte di verita' per il guard: aggiungere qui ogni nuovo handcrafted
# della famiglia "discovery" (find_*) o producer canonico equivalente
# che concettualmente "produce un dominio".
HANDCRAFTED_FAMILIES: frozenset[str] = frozenset({
    # Discovery primari (i target piu' frequenti del catch-all synth)
    "find_urls", "find_files", "find_messages",
    "find_dirs", "find_packages", "find_places",
    "find_images_indices", "find_persons_indices",
    # Lookup canonici
    "get_now", "get_location", "get_processes",
    "get_files", "get_signatures", "get_proposals",
    "get_urls", "get_persons", "get_places", "get_images_indices",
    # Read primari
    "read_files", "read_messages", "read_urls_html", "read_urls_pdf",
    "list_dirs",
    # Trasformativi non sostituibili
    "send_messages", "write_files", "move_files",
    "create_dirs", "delete_dirs",
})

# Soglia di Jaccard al di sopra (>=) della quale un synth viene rejected.
# 0.5 = almeno meta' dei termini sono in comune. Calibrata sul caso live
# 8/5/2026 (`find_texts` con 5/8 termini overlap su `find_urls`).
AFFINITY_OVERLAP_THRESHOLD: float = 0.5

# Soglia stretta per skill imported binding-suffixed (ADR 0123): il binding
# qualifica esplicitamente il dominio remoto, le keyword sovrapposte sono
# attese. 0.85 = quasi identita' richiesta per parlare di squatting.
AFFINITY_OVERLAP_THRESHOLD_BINDING: float = 0.85

_AFFINITY_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"


def _affinity_audit_path() -> Path:
    """Path del log audit per affinity rejection. Creazione lazy."""
    p = _AFFINITY_AUDIT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p / "affinity_rejected.jsonl"


def jaccard_affinity(a: set, b: set) -> float:
    """Single source of truth per Jaccard fra due affinity set.

    Deterministico §7.9. Ritorna 0.0 se uno dei due set e' vuoto.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def check_affinity_pair(candidate_aff: set, existing_aff: set,
                         *, threshold: float = AFFINITY_OVERLAP_THRESHOLD
                         ) -> tuple[bool, float]:
    """Single source of truth per il check Jaccard pairwise.

    Ritorna `(overlap_detected, jaccard_value)`. `overlap_detected=True`
    se `jaccard >= threshold`. Caller decide cosa fare (reject, audit,
    log) — questa funzione e' un puro predicato deterministico.

    Usato da:
    - `_check_affinity_overlap(catalog)` al boot (threshold 0.5),
    - `skill_admission._affinity_overlap_check(plan, ...)` at-import
       (threshold 0.5 default, 0.85 se plan binding-suffixed).
    """
    j = jaccard_affinity(candidate_aff, existing_aff)
    return (j >= threshold, j)


def _check_affinity_overlap(catalog: Catalog) -> list[dict]:
    """Pairwise Jaccard su affinity. Synth con Jaccard >= soglia verso
    UN handcrafted o un altro synth piu' vecchio (manifest_path mtime)
    viene rejected con motivo `affinity_overlap`. Audit JSONL append.

    Conserva la priorita' handcrafted-vince (ADR 0079): non viene MAI
    rifiutato un handcrafted, solo synth (manifest_path dentro
    SYNTHESIZED_EXECUTORS_DIR). Idempotente: append solo entries nuove.
    """
    rejected_local: list[dict] = []
    # Snapshot affinity per nome (set per Jaccard).
    affinities = {n: set(e.affinity or []) for n, e in catalog.executors.items()}
    # Path: synth = path dentro SYNTHESIZED_EXECUTORS_DIR.
    def _is_synth(name: str) -> bool:
        ex = catalog.executors.get(name)
        if ex is None:
            return False
        try:
            return str(SYNTHESIZED_EXECUTORS_DIR) in str(ex.manifest_path)
        except Exception:
            return False

    def _is_imported(name: str) -> bool:
        """Imported via skill_importer (ADR 0123) - path sotto `skills/`
        (new, ADR 0160) o `_imports/` (legacy back-compat).
        Esentati dal pairwise overlap: il binding (provenance.imported_from)
        qualifica esplicitamente il dominio remoto. Le keyword sovrapposte
        sono attese e legittime, non un doppione mascherato."""
        ex = catalog.executors.get(name)
        if ex is None:
            return False
        try:
            from skills_paths import is_skill_path as _isp
            return _isp(ex.manifest_path)
        except Exception:
            return False

    def _mtime(name: str) -> float:
        ex = catalog.executors.get(name)
        if ex is None:
            return 0.0
        try:
            return ex.manifest_path.stat().st_mtime
        except OSError:
            return 0.0

    to_remove: list[tuple[str, dict]] = []
    for synth_name, synth_aff in affinities.items():
        # Skip handcrafted (non rifiutiamo mai handcrafted per overlap).
        if synth_name in HANDCRAFTED_FAMILIES:
            continue
        if not _is_synth(synth_name):
            continue
        # Skip imported (ADR 0123): binding qualifica il dominio remoto.
        if _is_imported(synth_name):
            continue
        if not synth_aff:
            continue

        # Confronto verso TUTTI gli altri executor del catalog (handcrafted
        # canonici + altri synth piu' vecchi). Vince il piu' vecchio.
        overlap_with: str | None = None
        overlap_jaccard: float = 0.0
        overlap_shared: list[str] = []
        synth_mtime = _mtime(synth_name)
        for other_name, other_aff in affinities.items():
            if other_name == synth_name:
                continue
            if not other_aff:
                continue
            # Skip altri synth piu' giovani (devono stare loro a evitarci).
            if _is_synth(other_name) and _mtime(other_name) >= synth_mtime:
                continue
            # Imported (vs imported o vs altri): non bloccano un synth nativo
            # piu' giovane; il binding e' la sola garanzia di distinzione.
            if _is_imported(other_name):
                continue
            # Delega a SoT centralizzata (ADR 0159): pure Jaccard test.
            triggered, jaccard = check_affinity_pair(
                synth_aff, other_aff,
                threshold=AFFINITY_OVERLAP_THRESHOLD,
            )
            if not triggered:
                continue
            inter = synth_aff & other_aff
            # Preferisci handcrafted come "overlapping_with" se entrambi
            # candidati: l'audit message e' piu' chiaro.
            if overlap_with is None or (
                other_name in HANDCRAFTED_FAMILIES
                and overlap_with not in HANDCRAFTED_FAMILIES
            ):
                overlap_with = other_name
                overlap_jaccard = jaccard
                overlap_shared = sorted(inter)
        if overlap_with is not None:
            entry = {
                "name": synth_name,
                "reason": "affinity_overlap",
                "overlapping_with": overlap_with,
                "jaccard": round(overlap_jaccard, 3),
                "shared_terms": overlap_shared,
            }
            rejected_local.append(entry)
            to_remove.append((synth_name, entry))

    # Apply: rimuovi dal catalog + append a rejected list + audit log.
    if to_remove:
        import json as _json
        import time as _time
        audit_path = _affinity_audit_path()
        ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        try:
            with audit_path.open("a", encoding="utf-8") as fh:
                for name, entry in to_remove:
                    ex = catalog.executors.pop(name, None)
                    catalog.rejected.append((
                        str(ex.manifest_path) if ex else f"<{name}>",
                        f"affinity_overlap with '{entry['overlapping_with']}' "
                        f"(jaccard={entry['jaccard']}, shared={entry['shared_terms']})",
                    ))
                    line = dict(entry)
                    line["ts"] = ts
                    fh.write(_json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as ex:
            log.warning("[loader] affinity audit log write failed: %s", ex)
            # Anche se l'audit fallisce, applichiamo il reject (no silent
            # admission §2.8): rimuovi dal catalog comunque.
            for name, entry in to_remove:
                if name in catalog.executors:
                    catalog.executors.pop(name, None)
                    catalog.rejected.append((
                        f"<{name}>",
                        f"affinity_overlap with '{entry['overlapping_with']}' "
                        f"(jaccard={entry['jaccard']})",
                    ))
        log.info("[loader] %d synth rejected for affinity overlap (ADR 0114)",
                 len(to_remove))
    return rejected_local


def _gc_collisions(catalog: Catalog) -> None:
    """Sposta i synth marcati `rejected` per collision con handcrafted in
    `/tmp/metnos_synth_gc_<ts>/<name>/`. Non distruttivo: backup recoverable.

    Si limita ai rejected con motivo `name collision with handcrafted`
    (vedi `_load_dir_into_catalog`). Altri rejected (firma invalida,
    parse error, no code, ...) restano in place: la GC e' specifica per
    la collision-by-name.

    Idempotente: se la dir di destinazione esiste gia' (es. ts collision)
    aggiunge un suffisso. Tracciato in log a livello info per ogni move.
    """
    if not catalog.rejected:
        return
    import shutil as _shutil
    import time as _time
    gc_root = Path(f"/tmp/metnos_synth_gc_{int(_time.time())}")
    n_moved = 0
    for path_str, reason in catalog.rejected:
        if "name collision with handcrafted" not in reason:
            continue
        src = Path(path_str)
        if not src.exists() or not src.is_dir():
            continue
        # Solo synth (in SYNTHESIZED_EXECUTORS_DIR) — handcrafted non vanno mai mossi.
        try:
            src.relative_to(SYNTHESIZED_EXECUTORS_DIR)
        except ValueError:
            continue
        gc_root.mkdir(parents=True, exist_ok=True)
        dst = gc_root / src.name
        suffix = 1
        while dst.exists():
            dst = gc_root / f"{src.name}.{suffix}"
            suffix += 1
        try:
            _shutil.move(str(src), str(dst))
            log.info("[loader] GC synth %s → %s (collision)", src, dst)
            n_moved += 1
        except OSError as ex:
            log.warning("[loader] GC synth move failed %s: %s", src, ex)
    if n_moved:
        log.info("[loader] GC sweep terminata: %d synth in collisione spostati in %s",
                 n_moved, gc_root)


def _iter_executor_dirs(executors_dir: Path):
    """Yield le subdir con `manifest.toml`. Visita 1 livello + caso speciale
    `skills/<skill>/<executor>/manifest.toml` (ADR 0123 + ADR 0160) e back-
    compat `_imports/<skill>/<executor>/manifest.toml` (legacy installazioni):
    gli executor importati da skill stanno 2 livelli sotto la base synth per
    separarli visivamente dai synth nativi del Synt."""
    for sub in sorted(executors_dir.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name in ("skills", "_imports"):
            for skill_dir in sorted(sub.iterdir()):
                if not skill_dir.is_dir():
                    continue
                # ADR 0160: gate via skill_registry (enabled/disabled).
                try:
                    from skill_registry import is_skill_enabled as _isen
                    if not _isen(skill_dir.name):
                        continue
                except Exception:
                    pass
                for ex_dir in sorted(skill_dir.iterdir()):
                    if ex_dir.is_dir() and (ex_dir / "manifest.toml").is_file():
                        yield ex_dir
            continue
        if (sub / "manifest.toml").is_file():
            yield sub


def _load_dir_into_catalog(executors_dir: Path, catalog: Catalog, verify: bool,
                           *, is_synthesized: bool = False) -> None:
    for sub in _iter_executor_dirs(executors_dir):
        manifest_path = sub / "manifest.toml"
        if not manifest_path.exists():
            continue

        # parse manifest prima della verify per leggere il lifecycle
        try:
            manifest = tomllib.loads(manifest_path.read_text())
        except Exception as e:
            catalog.rejected.append((str(sub), f"toml parse error: {e}"))
            continue

        lifecycle = str(manifest.get("lifecycle", "active"))
        # Executor Standard v1: i legacy senza dichiarazione continuano a
        # caricarsi durante la migrazione. Chi dichiara conformita', invece,
        # deve provarla deterministicamente prima ancora della firma. Un draft
        # proposed usa il profilo candidate; ogni stato planner-visible usa il
        # profilo active completo.
        if manifest.get("executor_standard") is not None:
            try:
                from executor_standard import validate_for_lifecycle as _validate_standard
                _standard_findings = _validate_standard(
                    manifest,
                    require_declaration=True,
                )
            except Exception as e:
                catalog.rejected.append((str(sub), f"executor_standard_error:{e}"))
                continue
            if _standard_findings:
                _summary = "; ".join(
                    f"{finding.code}:{finding.message}"
                    for finding in _standard_findings[:5]
                )
                catalog.rejected.append((str(sub), f"executor_standard:{_summary}"))
                continue
        # proposed: manifest senza code, niente verify firma
        if lifecycle == "proposed":
            signed_by = "(proposed, no code yet)"
        elif verify:
            ok, info = verify_executor(sub)
            if not ok:
                catalog.rejected.append((str(sub), info.get("reason", "verify failed")))
                continue
            signed_by = info.get("signed_by", "?")
        else:
            signed_by = "(verify disabled)"

        name = manifest.get("name", sub.name)
        # Collision detection: synth NON puo' shadow un handcrafted gia' caricato
        # (regressione 30/4/2026: synth list_dirs IMAP shadow handcrafted list_dirs FS).
        # Handcrafted vince per costruzione: il pool seed e' canonico, il synth
        # e' di crescita ma deve usare un nome che non collide. Stage 1 di synt
        # dovra' verificare il nome libero prima di proporlo.
        if is_synthesized and name in catalog.executors:
            catalog.rejected.append((str(sub), f"name collision with handcrafted '{name}' (synth ignored)"))
            continue
        code_files = manifest.get("code", {}).get("files", [])
        if not code_files and lifecycle != "proposed":
            catalog.rejected.append((str(sub), "no [code].files"))
            continue
        code_path = (sub / code_files[0]) if code_files else None

        # Main entry check §7.3 (24/5/2026): ogni executor file deve avere
        # `if __name__ == "__main__":` per il dispatch stdin/stdout JSON
        # del subprocess `python <code_path>`. Senza, il subprocess esegue
        # il top-level import e esce con stdout vuoto → runtime emette
        # `non-JSON output: ''; stderr: ''` (bug live 25/5/2026 find_images_web).
        # Reject deterministico al boot §7.9: il catalog non espone executor
        # degeneri al PLANNER, evitando il loop di retry su error class
        # `non_json` o `unknown` non risolvibile dal dispatcher.
        if code_path is not None and lifecycle != "proposed":
            try:
                _code_text = code_path.read_text(encoding="utf-8")
                if '__name__ == "__main__"' not in _code_text \
                        and "__name__ == '__main__'" not in _code_text:
                    catalog.rejected.append((
                        str(sub),
                        f"missing main entry point in {code_path.name}: "
                        f"expected `if __name__ == \"__main__\":` for "
                        f"subprocess stdin/stdout JSON dispatch",
                    ))
                    continue
            except OSError as _e:
                catalog.rejected.append((
                    str(sub), f"cannot read code_path: {_e}",
                ))
                continue

        # ADR 0092 Phase 4 (5/5/2026): description multilingua come table
        # TOML. Schema atteso: `[description] it = "..." en = "..."`.
        # Schema legacy flat (`description = "..."`) → ValueError esplicito.
        # Pattern latest-wins simmetrico (the design guide §7.3): nessuna lingua
        # canonica IT-only.
        try:
            from config import DEFAULT_LANG as _CURRENT_LANG
        except Exception:
            _CURRENT_LANG = "it"
        try:
            raw_desc = _resolve_lang_text(
                manifest.get("description", {}),
                where=f"{name}.description",
                current_lang=_CURRENT_LANG,
            )
        except ValueError as e:
            catalog.rejected.append((str(sub), str(e)))
            continue
        # affinity rimane lista flat IT+EN mista (decisione 5/5/2026, ADR 0092
        # Phase 4 §G: «Affinity tags non sono testo prompt, non vanno tradotti»).
        raw_affinity = list(manifest.get("affinity", []))

        # args.properties.<arg>.description risolto in-place per ogni arg.
        # Stesso schema (table TOML) e stesso fallback. Errore esplicito su
        # legacy flat per garantire convergenza dell'intero corpus.
        args_schema = manifest.get("args", {})
        if isinstance(args_schema, dict):
            props = args_schema.get("properties") or {}
            new_props = {}
            had_error = False
            for arg_name, arg_def in props.items():
                if not isinstance(arg_def, dict):
                    new_props[arg_name] = arg_def
                    continue
                arg_def_resolved = dict(arg_def)
                if "description" in arg_def_resolved:
                    try:
                        arg_def_resolved["description"] = _resolve_lang_text(
                            arg_def_resolved["description"],
                            where=f"{name}.args.properties.{arg_name}.description",
                            current_lang=_CURRENT_LANG,
                        )
                    except ValueError as e:
                        catalog.rejected.append((str(sub), str(e)))
                        had_error = True
                        break
                new_props[arg_name] = arg_def_resolved
            if had_error:
                continue
            args_schema = dict(args_schema)
            if props:
                args_schema["properties"] = new_props
            # §7.3 universale (25/5/2026): se requires_one_of menziona
            # `from_step` ma la property non e' in args.properties, il
            # tool_use protocol non espone from_step al LLM (anche se il
            # runtime lo accetta via resolve_from_step). Inietta property
            # virtuale cosi' il PLANNER vede l'opzione e puo' usarla.
            _rof = args_schema.get("requires_one_of") or []
            _needs_from_step = any(
                isinstance(g, list) and "from_step" in g for g in _rof
            )
            if _needs_from_step:
                _props = dict(args_schema.get("properties") or {})
                if "from_step" not in _props:
                    _props["from_step"] = {
                        "type": "integer",
                        "description": (
                            "Numero dello step precedente che ha prodotto "
                            "le entries da consumare (§4.1). Alternativo a "
                            "paths/urls quando le entries vengono da uno "
                            "step producer (find_*, list_*, get_*, read_*)."
                        ),
                        "minimum": 1,
                    }
                    args_schema["properties"] = _props

        # Dormancy check (ADR 15/5/2026): executor importato da skill
        # ma senza credenziali → dormant=True, filtrato dal pool top-K.
        try:
            from skill_credentials import compute_dormancy as _compute_dormancy
            _dormant, _dormant_reason = _compute_dormancy(
                manifest.get("provenance") or {}
            )
        except Exception:
            _dormant, _dormant_reason = False, ""

        # Gating SKILL first-party (asse 2 rilascio pubblico): se l'executor
        # appartiene a una skill-capacità DISABILITATA dall'utente, dormant →
        # escluso dal pool del planner. Default auto_enable=True → is_skill_enabled
        # True → nessun effetto (ambiente in esercizio INVARIATO); il gating
        # nasconde solo ciò che l'utente disattiva (skill_enabled.json). §7.3.
        if not _dormant:
            try:
                from skills_catalog import skill_for_executor as _sfe
                from skill_registry import is_skill_enabled as _isen
                _fp_skill = _sfe(name)
                if _fp_skill != "core" and not _isen(_fp_skill):
                    _dormant = True
                    _dormant_reason = f"skill_disabled:{_fp_skill}"
            except Exception:
                pass

        # Sandbox profile dichiarativo (mini-version 17/5/2026):
        # legge [sandbox] dal manifest senza enforcement. Default vuoto
        # = comportamento attuale (sandbox bubblewrap globale).
        sandbox_profile = manifest.get("sandbox") or {}
        if not isinstance(sandbox_profile, dict):
            sandbox_profile = {}
        # Provenance: soltanto gli import third-party hanno [provenance]
        # (ADR 0123). I builtin handcrafted, incluso GitHub, lasciano il dict
        # vuoto e dichiarano separatamente ``origin = "handcrafted"``.
        provenance = manifest.get("provenance") or {}
        if not isinstance(provenance, dict):
            provenance = {}
        # Placement remoto (ADR 0034): [placement] scope/targets/class.
        _placement = manifest.get("placement") or {}
        if not isinstance(_placement, dict):
            _placement = {}

        # Planning complexity hint (19/5/2026): [planning] complexity = "low|medium|high".
        # Vuoto = fallback automatico in agent_runtime su verbo del name.
        _planning = manifest.get("planning") or {}
        _complexity = (_planning.get("complexity") or "").strip().lower()
        if _complexity not in ("low", "medium", "high", ""):
            _complexity = ""  # invalid → fallback automatico

        # Piattaforme device supportate (W3.2, §16.3): assente = ["linux"]
        # default onesto (tutto il parco e' nato POSIX, §16.0 the design guide);
        # presente = lista non vuota di valori ammessi, altrimenti REJECT —
        # mai un default silenzioso su un valore malformato (§2.8).
        _platforms_raw = manifest.get("platforms")
        if _platforms_raw is None:
            _platforms = ["linux"]
        elif (isinstance(_platforms_raw, list) and _platforms_raw
              and all(isinstance(x, str) and x in ALLOWED_PLATFORMS
                      for x in _platforms_raw)):
            _platforms = list(_platforms_raw)
        else:
            catalog.rejected.append((str(sub), "invalid_platforms"))
            continue

        ex = Executor(
            name=name,
            version=manifest.get("version", "0.0.0"),
            description=raw_desc,
            affinity=raw_affinity,
            args_schema=args_schema,
            capabilities=_normalize_capabilities(manifest.get("capabilities")),
            tests=list(manifest.get("tests", [])),
            code_path=code_path,
            manifest_path=manifest_path,
            signed_by=signed_by,
            revertible=bool(manifest.get("revertible", False)),
            lifecycle=lifecycle,
            superseded_by=manifest.get("superseded_by"),
            reverse_pattern=manifest.get("reverse_pattern"),
            deprecated_at=manifest.get("deprecated_at"),
            deprecation_ttl_hours=int(manifest.get("deprecation_ttl_hours", 24)),
            timeout_s=int(manifest.get("timeout_s", 30)),
            dormant=_dormant,
            dormant_reason=_dormant_reason,
            sandbox_profile=sandbox_profile,
            provenance=provenance,
            placement=_placement,
            complexity=_complexity,
            platforms=_platforms,
            digest=str((manifest.get("code") or {}).get("digest") or ""),
            executor_standard=str(manifest.get("executor_standard") or ""),
            standard_state=_standard_state(
                manifest.get("executor_standard"), lifecycle),
            source=_source_kind(manifest, synthesized=is_synthesized),
            transport=_transport_kind(manifest),
            output_schema=_declared_output_schema(manifest),
            execution_policy=_execution_policy(manifest),
            execution_policy_declared=isinstance(manifest.get("execution"), dict),
        )
        catalog.executors[name] = ex


# --- Visibility filter -----------------------------------------------------

VISIBILITY_COMPOSER = "composer"  # default: solo lifecycle == 'active'
VISIBILITY_SYNT     = "synt"      # active + indice compatto dei deprecated
VISIBILITY_ALL      = "all"       # tutto, anche deprecated/archived (undo, audit)


def filter_for_visibility(catalog: "Catalog", visibility: str = VISIBILITY_COMPOSER) -> "Catalog":
    """Ritorna un nuovo Catalog filtrato per visibility (5 stati lifecycle).

    composer: solo `active`. Esclusione procedurale dei non-active.
    synt:     `active` + `proposed` + `synthesized` (manifest pieni, sono
              target di lavoro per il sintetizzatore). I deprecated sono
              esposti come indice compatto via `deprecated_index(catalog)`.
    all:      include tutto, anche deprecated/archived. Per undo storici e audit.
    """
    out = Catalog(rejected=list(catalog.rejected))
    for name, ex in catalog.executors.items():
        if visibility == VISIBILITY_ALL:
            out.executors[name] = ex
            continue
        if visibility == VISIBILITY_SYNT:
            if ex.lifecycle in ("active", "proposed", "synthesized"):
                out.executors[name] = ex
            continue
        # default: composer
        if ex.lifecycle == "active":
            out.executors[name] = ex
    return out


def deprecated_index(catalog: "Catalog") -> list[dict]:
    """Indice compatto dei deprecated: {name, superseded_by, version, deprecated_at,
    deprecation_ttl_hours}. Per synt + per scan di deprecation imminente."""
    return [
        {
            "name": ex.name,
            "superseded_by": ex.superseded_by,
            "version": ex.version,
            "deprecated_at": ex.deprecated_at,
            "deprecation_ttl_hours": ex.deprecation_ttl_hours,
        }
        for ex in catalog.executors.values()
        if ex.lifecycle == "deprecated"
    ]



def main():
    """CLI: dump del catalogo per ispezione."""
    catalog = load_catalog()
    print(f"Caricati {len(catalog)} executor; {len(catalog.rejected)} rifiutati.\n")
    for ex in catalog:
        caps = ", ".join(c.get("name", "?") for c in ex.capabilities)
        print(f"  {ex.name:14s} v{ex.version}  caps=[{caps}]  signed_by={ex.signed_by}")
        print(f"                 affinity: {', '.join(ex.affinity[:6])}{'...' if len(ex.affinity) > 6 else ''}")
    if catalog.rejected:
        print("\nRifiutati:")
        for path, reason in catalog.rejected:
            print(f"  {path}: {reason}")


if __name__ == "__main__":
    main()
