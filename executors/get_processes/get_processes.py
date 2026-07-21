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
    # Sezioni descrittive (9/7): cpu/gpu/sistema/periferiche.
    "cpu": "health.cpu", "processor": "health.cpu", "core": "health.cpu",
    "cores": "health.cpu", "freq": "health.cpu", "frequency": "health.cpu",
    "gpu": "health.gpu", "vram": "health.gpu", "video": "health.gpu",
    "hostname": "health.system", "os": "health.system", "kernel": "health.system",
    "arch": "health.system", "distro": "health.system", "sistema": "health.system",
    "usb": "health.peripherals", "peripheral": "health.peripherals",
    "peripherals": "health.peripherals", "periferiche": "health.peripherals",
    "block": "health.peripherals", "ssd": "health.peripherals",
    "nvme": "health.peripherals",
}


def _parse_predicate(p) -> tuple[str, str, Any]:
    """Validate and normalise a single predicate.

    Robustezza al confine NL→determinismo (CLAUDE.md §2.4): tollerante a
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


_SNAPSHOT_FAIL_REASON = ""


def _set_fail(reason: str) -> None:
    global _SNAPSHOT_FAIL_REASON
    _SNAPSHOT_FAIL_REASON = reason


def _tasklist_snapshot() -> list[dict]:
    """Windows (C7 device, 5/7): `tasklist /fo csv` — stdlib only, stessa
    shape delle entry POSIX (i campi non disponibili restano onesti a 0/"").
    Prima del fix il device Windows rispondeva ok:true con 0 processi
    (il ramo POSIX moriva FileNotFoundError silenzioso)."""
    import csv as _csv
    import io as _io
    global _SNAPSHOT_FAIL_REASON
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, shell=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as ex:
        _SNAPSHOT_FAIL_REASON = f"tasklist: {type(ex).__name__}"
        return []
    if out.returncode != 0:
        _SNAPSHOT_FAIL_REASON = (
            f"tasklist rc={out.returncode}: {(out.stderr or '')[:200]}")
        return []
    rows: list[dict] = []
    for rec in _csv.reader(_io.StringIO(out.stdout)):
        # "Image Name","PID","Session Name","Session#","Mem Usage"
        if len(rec) < 5:
            continue
        try:
            pid = int(rec[1])
        except ValueError:
            continue
        mem_kb = 0
        try:
            mem_kb = int(rec[4].replace(".", "").replace(",", "")
                         .replace("K", "").strip())
        except ValueError:
            pass
        rows.append({
            "pid": pid, "ppid": 0, "user": "",
            # tasklist NON fornisce CPU%/MEM% → None (NON misurato), mai 0.0
            # fasullo (§2.8: un valore inventato inganna — sembrava "0% CPU
            # ovunque"). La memoria ASSOLUTA (mem_kb) e' reale: usata per il
            # ranking di fallback su Windows (vedi _rank_key).
            "cpu_pct": None, "mem_pct": None,
            "mem_kb": mem_kb, "etime": "",
            "comm": rec[0], "args": rec[0],
        })
    return rows


def _winapi_snapshot() -> list[dict]:
    """Windows: CPU% + memoria + nome REALI via API native Win32 (ctypes, stdlib
    — NIENTE subprocess/powershell, NIENTE psutil). Approccio nativo (Roberto,
    8/7): EnumProcesses (PSAPI) → PID; per PID OpenProcess +
    QueryFullProcessImageNameW (nome) + GetProcessMemoryInfo (WorkingSet) +
    GetProcessTimes (kernel+user). CPU% = delta dei tempi CPU del processo su un
    intervallo breve, rapportato al delta del tempo di sistema (GetSystemTimes,
    che include l'idle → capacita' totale) → 0-100 system-wide (come Task
    Manager). Fallback ONESTO a tasklist (cpu=None, mai 0.0 fasullo) su QUALSIASI
    errore FFI. Gira a job-object (code:exec): le API di processo sono accessibili."""
    import time as _time
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt

        k32 = _ct.WinDLL("kernel32", use_last_error=True)
        psapi = _ct.WinDLL("psapi", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class _PMC(_ct.Structure):
            _fields_ = [
                ("cb", _wt.DWORD), ("PageFaultCount", _wt.DWORD),
                ("PeakWorkingSetSize", _ct.c_size_t), ("WorkingSetSize", _ct.c_size_t),
                ("QuotaPeakPagedPoolUsage", _ct.c_size_t), ("QuotaPagedPoolUsage", _ct.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", _ct.c_size_t), ("QuotaNonPagedPoolUsage", _ct.c_size_t),
                ("PagefileUsage", _ct.c_size_t), ("PeakPagefileUsage", _ct.c_size_t),
            ]

        # Signature CRITICHE: senza restype=HANDLE, un handle a 64-bit viene
        # troncato a int32 (corruzione). argtypes espliciti per correttezza.
        LPFT = _ct.POINTER(_wt.FILETIME)
        k32.OpenProcess.restype = _wt.HANDLE
        k32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
        k32.CloseHandle.argtypes = [_wt.HANDLE]
        k32.GetProcessTimes.argtypes = [_wt.HANDLE, LPFT, LPFT, LPFT, LPFT]
        k32.GetSystemTimes.argtypes = [LPFT, LPFT, LPFT]
        k32.QueryFullProcessImageNameW.argtypes = [
            _wt.HANDLE, _wt.DWORD, _wt.LPWSTR, _ct.POINTER(_wt.DWORD)]
        psapi.EnumProcesses.argtypes = [
            _ct.POINTER(_wt.DWORD), _wt.DWORD, _ct.POINTER(_wt.DWORD)]
        psapi.GetProcessMemoryInfo.argtypes = [_wt.HANDLE, _ct.POINTER(_PMC), _wt.DWORD]

        def _ft(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        def _sys_total():
            idle, kern, user = _wt.FILETIME(), _wt.FILETIME(), _wt.FILETIME()
            if not k32.GetSystemTimes(_ct.byref(idle), _ct.byref(kern), _ct.byref(user)):
                return None
            return _ft(kern) + _ft(user)  # kernel INCLUDE idle = capacita' totale

        def _proc_cpu(h):
            c, e, kt, ut = (_wt.FILETIME(), _wt.FILETIME(), _wt.FILETIME(), _wt.FILETIME())
            if not k32.GetProcessTimes(h, _ct.byref(c), _ct.byref(e),
                                       _ct.byref(kt), _ct.byref(ut)):
                return None
            return _ft(kt) + _ft(ut)

        # 1. EnumProcesses → lista PID (array cresciuto se pieno).
        cap = 4096
        while True:
            arr = (_wt.DWORD * cap)()
            needed = _wt.DWORD()
            if not psapi.EnumProcesses(arr, _ct.sizeof(arr), _ct.byref(needed)):
                return _tasklist_snapshot()
            if needed.value < _ct.sizeof(arr):
                break
            cap *= 2  # array pieno: potrebbe aver troncato, riprova piu' grande
        pids = [arr[i] for i in range(needed.value // _ct.sizeof(_wt.DWORD)) if arr[i]]

        # 2. apri handle + campione0 (cpu), nome, memoria.
        handles: dict = {}
        cpu0: dict = {}
        name: dict = {}
        mem_kb: dict = {}
        for pid in pids:
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                continue  # processo protetto/sistema: OpenProcess negato, skip
            t0 = _proc_cpu(h)
            if t0 is None:
                k32.CloseHandle(h)
                continue
            handles[pid] = h
            cpu0[pid] = t0
            buf = _ct.create_unicode_buffer(512)
            sz = _wt.DWORD(512)
            if k32.QueryFullProcessImageNameW(h, 0, buf, _ct.byref(sz)) and buf.value:
                name[pid] = buf.value.rsplit("\\", 1)[-1]
            else:
                name[pid] = str(pid)
            pmc = _PMC()
            pmc.cb = _ct.sizeof(_PMC)
            if psapi.GetProcessMemoryInfo(h, _ct.byref(pmc), _ct.sizeof(pmc)):
                mem_kb[pid] = int(pmc.WorkingSetSize) // 1024
            else:
                mem_kb[pid] = 0

        # 3. intervallo breve → campione1 + delta di sistema → CPU%.
        sys0 = _sys_total()
        _time.sleep(0.4)
        sys1 = _sys_total()
        sys_delta = (sys1 - sys0) if (sys0 and sys1) else 0

        rows: list[dict] = []
        for pid, h in handles.items():
            t1 = _proc_cpu(h)
            k32.CloseHandle(h)
            if t1 is None or sys_delta <= 0:
                cpu_pct = None
            else:
                cpu_pct = round(max(0.0, (t1 - cpu0[pid]) / sys_delta * 100.0), 1)
            rows.append({
                "pid": pid, "ppid": 0, "user": "",
                "cpu_pct": cpu_pct, "mem_pct": None,
                "mem_kb": mem_kb.get(pid, 0), "etime": "",
                "comm": name.get(pid, str(pid)), "args": name.get(pid, str(pid)),
            })
        return rows or _tasklist_snapshot()
    except Exception as ex:  # noqa: BLE001 — qualsiasi errore FFI → fallback onesto
        _set_fail(f"winapi: {type(ex).__name__}: {ex}")
        return _tasklist_snapshot()


def _ps_snapshot() -> list[dict]:
    """Run `ps` and parse one entry per line.

    Use `etime` (single token like '01:23:45' or '5-12:34:56') instead of
    `lstart` (which has 5 tokens) so each column is a single field.
    Ask for the args column LAST so we can capture it via maxsplit on
    the remaining tail.

    Windows (C7): delega a `_tasklist_snapshot` (ps assente).
    """
    if os.name == "nt":
        return _winapi_snapshot()  # CPU%/mem/nome reali via API native; fallback tasklist
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
        _set_fail("ps: timeout")
        return []
    except FileNotFoundError:
        _set_fail("ps: non trovato")
        return []
    if out.returncode != 0:
        _set_fail(f"ps rc={out.returncode}: {(out.stderr or '')[:200]}")
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
    """1/5/15min load avg + uptime in secondi. Su Windows il load avg non
    esiste → uptime (GetTickCount64) + uso CPU complessivo (GetSystemTimes):
    il formatter rende la variante MSG_HEALTH_LOAD_WIN."""
    out: dict[str, Any] = {"available": False}
    if os.name == "nt":
        try:
            import ctypes
            out["uptime_s"] = int(
                ctypes.windll.kernel32.GetTickCount64() / 1000)
            cp = _win_cpu_percent()
            if cp is not None:
                out["cpu_pct"] = cp
            out["available"] = True
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return out
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


# ── Rami WINDOWS nativi (10/7, Roberto: «il PC dice meno del server») ────────
# Solo stdlib ctypes/winreg — lo shim del device non ha psutil (§10.4). Stesso
# pattern del CPU% nativo Win32 dei processi (b3336a0). Ogni helper è
# best-effort: su errore la sezione resta onestamente vuota (§2.8).

def _win_memory() -> dict:
    """RAM+swap via GlobalMemoryStatusEx (shape identica al ramo /proc)."""
    import ctypes
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX(); st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return {"available": False, "reason": "GlobalMemoryStatusEx fallita"}
    total_mb = st.ullTotalPhys // (1024 * 1024)
    avail_mb = st.ullAvailPhys // (1024 * 1024)
    used_mb = total_mb - avail_mb
    # swap ≈ pagefile oltre la RAM fisica (approssimazione standard Win32)
    swap_total_mb = max(0, (st.ullTotalPageFile - st.ullTotalPhys) // (1024 * 1024))
    swap_free_mb = max(0, (st.ullAvailPageFile - st.ullAvailPhys) // (1024 * 1024))
    swap_used_mb = max(0, swap_total_mb - swap_free_mb)
    return {"available": True, "total_mb": total_mb, "used_mb": used_mb,
            "free_mb": avail_mb,
            "pct": round(100.0 * used_mb / total_mb, 1) if total_mb else 0.0,
            "swap_total_mb": swap_total_mb, "swap_used_mb": swap_used_mb,
            "swap_pct": (round(100.0 * swap_used_mb / swap_total_mb, 1)
                         if swap_total_mb else 0.0)}


def _win_disks() -> list[dict]:
    """Drive FISSI via GetLogicalDriveStringsW + shutil.disk_usage (shape
    identica al ramo /proc/mounts)."""
    import ctypes
    import shutil
    out: list[dict] = []
    buf = ctypes.create_unicode_buffer(256)
    n = ctypes.windll.kernel32.GetLogicalDriveStringsW(255, buf)
    drives = [d for d in buf[:n].split("\x00") if d]
    for drive in drives:
        if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:  # DRIVE_FIXED
            continue
        try:
            u = shutil.disk_usage(drive)
        except OSError:
            continue
        if not u.total:
            continue
        fsname = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            drive, None, 0, None, None, None, fsname, 64)
        out.append({"mount": drive, "device": drive,
                    "fstype": (fsname.value.lower() if ok else ""),
                    "total_gb": round(u.total / (1024 ** 3), 1),
                    "used_gb": round(u.used / (1024 ** 3), 1),
                    "free_gb": round(u.free / (1024 ** 3), 1),
                    "pct": round(100.0 * u.used / u.total, 1)})
    return out


def _win_cpu_percent(sample_s: float = 0.1) -> float | None:
    """Uso CPU complessivo via GetSystemTimes (2 campioni). NB: su Win il
    kernel-time INCLUDE l'idle → busy = (k+u−i)Δ / (k+u)Δ."""
    import ctypes
    k32 = ctypes.windll.kernel32

    def _times():
        idle = ctypes.c_ulonglong(); kern = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                  ctypes.byref(user)):
            return None
        return idle.value, kern.value, user.value
    a = _times()
    if a is None:
        return None
    time.sleep(sample_s)
    b = _times()
    if b is None:
        return None
    di, dk, du = (b[0] - a[0]), (b[1] - a[1]), (b[2] - a[2])
    tot = dk + du
    if tot <= 0:
        return None
    return round(100.0 * (tot - di) / tot, 1)


