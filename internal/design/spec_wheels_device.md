# SPEC IMPLEMENTATIVA — Dipendenze native sui device remoti (wheels + binari)

> **Destinatario**: LLM esecutore. File:riga verificati (client Rust + server Python), criteri di done, test, rischi.
> **Obiettivo**: far girare sui device remoti gli executor che oggi falliscono per dipendenze native. **Target minimo-rischio: `read_files_xlsx` (openpyxl, pure-python).** OCR e spreadsheet-family sono casi separati e più complessi (§4).
> **Autore analisi**: Fable, 6/7/2026.

---

## 0. Il quadro (fatti verificati)

**Il canale wheel è GIÀ COSTRUITO lato server, manca solo il consumatore client + rimozione di un gate.**

- **Server = proxy PyPI completo** `runtime/agent_mirror.py`: `/agent/pypi/simple/{pkg}/` (indice PEP 503, `:110`,`:335`), `/agent/pypi/files/{pkg}/{file}` (wheel lazy da PyPI, **sha256-verificato**, single-flight, `:195`,`:254`,`:268`,`:336`), `/agent/runtime/{file}` (tarball, `:285`,`:337`). Wired: `agent_server.py:803`.
- **Client = NESSUN consumatore**: `pyenv.rs` non chiama mai `/agent/pypi/*`. Un **GATE DURO** blocca: `client-rs/src/pyenv.rs:420` `assert_stdlib_only()` legge `manifest.dependencies`; se non vuoto → `Err("executor con dipendenze non-stdlib: venv/uv non ancora cablato (W5)")`. Chiamato in `runner.rs:251` PRIMA di shim/python. Oggi nessun manifest dichiara `[dependencies]`, quindi il gate è inerte — ma è già lì.
- **Download robusto pure-Rust** riusabile: `pyenv.rs:120-403` (chunk-Range + estrazione tar.gz, per il runtime Python). openpyxl è `py3-none-any` (pure-python) → basta **unzip della wheel**, nessun pip/uv.

**openpyxl è il target facile** (pure-python, ~250KB, nessun ABI). **Pillow/OCR NO** (§4).

---

## 1. STRATEGIA — 3 tipi di dipendenza, 3 trattamenti diversi

| Tipo | Esempio | Executor | Trattamento |
|---|---|---|---|
| **wheel pure-python** | openpyxl, et-xmlfile | `read_files_xlsx` | canale `/agent/pypi` + unzip (§2). **Questo è lo scope minimo.** |
| **wheel compilata (ABI)** | Pillow (`cp3XX-win_amd64`) | (immagini) | serve wheel per l'ABI esatto della CPython pinnata → più fragile, fuori scope minimo |
| **binario di sistema** | tesseract.exe, pdftoppm (poppler) | `read_files_ocr` | **NON è una wheel**: `read_files_ocr.py:37,51` fa shell-out al binario. Serve canale binari separato o pacchettizzazione — fuori scope (§4) |

**Fai SOLO il tipo 1 in questa spec.** Gli altri due sono note di rischio, non lavoro.

---

## 2. IMPLEMENTAZIONE — wheel pure-python per read_files_xlsx

### Passo 2.1 — Server: parsing manifest `[dependencies]`
Il loader NON parsa `dependencies` oggi (`runtime/loader.py` ~`:1229` parsa platforms/placement). Aggiungere:
- `Executor.dependencies: list[str] = field(default_factory=list)` nella dataclass.
- Parsing da `manifest.toml` sezione `[dependencies]` (lista `packages = ["openpyxl==3.1.5", "et-xmlfile==2.0.0"]`).
- **Done**: `loader.load_catalog()` → un executor con `[dependencies]` nel manifest espone `.dependencies` non vuoto.

### Passo 2.2 — Server: firmare il set dipendenze (ancora di fiducia)
Decisione di sicurezza: le wheel del mirror sono solo sha256-verificate contro PyPI (`agent_mirror.py:268`), NON firmate dal server. Lo shim invece è firmato con pubkey pinnata. **Per coerenza col modello di fiducia**, aggiungere un endpoint `/agent/deps/{executor}` che ritorna `{packages:[{name, version, sha256, url_path}], sig}` firmato con `invocations.sign_payload` (come `shim_bundle`, `agent_server.py:374`). Gli sha256 vengono dall'indice PyPI proxato. Così il client verifica la firma server (autenticità) + lo sha256 (integrità).

