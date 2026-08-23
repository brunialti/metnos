"""Catalogo e gestione centralizzata dei servizi afferenti a Metnos.

Ogni componente dichiara qui identita' logica, possibili target systemd ed
endpoint applicativo. UI, diagnostica ed executor consumano questa API: non
mantengono liste di unita', scope o porte indipendenti.

Il catalogo e' chiuso intenzionalmente. Le azioni HTTP non possono trasformarsi
in argomenti systemctl arbitrari e unita' scoperte casualmente non diventano
automaticamente controllabili.
"""
from __future__ import annotations

import json
import atexit
import os
import pwd
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path

import config as _C
from lre_config import (
    feature_configuration_lock,
    read_feature_configuration,
    write_feature_configuration,
)


@dataclass(frozen=True)
class ServiceTarget:
    """Una possibile collocazione systemd di un servizio logico."""

    unit: str
    scope: str  # "system" | "user"


@dataclass(frozen=True)
class ServiceSpec:
    """Descrittore stabile consumato da runtime, installer e UI."""

    key: str
    label: str
    description: str
    group: str
    targets: tuple[ServiceTarget, ...]
    base_url: str = ""
    endpoint_env: str = ""
    health_path: str = ""
    required: bool = False
    integrated: bool = False
    label_en: str = ""
    description_en: str = ""
    group_en: str = ""
    health_policy: str = "endpoint"  # endpoint | process | systemd | application


def _target(unit: str, scope: str = "user") -> ServiceTarget:
    return ServiceTarget(unit=unit, scope=scope)


# Fonte unica di verita'. Il primo target e' quello dell'installazione pubblica;
# i target successivi coprono installazioni system-level altrettanto valide.
SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "http", "Server HTTP", "API, chat e Settings", "Nucleo",
        (_target("metnos-http.service"),
         _target("metnos-http.service", "system")),
        "http://127.0.0.1:8770", "METNOS_HTTP_URL", "/agent/health",
        required=True, integrated=True,
        label_en="HTTP server", description_en="API, chat, and Settings",
        group_en="Core",
    ),
    ServiceSpec(
        "telegram", "Daemon Telegram", "Canale Telegram e notifiche", "Nucleo",
        (_target("metnos-telegram-daemon.service"),),
        integrated=True,
        label_en="Telegram daemon",
        description_en="Telegram channel and notifications", group_en="Core",
    ),
    ServiceSpec(
        "side_display", "Display laterale", "Display grafico virtuale del browser",
        "Navigazione web", (_target("metnos-side-display.service"),),
        integrated=True,
        label_en="Side display",
        description_en="Virtual browser display", group_en="Website browsing",
    ),
    ServiceSpec(
        "playwright", "Playwright sidecar", "Browser e sessioni Sites",
        "Navigazione web", (_target("metnos-playwright.service"),),
        "http://127.0.0.1:8771", "METNOS_PLAYWRIGHT_URL", "/health",
        integrated=True,
        label_en="Playwright sidecar",
        description_en="Browser and Sites sessions", group_en="Website browsing",
    ),
    ServiceSpec(
        "llm", "Modello linguistico locale",
        "Server locale per fast.micro, fast.procedural, fast.fidelity, middle, wise e creative.",
        "Nucleo",
        (_target("metnos-llm.service"),
         _target("llama-server.service", "system")),
        "http://127.0.0.1:8080", "METNOS_LLM_URL", "/health",
        integrated=True,
        label_en="Local language model",
        description_en=(
            "Local server for fast.micro, fast.procedural, fast.fidelity, "
            "middle, wise, and creative."
        ),
        group_en="Core",
        health_policy="process",
    ),
    ServiceSpec(
        "searxng", "SearXNG", "Motore di ricerca web", "Ricerca e geografia",
        (_target("metnos-searxng.service"),
         _target("searxng.service", "system")),
        "http://127.0.0.1:8888", "METNOS_SEARXNG_URL", "/search?q=metnos-health&format=json",
        integrated=True,
        label_en="SearXNG", description_en="Web search engine",
        group_en="Search & geo",
    ),
    ServiceSpec(
        "photon", "Server geografico", "Geocoding locale Photon/OSM", "Ricerca e geografia",
        (_target("metnos-photon.service"),
         _target("photon.service", "system")),
        "http://127.0.0.1:2322", "METNOS_PHOTON_URL", "/api/?q=Rome&limit=1",
        integrated=True,
        label_en="Geo server",
        description_en="Local Photon/OSM geocoding", group_en="Search & geo",
    ),
    ServiceSpec(
        "i18n", "Traduttore i18n",
        "Completa automaticamente le traduzioni differite con il ruolo wise.",
        "Nucleo", (_target("metnos-i18n-translator.timer"),),
        required=True, integrated=True,
        label_en="i18n translator",
        description_en=(
            "Automatically completes deferred translations with the "
            "wise role."
        ),
        group_en="Core",
    ),
    ServiceSpec(
        "durable_workloads", "LRE (Long Run Engine)",
        "Motore supervisionato per le attività LRE ammesse. Rimane inattivo finché la funzione non è abilitata.",
        "Nucleo", (_target("metnos-durable-worker.service"),),
        required=True, integrated=True,
        label_en="LRE (Long Run Engine)",
        description_en=(
            "Supervised engine for admitted LRE tasks. It remains "
            "inactive until the feature is enabled."
        ),
        group_en="Core",
        health_policy="application",
    ),
)