def _read_memory() -> dict:
    """RAM + swap da /proc/meminfo (kB → MB); su Windows via Win32 nativo."""
    out: dict[str, Any] = {"available": False}
    if os.name == "nt":
        try:
            return _win_memory()
        except Exception:  # noqa: BLE001 — best-effort
            return out
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
    """Per ogni mount user-rilevante in /proc/mounts: total/used/free/pct.
    Su Windows: drive fissi via Win32 nativo."""
    out: list[dict] = []
    if os.name == "nt":
        try:
            return _win_disks()
        except Exception:  # noqa: BLE001 — best-effort
            return out
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


# Il catalogo base dei servizi vive in runtime/services_registry.py. Questo
# executor mantiene solo il supporto a ``services_extra`` espliciti, che non
# diventano per questo servizi amministrabili dal core.


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


def _read_services(unit_names: tuple[str, ...] | None = None) -> list[dict]:
    """Legge il catalogo core oppure un insieme esplicito non amministrato."""
    out: list[dict] = []
    if shutil.which("systemctl") is None:
        return out
    if unit_names is None:
        try:
            import services_registry

            rows = services_registry.snapshots(
                probe_endpoints=False, include_missing=True,
            )
        except (ImportError, OSError):
            return out
        for row in rows:
            name = str(row.get("unit") or row.get("key") or "")
            if name.endswith(".service"):
                name = name[:-8]
            entry = {
                "name": name,
                "key": row.get("key", ""),
                "status": row.get("active_state", "unknown"),
                "scope": row.get("scope", "unknown"),
                "installed": bool(row.get("installed")),
            }
            if row.get("active_since"):
                entry["active_since"] = row["active_since"]
            out.append(entry)
        # Scheduler v2 e' co-hosted nel processo HTTP, quindi non appartiene
        # al catalogo systemd ma resta parte della salute del core.
        out.append({"name": "scheduler", "status": _scheduler_cohost_status()})
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


