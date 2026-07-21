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
# `hf_revision` PIN la riproducibilità (INSTALL_NOTES "pin sha256 in the release
# pipeline"): un commit-sha HF = file IMMUTABILE; `"main"` = ref MOBILE (avviso
# onesto a download, build non riproducibile). Solo il canonico è pinnato qui;
# gli altri restano `main` finché non validati (pin = edit di una riga).
# ---------------------------------------------------------------------------
CATALOG = [
    {"key": "qwen3-32b", "label": "Qwen3 32B", "params_b": 32, "q4_gb": 20,
     "min_budget_gb": 26, "wise_capable": True,
     "hf_repo": "Qwen/Qwen3-32B-GGUF", "hf_file": "Qwen3-32B-Q4_K_M.gguf",
     "hf_revision": "938a7432affaec9157f883a87164e2646ae17555",
     "tier_token": "qwen3:32"},
    {"key": "qwen3-14b", "label": "Qwen3 14B", "params_b": 14, "q4_gb": 9,
     "min_budget_gb": 13, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-14B-GGUF", "hf_file": "Qwen3-14B-Q4_K_M.gguf",
     "hf_revision": "main",
     "tier_token": "qwen3:14"},
    {"key": "qwen3-8b", "label": "Qwen3 8B", "params_b": 8, "q4_gb": 5,
     "min_budget_gb": 8, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-8B-GGUF", "hf_file": "Qwen3-8B-Q4_K_M.gguf",
     "hf_revision": "main",
     "tier_token": "qwen3:8"},
    {"key": "qwen3-4b", "label": "Qwen3 4B", "params_b": 4, "q4_gb": 3,
     "min_budget_gb": 5, "wise_capable": False,
     "hf_repo": "Qwen/Qwen3-4B-GGUF", "hf_file": "Qwen3-4B-Q4_K_M.gguf",
     "hf_revision": "main",
     "tier_token": "qwen3:4"},
]

# Release prebuilt llama.cpp pinnata (riproducibilità + describe deterministico
# §11: la build cambia i logits). Default = tag validato end-to-end dall'harness
# mnostest (12/6/2026). Override `METNOS_LLAMA_TAG=<bNNNN>`; opt-out esplicito
# `METNOS_LLAMA_TAG=latest` (build NON riproducibile, avviso onesto §2.8).
_LLAMA_TAG_DEFAULT = "b9608"

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
    hf_revision: str = "main"         # commit-sha HF (pin) o "main" (mobile)
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
    plan.hf_revision = chosen.get("hf_revision", "main")
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
    """Scarica url→dest + VERIFICA INTEGRITÀ (C1, fail-closed), resiliente ai
    reset per-flusso (alcune reti/ISP/middlebox resettano una singola TCP lunga
    dopo poche decine di MB). Delega a `downloads.robust_fetch`, che scarica un
    GGUF grande a CHUNK PARALLELI con resume per-chunk: nessuna connessione deve
    reggere i 19 GB in un colpo solo. Solo HTTPS; su mismatch sha → scarta. La
    rete singola-connessione qui falliva (il modello non scaricava su CGNAT)."""
    from . import downloads
    return downloads.robust_fetch(url, dest, sha256=expected_sha256,
                                  label=dest.name)


def _http_post_json(url: str, payload: dict):
    """POST JSON → risposta JSON (lista o dict). Usato per l'API HF `paths-info`
    (per-file, revision-scoped). Solo HTTPS."""
    if not url.lower().startswith("https://"):
        raise ValueError("solo HTTPS")
    body = _json.dumps(payload).encode()
    hdr = {"User-Agent": "metnos-llm-manager",
           "Content-Type": "application/json"}
    req = _ur.Request(url, data=body, headers=hdr, method="POST")
    with _ur.urlopen(req, timeout=30) as r:
        return _json.load(r)


