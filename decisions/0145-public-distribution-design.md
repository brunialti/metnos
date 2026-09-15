---
id: 0145
title: Distribuzione pubblica Metnos — wrapper supranet in-process + installer 6 fasi
date: 2026-05-18
status: proposed
area: distribution | packaging | install
related:
  - 0123  # skill importer agentskills.io
  - 0131  # credenziali single store Fernet+HKDF
  - 0140  # prefilter modulare (foundation skill sandbox)
  - 0143  # install-on-demand pattern (TODO, sinergico)
  - 0144  # supranet consolidation .33 (gemella)
---

## Context

> Licensing update, 2026-09-15: the owner replaced Metnos's first-party license
> with MIT. The AGPL references below describe the original proposal and are
> retained as historical context, not current licensing instructions. See the
> repository's `LICENSE` and README for the current terms. Third-party software
> and model licenses are unchanged.

Metnos e' nato come assistente self-hosted personale su `.33`
(Strix Halo 96GB, owner Roberto). La pipeline attuale di publishing
copre solo docs (Cloudflare Pages, `metnos.com`); il codice runtime
non e' mai stato impacchettato per consumo terzi.

Lo studio di fattibilita' (18/5/2026) ha mappato che la dipendenza
runtime Metnos verso suprastructure (ADR 0144) e' di superficie
contenuta:

- 1 classe Python in-process: `suprastructure.embedding.onnx_embedding.EmbeddingService`
- 2 endpoint HTTP localhost: llama-server `:8080`, VLM Qwen2-VL `:8081`
- 0 file di config persistenti referenziano `/opt/suprastructure`
- 0 dipendenze systemd lifecycle (Metnos consuma servizi up,
  non li orchestra)

Per la **distribuzione pubblica** e' stato deciso (18/5/2026):

1. **Licenza** AGPL-3.0 (strong copyleft network clause; protegge da
   hosted clones, compatibile con dipendenze GPL come llama.cpp).
2. **Visibilita'** repo: privato al primo push → audit → flip public.
3. **Direttiva architetturale**: Metnos distribuzione consuma supra
   **solo via interfacce Python in-process**. Niente httpx raw da
   codice Metnos verso `:8080`/`:8081`. La separazione di processo
   dei server LLM/VLM resta a livello sistema (GPU isolation,
   restart indipendenti), ma e' nascosta dal facade Python di supra.
4. **Single API contract dev + distribuzione**: l'interfaccia
   Metnos→supra e' **identica** tra `.33` e build pubblica. Niente
   dual-track. Le 2 callsite Metnos HTTP raw (LlamaCppProvider,
   `_call_vlm`) vengono refactorate per consumare
   `suprastructure.llm.LLMClient` in **entrambi** gli ambienti.
   Razionale: una sola superficie da testare, mantenere e
   documentare; il facade Python supra incapsula gia' la dispatch
   HTTP a `:8080` per provider `llamacpp` (vedi `providers/llamacpp.py`
   in supra), quindi in dev il comportamento osservabile resta
   invariato post-refactor.
5. **Esclusioni dichiarate**: Cloudflare components (dev-only),
   whisper-stt + TTS + voice_backend (mai usati da runtime Metnos —
   pulizia inventory). Photon **opzionale**.

## Decision

Tre artefatti distinti per la distribuzione pubblica:

### 1. Compat shim `metnos-suprashim` (Python wheel bundled)

Pacchetto Python piccolo (~50 KB codice + ~600 MB modello scaricato
post-install) che fornisce esattamente la API surface di supra che
Metnos chiama. Distribuito **vendored** sotto `metnos/_compat/suprashim/`
nel repo, in modo che `import suprastructure` funzioni senza
installare l'intera suprastructure. Esporta:

| Modulo facade | Implementazione minima |
|---|---|
| `suprastructure.embedding.onnx_embedding.EmbeddingService` | wrapper su `onnxruntime` che carica BGE-M3 ONNX int8 da `~/.cache/metnos/models/bge-m3-onnx/` |
| `suprastructure.llm.LLMClient` | multi-provider async (anthropic / openai / llamacpp_localhost / ollama). Per `llamacpp` provider: HTTP a `:8080` interno (utente ha installato llama.cpp localmente). Per `anthropic` / `openai`: API key da credentials store |
| `suprastructure.geocoding` | composite provider (photon optional, google fallback con API key) |

**Caratteristica critica**: il shim e' un **subset stripped** di supra,
con tutto cio' che Metnos NON usa rimosso (no whisper/stt, no tts, no
voice_backend, no speaker_id, no models registry completo, no health
check runner, no gateway). Pattern: vendor from supra repo via script
`scripts/vendor_suprashim.py` che pinna una versione supra e copia
solo i moduli enumerati, riscrivendo gli import.