def _network_fallback_stdlib() -> list[dict]:
    """Rete SENZA psutil (device shim, 10/7 turn 6dce715f: health.network=[]
    sul PC → «ip del pc-roberto» rispondeva vuoto): IP primario via UDP-connect
    (nessun pacchetto inviato), hostname, MAC primario via uuid.getnode().
    Meno ricco del ramo psutil (una sola «interfaccia» logica) ma ONESTO."""
    import socket
    import uuid as _uuid
    entry: dict[str, Any] = {"iface": "primary", "ipv4": [], "ipv6": [],
                             "mac": None, "up": True}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 80))   # TEST-NET-1: nessun traffico reale
            entry["ipv4"] = [s.getsockname()[0]]
        finally:
            s.close()
    except OSError:
        pass
    if not entry["ipv4"]:
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET)
            entry["ipv4"] = sorted({i[4][0] for i in infos
                                    if not i[4][0].startswith("127.")})
        except OSError:
            pass
    try:
        node = _uuid.getnode()
        if not (node >> 40) & 0x01:        # bit multicast = MAC fittizio
            entry["mac"] = ":".join(f"{(node >> b) & 0xFF:02x}"
                                    for b in range(40, -8, -8))
    except Exception:
        pass
    return [entry] if (entry["ipv4"] or entry["mac"]) else []


