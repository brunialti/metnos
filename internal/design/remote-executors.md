# Executor remoti — progettazione di dettaglio (implementabile)

> Documento **interno** di progettazione. Consolida le decisioni sparse negli
> ADR 0007/0011/0034/0037/0046 e le rende ESEGUIBILI: chi implementa (un
> agente coding frontier — Opus 4.8, Claude Code — o un umano) deve poter
> partire da qui senza ricostruire il contesto dagli ADR.
>
> Convenzione: prosa in italiano (§1 CLAUDE.md), identificatori di
> protocollo/codice/tipi in inglese verbatim (è ciò che serve a chi
> implementa, §7.8). Stato al 2/7/2026.
>
> **Legenda stato**: ✅ costruito e in repo · 🟡 scaffold parziale · ⛔ da
> costruire (questo doc ne è la specifica).

---

## 0. Riepilogo esecutivo

Metnos gira su `.33` (server unico, sorgente unica di policy). Alcune
richieste utente riguardano un ALTRO dispositivo — «installa VLC sul mio
portatile», «quali file ci sono su questo PC Windows» — e non sono
soddisfabili da `.33`. La soluzione è un'architettura **asimmetrica
client/server** (ADR 0011): il server emette invocazioni firmate verso un
piccolo daemon (`metnos-client`) sul dispositivo bersaglio, che le esegue
nel sandbox più forte che la piattaforma offre e restituisce il risultato.
**Il client non ha MAI iniziativa**: esegue, traccia, risponde.

Obiettivo di questo documento: definire (a) come si installa il client «al
volo» dalla UI su Linux e Windows, (b) il protocollo di filo fra core ed
executor locali, (c) linguaggi, identità, sandbox, distribuzione, modalità
di fallimento — al livello di dettaglio di firme di funzione e layout file.

**Cosa esiste già** (`git grep`): il server di pairing `agent_server.py`
(porta 8765, `/agent/health` + `/agent/register`) ✅; il mirror PEP 503
`agent_mirror.py` ✅; il registro dispositivi `devices.py` (Ed25519 token +
fingerprint) ✅; lo scaffold del client Rust `client-rs/` (comandi
`whoami`/`register` ✅, `run` che ancora fa `bail!("not wired")` 🟡).
**Il buco** = il loop di esecuzione (poll/execute), il protocollo di
invocazione, il sandbox, e il flusso install-at-the-fly. Questo doc li
specifica.

---

## 1. Architettura (ADR 0011, corpo tecnico)

```
        ┌──────────────────────────  .33 (metnos-server, Python) ──────────┐
        │  gateway · vaglio · mnestoma · telos · audit · policy UNICA      │
        │  agent_server (:8765)   ── pairing, register, poll, execute      │
        │  agent_mirror (:8765)   ── PEP 503 wheels + python-build-std     │
        │  build-client.sh        ── cross-build + firma Ed25519 binari    │
        └───────────────┬──────────────────────────────────────────────────┘
                        │  Headscale overlay (mTLS, outbound-only dal client)
                        │  ADR 0007: il control plane NON dialora nel client
        ┌───────────────▼──────────────  dispositivo bersaglio ────────────┐
        │  metnos-client (Rust launcher, 5-10 MB)                          │
        │   ├─ Ed25519 device key (mai lascia il disco)                    │
        │   ├─ python-build-standalone (scaricato lazy, read-only cache)   │
        │   ├─ uv (subprocess: venv + wheels dal mirror)                   │
        │   ├─ executor cache (manifest+.py firmati, verificati)           │
        │   └─ sandbox per-OS (bwrap/landlock/seccomp | Job Object/AppCtr) │
        └──────────────────────────────────────────────────────────────────┘
```

**Invarianti di sicurezza** (non negoziabili, ADR 0011):

1. **Policy centralizzata.** Vaglio, telos-matching, consent-gate, accesso
   mnestoma vivono SOLO su `.33`. Distribuire la policy = sistema
   multi-testa che può dissentire con sé stesso. Il client è subordinato.
2. **Client senza iniziativa.** Nessun comando è generato lato client.
   Elimina un'intera classe di attacchi (client compromesso che si
   auto-comanda).
3. **Connettività outbound-only.** Il client long-polla `.33` dentro
   l'overlay Headscale; `.33` non compone MAI verso la rete del client.