### 2. Refactor unificante callsite Metnos HTTP raw → supra facade

Due callsite Metnos fanno `httpx.post` raw verso supra services e
vengono refactorate in entrambi gli ambienti (dev + distribuzione,
single API contract per Context §4):

| Callsite | Sostituzione |
|---|---|
| `runtime/llm_provider.py::LlamaCppProvider.complete()` (linea 288) | `from suprastructure.llm import LLMClient` + `LLMClient(provider="llamacpp")` |
| `executors/create_images_indices/create_images_indices.py::_call_vlm()` (righe 74,80) | stesso `LLMClient` con modello vision-capable (chat API con `image_url` content type, o nuovo metodo dedicato se supra lo espone) |

Il refactor e' **invisibile in dev** (`suprastructure.llm.LLMClient`
internamente chiama HTTP a `:8080` come prima, vedi `providers/llamacpp.py`
in supra). In **distribuzione** lo stesso codice gira contro
`metnos/_compat/suprashim/` che fornisce la medesima API.

Verifica preliminare: `suprastructure.llm.LLMClient` supporta vision
**solo per provider anthropic/openai** (Chat Completions con
`image_url`); per `llamacpp` provider VLM Qwen2-VL serve un metodo
dedicato o estendere il provider llamacpp esistente. Decisione
implementativa rimandata a sessione di refactor (questa ADR registra
la direzione, non i dettagli del PR).

### 3. Installer scaffold `install/` autonomo a 6 fasi

Pacchetto installer in `install/` del repo, eseguibile come singolo
binario Python wheel (`pip install metnos-installer`) OR shell
bootstrap (`curl -sSf https://metnos.io/install.sh | sh`).
Architettura idempotente, ripartibile, stato persistito.

