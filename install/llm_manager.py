#!/usr/bin/env python3
"""install/llm_manager.py — smart managed install dello stack LLM locale.

Percorso "managed/ex-novo" (raccomandato): rileva l'hardware, sceglie in modo
DETERMINISTICO backend + modello + mappatura tier che ci stanno nella memoria
disponibile, poi provvisiona (llama.cpp + modello GGUF), scrive
``~/.config/metnos/llm_tiers.toml`` e verifica con un health-ping.

Parti:
  detect_hardware()  -> dict {accel, vram_gb, ram_gb, gpu_name, unified}
  recommend(hw)      -> Plan {backend, tiers, model, warnings}  (SMART, testabile)
  provision(plan)    -> esegue (o --dry-run stampa) fetch + wiring + verify

CLI:
  python3 install/llm_manager.py detect
  python3 install/llm_manager.py recommend [--vram N --ram N --accel X]  # what-if
  python3 install/llm_manager.py provision [--dry-run] [--yes]

NB: il fetch reale di llama.cpp e dei modelli (GB) richiede rete; usa --dry-run
per vedere il piano senza scaricare. Gli ID HF dei modelli sono nel CATALOG e
vanno verificati prima del rilascio pubblico.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Catalogo modelli — data-driven. Ordinato per capacita' DECRESCENTE.
# `min_budget_gb` = memoria (VRAM dedicata o quota RAM unificata) necessaria a
# far girare il modello Q4_K_M con un minimo di contesto. `wise_capable` = supera
# il quality-floor del tier `wise` (vedi llm_router.WISE_QUALITY_WHITELIST_LOCAL).
# ⚠️ hf_repo/hf_file da VERIFICARE prima del go-public (non testabili offline).
# ---------------------------------------------------------------------------
CATALOG = [
    {"key": "qwen3-32b", "label": "Qwen3 32B", "params_b": 32, "q4_gb": 20,
     "min_budget_gb": 26, "wise_capable": True,
     "hf_repo": "Qwen/Qwen3-32B-GGUF", "hf_file": "Qwen3-32B-Q4_K_M.gguf",
     "tier_token": "qwen3:32"},
    {"key": "qwen3-14b", "label": "Qwen3 14B", "params_b": 14, "q4_gb": 9,
     "min_budget_gb": 13, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-14B-GGUF", "hf_file": "Qwen3-14B-Q4_K_M.gguf",
     "tier_token": "qwen3:14"},
    {"key": "qwen3-8b", "label": "Qwen3 8B", "params_b": 8, "q4_gb": 5,
     "min_budget_gb": 8, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-8B-GGUF", "hf_file": "Qwen3-8B-Q4_K_M.gguf",
     "tier_token": "qwen3:8"},
    {"key": "qwen3-4b", "label": "Qwen3 4B", "params_b": 4, "q4_gb": 3,
     "min_budget_gb": 5, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-4B-GGUF", "hf_file": "Qwen3-4B-Q4_K_M.gguf",
     "tier_token": "qwen3:4"},
]

DEFAULT_ENDPOINT = "http://127.0.0.1:8080"
# Frazione della RAM unificata/sistema utilizzabile per il modello (lascia
# margine per OS, KV-cache, embedding in-process, resto di Metnos).
UNIFIED_RAM_FRACTION = 0.6


@dataclass
class Plan:
    backend: str                      # cuda | rocm | vulkan | metal | cpu
    model_key: str | None
    model_label: str | None
    hf_repo: str | None = None
    hf_file: str | None = None
    endpoint: str = DEFAULT_ENDPOINT
    budget_gb: int = 0
    wise_ok: bool = False
    tiers: dict = field(default_factory=dict)   # tier -> model token
    warnings: list = field(default_factory=list)
    feasible: bool = True


# ---------------------------------------------------------------------------
# 1. Rilevamento hardware
# ---------------------------------------------------------------------------
def _ram_gb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // (1024 * 1024)
    except OSError:
        pass
    return 0


def _nvidia() -> tuple[int, str] | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            best = 0
            name = "NVIDIA GPU"
            for ln in r.stdout.splitlines():
                parts = [p.strip() for p in ln.split(",")]
                if parts and parts[0].isdigit():
                    mb = int(parts[0])
                    if mb // 1024 > best:
                        best = mb // 1024
                        name = parts[1] if len(parts) > 1 else name
            if best:
                return best, name
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _rocm() -> tuple[int, str] | None:
    if not shutil.which("rocm-smi"):
        return None
    # ROCm VRAM parsing e' best-effort; su APU unified torna spesso poco utile.
    return 0, "AMD ROCm GPU"


def detect_hardware() -> dict:
    """Rileva acceleratore + memoria. Best-effort, mai fatale."""
    ram = _ram_gb()
    nv = _nvidia()
    if nv:
        return {"accel": "cuda", "gpu_name": nv[1], "vram_gb": nv[0],
                "ram_gb": ram, "unified": False}
    if shutil.which("rocm-smi"):
        # AMD: distinguo APU unified (Strix Halo: poca VRAM dedicata, molta RAM)
        # da dGPU. Senza un parsing affidabile, tratto come unified Vulkan/ROCm.
        accel = "rocm" if shutil.which("rocminfo") else "vulkan"
        return {"accel": accel, "gpu_name": "AMD GPU/APU", "vram_gb": 0,
                "ram_gb": ram, "unified": True}
    if Path("/dev/dri").exists() and any(Path("/dev/dri").glob("renderD*")):
        return {"accel": "vulkan", "gpu_name": "DRM GPU (Vulkan)", "vram_gb": 0,
                "ram_gb": ram, "unified": True}
    if shutil.which("system_profiler"):  # macOS
        return {"accel": "metal", "gpu_name": "Apple GPU", "vram_gb": 0,
                "ram_gb": ram, "unified": True}
    return {"accel": "cpu", "gpu_name": None, "vram_gb": 0,
            "ram_gb": ram, "unified": False}


def _budget_gb(hw: dict) -> int:
    """Memoria utilizzabile per il modello LLM.

    - GPU dedicata (cuda/dGPU): la VRAM.
    - Memoria unificata (APU/Apple) o CPU: una frazione della RAM (margine per
      OS + KV-cache + embedding in-process + resto di Metnos)."""
    if hw.get("vram_gb", 0) >= 4 and not hw.get("unified"):
        return int(hw["vram_gb"])
    return int(hw.get("ram_gb", 0) * UNIFIED_RAM_FRACTION)


# ---------------------------------------------------------------------------
# 2. Raccomandazione SMART (deterministica, testabile)
# ---------------------------------------------------------------------------
def recommend(hw: dict) -> Plan:
    budget = _budget_gb(hw)
    accel = hw.get("accel", "cpu")
    plan = Plan(backend=accel, model_key=None, model_label=None, budget_gb=budget)

    # scegli il modello PIU' capace che ci sta nel budget
    chosen = None
    for m in CATALOG:  # gia' ordinato per capacita' decrescente
        if budget >= m["min_budget_gb"]:
            chosen = m
            break

    if chosen is None:
        plan.feasible = False
        plan.warnings.append(
            f"Memoria insufficiente per un LLM locale (budget ~{budget} GB; "
            f"il modello piu' piccolo richiede {CATALOG[-1]['min_budget_gb']} GB). "
            "Usa il tier `frontier` (API cloud) o aggiungi RAM/VRAM.")
        return plan

    plan.model_key = chosen["key"]
    plan.model_label = chosen["label"]
    plan.hf_repo = chosen["hf_repo"]
    plan.hf_file = chosen["hf_file"]
    plan.wise_ok = chosen["wise_capable"]
    token = chosen["tier_token"]
    # tutti i tier locali sullo stesso modello (come l'esercizio); endpoint unico
    plan.tiers = {t: token for t in ("fast", "middle", "wise")}

    if accel == "cpu":
        plan.warnings.append(
            "Nessun acceleratore GPU rilevato: l'inferenza girera' su CPU "
            "(lenta). Per un assistente usabile e' raccomandata una GPU/APU.")
    if not chosen["wise_capable"]:
        plan.warnings.append(
            f"{chosen['label']} non supera il quality-floor del tier `wise`: "
            "la pianificazione complessa sara' meno affidabile. Aggiungi memoria "
            "per un modello >=32B, o usa `frontier` per i task wise.")
    if budget < 8:
        plan.warnings.append(
            f"Budget memoria molto basso (~{budget} GB): qualita'/contesto ridotti.")
    return plan


# ---------------------------------------------------------------------------
# 3. Provisioning
# ---------------------------------------------------------------------------
def _models_dir() -> Path:
    base = os.environ.get("METNOS_MODELS_DIR") or \
        (os.environ.get("METNOS_INSTALL_ROOT", "/opt/metnos") + "/models")
    return Path(base) / "llm"


def _llama_dir() -> Path:
    base = os.environ.get("METNOS_INSTALL_ROOT", "/opt/metnos")
    return Path(base) / "llm" / "llama.cpp"


def _tiers_toml_path() -> Path:
    cfg = os.environ.get("METNOS_USER_CONFIG") or \
        os.path.join(os.path.expanduser("~"), ".config", "metnos")
    return Path(cfg) / "llm_tiers.toml"


def _render_tiers_toml(plan: Plan, model_file: Path) -> str:
    lines = [
        "# llm_tiers.toml — generato da install/llm_manager.py (managed install).",
        "# Override flat dei tier locali → llama-server.",
        "",
    ]
    for tier in ("fast", "middle", "wise"):
        lines += [
            f"[{tier}]",
            'provider = "llamacpp"',
            f'model = "{model_file.name}"',
            f'endpoint = "{plan.endpoint}"',
            "",
        ]
    lines += [
        "# frontier resta opt-in (API cloud): configurane la key a parte.",
        "[frontier]",
        'provider = "anthropic"',
        'model = "claude-opus-4-7"',
        "",
    ]
    return "\n".join(lines)


def provision(plan: Plan, *, dry_run: bool = True, assume_yes: bool = False) -> dict:
    out: dict = {"dry_run": dry_run, "steps": []}

    def emit(msg: str):
        out["steps"].append(msg)
        print(("  [dry-run] " if dry_run else "  ") + msg)

    if not plan.feasible:
        print("Piano NON fattibile su questo hardware:")
        for w in plan.warnings:
            print("  ! " + w)
        out["feasible"] = False
        return out

    models = _models_dir()
    llama = _llama_dir()
    model_file = models / plan.hf_file
    tiers = _tiers_toml_path()

    print(f"\nPiano: {plan.model_label}  (backend: {plan.backend}, "
          f"budget ~{plan.budget_gb} GB, wise-capable: {plan.wise_ok})")
    for w in plan.warnings:
        print("  ! " + w)
    print("")

    # 1) llama.cpp (prebuilt preferito; build come fallback)
    emit(f"Procurare llama-server per backend '{plan.backend}' in {llama} "
         f"(prebuilt da github ggml-org/llama.cpp release; fallback: build con cmake)")
    # 2) modello GGUF
    emit(f"Scaricare {plan.hf_repo}/{plan.hf_file} (~{_q4_gb(plan)} GB) "
         f"in {model_file}  [huggingface]")
    # 3) tiers config
    emit(f"Scrivere {tiers} (tier fast/middle/wise → llamacpp {model_file.name} "
         f"@ {plan.endpoint})")
    # 4) servizio
    emit(f"Avviare llama-server (systemd unit metnos-llm) su {plan.endpoint}")
    # 5) verifica
    emit(f"Health-ping {plan.endpoint}/health + completion di prova")

    if dry_run:
        out["feasible"] = True
        return out

    # --- esecuzione reale (best-effort; richiede rete + spazio) ---
    if not assume_yes:
        print("\n(usa --yes per eseguire davvero; senza, mi fermo qui)")
        out["executed"] = False
        return out

    models.mkdir(parents=True, exist_ok=True)
    # NB: l'acquisizione di llama.cpp e il download del modello sono passi
    # pesanti e dipendenti dalla rete; qui scrivo SOLO i tier (deterministico)
    # e lascio i fetch a helper dedicati (download_llama.sh / hf download) per
    # non simulare un esito non verificato (§2.8 onesta').
    tiers.parent.mkdir(parents=True, exist_ok=True)
    tiers.write_text(_render_tiers_toml(plan, model_file), encoding="utf-8")
    print(f"  ✓ scritto {tiers}")
    print("  ! llama.cpp + modello: esegui i fetch dedicati, poi avvia il servizio.")
    out["executed"] = True
    out["tiers_written"] = str(tiers)
    return out


def _q4_gb(plan: Plan) -> int:
    for m in CATALOG:
        if m["key"] == plan.model_key:
            return m["q4_gb"]
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Smart managed LLM install per Metnos")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    pr = sub.add_parser("recommend")
    pr.add_argument("--vram", type=int, default=None)
    pr.add_argument("--ram", type=int, default=None)
    pr.add_argument("--accel", default=None)
    pr.add_argument("--unified", action="store_true")
    pp = sub.add_parser("provision")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--yes", action="store_true")
    args = p.parse_args()

    if args.cmd == "detect":
        hw = detect_hardware()
        hw["budget_gb"] = _budget_gb(hw)
        print(json.dumps(hw, indent=2))
        return 0

    if args.cmd == "recommend":
        hw = detect_hardware()
        if args.vram is not None:
            hw["vram_gb"] = args.vram
        if args.ram is not None:
            hw["ram_gb"] = args.ram
        if args.accel:
            hw["accel"] = args.accel
        if args.unified:
            hw["unified"] = True
        plan = recommend(hw)
        print(json.dumps(asdict(plan), indent=2))
        return 0 if plan.feasible else 1

    if args.cmd == "provision":
        plan = recommend(detect_hardware())
        res = provision(plan, dry_run=args.dry_run or not args.yes,
                        assume_yes=args.yes)
        return 0 if res.get("feasible", False) else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