_BY_KEY = {service.key: service for service in SERVICES}
_ACTIONS = frozenset({"start", "stop", "restart"})
_DESIRED_STATES = frozenset({"running", "stopped"})
_CONTROL_LOCK = threading.Lock()
_SNAPSHOT_POOL: ThreadPoolExecutor | None = None
_SNAPSHOT_POOL_LOCK = threading.Lock()
_SHOW_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID",
    "ActiveEnterTimestamp", "UnitFileState",
)
_INTEGRATED_USER_AUXILIARIES = (
    "metnos-i18n-translator.service",
)
_LRE_HEALTH_MESSAGE_KEYS = {
    "feature_disabled": "UI_SERVICES_LRE_HEALTH_FEATURE_DISABLED",
    "runtime_bindings_unavailable": "UI_SERVICES_LRE_HEALTH_BINDINGS_UNAVAILABLE",
    "already_active": "UI_SERVICES_LRE_HEALTH_ALREADY_ACTIVE",
    "schema_incompatible": "UI_SERVICES_LRE_HEALTH_SCHEMA_INCOMPATIBLE",
    "database_unavailable": "UI_SERVICES_LRE_HEALTH_DATABASE_UNAVAILABLE",
    "startup_failed": "UI_SERVICES_LRE_HEALTH_STARTUP_FAILED",
    "recovery_incomplete": "UI_SERVICES_LRE_HEALTH_RECOVERY_INCOMPLETE",
    "recovery_failed": "UI_SERVICES_LRE_HEALTH_RECOVERY_FAILED",
    "worker_cycle_failed": "UI_SERVICES_LRE_HEALTH_WORKER_CYCLE_FAILED",
    "execution_deadline_exceeded": "UI_SERVICES_LRE_HEALTH_DEADLINE_EXCEEDED",
    "stopped": "UI_SERVICES_LRE_HEALTH_STOPPED",
    "health_unavailable": "UI_SERVICES_LRE_HEALTH_UNAVAILABLE",
    "health_stale": "UI_SERVICES_LRE_HEALTH_STALE",
    "feature_config_invalid": "UI_SERVICES_LRE_HEALTH_CONFIG_INVALID",
    "feature_state_mismatch": "UI_SERVICES_LRE_HEALTH_STATE_MISMATCH",
}


def catalog() -> tuple[ServiceSpec, ...]:
    """Ritorna il catalogo immutabile dei servizi logici."""
    return SERVICES


def _catalog_key(service_key: str, field: str) -> str:
    return f"UI_SERVICE_{service_key.upper()}_{field.upper()}"


