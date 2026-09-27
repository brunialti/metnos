# SPDX-License-Identifier: MIT
"""Pre-flight system checks.

Run before any phase. Catches the classic failures early (wrong Python,
not enough disk, missing libstdc++, no network) so we can fail fast with
an actionable message rather than 200 lines into phase 2.

Resource checks (Python, disk, RAM, VRAM/GPU, libstdc++, network, git)
report fatal vs non-fatal. Fatal failures abort phase 1; non-fatal ones
warn and continue.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import ui


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True  # fatal=False means warn-and-continue


def check_python() -> CheckResult:
    v = sys.version_info
    if (sys.implementation.name == "cpython" and v[:2] == (3, 12)
            and platform.system() == "Linux" and platform.machine() == "x86_64"):
        return CheckResult("Linux x86_64 · CPython 3.12", True, f"{v.major}.{v.minor}.{v.micro}")
    return CheckResult(
        "Linux x86_64 · CPython 3.12",
        False,
        f"found {v.major}.{v.minor}.{v.micro} on {platform.system()} {platform.machine()} — use the supported release platform",
    )


def check_disk(min_free_gb: int = 8) -> CheckResult:
    """Check free space on the partition holding $METNOS_USER_DATA."""
    home = Path(os.environ.get("METNOS_USER_DATA") or (Path.home() / ".local" / "share" / "metnos"))
    # ``python -m install --check`` promises a read-only pre-flight.  The
    # configured data directory may not exist yet on a fresh host, so inspect
    # the nearest existing parent instead of creating the directory merely to
    # call ``disk_usage``.
    probe = home
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_bytes = shutil.disk_usage(probe).free
    free_gb = free_bytes // (1024 ** 3)
    if free_gb >= min_free_gb:
        return CheckResult(
            "Disk space",
            True,
            f"{free_gb} GB free for {home} (need ≥ {min_free_gb} GB minimum)",
        )
    return CheckResult(
        "Disk space",
        False,
        f"only {free_gb} GB free for {home} — need ≥ {min_free_gb} GB",
    )


def memory_bytes() -> tuple[int, int]:
    """Total and available RAM, capped by visible cgroup v2 limits."""
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        fields = value.split()
        if fields and fields[0].isdigit():
            info[key] = int(fields[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    root = Path("/sys/fs/cgroup")
    try:
        memberships = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        memberships = []
    for line in memberships:
        if not line.startswith("0::/"):
            continue
        relative = Path(line[4:])
        if ".." in relative.parts:
            continue
        current = root / relative
        while True:
            try:
                limit = int((current / "memory.max").read_text().strip())
                used = int((current / "memory.current").read_text().strip())
                total = min(total, limit)
                available = min(available, max(0, limit - used))
            except (OSError, ValueError):
                pass  # Absent or unlimited cgroup; parent may still cap it.
            if current == root:
                break
            current = current.parent
    return total, available


def check_ram(min_available_gb: int = 8) -> CheckResult:
    """Non-fatal warning below the memory actually available to this process."""
    try:
        total, available = memory_bytes()
        avail_gb = available // (1024 ** 3)
        total_gb = total // (1024 ** 3)
        if avail_gb >= min_available_gb:
            return CheckResult(
                "RAM", True,
                f"{avail_gb} GB available of {total_gb} GB total (need ≥ {min_available_gb})",
            )
        return CheckResult(
            "RAM", False,
            f"only {avail_gb} GB available of {total_gb} GB total — "
            f"local LLM may run out of memory (need ≥ {min_available_gb})",
            fatal=False,
        )
    except (OSError, ValueError) as e:
        return CheckResult("RAM", False, f"could not read /proc/meminfo: {e}", fatal=False)


def check_vram(min_free_gb: int = 4) -> CheckResult:
    """Detect GPU + free VRAM.

    Probes in order: ``nvidia-smi`` (NVIDIA), ``rocm-smi`` (AMD ROCm),
    ``/dev/dri/`` (any DRM-capable GPU). VRAM bytes are best-effort; on
    Strix Halo and similar unified-memory APUs the value reported here
    is approximate.

    Purely INFORMATIONAL — a GPU is OPTIONAL (tiers are abstract; the LLM
    can run on CPU or via the frontier API). This check never fails and
    never warns: missing/low VRAM is a supported configuration, not a
    deficiency. ``min_free_gb`` only tunes the advisory wording.
    """
    # 1. NVIDIA
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free,name", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0 and r.stdout.strip():
                lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                free_mbs = []
                names = []
                for ln in lines:
                    parts = [p.strip() for p in ln.split(",")]
                    if parts and parts[0].isdigit():
                        free_mbs.append(int(parts[0]))
                        if len(parts) > 1:
                            names.append(parts[1])
                if free_mbs:
                    free_gb = max(free_mbs) // 1024
                    label = ", ".join(names) or "NVIDIA GPU"
                    hint = "" if free_gb >= min_free_gb else (
                        f" — below ~{min_free_gb} GB, a local GPU LLM may not fit "
                        "(CPU or frontier API will be used instead)")
                    return CheckResult(
                        "VRAM", True,
                        f"{free_gb} GB free on {label}{hint}",
                        fatal=False,
                    )
        except (subprocess.SubprocessError, OSError):
            pass

    # 2. AMD ROCm
    if shutil.which("rocm-smi"):
        try:
            r = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0 and r.stdout.strip():
                # parse first numeric (free bytes) column; best-effort
                for ln in r.stdout.splitlines():
                    nums = [int(x) for x in ln.replace(",", " ").split() if x.isdigit()]
                    if nums:
                        free_gb = max(nums) // (1024 ** 3)
                        hint = "" if free_gb >= min_free_gb else (
                            f" — below ~{min_free_gb} GB, a local GPU LLM may not fit "
                            "(CPU or frontier API will be used instead)")
                        return CheckResult(
                            "VRAM", True,
                            f"{free_gb} GB free on AMD GPU{hint}",
                            fatal=False,
                        )
        except (subprocess.SubprocessError, OSError):
            pass

    # 3. /dev/dri/ presence — GPU exists, VRAM unknown
    dri = Path("/dev/dri")
    if dri.exists():
        cards = list(dri.glob("card*"))
        if cards:
            return CheckResult(
                "VRAM", True,
                f"GPU detected ({len(cards)} DRM card{'s' if len(cards) > 1 else ''}) — "
                f"free VRAM not measured. Install nvidia-smi or rocm-smi for a precise read.",
                fatal=False,
            )

    return CheckResult(
        "VRAM", True,
        "no GPU detected — Metnos will use CPU or the frontier API (a local GPU is optional)",
        fatal=False,
    )


def check_libstdcpp() -> CheckResult:
    """onnxruntime needs libstdc++ ≥ 11 (GLIBCXX_3.4.29)."""
    candidates = [
        "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
        "/usr/lib64/libstdc++.so.6",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                # The symbol table is in the library itself; binutils is not
                # an installer prerequisite on a minimal Linux image.
                import mmap
                with open(c, "rb") as library, mmap.mmap(
                    library.fileno(), 0, access=mmap.ACCESS_READ,
                ) as contents:
                    available = contents.find(b"GLIBCXX_3.4.29\x00") >= 0
                if available:
                    return CheckResult("libstdc++ ≥ 11", True, c)
            except (OSError, ValueError):
                pass
    return CheckResult(
        "libstdc++ ≥ 11",
        False,
        "GLIBCXX_3.4.29 symbol not found — install libstdc++ 11+",
        fatal=False,  # may be ok if user only uses frontier-only mode
    )


def check_network() -> CheckResult:
    """Reachability of one known host (DNS + TCP)."""
    try:
        with socket.create_connection(("api.github.com", 443), timeout=5):
            return CheckResult("Network (api.github.com:443)", True, "reachable")
    except (socket.timeout, OSError) as e:
        return CheckResult(
            "Network (api.github.com:443)",
            False,
            f"{type(e).__name__}: {e} — installer needs internet for first run",
            fatal=False,  # if mirror is local, user may skip
        )


def check_git() -> CheckResult:
    if shutil.which("git"):
        return CheckResult("git binary", True, shutil.which("git") or "")
    return CheckResult(
        "git binary",
        False,
        "git not found — needed to fetch the source tree",
        fatal=False,
    )


def run_all(min_disk_gb: int = 8, min_ram_gb: int = 8, min_vram_gb: int = 4) -> bool:
    """Run all pre-flight checks. Print results. Return True if all fatal pass."""
    checks = [
        check_python(),
        check_disk(min_disk_gb),
        check_ram(min_ram_gb),
        check_vram(min_vram_gb),
        check_libstdcpp(),
        check_network(),
        check_git(),
    ]
    any_fatal_fail = False
    for c in checks:
        if c.ok:
            ui.ok(f"{c.name}: {c.detail}")
        else:
            if c.fatal:
                ui.warn(f"{c.name}: {c.detail} [fatal]")
                any_fatal_fail = True
            else:
                ui.warn(f"{c.name}: {c.detail} [non-fatal, continuing]")
    return not any_fatal_fail