4. **Token per-invocazione.** mTLS + token firmato per chiamata: un token
   trafugato non concede accesso continuo.
5. **Segreti solo-server.** Sul dispositivo vivono SOLO: la chiave Ed25519,
   la cache read-only di python-build-standalone, la cache wheel
   hash-keyed, lo spool. Niente SQLite locale, niente stato applicativo,
   niente secret OAuth (iniettati nell'env del subprocess a execution-time
   via mTLS, mai persistiti). **Dispositivo rubato ≠ segreti trafugati.**

---

## 2. Componenti e linguaggi (ADR 0046)

| Componente | Linguaggio | Dove | Stato |
|---|---|---|---|
| `metnos-server` / `agent_server` | Python 3.12 (aiohttp bare) | `.33` | ✅ pairing; ⛔ poll/execute |
| `agent_mirror` (PEP 503 + runtime) | Python | `.33` | ✅ |
| `metnos-client` launcher | **Rust** (5-10 MB, static-link §ADR 0037) | dispositivo | 🟡 scaffold |
| Runtime Python sul client | **python-build-standalone** (Astral), scaricato lazy | dispositivo | ⛔ |
| Gestione venv + wheel | **uv** come subprocess | dispositivo | ⛔ |
| Executor | **Python** (`verb_object`, manifest+`.py` firmati) | dispositivo | ⛔ (formato = quello di `.33`) |
| `build-client.sh` | bash + cross-toolchain su `.33` | `.33` | ⛔ (pattern = `deploy.sh`) |

**Perché Rust + python-build-standalone + uv** (ADR 0046, esaustivo): la
tensione è «binario piccolo che però esegue Python». Alternative scartate,
con motivo: PyOxidizer (congelato, autore lo dichiara «forse morto»);
RustPython (non carica wheel CPython: numpy/cryptography/pydantic-v2/lxml →
frammenta il pool executor); PyO3+libpython statico (cross-compile Windows
doloroso, fragilità ABI, perde il «client piccolo»); PyInstaller/Nuitka/
shiv/pex (60-100 MB per OS, tradiscono lazy+incrementale); WASM/wasi-python
(ecosistema wheel-WASI embrionale, ri-valutare 2027+). Il Rust launcher
scarica python-build-standalone al primo uso e usa uv per venv/wheel.
Verificato E2E il 27/4: PC Windows di Roberto contro `.33`, `.exe` 2.5 MB
static-linked (solo DLL stock), mirror server-side operativo, cerimonia
`RegisterDevice` reale riuscita, zero installazioni esotiche sul PC.

**uv come subprocess per l'MVP** (option A vs riscrittura Rust): il mirror
sposta il costo-beneficio verso A (uv accetta `UV_INDEX_URL` custom).
Costo: due binari distribuiti (~7 MB client + ~16 MB uv compresso). Una
riscrittura Rust mirata (~500-1000 righe) è rimandata a v2 quando il
superset di wheel per i 27 seed si stabilizza.

---

## 3. Ciclo di vita end-to-end (dalla UI al primo executor)

```
[UI admin /admin/devices]  →  «Aggiungi dispositivo»
   │ 1. server genera token effimero (TTL 10 min) + one-liner install
   ▼
[dispositivo]  esegue il one-liner (§5)
   │ 2. scarica metnos-client firmato dal mirror
   │ 3. client genera Ed25519 locale (chiave privata mai esce)
   │ 4. RegisterDevice(token, public_key)  →  device_id + fingerprint
   ▼
[server]  device compare in /admin/devices come «paired», profilo hardware noto
   │ 5. client entra nel loop:  poll → (execute) → result → heartbeat
   ▼
[turno utente]  «installa git sul portatile»
   │ 6. planner .33 sceglie find_packages/pkg_install; placement (§10) → device
   │ 7. server firma l'invocazione, la mette in coda per quel device_id
   │ 8. client la preleva al poll, verifica firma+manifest, esegue in sandbox
   │ 9. result firmato torna a .33 → mnestoma/audit → risposta utente
```

Il primo executor remoto reale (fase 5+) NON si scrive prima che pairing,
sandbox e questo protocollo siano fissati (ADR 0011). Questo doc è quel
fissaggio.

---

## 4. Definizioni (glossario del sottosistema)