def _hf_expected_sha256(hf_repo: str, hf_file: str,
                        revision: str = "main") -> str | None:
    """Recupera lo SHA256 (LFS oid) del file alla `revision` data via l'API HF
    `paths-info` (POST, per-file, vale per qualsiasi ref: commit-sha pinnato o
    `main`). Verifica il download contro l'hash della SORGENTE (corruzione +
    MITM banale). Con `revision` = commit-sha l'hash è quello del file
    IMMUTABILE. None se non disponibile.

    NB: la forma GET `/api/models/{repo}/revision/{rev}?expand[]=lfs` NON
    popola l'lfs (ritorna `error`) → `paths-info` è l'unica forma corretta."""
    rev = revision if revision not in (None, "") else "main"
    try:
        info = _http_post_json(
            f"https://huggingface.co/api/models/{hf_repo}/paths-info/{rev}",
            {"paths": [hf_file]})
        for it in (info or []):
            if it.get("path") == hf_file:
                lfs = it.get("lfs") or {}
                return lfs.get("oid") or lfs.get("sha256")
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
                # Python 3.12+ "data" filter vets path traversal, absolute
                # paths, devices and ESCAPING links — while ALLOWING safe
                # internal symlinks. Real release tarballs (llama.cpp ships
                # e.g. libmtmd.so → libmtmd.so.0) rely on those, so we must
                # NOT blanket-reject every symlink.
                try:
                    t.extractall(dest, filter="data")
                except TypeError:
                    # py<3.11: no data filter — vet manually, allowing a link
                    # only if its target resolves inside dest.
                    for m in t.getmembers():
                        tgt = (dest / m.name).resolve()
                        if not str(tgt).startswith(str(dest) + os.sep) and tgt != dest:
                            print(f"    ✗ membro tar ostile: {m.name}"); return False
                        if m.issym() or m.islnk():
                            lt = ((dest / m.name).parent / m.linkname).resolve()
                            if not str(lt).startswith(str(dest) + os.sep):
                                print(f"    ✗ link tar ostile: {m.name}"); return False
                    t.extractall(dest)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"    estrazione fallita: {e}")
        return False


def _rocm_runtime_complete() -> bool:
    """True se il runtime ROCm ha le librerie che il build HIP di llama.cpp
    carica DAVVERO (rocBLAS). `rocminfo` da solo non basta: senza
    librocblas.so il binario hip ricade su CPU in silenzio (flag E2E
    12/6/2026) — e la produzione usa Vulkan proprio per questo."""
    import ctypes.util
    import glob as _glob
    if ctypes.util.find_library("rocblas"):
        return True
    for pat in ("/opt/rocm*/lib/librocblas.so*",
                "/usr/lib/*/librocblas.so*",
                "/usr/lib64/librocblas.so*"):
        if _glob.glob(pat):
            return True
    return False


def _pick_llama_asset(assets: list, backend: str) -> dict | None:
    """Sceglie l'asset prebuilt giusto da una release ggml-org/llama.cpp.

    Guard anti fallback-CPU-silenzioso: backend `rocm` con runtime ROCm
    incompleto (rocminfo presente ma librocblas assente) → si preferisce
    l'asset Vulkan, che accelera davvero sulle stesse GPU AMD."""
    if backend == "rocm" and not _rocm_runtime_complete():
        print("    ! runtime ROCm incompleto (rocminfo c'e', librocblas no): "
              "il build HIP ricadrebbe su CPU in silenzio → uso l'asset Vulkan.")
        backend = "vulkan"
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


def _find_llama_bin(root: Path, name: str) -> Path | None:
    """Trova un binario llama.cpp per nome sotto root (albero estratto)."""
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def _find_llama_server(root: Path) -> Path | None:
    # "server" = nome nelle release piu' vecchie.
    return _find_llama_bin(root, "llama-server") or _find_llama_bin(root, "server")


def find_completion_bin() -> Path | None:
    """Path di `llama-completion` del managed install (se presente).

    Estratto dallo STESSO archivio release di llama-server → versione
    allineata per costruzione. Usato da phase5 per esporre
    ``METNOS_LLAMACPP_COMPLETION_BIN`` nell'unit metnos-http (il runtime
    lo usa per il describe byte-deterministico, vedi
    ``runtime/llm_helpers.py::_completion_bin``)."""
    return _find_llama_bin(_llama_dir(), "llama-completion")


def _ensure_completion_bin(root: Path) -> Path | None:
    """Rende eseguibile `llama-completion` estratto accanto a llama-server.

    NON fatale (§2.8 onesto): se la release non lo contiene (release
    vecchie), avvisa e degrada — il runtime ricade sul fallback HTTP
    non riproducibile (meta.deterministic=false)."""
    comp = _find_llama_bin(root, "llama-completion")
    if comp:
        comp.chmod(0o755)
        print(f"    llama-completion: {comp}")
        return comp
    print("    ! llama-completion assente dall'archivio (release vecchia?): "
          "il describe deterministico usera' il fallback HTTP "
          "(meta.deterministic=false).")
    return None