def localization_inventory(source_lang: str) -> tuple[tuple[str, str], ...]:
    """Enumerate service prose for the shared localization registry."""

    source = _C.normalize_language_tag(source_lang)
    rows: list[tuple[str, str]] = []
    for service in SERVICES:
        values = {
            "label": {"it": service.label, "en": service.label_en or service.label},
            "description": {
                "it": service.description,
                "en": service.description_en or service.description,
            },
            "group": {"it": service.group, "en": service.group_en or service.group},
        }
        for field, baselines in values.items():
            text = baselines.get(source) or baselines.get(_C.BOOTSTRAP_LANGUAGE)
            if text:
                rows.append((_catalog_key(service.key, field), text))
    return tuple(rows)


def system_units() -> tuple[str, ...]:
    """Unita' system-level controllabili, derivate dal catalogo chiuso."""
    return tuple(sorted({
        target.unit
        for service in SERVICES
        for target in service.targets
        if target.scope == "system"
    }))


def integrated_user_units() -> tuple[str, ...]:
    """User units owned as one lifecycle by ``metnos.target``."""
    primary = tuple(
        target.unit
        for service in SERVICES if service.integrated
        for target in service.targets if target.scope == "user"
    )
    return tuple(dict.fromkeys((*primary, *_INTEGRATED_USER_AUXILIARIES)))


def render_polkit_rule(user: str | None = None) -> str:
    """Genera la policy minima per target system-level.

    Concede al solo utente Metnos i tre verbi esposti dal core e soltanto
    sulle unita' ricavate da ``SERVICES``. Non concede enable, modifica degli
    unit file, daemon-reload o comandi systemd generici.
    """
    subject_user = user or service_user()
    units_js = json.dumps(system_units(), ensure_ascii=True)
    user_js = json.dumps(subject_user, ensure_ascii=True)
    return f"""// Generated by Metnos. Do not add wildcard units here.
polkit.addRule(function(action, subject) {{
    if (action.id !== "org.freedesktop.systemd1.manage-units" ||
        subject.user !== {user_js}) {{
        return polkit.Result.NOT_HANDLED;
    }}
    var units = {units_js};
    var verbs = ["start", "stop", "restart"];
    var unit = action.lookup("unit");
    var verb = action.lookup("verb");
    if (units.indexOf(unit) >= 0 && verbs.indexOf(verb) >= 0) {{
        return polkit.Result.YES;
    }}
    return polkit.Result.NOT_HANDLED;
}});
"""


def get(key: str) -> ServiceSpec | None:
    return _BY_KEY.get(key)


def key_for_unit(unit: str, scope: str | None = None) -> str:
    """Resolve an exact catalog target back to its logical service key."""
    for spec in SERVICES:
        if any(
            target.unit == unit and (scope is None or target.scope == scope)
            for target in spec.targets
        ):
            return spec.key
    return ""


def _desired_state_path() -> Path:
    return Path(_C.PATH_USER_STATE) / "service_desired_states.json"


