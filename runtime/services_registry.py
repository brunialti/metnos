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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import config as _C


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


def _target(unit: str, scope: str = "user") -> ServiceTarget:
    return ServiceTarget(unit=unit, scope=scope)


# Fonte unica di verita'. Il primo target e' quello dell'installazione pubblica;
# i target successivi coprono installazioni system-level altrettanto valide.
SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "http", "HTTP server", "API, chat e Settings", "Core",
        (_target("metnos-http.service"),
         _target("metnos-http.service", "system")),
        "http://127.0.0.1:8770", "METNOS_HTTP_URL", "/agent/health",
        required=True, integrated=True,
    ),
    ServiceSpec(
        "telegram", "Telegram daemon", "Canale Telegram e notifiche", "Core",
        (_target("metnos-telegram-daemon.service"),),
        integrated=True,
    ),
    ServiceSpec(
        "side_display", "Side display", "Display grafico virtuale del browser",
        "Website browsing", (_target("metnos-side-display.service"),),
        integrated=True,
    ),
    ServiceSpec(
        "playwright", "Playwright sidecar", "Browser e sessioni Sites",
        "Website browsing", (_target("metnos-playwright.service"),),
        "http://127.0.0.1:8771", "METNOS_PLAYWRIGHT_URL", "/health",
        integrated=True,
    ),
    ServiceSpec(
        "llm", "Local language model", "llama-server dei tier locali", "Core",
        (_target("metnos-llm.service"),),
        "http://127.0.0.1:8080", "METNOS_LLM_MID_URL", "/health",
        integrated=True,
    ),
    ServiceSpec(
        "searxng", "SearXNG", "Motore di ricerca web", "Search & geo",
        (_target("metnos-searxng.service"),
         _target("searxng.service", "system")),
        "http://127.0.0.1:8888", "METNOS_SEARXNG_URL", "/search?q=metnos-health&format=json",
        integrated=True,
    ),
    ServiceSpec(
        "photon", "Geo server", "Geocoding locale Photon/OSM", "Search & geo",
        (_target("metnos-photon.service"),
         _target("photon.service", "system")),
        "http://127.0.0.1:2322", "METNOS_PHOTON_URL", "/api/?q=Rome&limit=1",
        integrated=True,
    ),
    ServiceSpec(
        "cloudflare", "Cloudflare tunnel", "Accesso remoto alla chat",
        "Connectivity", (_target("cloudflared-metnos-chat.service"),),
        integrated=True,
    ),
    ServiceSpec(
        "issues", "Issues sidecar", "Bridge locale per issue amministrative",
        "Connectivity", (_target("metnos-issues-sidecar.service"),),
        integrated=True,
    ),
    ServiceSpec(
        "i18n", "i18n translator", "Riempimento differito delle traduzioni",
        "Core", (_target("metnos-i18n-translator.timer"),),
        integrated=True,
    ),
)

_BY_KEY = {service.key: service for service in SERVICES}
_ACTIONS = frozenset({"start", "stop", "restart"})
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


def catalog() -> tuple[ServiceSpec, ...]:
    """Ritorna il catalogo immutabile dei servizi logici."""
    return SERVICES


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


def _inspect_target(target: ServiceTarget) -> dict:
    cmd, env = _systemctl(
        target, "show", target.unit,
        "--property=" + ",".join(_SHOW_PROPERTIES),
    )
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False, env=env,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "unit": target.unit, "scope": target.scope,
            "load_state": "error", "active_state": "unknown",
            "sub_state": "unknown", "manager_error": type(exc).__name__,
        }

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "unit": target.unit,
        "scope": target.scope,
        "id": values.get("Id", target.unit),
        "load_state": values.get("LoadState", "not-found"),
        "active_state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "main_pid": values.get("MainPID", "0"),
        "active_since": values.get("ActiveEnterTimestamp", ""),
        "unit_state": values.get("UnitFileState", "unknown"),
        "manager_error": (result.stderr or "").strip()[-300:] if result.returncode else "",
    }