def acquire_llama(backend: str, dest: Path) -> Path | None:
    """Scarica un binario prebuilt llama.cpp per il backend ed estrae llama-server.

    Prebuilt da github ggml-org/llama.cpp latest release. Ritorna il path del
    binario llama-server, o None (fallback: build da sorgente, fuori scope qui).
    """
    existing = _find_llama_server(dest)
    if existing:
        print(f"    llama-server già presente: {existing}")
        _ensure_completion_bin(dest)   # idempotente: chmod su re-run
        return existing
    # Pin del tag release (riproducibilità + verificabilità). Default = tag
    # validato (_LLAMA_TAG_DEFAULT); override `METNOS_LLAMA_TAG=<bNNNN>`;
    # opt-out esplicito `=latest` (build mobile, avviso onesto §2.8).
    tag = os.environ.get("METNOS_LLAMA_TAG", "").strip() or _LLAMA_TAG_DEFAULT
    if tag.lower() == "latest":
        api = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
        print("    ! release NON pinnata (latest, opt-out esplicito): "
              "build non riproducibile.")
    else:
        api = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"
        _src = "env" if os.environ.get("METNOS_LLAMA_TAG", "").strip() else "default"
        print(f"    release pinnata: {tag} ({_src})")
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
        # Stesso archivio → llama-completion (describe deterministico)
        # allineato di versione col server per costruzione.
        _ensure_completion_bin(dest)
    return binp


def download_model(hf_repo: str, hf_file: str, dest: Path,
                   revision: str = "main") -> bool:
    """Scarica un GGUF da HuggingFace con VERIFICA SHA256 (dall'API HF). Idempotente.

    `revision` = commit-sha HF (file IMMUTABILE, build riproducibile) o `"main"`
    (ref MOBILE → avviso onesto §2.8: una ri-pubblicazione upstream cambia il
    file e l'hash atteso slitta con esso)."""
    exp = _hf_expected_sha256(hf_repo, hf_file, revision=revision)
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        # Verifica-on-reuse per hash, non per dimensione (L5): un file corrotto
        # della stessa dimensione NON deve essere accettato.
        if exp and _sha256_file(dest).lower() != exp.lower():
            print(f"    modello esistente con hash errato → ri-scarico")
            dest.unlink(missing_ok=True)
        else:
            print(f"    modello già presente{' (sha verificato)' if exp else ''}: {dest}")
            return True
    if revision in (None, "", "main"):
        print("    ! GGUF NON pinnato (revision=main): ref mobile, build non "
              "riproducibile (pin hf_revision al commit-sha).")
    _rev = revision if revision not in (None, "") else "main"
    url = f"https://huggingface.co/{hf_repo}/resolve/{_rev}/{hf_file}?download=true"
    print(f"    scarico {hf_repo}/{hf_file} @ {_rev[:12]} …")
    return _download(url, dest, attempts=6, expected_sha256=exp)


def _write_systemd_unit(llama_bin: Path, model_file: Path, endpoint: str,
                        ngl: int, unit_path: Path) -> None:
    host = "127.0.0.1"
    port = endpoint.rsplit(":", 1)[-1] if ":" in endpoint else "8080"
    unit = f"""[Unit]
Description=Metnos local LLM (llama-server)
After=network-online.target
Before=metnos-stack-ready.service
PartOf=metnos.target

[Service]
ExecStart={llama_bin} -m {model_file} --host {host} --port {port} -ngl {ngl} -c 8192
Restart=on-failure
Nice=5

[Install]
WantedBy=metnos.target
"""
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit, encoding="utf-8")


def _user_unit_dir() -> Path:
    """Directory delle systemd USER unit (stessa di phase5: no sudo)."""
    return Path.home() / ".config" / "systemd" / "user"