| Termine | Definizione |
|---|---|
| **device** | Un computer bersaglio appaiato. Identità = Ed25519 pubkey; `device_id` interno numerico; `device_name` scelto al pairing (`/pair-device laptop-windows`). |
| **pairing** | Cerimonia firmata da un canale già fidato (DM Telegram o UI admin). Senza pairing valido il client non riceve token. |
| **device token** | Token effimero (TTL 10 min) consumabile UNA volta per `RegisterDevice`. Dopo, l'auth è mTLS + token per-invocazione. |
| **invocation** | Un executor + args, firmato dal server, indirizzato a un `device_id`. Idempotente per `invocation_id`. |
| **executor** | Come su `.33`: `verb_object`, manifest TOML + `<name>.py` firmato. Sul device gira nel sandbox. |
| **manifest scope** | Campo `scope: server \| device \| any` + `targets: [filesystem\|network\|hardware\|...]` + `class: net\|cpu\|io_fs\|mem\|mixed\|llm_local`. Guida il placement (§10). |
| **heartbeat** | POST periodico device→server (< 60s) con profilo carico. Gate di disponibilità per il placement. |
| **spool** | Buffer locale per log/telemetria/result di esecuzioni in-flight quando `.33` è giù. NON una coda persistente di nuove azioni (quella è fase-7). |

---

## 5. Install-at-the-fly dalla UI (il cuore della richiesta)

### 5.1 Principio

Un file scaricabile ed eseguibile, ZERO prerequisiti esotici sul target
(niente mingw/VC++ Redist/lib — ADR 0037, static-link). Il one-liner
scarica il client firmato dal mirror di `.33`, lo rende eseguibile, e lancia
`register` col token effimero. Tutto il resto (runtime Python, wheel) è
lazy.

### 5.2 UI admin — nuovo endpoint ⛔

`/admin/devices` (HTML htmx, coerente con §14 CLAUDE.md). Bottone «Aggiungi
dispositivo» → `POST /admin/devices/token` che:

1. chiama `devices.generate_token(name, owner_user_id, ttl=600)` ✅ (esiste);
2. rende DUE one-liner (Linux, Windows) con token + URL server inline;
3. mostra un countdown 10 min + QR opzionale.

La lista mostra i device appaiati (`devices.list_devices()` ✅), stato
heartbeat, profilo hardware, e un bottone Revoca (`devices.revoke_device`
✅).

### 5.3 One-liner Linux ⛔

```sh
# reso dalla UI, token e host già sostituiti
curl -fsSL https://metnos.com/agent/client/install.sh | \
  METNOS_SERVER=https://<headscale-ip>:8765 \
  METNOS_TOKEN=<token-effimero> sh
```

`install.sh` (servito da `agent_mirror`, ~40 righe, POSIX sh):
- rileva `uname -m` → sceglie il target (`x86_64-unknown-linux-musl`,
  `aarch64-unknown-linux-musl`);
