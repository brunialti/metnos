---
id: 0144
title: Consolidamento ownership infra su supranet (host .33)
date: 2026-05-18
status: proposed
area: ops | infra | host
related:
  - 0140  # prefilter modular + scaling
  - 0145  # public distribution design (gemella, supranet wrapper)
---

## Context

Il censimento `docs/it/internal/infrastructure_inventory.html` (17/5/2026)
mappa le componenti che fanno funzionare Metnos su `.33` (`beelink`,
Strix Halo 96GB). Owner distribuiti: 35 myclaw direct, 10 supra
delegated, 6 host system, 8 SaaS external.

L'esplorazione mirata della API surface Metnos→supra (18/5/2026,
agente Explore) ha rilevato che la dipendenza concreta verso supra e'
asimmetrica: 1 import Python, 2 HTTP raw.

- **1 sola classe Python importata**: `suprastructure.embedding.onnx_embedding.EmbeddingService`
  (5 callsite, di cui 4 in bench script e 1 sola in produzione,
  `executors/find_urls/find_urls.py:701`).
- **2 endpoint HTTP locali** chiamati con `httpx` raw, entrambi
  OpenAI-compat: `llama-server :8080` (planner, via
  `runtime/llm_provider.py::LlamaCppProvider.complete()` linea 288)
  e VLM Qwen2-VL `:8081` (image enrichment, via
  `executors/create_images_indices/create_images_indices.py::_call_vlm()`
  righe 74,80).
- **0 file di config persistenti** in `~/.config/metnos/` referenziano
  `/opt/suprastructure`. Tutte le coordinate vivono in env vars
  `METNOS_PLANNER_ENDPOINT`, `METNOS_VLM_URL`, `METNOS_VLM_MODEL`,
  `METNOS_VLM_TIMEOUT_S`.
- **0 dipendenze systemd lifecycle**: Metnos assume i servizi up,
  non li spawna ne' li riavvia.

L'asimmetria HTTP-vs-Python e' tollerata in dev ma e' incompatibile
con la distribuzione pubblica: ADR 0145 gemella stabilisce che la
build distributable consuma supra solo via interfacce in-process
Python (`from suprastructure.* import …`), e si fa carico di
fornire un adapter tra il codice Metnos che oggi fa httpx raw verso
`:8080`/`:8081` e il facade `suprastructure.llm.LLMClient`
multi-provider gia' esistente in supra. Questa ADR (0144) **non
muove codice Metnos**: dev su `.33` continua a chiamare i due
endpoint HTTP locali invariati.

Stato attuale dei systemd unit di rilievo per Metnos (livello system,
`/etc/systemd/system/`):

| Unit | Ownership de-facto | Note |
|---|---|---|
| `llama-server.service` | host (User=roberto) | ExecStart sorgenta `/home/user/llamacpp.env` derivato da `/opt/suprastructure/config/models.yaml` via `supra-model` |
| `ollama.service` | host | legacy, non risulta referenziato da codice produzione Metnos (provider menzionato in `llm_provider.py:208` ma tier default va a llamacpp) |
| `photon.service` | host (config supra) | Description gia' dichiara `suprastructure/geo`. Dataset symlink `data/current` |
| `searxng.service` | host | self-hosted, ruolo in pipeline web (ADR 0081) — da verificare se referenziato |
| `stt-server.service` | host (config supra) | **non usato da Metnos** (whisper/voice mai integrato in runtime) |
| `suprastructure-backup.service` + `.timer` | supra | OK |
| `suprastructure-docs.service` | supra | OK |
| VLM Qwen2-VL `:8081` | **nessuna unit** | spawn manuale on-demand, niente systemd |
| Cloudflare tunnel + Pages | host/SaaS | **dev-only**, fuori scope distribuzione |

Componenti **mai usate** dal runtime Metnos (da rimuovere dall'inventory
"ML infra shared", restano legittime in supra come capacita' standalone
per altri client):

