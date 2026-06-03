# Studio di fattibilità + report di implementazione — Convergenza competitiva Metnos

> Confronto OpenClaw / Hermes Agent → feature da assorbire in Metnos.
> Redatto 30/5/2026. Ancorato al codice reale (file:riga verificati via esplorazione).
> **Avvio: appena consolidato Metnos** (doc overhaul + indice foto già chiusi).

---

## 0. Sintesi esecutiva

Metnos è l'unico dei tre **veramente local-first con sintesi di codice e allineamento teleologico** (vaglio). Il fossato è lì. Ma due competitor hanno feature di *percezione di intelligenza* che Metnos ha solo a metà:

- **Hermes** → loop di auto-apprendimento esplicito (scrive skill riusabili dopo task complessi) + user-modeling persistente + memoria a 3 strati.
- **OpenClaw** → reach (canali/voce/canvas) — fuori dal nostro obiettivo, NON inseguire.

Metnos ha **già le infrastrutture base** (autopath, introvertiva, synt, mnestoma, terminator con `lacune.n_seen`). Mancano gli **innesti di orchestrazione**, non i moduli. Questo rende i 3 interventi prioritari **a basso rischio** (estensione, non riscrittura).

**Ordine raccomandato** (valore/sforzo):
1. **W1 — Skill-learning loop esplicito** (alto valore, medio sforzo) — il vero gap vs Hermes.
2. **W2 — User modeling persistente** (alto valore, medio) — `USER.md`/preferenze per-attore.
3. **W3 — Memoria episodica cross-session** (medio-alto, medio) — recall turni passati (FTS5).
4. **W4 — Prompt-snapshot cache-aware** (medio, basso) — latenza, quick win.
5. **W5 — Backend sandbox aggiuntivi** (medio, medio) — abilita fase 7 (client Rust/remoto).

---

## 1. W1 — Skill-learning loop esplicito (priorità 1)

### 1.1 Cosa fa Hermes (riferimento)
Dopo un task "non banale" (≥5 tool-call, oppure recovery da errore, oppure workflow non ovvio) scrive una **skill `.md`** riusabile, la indicizza, e la ricarica contestualmente su task simili. Review globale ogni 15 task. Bench: +40% velocità su task ricorrenti.

### 1.2 Cosa ha già Metnos (ESISTENTE, verificato)
| Pezzo | File | Stato |
|---|---|---|
| Esito turno + `match_source` | `runtime/engine/dispatch.py` (run_turn, `DispatchResult.match_source`: fastpath/autopath/engine/recovery/terminator) | ✅ |
| Autopath learned-framework | `runtime/engine/autopath.py` (`record_observation`, `record_feedback`→`_promote_skill`, champion/challenger, anti_skill TTL 30gg) su `autopath.sqlite` | ✅ ma promuove **framework cache**, non codegen |
| Fastpath approvato-utente | `runtime/engine/fastpath.py` (`approve()`, hash + coseno BGE-M3) su `fastpaths.sqlite` | ✅ |
| Terminator con lacune | `runtime/engine/terminator.py` (`_record_lacuna`, `lacune.n_seen` idempotente) su `terminator_log.sqlite` | ✅ **trigger naturale** |
| Introvertiva pattern-mining | `runtime/introvertiva.py` (SPECIALIZE su turns JSONL) | ✅ ma MVP: solo identificazione, **no auto-promote** |
| Synt codegen 5-stage | `runtime/synt_multistage.py` + admission 6-layer `runtime/skill_admission.py` | ✅ |
| Lifecycle proposta | `runtime/change_intents.py` (`change_intents.sqlite`, proposed→…→finalized) | ✅ |
| TurnLog telemetria | `runtime/agent_runtime.py` TurnLog.write() → `~/.local/share/metnos/turns/<data>.jsonl` (steps, final_kind, n_step) | ✅ |

### 1.3 Il GAP preciso
1. **Nessun trigger automatico "task complesso → cattura capacità"**: l'introvertiva è solo notturna/identificativa; il terminator traccia `n_seen` ma non agisce.
2. **Due livelli di "skill" non collegati**: autopath (framework cache, no codice) vs synt (executor codegen). Manca la **scala graduata**: ripetizione lieve → seed autopath; ripetizione forte/strutturale → proposta synt.
3. **Niente "successo riusabile" su path lunghi**: oggi autopath impara da feedback ✓ utente; non auto-semina da turni engine riusciti ma costosi (≥N step).

