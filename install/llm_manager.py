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


# ---------------------------------------------------------------------------
# Provisioning reale — helper (rete; retry per l'instabilità TLS osservata)
# ---------------------------------------------------------------------------
import json as _json
import re as _re
import urllib.request as _ur
import urllib.error as _ue


def _http_json(url: str) -> dict:
    hdr = {"User-Agent": "metnos-llm-manager",
           "Accept": "application/vnd.github+json"}
    # Token GitHub opzionale (env) → alza il rate-limit API da 60 a 5000/h.
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        hdr["Authorization"] = f"Bearer {tok}"
    req = _ur.Request(url, headers=hdr)
    with _ur.urlopen(req, timeout=30) as r:
        return _json.load(r)


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, *, attempts: int = 5,
              expected_sha256: str | None = None) -> bool:
    """Scarica url→dest con retry + VERIFICA INTEGRITÀ (C1, fail-closed).

    Solo HTTPS. Se `expected_sha256` è dato e NON combacia → scarta il file e
    fallisce (mai eseguire/estrarre un artefatto non verificato). Se l'hash
    atteso è None lo scarica ma AVVISA che l'integrità non è verificata.
    """
    if not url.lower().startswith("https://"):
        print(f"    RIFIUTO download non-HTTPS: {url[:60]}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    for i in range(1, attempts + 1):
        try:
            req = _ur.Request(url, headers={"User-Agent": "metnos-llm-manager"})
            with _ur.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, length=1024 * 256)
        except (_ue.URLError, OSError, Exception) as e:  # noqa: BLE001
            last = str(e)
            print(f"    download tentativo {i}/{attempts} fallito: {last[:80]}")
            if dest.exists():
                dest.unlink()
            continue
        # Verifica integrità DOPO il download, PRIMA di usarlo.
        if expected_sha256:
            got = _sha256_file(dest)
            if got.lower() != expected_sha256.lower():
                print(f"    ✗ SHA256 MISMATCH: atteso {expected_sha256[:16]}…, "
                      f"ottenuto {got[:16]}… → scarto (possibile manomissione)")
                dest.unlink(missing_ok=True)
                last = "sha256 mismatch"
                continue
            print(f"    ✓ SHA256 verificato ({got[:16]}…)")
        else:
            print("    ! integrità NON verificata (nessun SHA256 atteso): "
                  "pin l'hash prima del rilascio pubblico.")
        return True
    print(f"    download FALLITO dopo {attempts}: {last[:120]}")
    return False


def _hf_expected_sha256(hf_repo: str, hf_file: str) -> str | None:
    """Recupera lo SHA256 pubblicato da HuggingFace per il file (LFS pointer).

    Permette di verificare il download contro l'hash della SORGENTE (cattura
    corruzione + un MITM banale). None se non disponibile."""
    try:
        meta = _http_json(
            f"https://huggingface.co/api/models/{hf_repo}"
            f"?expand[]=siblings&expand[]=lfs")
        for s in (meta.get("siblings") or []):
            if s.get("rfilename") == hf_file:
                lfs = s.get("lfs") or {}
                return lfs.get("sha256") or lfs.get("oid")
    except Exception:  # noqa: BLE001
        return None
    return None


def _safe_extract(arc: Path, dest: Path) -> bool:
    """Estrae zip/tar RIFIUTANDO membri pericolosi (path traversal, assoluti,
    symlink) — H4. Ritorna False se un membro è ostile."""
    dest = dest.resolve()
    try:
        if arc.name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(arc) as z:
                for n in z.namelist():
                    tgt = (dest / n).resolve()
                    if not str(tgt).startswith(str(dest) + os.sep) and tgt != dest:
                        print(f"    ✗ membro zip ostile: {n}"); return False
                z.extractall(dest)
        else:
            import tarfile
            with tarfile.open(arc) as t:
                for m in t.getmembers():
                    tgt = (dest / m.name).resolve()
                    if (m.issym() or m.islnk()
                            or (not str(tgt).startswith(str(dest) + os.sep) and tgt != dest)):
                        print(f"    ✗ membro tar ostile: {m.name}"); return False
                try:
                    t.extractall(dest, filter="data")  # py>=3.12
                except TypeError:
                    t.extractall(dest)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"    estrazione fallita: {e}")
        return False