- `infra_services/tts/` — TTS server (Fase 6 voce in STANDBY)
- `infra_services/voice_backend/` — STT/wake (Fase 6 STANDBY)
- `stt-server.service` (system) — whisper.cpp Vulkan
- `whisper-server.service` (se presente, system)

Sono comunque mantenute in supra per altri client/future Fase 6, ma
escluse dalle dipendenze dichiarate di Metnos.

## Decision

**Scope minimale (Opzione 1 del questionario 18/5/2026)**: riallineare
ownership dei systemd unit residui senza muovere dati o codice
applicativo Metnos. Tre azioni:

### 1. Ownership documentale di llama-server → supra

Verifica empirica (18/5/2026): le unit gia' supra-managed
(`suprastructure-backup.service`, `suprastructure-docs.service`)
vivono a `/etc/systemd/system/` come tutte le altre — la
"convenzione supra" non e' filesystem-location ma ownership
logica: il unit referenzia config di supra (`/opt/suprastructure/config/models.yaml`,
`/opt/suprastructure/deploy/backup_nas.sh`, ...) e i suoi binari sono
gia' tracciati nel repo supra.

Per coerenza con questa convenzione, **non si sposta il file**.
L'azione concreta e' solo documentale + minore:

- `infrastructure_inventory.html` aggiornato: `llama-server.service`
  owner `supra` (era `host`), con annotazione "config canonica
  `/opt/suprastructure/config/models.yaml` via `supra-model`".
- Copia versionata del unit corrente in
  `/opt/suprastructure/systemd/llama-server.service.canonical` come
  artefatto repo (sorgente di backup, ricreabile via `cp` non symlink
  per evitare break su rimount).
- `ExecStart` e drop-in `override.conf` invariati.

### 2. VLM Qwen3-VL — promozione documentale, lazy-spawn preservato

Lo script di gestione canonico e' `/opt/myclaw/scripts/vlm_server.sh`
(spawn lazy + watchdog auto-stop dopo 600s idle, design deliberato
per liberare ~8 GB VRAM tra una indicizzazione e l'altra). Promuoverlo
a unit systemd `Type=simple` `enabled` cambierebbe la semantica
operativa (VRAM sempre occupata). Decisione: **lasciare il modello
lazy-spawn**, solo riallineamento di ownership.

- `infrastructure_inventory.html` aggiornato: VLM ownership `supra`
  (era `host`), nota "lazy-spawn via `vlm_server.sh`, NON systemd-resident".
- Copia versionata di `vlm_server.sh` in
  `/opt/suprastructure/scripts/vlm_server.sh` come artefatto repo,
  riconciliata periodicamente con `/opt/myclaw/scripts/`.
- (futuro, opzionale) unit systemd `vlm-server.service` disabilitata
  by-default per uso `systemctl start vlm-server` quando si vuole
  forzare la presenza in foreground — fuori scope di questa ADR.

### 3. Cloudflare components dichiarate dev-only

`cloudflared-metnos-chat.service` (user-level, tunnel verso
`chat.metnos.com`) e tutti gli script `deploy.sh` Cloudflare Pages
sono **dev environment** del nodo `.33`. Non fanno parte del runtime
produttivo Metnos ne' dell'inventory di distribuzione. Etichettati
esplicitamente come `dev-only` in `infrastructure_inventory.html`
sezione G (SaaS), e **esclusi** dall'installer pubblico (ADR 0145).

## Out of scope (esplicito)

I seguenti elementi **non vengono mossi** sotto supra, anche se la
tentazione di consolidare e' grande:

| Elemento | Resta a | Motivo |
|---|---|---|
| `~/.cache/metnos/affinity_emb/` (BGE-M3 cache) | myclaw | E' application cache, dimensione contenuta, rigenerabile. Il modello e' supra ma la cache e' per-application. |
| `~/.local/share/metnos/index/` (487 MB unified image) | myclaw | Application data utente (foto personali). Non riusabile da altri client. |
| `~/.config/metnos/` (admin.key, mail.env, llm_tiers.toml) | myclaw | Application config. Niente di supra. |
| `runtime/credentials.py` (Fernet+HKDF, ADR 0131) | myclaw | Specifico di Metnos. Non e' un servizio shared. |
| Telegram BOT_TOKEN, IMAP creds | myclaw | App-level secrets. Mai in supra. |
| `~/.local/share/metnos/skills/` (google-workspace + github) | myclaw | Skill bundle Metnos. |
| `ollama.service` | host (deprecabile) | Vedi step 4. |
| `stt-server.service`, `infra_services/{tts,voice_backend}/` | supra ma dormant | Mai chiamati da Metnos. Restano supra-owned ma fuori inventory Metnos. |

## Implementation steps (~30 minuti, nessun cambio runtime)

Audit fatto in pre-flight (18/5/2026):
- `ollama.service` **gia' inactive + disabled** — nessuna azione.
- `vlm_server.sh` invocato da 1 callsite Metnos
  (`executors/create_images_indices/create_images_indices.py:315`) —
  non spostato per preservare lazy-spawn.
- `/opt/suprastructure/` owner `roberto:roberto 755` — write OK senza sudo.

Steps:

1. **Copia artefatto unit** (no sudo, no symlink, no /etc/ touch):
   `mkdir -p /opt/suprastructure/systemd` +
   `cp /etc/systemd/system/llama-server.service /opt/suprastructure/systemd/llama-server.service.canonical`.
2. **Copia artefatto script VLM**:
   `cp /opt/myclaw/scripts/vlm_server.sh /opt/suprastructure/scripts/vlm_server.sh`.
3. **Update inventory HTML**: owner `supra` per llama-server + VLM,
   Cloudflare → `dev-only`, ollama → "dormant (disabled)", rimozione
   whisper/tts/voice_backend dall'inventory Metnos (restano supra
   ma non parte del runtime Metnos). Conteggi: supra 10 → 12,
   host 6 → 4.