def _load_desired_states() -> dict[str, str]:
    """Load administrator intent; invalid state fails closed to ``running``.

    The file records only logical catalog keys, never unit names supplied by
    callers.  A missing/corrupt file therefore cannot broaden the control
    surface or silently suppress availability alerts.
    """
    try:
        raw = json.loads(_desired_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    values = raw.get("services") if isinstance(raw, dict) else None
    if not isinstance(values, dict):
        return {}
    return {
        key: value
        for key, value in values.items()
        if (key in _BY_KEY and value in _DESIRED_STATES
            and not _BY_KEY[key].required)
    }


def desired_state(key: str) -> str:
    """Return the centrally recorded target state for a catalog service."""
    service = _BY_KEY.get(key)
    if service is None:
        return ""
    if service.required:
        return "running"
    return _load_desired_states().get(key, "running")


def _record_desired_state(key: str, value: str) -> None:
    if key not in _BY_KEY or value not in _DESIRED_STATES:
        raise ValueError("invalid desired service state")
    if _BY_KEY[key].required and value != "running":
        raise ValueError("required services must remain running")
    path = _desired_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    states = _load_desired_states()
    states[key] = value
    payload = {
        "schema_version": 1,
        "services": dict(sorted(states.items())),
        "updated_at": time.time(),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _allowed_actions(spec: ServiceSpec, state: dict) -> list[str]:
    if state.get("load_state") in {"not-found", "error"}:
        return []
    # Stopping the HTTP server from the page it serves would remove the only
    # control that could start it again.  It remains centrally catalogued and
    # restartable, but deliberate shutdown requires an independent control
    # plane (CLI/systemd).
    if spec.key == "http":
        return ["restart"]
    if spec.required:
        return ["start", "restart"]
    return ["start", "stop", "restart"]


def service_user() -> str:
    """Utente del runtime, anche quando HTTP e' una system unit."""
    configured = os.environ.get("METNOS_SERVICE_USER", "").strip()
    if configured:
        return configured
    try:
        uid = Path(_C.PATH_USER_DATA).stat().st_uid
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        return pwd.getpwuid(os.getuid()).pw_name


def _user_systemd_env() -> dict[str, str]:
    """Costruisce esplicitamente l'ambiente del bus user systemd."""
    env = os.environ.copy()
    try:
        uid = pwd.getpwnam(service_user()).pw_uid
    except KeyError:
        uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    return env


def _systemctl(target: ServiceTarget, *args: str) -> tuple[list[str], dict | None]:
    cmd = ["systemctl"]
    env = None
    if target.scope == "user":
        cmd.append("--user")
        env = _user_systemd_env()
    cmd.extend(args)
    return cmd, env


def _remaining(deadline_at: float | None, cap_s: float) -> float:
    if deadline_at is None:
        return cap_s
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("service snapshot deadline exhausted")
    return min(cap_s, remaining)


def _inspect_target(target: ServiceTarget, *,
                    deadline_at: float | None = None) -> dict:
    cmd, env = _systemctl(
        target, "show", target.unit,
        "--property=" + ",".join(_SHOW_PROPERTIES),
    )
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_remaining(deadline_at, 5), check=False, env=env,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "unit": target.unit, "scope": target.scope,
            "load_state": "error", "active_state": "unknown",
            "sub_state": "unknown", "manager_error": type(exc).__name__,
            "observation_error": type(exc).__name__,
        }

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    # `systemctl show` may return non-zero for a genuinely absent unit while
    # still emitting the canonical LoadState=not-found.  Any other non-zero
    # result without trustworthy state is an observation failure (bus,
    # permission, manager unavailable), not semantic evidence that a service
    # is missing.
    trustworthy_missing = values.get("LoadState") == "not-found"
    observation_error = (
        "" if result.returncode == 0 or trustworthy_missing
        else "manager_command_failed"
    )
    return {
        "unit": target.unit,
        "scope": target.scope,
        "id": values.get("Id", target.unit),
        "load_state": values.get(
            "LoadState", "error" if observation_error else "not-found"),
        "active_state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "main_pid": values.get("MainPID", "0"),
        "active_since": values.get("ActiveEnterTimestamp", ""),
        "unit_state": values.get("UnitFileState", "unknown"),
        "manager_error": (result.stderr or "").strip()[-300:] if result.returncode else "",
        "observation_error": observation_error,
    }


def resolve_target(spec: ServiceSpec, *,
                   deadline_at: float | None = None) -> dict:
    """Prefer the active loaded target, then the first loaded alternative.

    Upgrade hosts deliberately carry an inactive user HTTP unit beside the
    active system rollback baseline.  Choosing merely the first loaded unit
    would therefore report and control the wrong process.
    """
    fallback: dict | None = None
    loaded: dict | None = None
    for target in spec.targets:
        state = _inspect_target(target, deadline_at=deadline_at)
        if fallback is None:
            fallback = state
        if state.get("load_state") in {"not-found", "error"}:
            continue
        if loaded is None:
            loaded = state
        if state.get("active_state") in {"active", "activating", "reloading"}:
            return state
    return loaded or fallback or {
        "unit": "", "scope": "system", "load_state": "not-found",
        "active_state": "unknown", "sub_state": "unknown",
    }


def endpoint(key: str, *, include_env: bool = True) -> str:
    """Base URL canonica di un servizio, senza il percorso di health."""
    spec = _BY_KEY.get(key)
    if spec is None:
        return ""
    if include_env and spec.endpoint_env:
        configured = os.environ.get(spec.endpoint_env, "").strip()
        if configured:
            return configured.rstrip("/")
    return spec.base_url.rstrip("/")


def health_url(spec: ServiceSpec) -> str:
    base = endpoint(spec.key)
    if not base:
        return ""
    return base + spec.health_path


def _probe(url: str, *, deadline_at: float | None = None
           ) -> tuple[bool | None, str]:
    if not url:
        return None, ""
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "metnos-service-health/1.0"},
        )
        with urllib.request.urlopen(
                request, timeout=_remaining(deadline_at, 3)) as response:
            status = int(getattr(response, "status", 0) or 0)
        return 200 <= status < 400, str(status)
    except urllib.error.HTTPError as exc:
        return False, str(exc.code)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, type(exc).__name__