### Passo 2.3 — Client: provisioner wheel (sostituisce il gate)
In `client-rs/src/`, nuovo modulo `deps.rs` con `provision_deps(server, pubkey, deps, cache_root) -> Result<PathBuf>`:
1. GET `/agent/deps/{executor}`, verifica firma con pubkey pinnata (come `ensure_shim`, `executors.rs:170-177`).
2. Per ogni package: scarica la wheel da `/agent/pypi/files/...` (riusa `fetch_robust` di `pyenv.rs`), verifica sha256.
3. **Unzip** la wheel (è uno zip) in `<cache>/site/<hash-deps>/` (una dir cache-ata per set-di-deps, NON sotto lo scratch per-invocazione che viene cancellato). openpyxl pure-python → l'unzip basta, nessun pip.
4. Ritorna il path `site/<hash>`.

In `runner.rs:251`: sostituire `assert_stdlib_only()` con: se `manifest.dependencies` non vuoto → `deps::provision_deps(...)` → ottieni `site_dir`; passalo a `run_sandboxed`.

### Passo 2.4 — Client: mount additivo del site-packages
In `client-rs/src/sandbox_windows.rs:197` (e gemello `sandbox_linux.rs:87`): la composizione `pythonpath` deve **APPENDERE** `site_dir` al pythonpath calcolato (shim + exec.dir + **site_dir**). ATTENZIONE: NON via `extra_env` (`sandbox_windows.rs:229` usa `cmd.env` che SOVRASCRIVE → clobbererebbe shim). Passare `site_dir: Option<&Path>` a `run_sandboxed` e concatenarlo nella riga `:197`.

### Passo 2.5 — Manifest read_files_xlsx
`executors/read_files_xlsx/manifest.toml`: aggiungere `platforms = ["linux","windows"]`, `[dependencies] packages=["openpyxl==3.1.5","et-xmlfile==2.0.0"]`, `[placement] scope="any" device_ok=true`. **Re-sign §7.10.**

---

## 3. DONE + TEST

1. **Unit Rust** (`deps.rs` test): `provision_deps` con un mock server → scarica+unzip → il site_dir contiene `openpyxl/__init__.py`.
2. **e2e** `scripts/c7-validate-xlsx-remote.sh` (pattern di `c7-validate-mutants-remote.sh`): server isolato con mirror wheel locale + client Rust reale → `read_files_xlsx` su un `.xlsx` di test → entries col contenuto letto (openpyxl ha girato sul device).
3. **Client version bump** + build/mirror (`scripts/build-client.sh`). Rollout via self-update ADR 0184.
4. **Windows reale**: quando il PC-ROBERTO è online, `read_files_xlsx` su un `.xlsx` reale via chat.

---

## 4. FUORI SCOPE (note di rischio, NON lavoro di questa spec)

- **OCR**: `read_files_ocr` richiede tesseract.exe + poppler (pdftoppm) — BINARI, non wheel. `/agent/pypi` non li copre. Servirebbe un canale binari separato o pacchettizzarli nell'installer. **Non fare qui.**
- **Spreadsheet-family** (`create/read/write_files_spreadsheet`): hanno import EAGER di `google_workspace` (`create_files_spreadsheet.py:32` → `skill_wrapper`) NON nel bundle → falliscono a IMPORT sul device PRIMA di qualsiasi wheel. Prerequisito: rendere lazy `from backends.files import google_workspace` (stesso fix già fatto per find/read/write_files il 5/7). Solo DOPO quel fix + il canale wheel funzionerebbero. `read_files_xlsx` è esente (import openpyxl diretto) = target pulito.
- **Pillow (compilata)**: wheel `cp3XX-win_amd64` deve combaciare con la CPython pinnata (`agent_server.py:618`). Fragile. Solo OCR/immagini la userebbero.
- **AppContainer/sandbox fs** (W4): su Windows nessun isolamento fs ancora. La dir site-packages sotto `cache_dir` è persistente (corretto), NON sotto lo scratch cancellato.

**Valutazione onesta**: lo scope minimo (openpyxl per `read_files_xlsx`) è ~1 giornata (deps.rs + endpoint firmato + mount + e2e). Ma il VALORE è basso: quante volte l'utente vuole leggere un .xlsx che sta SOLO sul PC e non sul server? Il server fa già xlsx. Il caso reale forte è l'OCR di documenti locali — che però è il ramo più complesso (binari). **Raccomando di NON iniziare da qui** salvo un bisogno utente concreto e ricorrente.