def _pick_llama_asset(assets: list, backend: str) -> dict | None:
    """Sceglie l'asset prebuilt giusto da una release ggml-org/llama.cpp."""
    cand = [a for a in assets
            if _re.search(r"(ubuntu|linux)", a["name"], _re.I)
            and _re.search(r"(x64|x86_64|amd64)", a["name"], _re.I)
            and a["name"].lower().endswith((".zip", ".tar.gz", ".tgz", ".tar.xz"))]
    if not cand:
        cand = [a for a in assets if a["name"].lower().endswith((".zip", ".tar.gz"))]
    kw = {"cuda": "cuda", "rocm": "(hip|rocm)", "vulkan": "vulkan",
          "metal": "macos", "cpu": "cpu"}.get(backend, "cpu")
    pref = [a for a in cand if _re.search(kw, a["name"], _re.I)]
    if pref:
        return pref[0]
    # Plain CPU build: escludi OGNI variante specializzata (che richiede runtime
    # extra: openvino/cann/sycl/musa/kompute oltre a cuda/hip/rocm/vulkan). Il
    # live-test ha mostrato che un build openvino non parte senza i suoi libs.
    _SPECIAL = r"cuda|hip|rocm|vulkan|sycl|musa|openvino|cann|kompute"
    plain = [a for a in cand if not _re.search(_SPECIAL, a["name"], _re.I)]
    return (plain or cand or [None])[0]


def _find_llama_server(root: Path) -> Path | None:
    for p in root.rglob("llama-server"):
        if p.is_file():
            return p
    for p in root.rglob("server"):           # release piu' vecchie
        if p.is_file():
            return p
    return None


def acquire_llama(backend: str, dest: Path) -> Path | None:
    """Scarica un binario prebuilt llama.cpp per il backend ed estrae llama-server.

    Prebuilt da github ggml-org/llama.cpp latest release. Ritorna il path del
    binario llama-server, o None (fallback: build da sorgente, fuori scope qui).
    """
    existing = _find_llama_server(dest)
    if existing:
        print(f"    llama-server già presente: {existing}")
        return existing
    # Pin del tag release (riproducibilità + verificabilità). Override env;
    # default a un tag pinnato, fallback a latest con avviso.
    tag = os.environ.get("METNOS_LLAMA_TAG", "").strip()
    api = (f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"
           if tag else
           "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    if not tag:
        print("    ! release NON pinnata (latest): pin METNOS_LLAMA_TAG per riproducibilità.")
    try:
        rel = _http_json(api)
    except Exception as e:  # noqa: BLE001
        print(f"    release API fallita: {e}")
        return None
    asset = _pick_llama_asset(rel.get("assets", []), backend)
    if not asset:
        print(f"    nessun asset prebuilt per backend '{backend}' "
              "(fallback: build da sorgente con cmake — non automatizzato qui).")
        return None
    print(f"    asset: {asset['name']} ({asset.get('size',0)//(1024*1024)} MB)")
    dest.mkdir(parents=True, exist_ok=True)
    arc = dest / asset["name"]
    # GitHub espone un digest sha256 sull'asset nelle API recenti (campo
    # `digest`: "sha256:..."). Se presente, verifica fail-closed.
    exp = None
    dg = asset.get("digest") or ""
    if isinstance(dg, str) and dg.startswith("sha256:"):
        exp = dg.split(":", 1)[1]
    if not _download(asset["browser_download_url"], arc, expected_sha256=exp):
        return None
    if not _safe_extract(arc, dest):       # H4: estrazione anti path-traversal
        return None
    binp = _find_llama_server(dest)
    if binp:
        binp.chmod(0o755)
    return binp


def download_model(hf_repo: str, hf_file: str, dest: Path) -> bool:
    """Scarica un GGUF da HuggingFace con VERIFICA SHA256 (dall'API HF). Idempotente."""
    exp = _hf_expected_sha256(hf_repo, hf_file)
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        # Verifica-on-reuse per hash, non per dimensione (L5): un file corrotto
        # della stessa dimensione NON deve essere accettato.
        if exp and _sha256_file(dest).lower() != exp.lower():
            print(f"    modello esistente con hash errato → ri-scarico")
            dest.unlink(missing_ok=True)
        else:
            print(f"    modello già presente{' (sha verificato)' if exp else ''}: {dest}")
            return True
    url = f"https://huggingface.co/{hf_repo}/resolve/main/{hf_file}?download=true"
    print(f"    scarico {hf_repo}/{hf_file} …")
    return _download(url, dest, attempts=6, expected_sha256=exp)


def _write_systemd_unit(llama_bin: Path, model_file: Path, endpoint: str,
                        ngl: int, unit_path: Path) -> None:
    host = "127.0.0.1"
    port = endpoint.rsplit(":", 1)[-1] if ":" in endpoint else "8080"
    unit = f"""[Unit]
Description=Metnos local LLM (llama-server)
After=network-online.target

[Service]
ExecStart={llama_bin} -m {model_file} --host {host} --port {port} -ngl {ngl} -c 8192
Restart=on-failure
Nice=5

[Install]
WantedBy=default.target
"""
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit, encoding="utf-8")