def _probe_process(state: dict) -> tuple[bool | None, str]:
    """Cheap liveness evidence for services whose HTTP slots may be busy.

    The local LLM can legitimately keep its health endpoint occupied during a
    long inference.  Process state catches stopped/zombie failures without
    turning normal queueing into a false admin alert.
    """
    try:
        pid = int(state.get("main_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 1:
        return False, "main_pid_unavailable"
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text(
            encoding="utf-8", errors="replace",
        ).split()
        process_state = fields[2] if len(fields) > 2 else ""
    except OSError as exc:
        return False, type(exc).__name__
    healthy = process_state not in {"", "T", "t", "X", "x", "Z"}
    return healthy, f"process:{process_state or '?'}"


def _canonical_status(row: dict, *, required: bool, healthy: bool | None) -> str:
    if row.get("load_state") == "not-found":
        return "missing"
    active = row.get("active_state")
    if active == "failed":
        return "failed"
    if active in {"activating", "reloading", "deactivating"}:
        return "transitioning"
    if active == "active" and healthy is False:
        return "degraded"
    if active == "active":
        return "running"
    if required:
        return "failed"
    return "stopped"


def snapshot_one(spec: ServiceSpec, *, probe_endpoint: bool = True,
                 deadline_at: float | None = None) -> dict:
    state = resolve_target(spec, deadline_at=deadline_at)
    url = health_url(spec)
    feature = (
        read_feature_configuration()
        if spec.key == "durable_workloads" else None
    )
    feature_converged: bool | None = None
    if not probe_endpoint:
        healthy, health_detail = None, ""
    elif spec.health_policy == "process":
        healthy, health_detail = _probe_process(state)
    elif spec.health_policy == "systemd":
        healthy, health_detail = None, ""
    elif spec.health_policy == "application":
        try:
            from durable_workloads.service import health_snapshot

            application_health = health_snapshot()
            ready = application_health.get("state") == "ready"
            if feature is None:
                healthy = ready
            else:
                observed_enabled = application_health.get("enabled")
                feature_converged = (
                    isinstance(observed_enabled, bool)
                    and observed_enabled == feature.enabled
                )
                feature_disabled = (
                    feature.valid
                    and feature_converged
                    and observed_enabled is False
                    and application_health.get("reason_code")
                    == "feature_disabled"
                )
                healthy = (
                    ready and feature.valid and feature_converged
                ) or feature_disabled
            health_detail = str(application_health.get("reason_code") or "")[:64]
            if feature is not None and not feature.valid:
                health_detail = "feature_config_invalid"
            elif feature is not None and not feature_converged:
                health_detail = "feature_state_mismatch"
        except Exception:
            healthy, health_detail = False, "health_unavailable"
    else:
        healthy, health_detail = _probe(url, deadline_at=deadline_at)
    installed = state.get("load_state") not in {"not-found", "error"}
    actions = _allowed_actions(spec, state) if installed else []
    target_state = desired_state(spec.key)
    row = {
        **asdict(spec), **state,
        "installed": installed,
        "healthy": healthy,
        "health_detail": health_detail,
        "health_url": url,
        "actionable": bool(actions),
        "allowed_actions": actions,
        "desired_state": target_state,
        "managed_by": "metnos.target" if spec.integrated else "",
    }
    if feature is not None:
        row.update({
            "feature_enabled": feature.enabled,
            "feature_config_valid": feature.valid,
            "feature_converged": feature_converged,
            "feature_configurable": (
                installed and feature.source != "environment"
            ),
            "health_message_key": _LRE_HEALTH_MESSAGE_KEYS.get(
                health_detail, "",
            ),
        })
    row["status"] = _canonical_status(
        row, required=spec.required, healthy=healthy,
    )
    row["in_desired_state"] = (
        (target_state == "running" and row["status"] == "running")
        or (target_state == "stopped" and row["status"] == "stopped")
    )
    return row


def _failed_snapshot(spec: ServiceSpec, reason: str) -> dict:
    target = spec.targets[0]
    row = {
        **asdict(spec),
        "unit": target.unit,
        "scope": target.scope,
        "load_state": "error",
        "active_state": "unknown",
        "sub_state": "unknown",
        "installed": False,
        "healthy": False if spec.health_path or spec.health_policy == "application" else None,
        "health_detail": reason,
        "observation_error": reason,
        "health_url": health_url(spec),
        "actionable": False,
        "allowed_actions": [],
        "desired_state": desired_state(spec.key),
        "in_desired_state": False,
        "status": "failed" if spec.required else "missing",
    }
    if spec.key == "durable_workloads":
        feature = read_feature_configuration()
        row.update({
            "feature_enabled": feature.enabled,
            "feature_config_valid": feature.valid,
            "feature_converged": None,
            "feature_configurable": False,
            "health_message_key": _LRE_HEALTH_MESSAGE_KEYS.get(reason, ""),
        })
    return row


def _safe_snapshot(spec: ServiceSpec, probe_endpoints: bool,
                   deadline_at: float | None = None) -> dict:
    try:
        return snapshot_one(
            spec, probe_endpoint=probe_endpoints, deadline_at=deadline_at)
    except Exception as exc:  # noqa: BLE001 — isolamento per-servizio
        reason = "deadline_exhausted" if isinstance(exc, TimeoutError) \
            else "probe_unavailable"
        return _failed_snapshot(spec, reason)


def _snapshot_pool() -> ThreadPoolExecutor:
    """Lazily create the bounded probe pool once per process."""
    global _SNAPSHOT_POOL
    with _SNAPSHOT_POOL_LOCK:
        if _SNAPSHOT_POOL is None:
            _SNAPSHOT_POOL = ThreadPoolExecutor(
                max_workers=min(8, len(SERVICES)),
                thread_name_prefix="metnos_service_probe",
            )
        return _SNAPSHOT_POOL


def _shutdown_snapshot_pool() -> None:
    global _SNAPSHOT_POOL
    with _SNAPSHOT_POOL_LOCK:
        pool, _SNAPSHOT_POOL = _SNAPSHOT_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_snapshot_pool)


def snapshots(*, probe_endpoints: bool = True,
              include_missing: bool = True,
              timeout_s: float | None = None) -> list[dict]:
    # I probe sono indipendenti. ``map`` conserva l'ordine del catalogo e un
    # endpoint lento non serializza l'intera pagina amministrativa. Il pool è
    # riusato: una pagina admin non deve creare/distruggere otto thread ogni
    # volta. Il processo lo chiude con atexit.
    pool = _snapshot_pool()
    if timeout_s is None:
        rows = list(pool.map(
            lambda service: _safe_snapshot(service, probe_endpoints), SERVICES,
        ))
    else:
        budget = max(0.001, float(timeout_s))
        deadline_at = time.monotonic() + budget
        futures = {
            pool.submit(
                _safe_snapshot, service, probe_endpoints, deadline_at): index
            for index, service in enumerate(SERVICES)
        }
        done, pending = wait(tuple(futures), timeout=budget)
        by_index = {futures[future]: future.result() for future in done}
        for future in pending:
            future.cancel()
            index = futures[future]
            by_index[index] = _failed_snapshot(
                SERVICES[index], "deadline_exhausted")
        rows = [by_index[index] for index in range(len(SERVICES))]
    if not include_missing:
        rows = [row for row in rows if row["installed"]]
    return rows


def localized(rows: list[dict], lang: str) -> list[dict]:
    """Localize catalog prose without changing technical state fields."""
    import i18n

    out: list[dict] = []
    for source in rows:
        row = dict(source)
        key = str(row.get("key") or "")
        for field in ("label", "description", "group"):
            baselines = {
                "it": str(row.get(field) or ""),
                "en": str(row.get(f"{field}_en") or row.get(field) or ""),
            }
            row[field] = i18n.editorial_text(
                _catalog_key(key, field), lang, baselines,
            )
        out.append(row)
    return out


def control(key: str, action: str) -> tuple[bool, str]:
    """Esegue un'azione chiusa sul target risolto dal catalogo.

    ``--no-block`` evita che il riavvio del server HTTP interrompa la risposta
    prima che systemd abbia accettato l'operazione. Lo stato successivo viene
    sempre osservato tramite ``snapshots`` e non dedotto dal return code.
    """
    spec = _BY_KEY.get(key)
    if spec is None or action not in _ACTIONS:
        return False, "invalid service action"
    state = resolve_target(spec)
    if state.get("load_state") in {"not-found", "error"}:
        return False, "service unit is not installed"
    if action not in _allowed_actions(spec, state):
        return False, "service action is unavailable from this control plane"
    target = ServiceTarget(state["unit"], state["scope"])
    cmd, env = _systemctl(target, "--no-block", action, target.unit)
    try:
        with _CONTROL_LOCK:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                check=False, env=env,
            )
            if result.returncode == 0:
                _record_desired_state(
                    key, "stopped" if action == "stop" else "running",
                )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    detail = (result.stderr or result.stdout or "").strip()[-300:]
    return result.returncode == 0, detail