### 1.4 Disegno proposto — "Crescere dall'esito reale" (rispetta [[feedback-no-training-amplify-reality]])

Trigger a DUE soglie, ancorati a fatti del turno (no ML):

```
fine turno (dispatch.run_turn)
  │
  ├─ turno OK con match_source=engine  AND  n_step >= SEED_STEPS (default 4)
  │     → autopath.seed_from_run(run)        # frame cache "shadow", non champion
  │        (riusa record_observation + promote-on-repeat esistente)
  │
  ├─ terminator lacuna con n_seen >= PROPOSE_SEEN (default 3)
  │     → introvertiva.propose_from_lacuna(lacuna)
  │        → change_intent(kind=create_executor|extend, origin=learning_loop)
  │        → synt_multistage (se intent strutturale) dietro admission 6-layer
  │
  └─ review periodica ogni REVIEW_EVERY task (default 15)
        → job scheduler: consolida shadow→active, pota anti_skill, ranking proposte
```

**Punti di innesto (codice):**
- `runtime/engine/dispatch.py` — dopo l'esecuzione engine riuscita: chiamare `autopath.seed_from_run()` (nuova, wrappa `record_observation` + flag `shadow=1`).
- `runtime/engine/terminator.py::SimpleTerminator.explain()` — dopo `_record_lacuna`, se `n_seen >= PROPOSE_SEEN` → emette `change_intent` via `change_intents.py` (NON sintesi inline: solo proposta, l'admission resta gate).
- `runtime/introvertiva.py` — nuova `propose_from_lacuna(lacuna)` che riusa `candidates_specialize`.
- Job scheduler v2 — nuovo builtin `learning_loop_review` every_72h (allineato ai GPU-heavy staggerati), consolida shadow.

**Storage:** riusa `autopath.sqlite` (campo `status` ha già active/shadow logico via champion) + `change_intents.sqlite`. Nessuna nuova tabella necessaria (verificare colonna `shadow`/`origin_family`).

### 1.5 Sicurezza / coerenza con i principi
- Tutto passa da **admission 6-layer** (vocab, Jaccard ≥0.5, ager, smoke, verifier) e dal **vaglio** a runtime → nessuna capacità auto-promossa bypassa i gate.
- Le proposte synt **non si auto-applicano**: restano `change_intent(proposed)` in `/admin/changes` per triage (o auto-promote dietro kill-switch grace come il promoter).
- Onestà §2.8: il seed autopath è `shadow`, vince solo dopo ripetizioni reali (champion/challenger già esistente).

### 1.6 Stima
- **Sforzo**: ~3-5 gg. Estensione di 3 moduli esistenti + 1 job. Nessun nuovo sottosistema.
- **Rischio**: basso (gate già in place). Medio solo sul tuning soglie (SEED_STEPS/PROPOSE_SEEN/REVIEW_EVERY) — esporre via env, default conservativi.
- **Misura di successo**: % turni risolti da fastpath/autopath in salita settimana-su-settimana sullo stesso corpus query; calo di `lacune.n_seen` ricorrenti; bench ricorrenza (replica del "+40% Hermes" su 20 query ripetute).

---

## 2. W2 — User modeling persistente (priorità 2)

### 2.1 Riferimento Hermes
`USER.md` + profilo dialettico (Honcho): preferenze, stile, dominio, pattern — letti dal proposer per "grows with *you*".

### 2.2 Stato Metnos
- ESISTENTE: `runtime/users.py` (`users.db`: id, name, role, autonomy, email, channels) + `runtime/persons_registry.py` (`persons.sqlite`: face embeddings) + placeholder `${RUNTIME:actor|lang|channel}` in `runtime/engine/executor.py`.
- **GAP**: zero campo "preferenze" semantiche (lingua preferita, tono, modello/tier preferito, domini, unità di misura, "conta solo file regolari" tipo [[feedback-count-files-semantic]]). `read_persons` dà profilo anagrafico/biometrico, non preferenziale.

### 2.3 Disegno proposto
- Tabella/area `user_prefs` (in `users.db` o file `USER.md` per-attore sotto `~/.local/share/metnos/users/<id>/USER.md`).
- Sorgenti di apprendimento preferenze (no LLM training, solo accumulo da fatti):
  - feedback ✓/✗ ricorrenti su un asse (es. sempre ✗ su risposte lunghe → pref "stringato");
  - scelte esplicite nei dialoghi (`get_inputs`);
  - correzioni utente ("no, intendevo X").
- Lettura: `${RUNTIME:pref.<key>}` esteso + blocco prompt iniettato dal proposer (come `telos_loader.render_planner_block`).

**Punti di innesto:**
- `runtime/users.py` — nuove `get_prefs(user_id)` / `set_pref(user_id, key, value, source)`.
- `runtime/engine/executor.py::_build_runtime_resolvers` — estendere whitelist con `pref.*`.
- `runtime/turn_feedback.py` — su pattern feedback stabile, scrivere pref (audit-logged, reversibile).
- Proposer (`runtime/engine/proposer*.py`) — iniettare blocco preferenze.

### 2.4 Stima
- **Sforzo**: ~2-3 gg. **Rischio**: basso-medio (privacy: preferenze sono dati utente → restano locali, scrubbing già esistente).
- Sinergia forte con W1 (entrambi "amplify reality").

---

## 3. W3 — Memoria episodica cross-session (priorità 3)

### 3.1 Riferimento Hermes
Memoria episodica SQLite FTS5 + summarization LLM → recall "cosa è successo N giorni fa" senza gonfiare il contesto.

### 3.2 Stato Metnos
- ESISTENTE: `runtime/scratchpad.py` (`scratchpad.db`) ma è **solo pagination layer con TTL 1h** — non sopravvive, non è semantico. `mnestoma` è il grafo co-attivazione (FUNZIONALE, `mnest.sqlite`, decay/state machine/ager + `canonical_query_log`). Turn JSONL è cronologico ma non indicizzato per recall.
- **GAP**: nessun recall conversazionale cross-session per il proposer/planner. Il `conversation_id` esiste in TurnLog ma non è sfruttato per richiamo.

### 3.3 Disegno proposto
- Indice FTS5 sui turn JSONL (o tabella dedicata `episodes`): `(turn_id, conversation_id, actor, ts, user_query, final_message, tools, summary)`.
- Builtin `recall_episodes(query, actor, k)` esposto al planner SOLO quando l'intent ha marker memoria ("ricordi quando…", "l'altra volta", "come la settimana scorsa") — pattern conditional-injection come `*_tasks`.
- Summary via tier middle, cache-aware (vedi W4).

**Punti di innesto:**
- Nuovo `runtime/episodic.py` + indice FTS5 popolato da TurnLog.write() (hook esistente) o da job notturno.
- Reaper unico `state_reaper` (già esistente) per retention.

### 3.4 Stima
- **Sforzo**: ~3-4 gg. **Rischio**: medio (qualità summary; rumore recall → soglia + conditional injection).

---

## 4. W4 — Prompt-snapshot cache-aware (priorità 4, quick win)

### 4.1 Riferimento Hermes
Congela lo snapshot del system prompt a inizio sessione → riusa context cache, niente crescita token-bill.

### 4.2 Stato Metnos
- `runtime/llm_router.py`: ogni call ricalcola il system (`_system_for_tier`); nessun concetto di sessione con snapshot. Su locale il token-bill non esiste, ma il **ricomputo prompt + cache-miss di llama.cpp** costa latenza.

### 4.3 Disegno proposto
- Snapshot del blocco di sistema per `conversation_id` (catalogo+telos+preferenze stabili nel turno) → invariato fra le call dello stesso turno/sessione → massimizza il **prompt-cache di llama-server** (prefix KV reuse).
- Per il tier frontier (Anthropic), abilitare `cache_control` reale.

**Punti di innesto:** `runtime/llm_router.py` (`chat`, `_system_for_tier`) + un piccolo session store (riusa `conversation_id` di TurnLog).

### 4.4 Stima
- **Sforzo**: ~1-2 gg. **Rischio**: basso. **Beneficio**: latenza per-turno (misurabile con `bench_latency_breakdown.py`).

---

## 5. W5 — Backend sandbox aggiuntivi (priorità 5, abilita fase 7)

### 5.1 Riferimento
OpenClaw (Docker/SSH/OpenShell) e Hermes (6 backend: local/Docker/SSH/Singularity/Modal/Daytona) per subagent isolati / esecuzione remota.

### 5.2 Stato Metnos
- `runtime/sandbox.py`: **solo bubblewrap locale** (namespace user/ipc/uts, bind ro/rw, `--unshare-net` default). Profili dichiarativi nel manifest (`Executor.sandbox_profile/provenance/is_imported`). NO Docker/landlock/seccomp custom.

### 5.3 Disegno proposto
- Astrarre `wrap_command` dietro un'interfaccia `SandboxBackend` con impl `bubblewrap` (default) + `ssh` (per executor remoti, base del client Rust fase 7) + opz. `docker`.
- Selezione per-executor via manifest `[sandbox] backend=…`.

### 5.4 Stima
- **Sforzo**: ~4-6 gg (SSH/remote non banale). **Rischio**: medio. **Razionale**: non urgente ora, ma è il ponte per fase 7 (executor remoti) — coordinare lì.

---

## 6. Cosa NON fare
- **Ampiezza canali OpenClaw** (~20: WhatsApp/Signal/iMessage/Teams/WeChat…): è il loro fossato, non il nostro. Metnos = Telegram + HTTP per design (self-hosted personale). Ogni canale è manutenzione e superficie d'attacco.
- **Live Canvas / marketplace skill pubblico**: grosso lavoro UI/comunità fuori dagli obiettivi attuali. La voce è già fase 6 (standby), riusare satellite proprietario.
- **Skill a testo libero stile Hermes/OpenClaw**: Metnos ha vocabolario chiuso + codegen — superiore per determinismo. NON degradare a skill-`.md` non vincolate; semmai usarle solo come *seed* verso synt.

---

## 7. Roadmap consigliata (post-consolidamento)

| Fase | Item | Sforzo | Dipendenze | Esito misurabile |
|---|---|---|---|---|
| L1 | W4 prompt-snapshot | 1-2 gg | — | ↓ latenza/turno (bench) |
| L2 | W2 user-prefs | 2-3 gg | — | preferenze applicate (es. stringato auto) |
| L3 | **W1 learning-loop** | 3-5 gg | W2 (pref nel ranking) | ↑ % fastpath/autopath; ↓ lacune ricorrenti |
| L4 | W3 episodic recall | 3-4 gg | W4 (summary cache) | recall "l'altra volta" funzionante |
| L5 | W5 sandbox backend | 4-6 gg | coordinare fase 7 | executor remoto isolato |

**Totale**: ~13-20 gg-uomo. W1+W2 (≈6-8 gg) coprono il **grosso del gap percepito** vs Hermes.

### Prerequisiti di avvio (gate "Metnos consolidato")
- [ ] Doc overhaul IT+EN completato + sitemap + deploy.
- [ ] Indice foto verificato (✅ fatto: bug symlink-hash risolto).
- [ ] Restart differito applicato (✅ fatto: kill-switch + fix chat LIVE).
- [ ] Per ogni W: ADR dedicato in `decisions/` PRIMA del codice (§9.1).

---

## 8. Decisioni aperte per Roberto (da fissare prima di L3)
1. **Auto-apply delle proposte synt del learning-loop**: triage manuale (`/admin/changes`) o auto-promote dietro kill-switch grace (come `jobs/promoter.py`)? Default proposto: triage finché non maturo, poi grace osserva-di-default.
2. **Soglie**: SEED_STEPS=4, PROPOSE_SEEN=3, REVIEW_EVERY=15 — confermare o calibrare su dati reali turn JSONL.
3. **User-prefs storage**: `USER.md` per-attore (ispezionabile, stile Hermes) vs tabella `users.db` (queryabile). Proposto: tabella + export `USER.md` read-only per ispezione.
4. **Vocabolario**: il learning-loop può richiedere nuovi token §2.2? In tal caso passa dalla governance (3 criteri: necessario/generale/comprensibile) — il loop NON estende il vocab da solo.

---

*Fonti competitor: OpenClaw (github.com/openclaw/openclaw, docs.openclaw.ai), Hermes Agent (github.com/nousresearch/hermes-agent, hermes-agent.org). Fonti Metnos: codice repo verificato 30/5/2026 (file:riga nel testo).*