def _endpoint_health(endpoint: str, *, timeout_s: float = 3.0) -> bool:
    """200 su <endpoint>/health (urllib, nessuna dipendenza extra)."""
    try:
        with _ur.urlopen(f"{endpoint.rstrip('/')}/health",
                         timeout=timeout_s) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def install_user_unit(unit_src: Path, *, endpoint: str,
                      wait_s: int = 180) -> dict:
    """Installa+abilita+avvia metnos-llm.service come USER unit (no sudo,
    coerente con phase5). Flag E2E 12/6/2026: prima l'unit veniva solo
    SCRITTA e il 1° turno falliva finche' l'utente non avviava il server
    a mano. Esiti onesti §2.8: ogni campo riflette cio' che e' successo.
    Se l'endpoint risponde GIA', non avvia un secondo server (il bind
    fallirebbe): l'unit resta installata per i prossimi boot.
    Ritorna {installed, enabled, started, healthy, reason}."""
    out = {"installed": False, "enabled": False, "started": False,
           "healthy": False, "reason": ""}
    if not shutil.which("systemctl"):
        out["reason"] = ("systemctl assente (no systemd): avvia il server a "
                         f"mano (ExecStart in {unit_src})")
        return out
    dest = _user_unit_dir() / unit_src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(unit_src, dest)
    out["installed"] = True
    if _endpoint_health(endpoint):
        out["healthy"] = True
        out["reason"] = (f"endpoint gia' attivo su {endpoint}: non avvio un "
                         "secondo server (unit installata per i prossimi boot)")
        return out
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, text=True, timeout=30)
        r = subprocess.run(
            ["systemctl", "--user", "enable", "--now", dest.name],
            capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as e:
        out["reason"] = f"systemctl --user fallito: {e}"
        return out
    if r.returncode != 0:
        out["reason"] = ("enable --now fallito: "
                         + (r.stderr or "").strip()[:200])
        return out
    out["enabled"] = True
    out["started"] = True
    # Attesa onesta del caricamento modello (GGUF da GB: puo' volerci tempo).
    import time as _t
    deadline = _t.time() + max(0, wait_s)
    while _t.time() < deadline:
        if _endpoint_health(endpoint):
            out["healthy"] = True
            return out
        _t.sleep(3)
    out["reason"] = (f"servizio avviato ma {endpoint}/health non risponde "
                     f"entro {wait_s}s (modello in caricamento? "
                     "`systemctl --user status metnos-llm`)")
    return out


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
    _ltag = os.environ.get("METNOS_LLAMA_TAG", "").strip() or _LLAMA_TAG_DEFAULT
    emit(f"Procurare llama-server per backend '{plan.backend}' in {llama} "
         f"(prebuilt ggml-org/llama.cpp release {_ltag}; fallback: build con cmake)")
    # 2) modello GGUF
    _pin = ("pin " + plan.hf_revision[:12]
            if plan.hf_revision not in (None, "", "main") else "main MOBILE")
    emit(f"Scaricare {plan.hf_repo}/{plan.hf_file} (~{_q4_gb(plan)} GB, {_pin}) "
         f"in {model_file}  [huggingface]")
    # 3) tiers config
    emit(f"Scrivere {tiers} (tier fast/middle/wise → llamacpp {model_file.name} "
         f"@ {plan.endpoint})")
    # 4) servizio
    emit(f"Installare+abilitare+avviare llama-server (systemd USER unit "
         f"metnos-llm, no sudo) su {plan.endpoint}")
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
    comp = find_completion_bin()
    out["llama_completion"] = str(comp) if comp else None
    if not binp:
        print("  ✗ llama-server non acquisito (fallback build da sorgente, "
              "fuori scope). Mi fermo prima del modello.")
        out["ok"] = False
        return out
    print(f"  ✓ {binp}")

    # 2) modello GGUF
    print("[2/5] modello")
    if not download_model(plan.hf_repo, plan.hf_file, model_file,
                          revision=plan.hf_revision):
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

    # 4) systemd USER unit: installata+abilitata+AVVIATA (managed install =
    #    server in esercizio al 1° turno, non solo file scritto).
    print("[4/5] servizio (systemd user unit)")
    ngl = 0 if plan.backend == "cpu" else 999
    unit = llama.parent / "metnos-llm.service"
    _write_systemd_unit(binp, model_file, plan.endpoint, ngl, unit)
    out["systemd_unit"] = str(unit)
    wait_s = int(os.environ.get("METNOS_LLM_START_TIMEOUT_S", "180"))
    svc = install_user_unit(unit, endpoint=plan.endpoint, wait_s=wait_s)
    out["service"] = svc
    if svc["healthy"]:
        print(f"  ✓ llama-server in salute su {plan.endpoint}"
              + (f" ({svc['reason']})" if svc["reason"] else ""))
    else:
        print(f"  ! servizio NON in salute: {svc['reason']}")
        print("    riprova: systemctl --user enable --now metnos-llm")

    # 5) verifica
    print("[5/5] verifica")
    if svc["healthy"]:
        out["health"] = True
        print("  ✓ /health risponde sull'endpoint dei tier")
    else:
        # Fallback: il MECCANISMO viene comunque verificato con un processo
        # breve su porta di prova CPU (non contende la GPU); esito onesto,
        # distinto dallo stato del servizio (out["service"]).
        print("  health-check di meccanismo (CPU, porta 8084)")
        healthy = health_check(binp, model_file, port=8084, ngl=0,
                               timeout_s=90)
        out["health"] = healthy
        print("  ✓ llama-server risponde a /health (porta di prova)" if healthy
              else "  ! health-check non superato in tempo (modello grande su "
                   "CPU? il servizio reale usa la GPU).")
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
            plan.hf_revision = "main"   # repo diverso dal canonico: niente pin ereditato
            plan.backend = "cpu"
            plan.feasible = True
            plan.warnings.append("MODALITÀ TEST: modello tiny su CPU.")
        res = provision(plan, dry_run=args.dry_run or not args.yes,
                        assume_yes=args.yes)
        return 0 if res.get("feasible", False) else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