def configure_lre_feature(enabled: bool) -> tuple[bool, str]:
    """Persist and converge the closed LRE gate on its exact user unit.

    Enabling rolls the file back to its previous safe value if systemd rejects
    the restart.  Disabling keeps the file off even when restart fails and
    makes one bounded stop attempt, so a future restart cannot re-enable work.
    """

    if not isinstance(enabled, bool):
        return False, "invalid LRE feature state"
    spec = _BY_KEY["durable_workloads"]
    state = resolve_target(spec)
    target_identity = (state.get("scope"), state.get("unit"))
    allowed_targets = {(target.scope, target.unit) for target in spec.targets}
    if (
        state.get("load_state") in {"not-found", "error"}
        or target_identity not in allowed_targets
    ):
        return False, "LRE service unit is not installed"

    effective = read_feature_configuration()
    if effective.source == "environment":
        return False, "LRE feature state is overridden by the process environment"
    target = ServiceTarget(str(state["unit"]), str(state["scope"]))
    cmd, env = _systemctl(
        target, "--no-block", "restart", target.unit,
    )

    try:
        with _CONTROL_LOCK:
            with feature_configuration_lock():
                current = read_feature_configuration()
                if current.source == "environment":
                    return False, (
                        "LRE feature state is overridden by the process environment"
                    )
                previous_enabled = current.enabled if current.valid else False
                try:
                    write_feature_configuration(enabled)
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30,
                        check=False, env=env,
                    )
                except Exception:
                    if enabled:
                        write_feature_configuration(previous_enabled)
                    raise
                if result.returncode != 0 and enabled:
                    write_feature_configuration(previous_enabled)
                elif result.returncode != 0:
                    stop_cmd, stop_env = _systemctl(
                        target, "--no-block", "stop", target.unit,
                    )
                    subprocess.run(
                        stop_cmd, capture_output=True, text=True, timeout=30,
                        check=False, env=stop_env,
                    )
    except (FileNotFoundError, OSError, ValueError,
            subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__

    detail = (result.stderr or result.stdout or "").strip()[-300:]
    return result.returncode == 0, detail