| Fase | Cosa fa | Skippable se gia' done |
|---|---|---|
| **1 — Bootstrap** | crea venv `<METNOS_INSTALL_ROOT>/.venv/` Python 3.12+, separato dai dati di ogni utente; installa `metnos` wheel + deps. Verifica disco: 8 GB minimi (5 GB modelli + 2 GB index foto stimati + 1 GB resto). Verifica python>=3.12, libstdc++ >= 11. | sentinella `~/.local/state/metnos/install/phase1.done` |
| **2 — Infra** | scarica BGE-M3 ONNX int8 (~600 MB) in `~/.cache/metnos/models/`. Optionals con consenso esplicito: llama.cpp (build oppure release binary; modello Gemma 4 26B GGUF ~15 GB), VLM Qwen2-VL-7B GGUF (~5 GB), Photon + dataset country (~3 GB IT), SearXNG (Docker compose oppure systemd unit). Scrive `~/.config/metnos/` config file template. Smoke reachability: BGE-M3 carica + planner endpoint risponde (se llama.cpp installato) o frontier API key valida. | sentinella `phase2.done` per ciascun sub-step |
| **3 — Codice Metnos** | clone (o gia' presente da pip) sotto `/opt/metnos/` o `~/.local/lib/metnos/`. Crea skeleton `~/.local/share/metnos/{turns,index,credentials,scratchpad.db}` vuoto. Import `i18n.sqlite` bundled (chiavi `MSG_*` IT+EN, ADR 0104). NIENTE dati personali dell'ambiente sviluppo (esclusi via `.gitignore` + .gitattributes export-ignore + scrub pre-publish). | sentinella `phase3.done` |
| **4 — Colloquio dati riservati** | TUI interattivo (rich/prompt_toolkit) per: admin username + auto-gen admin.key HMAC (ADR 0088), porta HTTP (default 8770, conflict check), Telegram BOT_TOKEN [opzionale], account IMAP [N>=0], Anthropic API key [opzionale], OpenAI API key [opzionale], Google Workspace OAuth flow [opzionale], GitHub PAT [opzionale], paths workspace (Pictures, Documents), locale (it/en). Salva tutto via `runtime/credentials.py::store()` Fernet+HKDF (ADR 0131), niente plaintext su disco. | sentinella `phase4.done` |
| **5 — Systemd** | scrive user units `metnos-http.service` (sempre) + `metnos-telegram-daemon.service` (se Telegram configurato in fase 4). `systemctl --user daemon-reload && enable --now`. Smoke: `curl localhost:<port>/agent/health` ritorna 200, `systemctl --user status` healthy. Se llama.cpp installato in fase 2: scrive system unit `llama-server.service` (richiede `sudo`, prompt esplicito) + verifica `:8080` risponde. | sentinella `phase5.done` |
| **6 — Primo avvio** | apre browser su `http://localhost:<port>/` per first-login admin (genera token via `/admin/onboard`); se Telegram configurato, invia link pairing + QR; opzionale: bootstrap workspace con index foto di una directory campione (dialogo). Genera summary `~/.local/share/metnos/install_summary.md` con cosa e' attivo, cosa skippato, prossimi step. | sentinella `phase6.done` |

Tutte le fasi sono idempotenti: re-run dell'installer rilegge le
sentinelle, salta cio' che e' fatto, propone di rifare se l'utente
chiede `--force-phase N`. Lo stato consente "ho saltato fase 2 VLM,
domani la aggiungo" con `metnos install --resume --enable vlm`.

## Componenti opzionali — matrice e default

| Componente | Default install | Degrado se assente | Disco | RAM/VRAM idle |
|---|---|---|---|---|
| BGE-M3 ONNX int8 | **mandatory** | affinity semantic + query expansion + github QA tutti rotti | ~600 MB | ~1.5 GB RAM |
| llama-server local (Gemma 4 26B GGUF) | optional (recommended) | Metnos forza frontier SaaS per planner → latency + cost | ~15 GB | ~22 GB VRAM caricato |
| VLM Qwen2-VL-7B :8081 | optional | image enrichment senza VLM caption (solo EXIF + ArcFace + filename) | ~5 GB | ~8 GB VRAM caricato |
| Photon + country dataset | optional | fallback Google Maps API (se configurata) o `get_location` GPS-only | ~3 GB (IT only) | ~2 GB RAM |
| SearXNG | optional | web search degradato a direct origins e RSS | ~200 MB Docker | ~300 MB RAM |
| Telegram daemon | optional | solo canale HTTP web | trascurabile | trascurabile |
| Google Workspace skill | optional | no gmail / drive / calendar | trascurabile | trascurabile |
| GitHub skill | optional | no watcher / issues / pulls | trascurabile | trascurabile |
| Cloudflare tunnel/pages | **escluso** (dev-only) | — | — | — |
| Whisper STT / TTS / voice_backend | **escluso** (mai usati) | — | — | — |

Combinazione minima funzionante: BGE-M3 + Anthropic API key (=
frontier-only). Disco ~700 MB, costi mensili variabili ($5-30 stima
utilizzo modesto).

## Scrub policy pre-publish

Il primo push pubblico richiede 4 layer di pulizia:

### L1 — .gitignore esteso

File con dati personali o cache mai versionati. Aggiungere a
`.gitignore` del repo (oggi ne esiste uno minimale):

```
# Dati personali / runtime utente
~/.local/share/metnos/
~/.cache/metnos/
~/.config/metnos/
workspace/
*.sqlite
*.sqlite-journal
*.jsonl
*.env

# Cloudflare dev-only
deploy.sh
.cloudflare/

# Modelli scaricati post-install (~/.cache/metnos/models/)
models/*.gguf
models/*.onnx
```

### L2 — Pre-commit hook

`scripts/pre-commit-scrub.sh` cerca pattern noti e blocca il commit
se trova match:

- email: local-part personali + domini company/personali (vedi
  `PATTERN_EMAIL` in `scripts/scrub-scan.sh` per i pattern correnti)
- nomi propri: regex configurabile in `scripts/scrub_names.txt`
  (default include nomi familiari noti)
- host/IP: `\b192\.168\.1\.3[0-9]\b`, `\bbeelink\b`, dominio company
- secrets pattern: `BOT_TOKEN=`, `API_KEY=[A-Za-z0-9_-]{20,}`,
  `ghp_[A-Za-z0-9]{36}` (GitHub PAT), `sk-[A-Za-z0-9]{40,}` (API key)
- HMAC key file content (`admin.key` shouldn't ever appear)

### L3 — Audit ADR + docs interne

`decisions/*.md` contengono narrativa interna spesso con date,
nomi di test ("foto Matteo"), commit hash autorefenziali, riferimenti
a `.33`. Audit one-shot: leggere ogni ADR, sostituire ovunque
emerge un nome proprio con "guest" / "utente di test", rimuovere
riferimenti `192.168.1.33` o `beelink`, normalizzare path
`/home/user/...` a `~/...`. Documenti internal sotto
`docs/it/internal/` e `docs/en/internal/` **non vanno pubblicati**:
aggiungere a `.gitignore` o `.gitattributes export-ignore`.

### L4 — Audit pre-push automatico

Hook `pre-push` lancia uno scanner piu' aggressivo di L2 + verifica
che nessun file `*.gguf`, `*.onnx`, `*.sqlite`, `*.jsonl` venga pushato
(blacklist hard). Se trova violazioni, exit 1.

## Repository GitHub — passi controllati

Sequenza ordinata, ogni passo richiede conferma esplicita Roberto:

1. **Decisione license depositata** → `LICENSE` file AGPL-3.0 nel
   repo + `SPDX-License-Identifier: AGPL-3.0-only` headers nei
   sorgenti Python (script automatico).
2. **Scrub L1-L4 implementati e PASS locale** → commit di pulizia,
   nessun match nei pattern scanner.
3. **PAT GitHub generato** (Roberto, **non in chat**): fine-grained
   PAT scope `repo` per il primo push. Salvato in
   `~/.config/metnos/credentials/` via `runtime/credentials.py`
   sotto domain `github_pat_publish` (riusabile per CI/release
   future, distinto dal PAT skill `github`).
4. **`gh repo create metnos --private`** dal cli (auth via PAT).
   Nome canonico `metnos`, owner personale Roberto. Description:
   "Self-hosted personal assistant with executor synthesis and
   ReAct planner. AGPL-3.0."
5. **First push branch `main`**, branch protection minimal
   (require PR review for self, no force push).
6. **README pubblico** scritto a mano (overview, install bootstrap
   command, link a `docs/` rendered su Cloudflare Pages OR
   GitHub Pages — decidere; Cloudflare dev-only escluso, quindi
   GitHub Pages e' candidato).
7. **CI smoke** in `.github/workflows/smoke.yml`: runner Ubuntu,
   installa wheel, `python -m metnos.smoke` (battery
   `runtime/smoke.py` filtrata "no SaaS keys"). Test
   continuativi.
8. **Flip public** dopo 1 settimana di repo privato (audit
   community, eventuale beta-tester invitato). Comando:
   `gh repo edit --visibility public`.

## Out of scope (esplicito)

- Build Docker image / Helm chart: rimandato a fase post-MVP.
- Multi-user multi-tenant cloud-hosted: contrario al modello self-hosted.
- Mobile client native: gia' coperto da chat.html progressive web.
- Voice (Fase 6 in STANDBY): non parte della distribuzione iniziale.
- ARM / non-x86_64: stage 1 supporta solo Linux x86_64 con GPU
  Vulkan-compatibile per uso pieno; modalita' frontier-only puo'
  girare su qualsiasi Linux/Mac/WSL con Python 3.12+.

## Risks & mitigations

| Rischio | Probabilita' | Mitigazione |
|---|---|---|
| Secret sfuggito al scrub L1-L4 → repo pubblico | bassa-media | repo privato 7 giorni + 2 review passes + paranoid scanner pre-push. Rotation procedure documentata. |
| Utenti che eseguono installer con sudo non-prompted | bassa | tutti i `sudo` passano per `prompt_y_n` esplicito con riepilogo comando. Default deny. |
| llama.cpp build fallisce su distro non-Ubuntu | alta | release binary pre-built per Ubuntu 22.04+/Debian 12+ via GitHub Release. Per altre distro: skip prompt + fallback frontier-only. |
| BGE-M3 ONNX download fail (CDN/HF mirror) | media | mirror multi-host: primary HuggingFace, fallback github release asset bundled. SHA256 verify. |
| Licenza AGPL scoraggia adozione enterprise | media (accettato) | scelta deliberata (vedi Decisione §1). Roadmap futura: dual-license commerciale opzionale se domanda. |
| Wrapper `suprashim` divergerebbe da supra upstream | alta nel tempo | script `vendor_suprashim.py` pinna versione supra; CI quotidiano alza warning se supra ha nuova release con fix in moduli vendored. |
| User installa solo BGE-M3 ma poi attiva GitHub watcher con embedding 1024d non disponibile in shim minimal | bassa | shim minimal include sempre BGE-M3 (e' mandatory). GitHub watcher e' optional in fase 2 + opt-in dialog (ADR 0141). |

## Implementation phasing (sessione successive)

Questa ADR descrive la direzione. Il lavoro concreto si distribuisce
su sessioni dedicate, ordine consigliato:

1. **Sessione +1** — `scripts/vendor_suprashim.py` + `metnos/_compat/suprashim/`
   primo cut (solo BGE-M3 ONNX). Smoke: `python -c "from
   suprastructure.embedding.onnx_embedding import EmbeddingService"`.
2. **Sessione +2** — refactor 2 callsite Metnos HTTP raw → supra facade.
   Smoke battery PASS.
3. **Sessione +3** — installer scaffold `install/` fasi 1-3 (bootstrap +
   infra + codice). Test su VM Ubuntu fresh.
4. **Sessione +4** — installer fasi 4-6 (TUI riservati + systemd + first-boot).
5. **Sessione +5** — scrub L1-L4 implementati. Audit ADR interni.
6. **Sessione +6** — README + LICENSE + CI smoke. Repo create privato.
7. **Sessione +7** — flip public dopo audit.

Ogni sessione produce PR atomica. Nessuna delle sessioni 1-7 crea il
repo prima della 6.
