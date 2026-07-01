"""get_processes — snapshot dei processi correnti, con filtri opzionali.

Una sola funzione: produce dati FRESCHI dalla sorgente di verità del sistema
(`/proc` + `ps` per i campi non triviali). Se `filters` e' vuoto/omesso,
ritorna tutti i processi. Altrimenti applica i predicati in AND.

Schema entries:
  {pid, ppid, name, cmd, user, cpu_pct, mem_pct, started_at}

Predicato:
  {attribute, op, value}
    op ∈ {'=', '!=', '>', '>=', '<', '<=',
           'contains', 'startswith', 'endswith', 'regex', 'in'}
    op default = '='

Implementazione: invoca `ps -eo pid,ppid,user,pcpu,pmem,lstart,comm,args` come
sottoprocesso (capture_output, shell=False), parsa stdout, applica filtri.

Modalita' aggregata `include_health=true` (FIX 2, 6/5/2026): aggiunge al
risultato un dict top-level `health` con quattro sezioni — load (1/5/15min
+ uptime), memory (RAM + swap da /proc/meminfo), disk (per-mount via
os.statvfs su /proc/mounts), services (systemctl --user is-active su un
set di unit notevoli). Niente psutil: stdlib + subprocess `systemctl`.
Determinismo §7.9. Cross-platform: su SO non-Linux le sezioni mancanti
ritornano `{available: false, reason: ...}`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


_VALID_ATTRS = {
    "pid", "ppid", "name", "cmd", "user", "cpu_pct", "mem_pct", "started_at",
}
_NUMERIC_ATTRS = {"pid", "ppid", "cpu_pct", "mem_pct"}
_VALID_OPS = {"=", "!=", ">", ">=", "<", "<=",
              "contains", "startswith", "endswith", "regex", "in"}

# Mapping di attribute "errati" → campo health corrispondente. Quando il
# planner prova a filtrare su un concetto che non e' attributo di processo
# ma ESISTE come campo health (ip/network/temperature/...), il messaggio
# di errore indica esplicitamente la pipeline corretta. Riduce il loop
# osservato 21-22/5/2026 sulla query "quali sono gli ip del server".
_HEALTH_FIELD_HINT = {
    "ip": "health.network", "ipv4": "health.network", "ipv6": "health.network",
    "network": "health.network", "interface": "health.network",
    "interfaces": "health.network", "iface": "health.network",
    "address": "health.network", "addresses": "health.network",
    "mac": "health.network",
    "temperature": "health.thermal", "temp": "health.thermal",
    "thermal": "health.thermal", "cpu_c": "health.thermal", "gpu_c": "health.thermal",
    "load": "health.load", "uptime": "health.load",
    "memory": "health.memory", "ram": "health.memory", "swap": "health.memory",
    "disk": "health.disk", "fs": "health.disk", "filesystem": "health.disk",
    "service": "health.services", "services": "health.services", "systemd": "health.services",
}


def _parse_predicate(p) -> tuple[str, str, Any]:
    """Validate and normalise a single predicate.

    Robustezza al confine NL→determinismo (the design guide §2.4): tollerante a
    forme comuni che il planner LLM produce. Mai crash su input strutturato
    male: ritorna ValueError con messaggio actionable.

    Alias accettati:
    - `field` ≡ `attribute` (convenzione REST/SQL, frequente in LLM out)
    - `operator` ≡ `op` (idem)

    Hint health: se l'attribute richiesto non e' valido ma corrisponde a
    un campo di `health.*`, il messaggio di errore lo indica → planner
    ha next-step actionable invece di loopare su variazioni di filtro.
    """
    if not isinstance(p, dict):
        raise ValueError(
            f"predicate must be an object with {{attribute, op?, value}}; "
            f"got {type(p).__name__}: {p!r}"
        )
    attr = p.get("attribute") or p.get("field")
    if attr not in _VALID_ATTRS:
        msg = f"invalid attribute {attr!r}; valid: {sorted(_VALID_ATTRS)}"
        hint_target = _HEALTH_FIELD_HINT.get(
            str(attr).lower() if isinstance(attr, str) else ""
        )
        if hint_target:
            msg += (f". HINT: '{attr}' non e' attributo di processo, "
                    f"vive in {hint_target}: chiama get_processes("
                    f"include_health=true) e leggi quel campo.")
        raise ValueError(msg)
    op = p.get("op") or p.get("operator") or "="
    if op not in _VALID_OPS:
        raise ValueError(f"invalid op {op!r}; valid: {sorted(_VALID_OPS)}")
    value = p.get("value")
    if value is None:
        raise ValueError(f"predicate {p!r} missing 'value'")
    return attr, op, value


def _eval(entry: dict, attr: str, op: str, value: Any) -> bool:
    actual = entry.get(attr)
    if actual is None:
        return False
    if op in (">", ">=", "<", "<=") or attr in _NUMERIC_ATTRS:
        try:
            actual_num = float(actual)
            value_num = float(value)
        except (TypeError, ValueError):
            return False
        if op == "=":
            return actual_num == value_num
        if op == "!=":
            return actual_num != value_num
        if op == ">":
            return actual_num > value_num
        if op == ">=":
            return actual_num >= value_num
        if op == "<":
            return actual_num < value_num
        if op == "<=":
            return actual_num <= value_num
    a_str = str(actual)
    if op == "=":
        return a_str == str(value)
    if op == "!=":
        return a_str != str(value)
    if op == "contains":
        return str(value).lower() in a_str.lower()
    if op == "startswith":
        return a_str.lower().startswith(str(value).lower())
    if op == "endswith":
        return a_str.lower().endswith(str(value).lower())
    if op == "regex":
        try:
            return bool(re.search(str(value), a_str))
        except re.error:
            return False
    if op == "in":
        if not isinstance(value, list):
            return a_str in str(value)
        return a_str in [str(v) for v in value]
    return False


def _ps_snapshot() -> list[dict]:
    """Run `ps` and parse one entry per line.

    Use `etime` (single token like '01:23:45' or '5-12:34:56') instead of
    `lstart` (which has 5 tokens) so each column is a single field.
    Ask for the args column LAST so we can capture it via maxsplit on
    the remaining tail.
    """
    cmd = [
        "ps", "-eo",
        "pid,ppid,user:32,pcpu,pmem,etime,comm,args",
        "--no-headers",
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, shell=False,
        )
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    if out.returncode != 0:
        return []

    rows: list[dict] = []
    for line in out.stdout.splitlines():
        # 7 single-token fields then the args (variable spaces).
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        pid_s, ppid_s, user, pcpu_s, pmem_s, etime, comm, args = parts
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
            cpu_pct = float(pcpu_s)
            mem_pct = float(pmem_s)
        except ValueError:
            continue
        rows.append({
            "pid": pid,
            "ppid": ppid,
            "name": comm,
            "cmd": args,
            "user": user,
            "cpu_pct": cpu_pct,
            "mem_pct": mem_pct,
            "started_at": etime,  # elapsed time from start; reusing field name
        })
    return rows


def _read_load() -> dict:
    """1/5/15min load avg + uptime in secondi."""
    out: dict[str, Any] = {"available": False}
    try:
        l1, l5, l15 = os.getloadavg()
        out.update({"available": True, "1m": round(l1, 2),
                     "5m": round(l5, 2), "15m": round(l15, 2)})
    except (OSError, AttributeError):
        out["reason"] = "getloadavg non disponibile"
    try:
        with open("/proc/uptime", "r") as f:
            uptime_s = float(f.read().split()[0])
        out["uptime_s"] = int(uptime_s)
    except (OSError, ValueError, IndexError):
        pass
    return out


def _read_memory() -> dict:
    """RAM + swap da /proc/meminfo (kB → MB)."""
    out: dict[str, Any] = {"available": False}
    try:
        with open("/proc/meminfo", "r") as f:
            data = f.read()
    except OSError:
        out["reason"] = "/proc/meminfo non leggibile (non-Linux?)"
        return out
    fields: dict[str, int] = {}
    for line in data.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s+kB", line)
        if m:
            fields[m.group(1)] = int(m.group(2))
    if not fields.get("MemTotal"):
        return out
    total_mb = fields["MemTotal"] // 1024
    avail_mb = fields.get("MemAvailable", fields.get("MemFree", 0)) // 1024
    used_mb = total_mb - avail_mb
    swap_total_mb = fields.get("SwapTotal", 0) // 1024
    swap_used_mb = swap_total_mb - (fields.get("SwapFree", 0) // 1024)
    out.update({
        "available": True,
        "total_mb": total_mb,
        "used_mb": used_mb,
        "free_mb": avail_mb,
        "pct": round(100.0 * used_mb / total_mb, 1) if total_mb else 0.0,
        "swap_total_mb": swap_total_mb,
        "swap_used_mb": swap_used_mb,
        "swap_pct": (round(100.0 * swap_used_mb / swap_total_mb, 1)
                     if swap_total_mb else 0.0),
    })
    return out


# Filesystem da NON segnalare nella sezione disk (overlay, virtuali,
# bind-mount kernel, ecc.). Lasciare solo i mount user-rilevanti.
_DISK_SKIP_FSTYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2",
    "pstore", "bpf", "tracefs", "debugfs", "configfs", "fusectl",
    "mqueue", "hugetlbfs", "ramfs", "securityfs", "autofs",
    "binfmt_misc", "rpc_pipefs", "nfsd", "fuse.gvfsd-fuse",
    "fuse.portal", "overlay", "squashfs",
}


def _read_disk() -> list[dict]:
    """Per ogni mount user-rilevante in /proc/mounts: total/used/free/pct."""
    out: list[dict] = []
    try:
        with open("/proc/mounts", "r") as f:
            mounts = f.read().splitlines()
    except OSError:
        return out
    seen = set()
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1], parts[2]
        if fstype in _DISK_SKIP_FSTYPES:
            continue
        if mount in seen:
            continue
        seen.add(mount)
        try:
            st = os.statvfs(mount)
        except (OSError, FileNotFoundError):
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - (st.f_bfree * st.f_frsize)
        if total == 0:
            continue
        out.append({
            "mount": mount,
            "device": device,
            "fstype": fstype,
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "pct": round(100.0 * used / total, 1),
        })
    out.sort(key=lambda d: d["mount"])
    return out


# Set notevole di unit Metnos da controllare: 3 service long-running +
# 3 timer ricorrenti. I service one-shot attivati dai timer non sono
# inclusi (lo stato del timer 'active'/'waiting' copre gia' il flusso).
# Niente hardcoding di servizi non Metnos (llama-server e simili NON
# sono nostri).
_METNOS_SERVICES = (
    "metnos-http",
    "metnos-telegram-daemon",
    "metnos-prompts-translator.timer",
    "metnos-backup.timer",
)
# NB: `metnos-scheduler` e `metnos-i18n-translator.timer` NON sono qui: lo
# scheduler v2 è co-hosted nel processo http (ADR 0112), non un servizio
# systemd, e l'i18n è un suo job (`i18n_translate_pending`, every_6h), non un
# servizio a sé. Verificarli via `systemctl is-active` dava un falso ✗ su
# unità inesistenti. Lo scheduler è riportato sotto via heartbeat reale.


def _scheduler_cohost_status() -> str:
    """Stato REALE dello scheduler v2 co-host (ADR 0112): heartbeat del db.
    L'ultimo run < 180s ⇒ vivo (esiste un job `dialog_pending_sweep` every_1m).
    Niente assunzioni: se il co-host muore (raro init-fail silenzioso), i run
    si fermano e questo diventa 'inactive' davvero (§2.8)."""
    try:
        import sqlite3
        import datetime as _dt
        try:
            import config as _C
            db = Path(_C.PATH_USER_STATE) / "scheduler_v2.sqlite"
        except Exception:
            db = Path.home() / ".local/state/metnos/scheduler_v2.sqlite"
        if not Path(db).exists():
            return "inactive"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT max(finished_at) FROM runs").fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return "inactive"
        t = _dt.datetime.fromisoformat(str(row[0]))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        delta = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()
        return "active" if 0 <= delta < 180 else "inactive"
    except Exception:
        return "unknown"


def _user_runtime_env() -> dict:
    """Env minimale per parlare con `systemd --user` da un processo che non
    eredita XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS. Indispensabile quando
    l'executor gira da un service system-level (es. `metnos-http.service`):
    senza queste due variabili `systemctl --user` non sa quale instance
    contattare e ritorna sempre "inactive"/"unknown" silenziosamente.
    """
    env = os.environ.copy()
    if env.get("XDG_RUNTIME_DIR") and env.get("DBUS_SESSION_BUS_ADDRESS"):
        return env
    rt = f"/run/user/{os.getuid()}"
    if os.path.isdir(rt):
        env.setdefault("XDG_RUNTIME_DIR", rt)
        bus = f"{rt}/bus"
        if os.path.exists(bus):
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
    return env


def _is_active_dual(name: str) -> tuple[str, str]:
    """Sonda lo status di una unit su entrambi i bus systemd.

    Le unit Metnos vivono in scope misti: `metnos-http` e' system-level,
    mentre `metnos-telegram-daemon` e `metnos-scheduler` sono user-level.
    Ritorna `(status, scope)` con scope in {"system", "user"}; preferisce
    il bus che ritorna "active".
    """
    last_status, last_scope = "unknown", "system"
    user_env = _user_runtime_env()
    for scope, args, env in (
        ("system", ["systemctl", "is-active", name], None),
        ("user", ["systemctl", "--user", "is-active", name], user_env),
    ):
        try:
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=3, env=env,
            )
            s = r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if s == "active":
            return s, scope
        if s:
            last_status, last_scope = s, scope
    return last_status, last_scope


def _read_services(unit_names: tuple[str, ...] = _METNOS_SERVICES) -> list[dict]:
    """Per ogni unit: status (system o --user, primo bus che risponde
    'active') + active_since."""
    out: list[dict] = []
    if shutil.which("systemctl") is None:
        return out
    for name in unit_names:
        status, scope = _is_active_dual(name)
        entry = {"name": name, "status": status}
        # Active since (best-effort) — interroga lo stesso scope di status.
        scope_arg = ["--user"] if scope == "user" else []
        scope_env = _user_runtime_env() if scope == "user" else None
        try:
            r2 = subprocess.run(
                ["systemctl", *scope_arg, "show", name,
                 "-p", "ActiveEnterTimestampMonotonic", "--value"],
                capture_output=True, text=True, timeout=3, env=scope_env,
            )
            mono = r2.stdout.strip()
            if mono and mono.isdigit():
                if int(mono) > 0:
                    entry["active_since_s_mono"] = int(mono) // 1_000_000
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        out.append(entry)
    # Scheduler v2 co-host (ADR 0112): stato reale via heartbeat, non systemd.
    out.append({"name": "scheduler", "status": _scheduler_cohost_status()})
    return out


def _read_thermal() -> dict:
    """Delega a `host_health.collect_thermal` (SoT 24/5/2026). Mantiene
    il nome locale per back-compat (questo modulo `get_processes` viene
    invocato come subprocess separato e potrebbe non aver `runtime/`
    sul path); in caso di import fallito, mantiene la logica inline."""
    try:
        sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
            str(p / "runtime") for p in Path(__file__).resolve().parents
            if (p / "runtime" / "config.py").is_file()))
        from host_health import collect_thermal
        return collect_thermal()
    except (ImportError, AttributeError, StopIteration):
        pass
    # Fallback inline (subprocess sandbox senza runtime/ accessibile)
    targets = {"k10temp": "cpu_c", "amdgpu": "gpu_c", "nvme": "nvme_c"}
    out: dict[str, Any] = {"available": False}
    base = "/sys/class/hwmon"
    if not os.path.isdir(base):
        return out
    try:
        for hwmon_dir in sorted(os.listdir(base)):
            hpath = os.path.join(base, hwmon_dir)
            try:
                with open(os.path.join(hpath, "name")) as f:
                    name = f.read().strip()
            except OSError:
                continue
            key = targets.get(name)
            if not key or key in out:
                continue
            inputs = [p for p in os.listdir(hpath) if p.endswith("_input")]
            if not inputs:
                continue
            chosen = "temp1_input" if "temp1_input" in inputs else sorted(inputs)[0]
            try:
                with open(os.path.join(hpath, chosen)) as f:
                    raw = f.read().strip()
                if raw and raw.lstrip("-").isdigit():
                    out[key] = int(raw) // 1000
                    out["available"] = True
            except OSError:
                continue
    except OSError:
        return out
    return out


def _read_power() -> dict:
    """Consumo istantaneo CPU + GPU in Watt.

    CPU: Intel/AMD RAPL via `/sys/class/powercap/intel-rapl:*/energy_uj`
    (lettura dual a 100ms per derivare i Watt). AMD ha pacchetti
    `intel-rapl:N` malgrado il nome (kernel mainline). Se assente:
    skip CPU (`available_cpu: false`).

    GPU: detection vendor + tool:
    - AMD: `rocm-smi --showpower` (parse "Power (W): X")
    - NVIDIA: `nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits`
    Se nessun tool trovato → skip GPU (`available_gpu: false`).

    Determinismo §7.9. Subprocess timeout 2s per non bloccare il turno.
    """
    import glob
    import subprocess
    out: dict[str, Any] = {
        "available_cpu": False, "available_gpu": False,
        "cpu_watts": None, "gpu_watts": None, "vendor": None,
    }

    # --- CPU via RAPL ----------------------------------------------------
    rapl_files = sorted(glob.glob(
        "/sys/class/powercap/intel-rapl:*/energy_uj"
    ))
    # Filtra solo i package roots (no subzone -dram/-uncore).
    rapl_files = [p for p in rapl_files
                  if p.split("/")[-2].count(":") == 1]
    if rapl_files:
        try:
            def _read_energy(paths):
                total = 0
                for p in paths:
                    try:
                        with open(p) as f:
                            total += int(f.read().strip())
                    except (OSError, ValueError):
                        pass
                return total
            e0 = _read_energy(rapl_files)
            time.sleep(0.1)
            e1 = _read_energy(rapl_files)
            if e1 >= e0:
                # microjoules → watts su 100ms = (delta_uj / 1e6) / 0.1
                watts = (e1 - e0) / 1_000_000.0 / 0.1
                out["cpu_watts"] = round(watts, 2)
                out["available_cpu"] = True
        except Exception:
            pass

    # --- GPU vendor detection --------------------------------------------
    # Prova rocm-smi prima (più rapido fail su sistemi NVIDIA).
    for cmd, vendor, parser in (
        (["rocm-smi", "--showpower"], "amd", "rocm"),
        (["nvidia-smi", "--query-gpu=power.draw",
          "--format=csv,noheader,nounits"], "nvidia", "nv"),
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=2.0)
            if r.returncode != 0:
                continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        text = r.stdout or ""
        watts: float | None = None
        if parser == "rocm":
            # "Current Socket Graphics Package Power (W): 27.038"
            import re as _re
            m = _re.search(r"Power\s*\(W\)\s*:\s*([\d.]+)", text)
            if m:
                try:
                    watts = float(m.group(1))
                except ValueError:
                    pass
        elif parser == "nv":
            # Una o più righe con valore numerico (per GPU multipla, somma).
            tot = 0.0
            any_val = False
            for line in text.strip().splitlines():
                v = line.strip()
                try:
                    tot += float(v)
                    any_val = True
                except ValueError:
                    continue
            if any_val:
                watts = round(tot, 2)
        if watts is not None:
            out["gpu_watts"] = watts
            out["vendor"] = vendor
            out["available_gpu"] = True
            break
    return out


def _read_network() -> list[dict]:
    """Interfacce di rete con IP IPv4/IPv6 (esclude loopback).
    Determinismo §7.9: psutil deterministico, niente comandi esterni."""
    out: list[dict] = []
    try:
        import socket
        import psutil  # type: ignore
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return out
    for iface, addr_list in addrs.items():
        if iface == "lo":
            continue
        ipv4 = [a.address for a in addr_list if a.family == socket.AF_INET]
        ipv6 = [a.address for a in addr_list
                if a.family == socket.AF_INET6
                and not a.address.startswith("fe80")]  # esclude link-local
        if not ipv4 and not ipv6:
            continue
        up = bool(stats.get(iface) and stats[iface].isup)
        out.append({
            "iface": iface,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "up": up,
        })
    return out


def _collect_health(services_extra: tuple[str, ...] | None = None) -> dict:
    """Aggrega le 6 sezioni. Nessuna chiamata LLM (§7.9)."""
    units = _METNOS_SERVICES
    if services_extra:
        units = _METNOS_SERVICES + tuple(services_extra)
    return {
        "load": _read_load(),
        "memory": _read_memory(),
        "thermal": _read_thermal(),
        "power": _read_power(),
        "disk": _read_disk(),
        "network": _read_network(),
        "services": _read_services(units),
        "collected_at": int(time.time()),
    }


def invoke(args: dict, ctx: dict | None = None) -> dict:
    filters_in = args.get("filters") or []
    # §2.4 robustezza NL→determinismo: l'LLM passa spesso `filters` come STRINGA
    # (es. 'memory' per "processo che usa più memoria") invece di list[dict].
    # Una forma non-lista non è un filtro valido → IGNORALA (coerce a []) invece
    # di hard-fail: get_processes ritorna comunque i top processi (bug q39 5/6).
    if not isinstance(filters_in, list):
        filters_in = []
    top = args.get("top")
    if top is not None:
        try:
            top = int(top)
            if top < 1:
                raise ValueError
        except (TypeError, ValueError):
            return {
                "ok": False, "ok_count": 0, "fail_count": 1,
                "entries": [], "failed": [{"error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="top")}],
            }
    include_health = bool(args.get("include_health"))
    services_extra_in = args.get("services_extra") or []
    if services_extra_in and not isinstance(services_extra_in, list):
        return {
            "ok": False, "ok_count": 0, "fail_count": 1,
            "entries": [],
            "failed": [{"error": _msg("ERR_ARG_NOT_LIST_OF", arg="services_extra", of="unit")}],
        }

    # Parse predicati: se filter parse fail MA include_health=true, ritorna
    # comunque il blocco health (l'utente lo ha chiesto esplicitamente). Il
    # campo `failed[]` contiene l'errore del filtro per audit. §2.8: no silent
    # failure ma anche §2.4 robustezza confine: rispetta gli arg validi (health)
    # anche quando altri sono malformati.
    predicates: list = []
    filter_failed: list[dict] = []
    try:
        predicates = [_parse_predicate(p) for p in filters_in]
    except ValueError as e:
        if not include_health:
            return {
                "ok": False, "ok_count": 0, "fail_count": 1,
                "entries": [], "failed": [{"error": str(e)}],
            }
        filter_failed = [{"error": str(e)}]
        predicates = []  # ignora i filtri e procedi con health

    snapshot = _ps_snapshot()
    if predicates:
        snapshot = [
            e for e in snapshot
            if all(_eval(e, a, o, v) for (a, o, v) in predicates)
        ]
    snapshot.sort(key=lambda e: e["cpu_pct"], reverse=True)
    truncated = False
    if top is not None and len(snapshot) > top:
        truncated = True
        used = top
        available_total = len(snapshot)
        snapshot = snapshot[:top]
    else:
        used = len(snapshot)
        available_total = len(snapshot)

    # ok=True anche con filter_failed quando health e' stato comunque
    # ritornato: l'LLM legge `ok: False` come "tool fallito, riprova".
    # Se include_health=true ha avuto successo, l'esito utile c'e';
    # filter_failed e' un warning di partial output (vedi `warnings`).
    result: dict[str, Any] = {
        "ok": True,
        "ok_count": len(snapshot),
        "fail_count": 0,
        "entries": snapshot,
        "truncated": truncated,
        "truncated_what": _msg("MSG_OBJECT_PROCESSES") if truncated else None,
        "truncated_intentional": truncated,  # top=K is user-requested cap
        "used": used,
        "available_total": available_total,
        "cap_field": "top" if truncated else None,
        "cap_value": top if truncated else None,
    }
    if filter_failed:
        result["warnings"] = [
            f"filter ignored: {fe['error']}" for fe in filter_failed
        ]
    if include_health:
        result["health"] = _collect_health(
            tuple(s for s in services_extra_in if isinstance(s, str)),
        )
    return result


def main():
    run_stdio(invoke, default=str, allow_empty=True)


if __name__ == "__main__":
    main()