def resolve_target(spec: ServiceSpec) -> dict:
    """Prefer the active loaded target, then the first loaded alternative.

    Upgrade hosts deliberately carry an inactive user HTTP unit beside the
    active system rollback baseline.  Choosing merely the first loaded unit
    would therefore report and control the wrong process.
    """
    fallback: dict | None = None
    loaded: dict | None = None
    for target in spec.targets:
        state = _inspect_target(target)
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


def _probe(url: str) -> tuple[bool | None, str]:
    if not url:
        return None, ""
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "metnos-service-health/1.0"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            status = int(getattr(response, "status", 0) or 0)
        return 200 <= status < 400, str(status)
    except urllib.error.HTTPError as exc:
        return False, str(exc.code)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, type(exc).__name__


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


def snapshot_one(spec: ServiceSpec, *, probe_endpoint: bool = True) -> dict:
    state = resolve_target(spec)
    url = health_url(spec)
    healthy, health_detail = _probe(url) if probe_endpoint else (None, "")
    installed = state.get("load_state") not in {"not-found", "error"}
    row = {
        **asdict(spec), **state,
        "installed": installed,
        "healthy": healthy,
        "health_detail": health_detail,
        "health_url": url,
        "actionable": installed and not spec.integrated,
        "managed_by": "metnos.target" if spec.integrated else "",
    }
    row["status"] = _canonical_status(
        row, required=spec.required, healthy=healthy,
    )
    return row


def _safe_snapshot(spec: ServiceSpec, probe_endpoints: bool) -> dict:
    try:
        return snapshot_one(spec, probe_endpoint=probe_endpoints)
    except Exception as exc:  # noqa: BLE001 — isolamento per-servizio
        target = spec.targets[0]
        return {
            **asdict(spec),
            "unit": target.unit,
            "scope": target.scope,
            "load_state": "error",
            "active_state": "unknown",
            "sub_state": "unknown",
            "installed": False,
            "healthy": False if spec.health_path else None,
            "health_detail": type(exc).__name__,
            "health_url": health_url(spec),
            "actionable": False,
            "status": "failed" if spec.required else "missing",
        }


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
              include_missing: bool = True) -> list[dict]:
    # I probe sono indipendenti. ``map`` conserva l'ordine del catalogo e un
    # endpoint lento non serializza l'intera pagina amministrativa. Il pool è
    # riusato: una pagina admin non deve creare/distruggere otto thread ogni
    # volta. Il processo lo chiude con atexit.
    pool = _snapshot_pool()
    rows = list(pool.map(
        lambda service: _safe_snapshot(service, probe_endpoints), SERVICES,
    ))
    if not include_missing:
        rows = [row for row in rows if row["installed"]]
    return rows


def control(key: str, action: str) -> tuple[bool, str]:
    """Esegue un'azione chiusa sul target risolto dal catalogo.

    ``--no-block`` evita che il riavvio del server HTTP interrompa la risposta
    prima che systemd abbia accettato l'operazione. Lo stato successivo viene
    sempre osservato tramite ``snapshots`` e non dedotto dal return code.
    """
    spec = _BY_KEY.get(key)
    if spec is None or action not in _ACTIONS:
        return False, "invalid service action"
    if spec.integrated:
        return False, "service is managed by metnos.target; use stack_reconcile"
    state = resolve_target(spec)
    if state.get("load_state") in {"not-found", "error"}:
        return False, "service unit is not installed"
    target = ServiceTarget(state["unit"], state["scope"])
    cmd, env = _systemctl(target, "--no-block", action, target.unit)
    try:
        with _CONTROL_LOCK:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                check=False, env=env,
            )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    detail = (result.stderr or result.stdout or "").strip()[-300:]
    return result.returncode == 0, detail