def _read_network() -> list[dict]:
    """Interfacce di rete con IP IPv4/IPv6 (esclude loopback).
    Determinismo §7.9: psutil deterministico, niente comandi esterni.
    Senza psutil (device shim) → fallback stdlib (IP+MAC primari)."""
    out: list[dict] = []
    try:
        import socket
        import psutil  # type: ignore
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return _network_fallback_stdlib()
    # MAC: AF_LINK (psutil) o AF_PACKET (Linux) — descrittivo (Roberto 9/7).
    _link_fams = set()
    for _fam_name in ("AF_LINK", "AF_PACKET"):
        _f = getattr(psutil, _fam_name, None) or getattr(socket, _fam_name, None)
        if _f is not None:
            _link_fams.add(_f)
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
        mac = next((a.address for a in addr_list
                    if a.family in _link_fams and a.address), None)
        out.append({
            "iface": iface,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
            "up": up,
        })
    return out


def _read_system() -> dict:
    """Identità descrittiva della macchina: hostname, OS, kernel, arch.
    Solo stdlib (gira anche sul device senza psutil). Best-effort."""
    out: dict[str, Any] = {"available": False}
    try:
        import platform
        out.update({
            "available": True,
            "hostname": platform.node(),
            "os": platform.system(),
            "os_release": platform.release(),
            "arch": platform.machine(),
        })
        # Distro leggibile (Linux): PRETTY_NAME da os-release, senza comandi.
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        out["distro"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
    except Exception:
        pass
    return out


def _read_cpu() -> dict:
    """CPU descrittiva: modello, core fisici/logici, freq, uso% complessivo.
    stdlib + /proc/cpuinfo + psutil (opzionale). Best-effort cross-platform."""
    out: dict[str, Any] = {"available": False}
    try:
        out["logical_cores"] = os.cpu_count()
        out["available"] = True
    except Exception:
        pass
    try:  # modello da /proc/cpuinfo (Linux); su Windows platform.processor()
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    out["model"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        try:
            import platform
            out["model"] = platform.processor() or None
        except Exception:
            pass
    try:
        import psutil  # type: ignore
        out["physical_cores"] = psutil.cpu_count(logical=False)
        freq = psutil.cpu_freq()
        if freq:
            out["freq_mhz"] = int(freq.current)
            if freq.max:
                out["freq_max_mhz"] = int(freq.max)
        # uso complessivo: finestra breve, costo ~0.1s una volta per health
        out["usage_pct"] = psutil.cpu_percent(interval=0.1)
    except Exception:
        # Windows senza psutil (device shim): freq nominale dal registry,
        # uso complessivo via GetSystemTimes (stdlib, zero dipendenze).
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                    out["freq_mhz"] = int(winreg.QueryValueEx(k, "~MHz")[0])
            except Exception:  # noqa: BLE001
                pass
            try:
                cp = _win_cpu_percent()
                if cp is not None:
                    out["usage_pct"] = cp
            except Exception:  # noqa: BLE001
                pass
    return out


def _read_gpu() -> list[dict]:
    """GPU da /sys/class/drm (Linux, niente comandi esterni §7.9): vendor,
    ids PCI; per amdgpu anche VRAM totale/usata e busy%. Lista (multi-GPU);
    vuota dove /sys non c'è (device Windows/sandbox senza bind)."""
    out: list[dict] = []
    try:
        import glob as _glob
        _VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
        for card in sorted(_glob.glob("/sys/class/drm/card[0-9]")):
            dev = os.path.join(card, "device")
            if not os.path.isdir(dev):
                continue
            g: dict[str, Any] = {"card": os.path.basename(card)}

            def _r(name, base=dev):
                try:
                    with open(os.path.join(base, name)) as f:
                        return f.read().strip()
                except OSError:
                    return None
            vid = _r("vendor")
            g["vendor"] = _VENDORS.get(vid or "", vid)
            g["device_id"] = _r("device")
            vram_t = _r("mem_info_vram_total")
            vram_u = _r("mem_info_vram_used")
            busy = _r("gpu_busy_percent")
            if vram_t and vram_t.isdigit():
                g["vram_total_mb"] = int(vram_t) // (1024 * 1024)
            if vram_u and vram_u.isdigit():
                g["vram_used_mb"] = int(vram_u) // (1024 * 1024)
            if busy and busy.isdigit():
                g["busy_pct"] = int(busy)
            out.append(g)
    except Exception:
        pass
    return out


def _read_peripherals() -> dict:
    """Periferiche descrittive: dispositivi USB (product/manufacturer da
    /sys/bus/usb) e block device fisici (/sys/block, nome+size). Best-effort,
    niente comandi esterni (§7.9); dict vuoto-onesto dove /sys manca."""
    out: dict[str, Any] = {"usb": [], "block": []}
    try:
        import glob as _glob
        for d in sorted(_glob.glob("/sys/bus/usb/devices/[0-9]*")):
            try:
                with open(os.path.join(d, "product")) as f:
                    prod = f.read().strip()
            except OSError:
                continue  # hub/interfacce senza product: salta
            entry = {"product": prod}
            try:
                with open(os.path.join(d, "manufacturer")) as f:
                    entry["manufacturer"] = f.read().strip()
            except OSError:
                pass
            out["usb"].append(entry)
        for b in sorted(_glob.glob("/sys/block/*")):
            name = os.path.basename(b)
            if name.startswith(("loop", "ram", "zram")):
                continue
            entry = {"name": name}
            try:
                with open(os.path.join(b, "size")) as f:
                    entry["size_gb"] = round(int(f.read().strip()) * 512 / 1e9, 1)
            except (OSError, ValueError):
                pass
            try:
                with open(os.path.join(b, "device", "model")) as f:
                    entry["model"] = f.read().strip()
            except OSError:
                pass
            out["block"].append(entry)
    except Exception:
        pass
    return out


def _collect_health(services_extra: tuple[str, ...] | None = None) -> dict:
    """Aggrega le sezioni descrittive+dinamiche. Nessuna chiamata LLM (§7.9).
    Raccolta SEMPRE completa (Roberto 9/7): la risposta poi usa la parte
    pertinente alla richiesta (blocco-status sintetico vs domande specifiche
    su cpu/gpu/ip/periferiche che pescano dalle sezioni)."""
    services = _read_services()
    if services_extra:
        services.extend(_read_services(tuple(services_extra)))
    return {
        "system": _read_system(),
        "cpu": _read_cpu(),
        "gpu": _read_gpu(),
        "load": _read_load(),
        "memory": _read_memory(),
        "thermal": _read_thermal(),
        "power": _read_power(),
        "disk": _read_disk(),
        "network": _read_network(),
        "peripherals": _read_peripherals(),
        "services": services,
        "collected_at": int(time.time()),
    }


def _rank_key(e: dict):
    """Chiave di ranking robusta a CPU non-misurata (§2.8). Se `cpu_pct` e'
    noto (POSIX) ordina per CPU; se e' None (Windows/tasklist) ripiega sulla
    MEMORIA reale (`mem_kb`) — mai crash confrontando None con float, mai un
    ordinamento fasullo per un valore inventato. La tupla mette gli item con
    CPU nota sopra quelli senza (a parita' di richiesta reverse=True)."""
    cpu = e.get("cpu_pct")
    if cpu is not None:
        return (1, float(cpu))
    return (0, float(e.get("mem_kb") or 0))


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
    # §2.8: snapshot GREZZO vuoto = strumento fallito (un sistema vivo ha
    # sempre processi) — mai «ok con 0» che maschera il guasto (visto live
    # 5/7 sul device Windows: il ramo POSIX moriva in silenzio).
    if not snapshot:
        return {"ok": False, "error_code": "ERR_EXT_TOOL_FAILED",
                "error": _msg("ERR_EXT_TOOL_FAILED",
                               tool="ps/tasklist",
                               reason=_SNAPSHOT_FAIL_REASON or "output vuoto")}
    if predicates:
        snapshot = [
            e for e in snapshot
            if all(_eval(e, a, o, v) for (a, o, v) in predicates)
        ]
    snapshot.sort(key=_rank_key, reverse=True)
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