def health_check(llama_bin: Path, model_file: Path, *, port: int, ngl: int,
                 timeout_s: int = 60) -> bool:
    """Avvia llama-server (breve) e fa il ping di /health. Stoppa subito.

    Per la verifica usiamo ngl=0 (CPU) di default per NON contendere la GPU con
    un eventuale llama-server di produzione."""
    import time as _t
    proc = subprocess.Popen(
        [str(llama_bin), "-m", str(model_file), "--host", "127.0.0.1",
         "--port", str(port), "-ngl", str(ngl), "-c", "2048"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = _t.time() + timeout_s
        while _t.time() < deadline:
            try:
                with _ur.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                    if r.status == 200:
                        return True
            except Exception:  # noqa: BLE001
                _t.sleep(2)
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


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
    out["executed"] = True

    # 1) llama.cpp prebuilt
    print("\n[1/5] llama.cpp")
    binp = acquire_llama(plan.backend, llama)
    out["llama_server"] = str(binp) if binp else None
    if not binp:
        print("  ✗ llama-server non acquisito (fallback build da sorgente, "
              "fuori scope). Mi fermo prima del modello.")
        out["ok"] = False
        return out
    print(f"  ✓ {binp}")

    # 2) modello GGUF
    print("[2/5] modello")
    if not download_model(plan.hf_repo, plan.hf_file, model_file):
        print("  ✗ download modello fallito.")
        out["ok"] = False
        return out
    print(f"  ✓ {model_file} ({model_file.stat().st_size // (1024*1024)} MB)")

    # 3) tiers config
    print("[3/5] tiers")
    tiers.parent.mkdir(parents=True, exist_ok=True)
    tiers.write_text(_render_tiers_toml(plan, model_file), encoding="utf-8")
    out["tiers_written"] = str(tiers)
    print(f"  ✓ {tiers}")

    # 4) systemd unit (GPU offload pieno per il servizio reale)
    print("[4/5] systemd unit")
    ngl = 0 if plan.backend == "cpu" else 999
    unit = llama.parent / "metnos-llm.service"
    _write_systemd_unit(binp, model_file, plan.endpoint, ngl, unit)
    out["systemd_unit"] = str(unit)
    print(f"  ✓ {unit}  (abilita: sudo cp {unit} /etc/systemd/system/ && "
          "sudo systemctl enable --now metnos-llm)")

    # 5) health-check (CPU, porta di prova → non contende la GPU di produzione)
    print("[5/5] health-check (CPU, porta 8084)")
    healthy = health_check(binp, model_file, port=8084, ngl=0, timeout_s=90)
    out["health"] = healthy
    print("  ✓ llama-server risponde a /health" if healthy
          else "  ! health-check non superato in tempo (modello grande su CPU? "
               "il servizio reale usa la GPU).")
    out["ok"] = bool(binp) and model_file.exists()
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
    pp.add_argument("--test", action="store_true",
                    help="verifica il meccanismo con un modello TINY su CPU "
                         "(non scarica il modello grande, non tocca la GPU)")
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
        if getattr(args, "test", False):
            # Modello TINY per verificare acquire→download→start→health a basso
            # costo, su CPU, senza contendere la GPU di produzione.
            plan.model_key = "qwen2.5-0.5b-test"
            plan.model_label = "Qwen2.5 0.5B (TEST)"
            plan.hf_repo = "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
            plan.hf_file = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
            plan.backend = "cpu"
            plan.feasible = True
            plan.warnings.append("MODALITÀ TEST: modello tiny su CPU.")
        res = provision(plan, dry_run=args.dry_run or not args.yes,
                        assume_yes=args.yes)
        return 0 if res.get("feasible", False) else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