4. **Smoke**: `systemctl is-active llama-server` (atteso `active`),
   `curl -s localhost:8080/v1/models` (atteso 200).

Step 1-3 reversibili banalmente. Step 4 e' read-only.

## Risks & mitigations

| Rischio | Probabilita' | Mitigazione |
|---|---|---|
| `systemd daemon-reload` interrompe sessioni active | bassa | unit status `Type=simple`, restart triggera solo se necessario. Step 5 verifica explicit. |
| Symlink rotto se `/opt/suprastructure` viene rimontato | bassa | path stabile su disco locale, non NFS/CIFS. |
| Drop-in `override.conf` perso nello spostamento | media | step 1 backup + step 3 controlla che il drop-in dir esista; `override.conf` resta dove sta (e' del nodo, non del repo). |
| VLM nuovo systemd unit blocca GPU per llama-server | media | `--ctx 65536 --parallel 8` su Strix Halo 96GB regge entrambi; testato 17/5 sera-2 in produzione. `After=llama-server.service` per warmup ordinato. |
| Ollama disable rompe un fallback path non documentato | bassa | step 8 audit codice prima del disable. Resta `systemctl enable ollama` per emergency. |

## Verification

Smoke battery `runtime/smoke.py` PASS post-migrazione. In particolare:
- query routing a `llama-server :8080` invariato.
- `find_images_indices` con VLM (test foto Roberto) ritorna caption.
- `systemctl --user status metnos-http` healthy.

## Related future work

- **ADR 0145** (gemella): la consolidazione `.33` qui descritta e' la
  base per la distribuzione pubblica. Le 2 unit supra-managed
  diventano il template che l'installer pubblico clona.
- **ADR 0143 TODO** (install-on-demand): l'installer pubblico
  estendera' il pattern apt-on-demand anche ai modelli (BGE-M3 ONNX,
  Gemma 4 26B GGUF, Qwen2-VL) con consenso esplicito utente.