- `curl` del binario firmato da `/agent/client/<version>/<target>/metnos-client`;
- **verifica la firma Ed25519** del binario contro la pubkey del server
  (pinnata nello script — l'unico segreto che lo script porta);
- `install -m755` in `~/.local/bin/metnos-client`;
- `metnos-client register --server "$METNOS_SERVER" --token "$METNOS_TOKEN"`;
- installa la user-unit systemd `metnos-client.service`
  (`systemctl --user enable --now`), linger ON.

Razionale robustezza download: `robust_fetch` adattivo (single-fetch, poi
CONSENSUS doppio-fetch se lo sha non torna) — l'ISP/proxy può corrompere il
CONTENUTO non-deterministicamente sotto TLS (stesso problema visto per
embedder/LLM). Riusare l'helper già scritto in `install/sidecar.py`.

### 5.4 One-liner Windows ⛔

PowerShell (l'utente incolla in un terminale non-admin):

```powershell
$env:METNOS_SERVER='https://<headscale-ip>:8765'; $env:METNOS_TOKEN='<token>'; `
iwr https://metnos.com/agent/client/install.ps1 -UseBasicParsing | iex
```

`install.ps1`:
- scarica `metnos-client.exe` (`x86_64-pc-windows-gnu`, static-link
  libgcc/libstdc++/winpthread — ADR 0037, il `.exe` 2.5 MB già validato);
- verifica firma Ed25519;
- copia in `%LOCALAPPDATA%\Metnos\metnos-client.exe`;
- `register`;
- registra un **Windows Service** (auto-start, account utente) via
  `sc.exe create` o, meglio, `metnos-client.exe install-service` (il client
  si auto-registra — evita privilegi admin dove possibile; fallback:
  Scheduled Task «At logon» se il Service richiede elevazione).

Nota firma-codice: per l'MVP la fiducia viene dalla firma Ed25519 del
server (verificata dallo script) + mTLS, NON da Authenticode. Azure
Artifact Signing ($9.99/mese) è W6+ opzionale se SmartScreen infastidisce.

### 5.5 Auto-update ⛔

Il client, al poll, riceve `server_client_version`. Se maggiore della
propria, `self_update`: scarica il nuovo binario firmato, verifica, scrive
accanto, riavvia la unit (Linux) / il Service (Windows). Server-signed,
mai auto-generato lato client.

---

## 6. Protocollo core↔executor (wire format)

**Trasporto**: HTTPS dentro l'overlay Headscale, mTLS. Per l'MVP il bind è
`127.0.0.1` senza TLS (già così in `agent_server.py`), TLS self-signed
pin-by-fingerprint in fase successiva. Stile aiohttp bare uniforme (§14),
JSON, envelope `_error()`.

### 6.1 Endpoint server (`agent_server`, :8765)

| Metodo | Path | Stato | Scopo |
|---|---|---|---|
| GET | `/agent/health` | ✅ | liveness |
| POST | `/agent/register` | ✅ | consuma token → `device_id` |
| POST | `/agent/poll` | ⛔ | long-poll: il client chiede la prossima invocazione |
| POST | `/agent/result` | ⛔ | il client restituisce il risultato di un'invocazione |
| POST | `/agent/heartbeat` | ⛔ | profilo carico + liveness device |
| GET | `/agent/pypi/simple/{pkg}/` | ✅ | mirror PEP 503 index |
| GET | `/agent/pypi/files/{pkg}/{file}` | ✅ | wheel file |
| GET | `/agent/runtime/cpython-...` | ✅(mirror) | python-build-standalone |
| GET | `/agent/client/{ver}/{target}/...` | ⛔ | binario client firmato + install.sh/ps1 |
| GET | `/agent/executor/{name}` | ⛔ | manifest+.py firmati (pull su cache-miss) |

### 6.2 `/agent/poll` — long-poll (client → server)

Richiesta (firmata dal device con la sua Ed25519):
```json
{ "device_id": "d-7a3f", "cursor": "<last-invocation-id-or-null>",
  "capabilities": ["fs","net","pkg"], "block_ms": 25000 }
```
Risposta 200 — o `{"invocation": null}` allo scadere del block (il client
ri-polla), o UNA invocazione firmata dal server:
```json
{ "invocation": {
    "invocation_id": "inv-01J...", "turn_id": "t-...",
    "executor": "find_packages",
    "manifest_sha256": "9f...", "code_sha256": "3c...",
    "args": { "names": ["git"] },
    "scope": "device", "reversibility": "read_only",
    "env_injections": { "GITHUB_TOKEN": "..." },   // solo a execute-time, mai su disco
    "deadline_ms": 60000,
    "server_sig": "<ed25519(server, canonical(invocation))>" } }
```
Il client VERIFICA `server_sig` con la pubkey del server pinnata; se non
verifica, RIFIUTA e logga (attacco/replay). `invocation_id` monotòno =
idempotenza: un'invocazione già eseguita non si ri-esegue (dedup locale in
memoria + `cursor`).

### 6.3 `/agent/result` (client → server)

```json
{ "invocation_id": "inv-01J...", "device_id": "d-7a3f",
  "ok": true, "entries": [...],            // stessa shape §2.6 di .33
  "n_processed": 1, "elapsed_ms": 812,
  "sandbox": "bwrap+landlock", "audit_line": "...",
  "device_sig": "<ed25519(device, canonical(result))>" }
```
Il server verifica `device_sig`, correla per `invocation_id` col leg
server-side, scrive la riga audit device-side (correlata per `trace_id`),
e consegna gli `entries`/`results` al runtime che attende. **Undo onesto
§2.8**: se il device ha eseguito un mutante, il result porta i campi per il
reverse_pattern (blob_path/sha nel caso restore).

### 6.4 Idempotenza & doppia esecuzione

Gemello della guardia `.33` (2/7/2026, `committed_mutations`): un
`invocation_id` con side-effect già committato NON si ri-esegue mai. Il
client tiene un set `done_invocations` in memoria + spool; il server non
ri-mette in coda un `invocation_id` per cui ha già ricevuto un `/agent/result`.

---

## 7. Identità, pairing, revoca (ADR 0046 §3, `devices.py` ✅)

- **Genesi chiave**: al primo `whoami`/`register` il client genera Ed25519
  LOCALE. Privata mai fuori dal device. Storage MVP:
  `%LOCALAPPDATA%\metnos\key` (Windows), `~/.local/share/metnos/key`
  (Linux). v2: TPM / Secure Enclave / keyring.
- **Pairing**: token effimero (`devices.generate_token`, TTL 10 min) +
  `RegisterDevice(token, public_key)` (`devices.consume_token` ✅, atomico
  SQLite UNIQUE — a prova di doppia invocazione). Challenge-response è
  gratis dall'handshake mTLS.
- **Naming multi-device**: nome scelto al pairing (`/pair-device
  laptop-windows`); `device_id` numerico interno; `/rename-device old new`.
- **Revoca**: un comando DM/UI rimuove il device dalla lista appaiata
  (`devices.revoke_device` ✅); token futuri rifiutati. Unpair esplicito =
  fase-7 (deferito).
- **Fingerprint**: `devices.fingerprint_of(pubkey)` ✅ (sha256), mostrato
  nei log e nella UI.

---

## 8. Distribuzione: mirror, runtime, executor (ADR 0046 §1)

- **Wheel**: mirror PEP 503 server-side da giorno 1 (NON PyPI diretto).
  `agent_mirror.py` ✅ già serve `/agent/pypi/simple/{pkg}/` +
  `/agent/pypi/files/...`. Cache hash-keyed in `/var/lib/metnos/wheel-cache/`
  (no eviction in MVP). Verifica hash a DUE livelli: server (a cache-time) +
  client (a install-time). Il pinning hash nei manifest executor resta
  obbligatorio. Perché: supply chain controllata, audit unificato, funziona
  in reti aziendali che bloccano `pypi.org`, secondo device che chiede lo
  stesso wheel lo ha istantaneo dalla cache.
- **Runtime**: python-build-standalone, minor pin (`3.13.x`), auto-bump
  patch in CI. Scaricato lazy al primo executor, cache read-only.
- **Executor cache (push vs pull)** ⛔: **pull-on-miss**. L'invocazione porta
  `manifest_sha256`+`code_sha256`; se il client non li ha in cache, li tira
  da `/agent/executor/{name}`, verifica le firme (`loader.verify_executor`
  gemello di `.33`), poi esegue. Cache hash-keyed, immutabile.
- **Build & distribuzione del client**: tutto su `.33`, NIENTE GitHub
  Actions (ADR 0046). `scripts/build-client.sh <version>` ⛔ cross-builda per
  ogni target, firma con la chiave Ed25519 del server, copia in
  `/var/lib/metnos/client/<version>/<target>/`, aggiorna il manifest. Stesso
  pattern di `deploy.sh`. macOS tier-2 (build manuale su un Mac al bisogno).

---

## 9. Sandbox per-OS (ADR 0011, asimmetrico per piattaforma) ⛔

La forza del sandbox è asimmetrica; il sandbox debole si compensa con
verifiche extra.

| OS | Sandbox | Forza | Compensazione |
|---|---|---|---|
| Linux | bubblewrap + landlock + seccomp + namespace mount | forte (parità `.33`) | — |
| macOS | sandbox-exec + entitlements | media | — |
| Windows | AppContainer + Job Object + NTFS ACL | debole | doppia verifica profilo (firma server + check client pre-op), reverse_pattern before/after OBBLIGATORIO, idempotenza richiesta, doppio audit |

Contratto sandbox (Rust, per ogni OS un modulo `sandbox_<os>.rs`):
```
fn run_sandboxed(exec: &Executor, args: &Value, limits: &Limits) -> Result<Output>
// limits: cpu_ms, mem_bytes, wall_ms, fs_read[], fs_write[], net_allow[]
```
I `fs_read/fs_write/net_allow` derivano dalle `capabilities` del manifest
(§11) — MAI più larghi di quanto il manifest dichiara. Riusa la disciplina
`platform_policy.protected_paths()` di `.33` (2/7: `vaglio.guard_check`
blocca i mutanti sugli alberi di sistema) — il client porta una COPIA di
quella politica (replicata in Rust; il server non assume nulla sull'OS del
client).

---

## 10. Placement — dove gira un executor (ADR 0034) ✅ deciso, ⛔ da wirare

Tre livelli, valutati in ordine; il primo che decide vince. KIS: tabella
lineare 4×6, niente ML.

- **L1 affinità assoluta** (deterministico, mai scavalcato):
  1a target su risorsa locale di un device → quel device (manifest
  `targets`); 1b `scope: server\|device` vincolante; 1c override esplicito
  utente; 1d gate disponibilità (heartbeat < 60s).
- **L2 classificazione workload** (se L1 non decide): manifest `class`.
  `net`→miglior banda (default `.33`); `cpu`→più potente (default `.33`,
  Strix Halo); `io_fs`→località filesystem; `llm_local`→dove gira il
  provider; `mixed`→server. Profilo device via heartbeat:
  `cpu_bench_single/multi`, `ram_free`, `net_throughput`,
  `net_latency_to_server`, `has_gpu`, `gpu_model`, `current_load`.
- **L3 tiebreaker**: 3a località cache; 3b carico corrente; 3c default `.33`.

7 policy candidate in coda, aggiunte una alla volta, feature-flag OFF
(privacy/data-locality prima). Il wiring: una funzione pura
`choose_placement(manifest, intent, devices) -> device_id | "server"` nel
dispatch di `.33`, gemella di `routing_pool` (testabile senza device reali).

---

## 11. Manifest & scope (estensione del formato `.33`)

Il formato manifest è quello di `.33` (§2.5 CLAUDE.md) con TRE campi in più,
già previsti da ADR 0034:

```toml
[placement]
scope   = "device"                 # server | device | any
targets = ["filesystem"]           # filesystem | network | hardware | ...
class   = "io_fs"                  # net | cpu | io_fs | mem | mixed | llm_local

[[capabilities]]                   # come .33, ma qui diventano i limiti sandbox
name = "fs:read"
hint = ["~/**"]                    # → limits.fs_read del §9
```

Un executor senza `[placement]` = `scope="any"` (compat: gira su `.33` come
oggi). La generazione (synt stage 2 SIGNATURE) impara a emettere
`[placement]` quando l'intent nomina un dispositivo; il verifier di `.33`
(gemello `importer_verb_verify`) controlla che `capabilities` ⊇ i
`fs_read/fs_write/net_allow` che il sandbox concederà.

---

## 12. Modalità di fallimento (ADR 0011/0046, formalizzare) ⛔

| Scenario | Comportamento |
|---|---|
| **Client offline** (device spento/scollegato) | Il gate heartbeat < 60s (L1.d) esclude il device dal placement → l'invocazione device-only ATTENDE o dà errore ONESTO §2.8 «dispositivo X non raggiungibile». MAI coda persistente in MVP (fase-7). |
| **Server offline** (`.33` giù) | Il client SMETTE, con notifica al prossimo tentativo. Eccezione a basso costo: buffera LOCALMENTE log + telemetria + result di esecuzioni IN-FLIGHT (spool). Nessuna nuova azione. |
| **Client compromesso** | Non può auto-comandarsi (invariante 2). Il danno è limitato dal sandbox + reverse_pattern + doppio audit. Revoca via DM/UI taglia i token futuri. |
| **Invocazione duplicata** | `invocation_id` idempotente (§6.4): mai doppio side-effect. |
| **Firma non valida** (server_sig/device_sig) | Rifiuto + log; nessuna esecuzione, nessun result accettato. |
| **Deadline superata** | Il client killa il sandbox (`limits.wall_ms`), restituisce `ok:false` + `timeout`, onesto §2.8. |

---

## 13. Roadmap MVP (ADR 0046, 5 settimane, immutabile) + stato

| Sett. | Contenuto | Stato |
|---|---|---|
| W1-2 | Linux MVP: client Rust + mTLS a `.33` + python-build-std via mirror + bwrap | 🟡 (register ✅, run/execute/sandbox ⛔) |
| W3 | Windows base: Job Object sandbox + python-build-std Windows tarball | ⛔ |
| W4 | Pairing + auto-update: `/pair-device`, `self_update` da release firmate | 🟡 (pairing ✅, self_update ⛔) |
| W5 | Cache + telemetria: wheel+executor cache hash-keyed, GC, log strutturati, heartbeat | 🟡 (wheel mirror ✅, resto ⛔) |
| W6+ | Hardening: AppContainer Windows, Azure Artifact Signing se serve | ⛔ |

**Aperti deferiti** (ADR 0046 §Consequences): concorrenza executor
paralleli per device; unpair esplicito; dettaglio telemetria salute; cert
pinning CA vs leaf; delta-update del binario. Macro-topic fase-7:
multi-user (oggi single-user host+guest, ADR 0035); misura efficacia E2E;
modi di fallimento formalizzati; disconnect-proof.

---

## 14. Contratti implementativi (per chi scrive il codice)

Chi implementa parta da questi punti concreti — sono il minimo per chiudere
W1-2 (Linux, execute path):

### 14.1 Server (`.33`, Python)

- `agent_server.py`: aggiungi `poll`, `result`, `heartbeat` handler
  (pattern degli esistenti `register`/`health`). Firma le invocazioni con
  `sign.py` (chiave server Ed25519). Coda per-device: tabella
  `invocations(invocation_id PK, device_id, payload_json, state, sig,
  created_at)` in `devices.py` DB (stesso SQLite, atomico).
- `engine/dispatch.py`: `choose_placement(...)` (§10). Quando placement =
  device, invece di `executor.run` locale, `enqueue_invocation(device_id,
  framework_step)` e attendi il `/agent/result` (con `deadline_ms`).
- `http_routes_admin.py`: `/admin/devices` + `/admin/devices/token`
  (§5.2).
- `scripts/build-client.sh` + `install.sh`/`install.ps1` serviti da
  `agent_mirror`.

### 14.2 Client (Rust, `client-rs/`)

- `main.rs::Cmd::Run`: sostituisci il `bail!` con il loop:
  `poll → verify server_sig → pull executor (cache-miss) → run_sandboxed →
  post result → heartbeat`. Un modulo `runner.rs` (loop), `sandbox_linux.rs`
  (bwrap+landlock+seccomp), `pyenv.rs` (scarica python-build-standalone,
  invoca uv), `wire.rs` (tipi serde delle §6.2/6.3).
- `identity.rs` ✅ già firma/verifica Ed25519; aggiungi `verify_server_sig`.
- Test: harness isolato (utente dedicato + clone + porta alt) come §10.7
  CLAUDE.md — mai «è partito», sempre un execute reale end-to-end
  (`find_packages(git)` su un device Linux di prova → result verificato).

### 14.3 Piano di test (gate di accettazione W1-2)

1. Pairing E2E: token effimero → register → device in `/admin/devices`.
2. Poll vuoto: block_ms scade, client ri-polla, zero busy-loop.
3. Execute read-only: `find_packages` su device Linux → entries corrette,
   `device_sig` verificata, riga audit correlata per `trace_id`.
4. Firma manomessa: `server_sig` alterato → client rifiuta, nessuna
   esecuzione.
5. Idempotenza: stesso `invocation_id` due volte → un solo effetto.
6. Sandbox: executor che tenta `fs_write` fuori da `capabilities.hint` →
   bloccato dal sandbox, `ok:false` onesto.
7. Server giù a metà: result in-flight bufferato nello spool, consegnato al
   ritorno di `.33`.

---

## Riferimenti

- ADR 0007 (topologia self-hosted, overlay Headscale) · 0011 (client/server
  asimmetrico, il *cosa*) · 0034 (routing/placement 3 livelli) · 0037
  (binario self-contained static-link) · 0046 (Rust + python-build-standalone
  + uv, il *come*).
- Codice: `runtime/agent_server.py`, `runtime/agent_mirror.py`,
  `runtime/devices.py`, `runtime/pairing.py`, `client-rs/`.
- Doc architettura: `docs/it/architecture/pairing.html`, `sandbox.html`
  (da riallineare a questo doc alla chiusura di W1-2, §9.1 CLAUDE.md).
