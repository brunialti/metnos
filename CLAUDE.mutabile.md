# Metnos — CLAUDE.mutabile.md · PARTE MUTABILE

> Compagno di `CLAUDE.md` (parte invariante, governance in testa a quel file). **Questo file lo manutiene l'agente.**
>
> **QUANDO AGGIORNARLO** (ex §13): nuova decisione di runtime (tier LLM, helper universale, vincolo dominio, gate/env); chiusura fase o nuovo macro-topic; sezione contraddetta dal codice (aggiornare PRIMA della PR). **NON aggiornarlo per**: bug fix puntuali (commit message); decisioni temporanee/sperimentali e stato di sessione (→ memorie); dettagli di un singolo ADR (→ l'ADR). Stile: una decisione = poche righe operative + puntatore ADR/spec/test.

## S. Stato corrente (7/7/2026)

- **Host**: `.33` (Strix Halo 96GB unified). Servizi: `metnos-http.service` (SYSTEM, porta 8770) + telegram-daemon (unit USER) + llama-server `:8080`.
- **LLM**: locale = Qwen 3.6 35B-A3B Q4_K_M/MTP su `:8080` (fast/middle/wise = stesso server, differenza nei parametri per-call); frontier = Anthropic Opus opt-in. SoT: `runtime/llm_router.py::DEFAULT_TIERS` + ADR 0146 — modello virtualizzato lì, MAI nome hardcoded altrove.
- **Prod = engine v3**: drop-in systemd `proposer-hardening.conf` (`METNOS_ENGINE=v3`, grammar+verb_filter ON). I guard compound sono v3-gated → **bench compound SEMPRE con `METNOS_ENGINE=v3`**.
- **ADR registry**: `0001-0187` (skipped: `0055`/`0115`/`0116`/`0121`).

## 3. Synth pipeline (5 stadi)

| Stage | Tipo | Tier | Output |
|-------|------|------|--------|
| 1 NAMING | procedurale | middle | `name`, `revertible`, `critical`, `target_kind` |
| 2 SIGNATURE | procedurale | middle | `args_schema`, `capabilities`, `reverse_pattern` |
| 3 TESTS | procedurale | middle | 4-6 test (felice, lista vuota, args invalidi, edge) |
| 4 DESCRIPTION | creativo | middle | description §2.5 + affinity |
| 5 CODE | creativo+proc. | wise | `<name>.py` con `def invoke()` |

Vincoli: vocabolario chiuso SOLO in stage 1; ogni stage vede la fetta minima di contesto; quality floor (no degradare a fast); sintesi locale only.

## 4. Planner/Engine — contratti dei piani

- **4.1 Da-piping fra step**: liste fra step → SEMPRE `from_step: N` (int); valori singoli → placeholder `{{stepN.field}}`. NIENTE `entries: "{{step1.entries}}"`.
- **4.2 Caso degenere N=1 con literal**: letterali (path, url) come typed list arg inline. OK `delete_files(paths=["/tmp/x.txt"])`; ERRORE `delete_files(from_step=1)` con history vuota.
- **4.3 Action verbs portano a termine**: verbo d'azione esplicito → read/find/get → classify/filter (se serve) → VERBO_AZIONE → final_answer. Niente `describe_entries` PRIMA del verbo d'azione.
- **4.4 Cap**: 12 step max per turno; stesso executor 3× di seguito = loop_break; `DUPLICATE_CALL` → `final_answer`.
- **4.5 Undo**: `undo_last_turn` ok con `undone_count>=1` → step successivo DEVE essere `final_answer`. Mai due undo nello stesso turno.

## 5. Vincoli di dominio (nel PLANNER prompt)

- **EMAIL/IMAP** → `*_messages`; mai `move_files` su mail; cancellazione = `move_messages(dst_folder="Trash")` (`delete_messages` non esiste).
- **FOTO/EXIF/GPS** → `get_files`.
- **IDENTITÀ/PROFILO** → `read_persons(name="${RUNTIME:actor}")` per "chi sono io"; `read_persons(role="guest")` per lista paired; distinto da `get_persons` (registro biometrico). ADR 0163.
- **ENROLLMENT** → dominio `*_persons` (elenco=`get_persons()`; «cancella l'enrollment di X»=`delete_persons(names=["X"])`). MAI `*_credentials`.
- **POSIZIONE** → `get_location`. **TEMPO/DATA** → `get_now`.
- **DESTINAZIONE spam/cestino/archivio** → nome utente come `dst_folder`; l'executor risolve via `M.list`. Non hardcodare `INBOX.Junk`.

## 11. Decisioni di runtime

- **Glossario livelli** (12/6): **fastpath=L0** cache della stessa query (hash+coseno; può tenere args concreti); **autopath=L1** piano generalizzato per cluster (scheletro senza args, promosso dal ✓ umano; store `autopath.sqlite`); **executor**=singolo tool firmato; **skill**=INSIEME di executor (bundle, ADR 0170). VIETATO «skill» per il piano L1.
- **Tool-use protocol**: nativo (tool_calls strutturati). NIENTE parser JSON fragile.
- **Routing deterministico** (8/6): seed fisso `METNOS_LLM_SEED` (default 42; `-1`=random); affinity-match boost nel prefilter rompe i pareggi fra fratelli stesso-object.
- **Describe deterministico** (12/6): testo byte-riproducibile via processo `llama-completion` monouso (temp=0+seed); gate `METNOS_DESCRIBE_DETERMINISTIC` (ON); fallback HTTP onesto `meta.deterministic=false`. **Cap anti-runaway map-reduce** (6/7): `METNOS_DESCRIBE_MR_MAX_ENTRIES` (default 100, 0=illimitato) — oltre: prime N + nota utente NEL summary + campi §2.7.
- **Compound: path di planning UNICO = engine** (ADR 0177 D1): decomposer eliminato; in `compound_decomposer.py` restano solo helper condivisi. Guard deterministici align/enforce anche sugli HIT cache L0/L1; `_compute_intent_sig` compound-aware; clausole STORE normalizzate a `entries` pre-cache (ADR 0174).
- **Provenienza args** (ADR 0177 S4 + `internal/design/spec_args_provenance_architecture.md`, 6-7/7): mappa runtime/clause/semantic in `runtime/arg_provenance.py`; config-args marcati `runtime_resolved` (il proposer li nasconde; politica-invariante `runtime/tests/test_config_args_marking_policy.py`, esenzioni intent-bearing in `is_intent_bearing_config`); backstop `coerce_args_to_schema` = Guard #0 (drop fuori-schema/leak marcati, enum case-normalize o drop; esenzione arg guard-owned per idempotenza); registro `Guard` tipizzato + oracolo di equivalenza golden (PROV.1-3). Cat. C (`overwrite_phantom_install_args`): rimovibile con journal `[phantom_install]` 0-fire ≥14gg (verifica ≥21/7).
- **Backend multi-provider** (ADR 0165/0136): selezione provider = config, non intento; `backend_resolver.OBJECT_BACKENDS` = events, files, contacts, **dirs** (7/7: cartelle Drive via NL; `delete_dirs` risolve i nomi name-first via `find_dirs`). Injection enum-aware: il default per-object non scavalca l'enum del tool; MAI iniettare un arg non dichiarato; l'esplicito non è clampato (errore onesto a valle). Provider via CLIENT-ARG, non executor-suffisso.
- **Cache-validity** (ADR 0182): ogni piano cachato (L0/L1/alternative-LRU) porta `tools_sig`+`pool_sig` VERIFICATE A LETTURA → mismatch=MISS; re-sign o capacità nuova invalidano per costruzione. Firmare SEMPRE col catalogo del chiamante.
- **Undo** (ADR 0183): scrittore al choke-point `invoke_executor` (`_undo_pending`/`_undo_done`, campo `device`); reverse device-aware accodato allo STESSO device; `restore_blob_backup` non remotabile. **Self-update client** firmato+idempotente (ADR 0184).
- **Learning-loop W1** (ADR 0185): turno costoso ripetuto → autopath **shadow** (il ✓ umano conferma); lacuna ricorrente → change_intent PROPOSED (triage umano su /admin/changes); review notturna TTL 21gg. Soglie via env; SEED_STEPS=4 (confermato 7/7).
- **Manutenzione domini esterni = comandi NL schedulati** (ADR 0186): mai job bespoke; organi interni = builtin in `NIGHTLY_SEQUENCE`; osservatori esterni = timer di sistema. Aging: esenzioni alla fonte.
- **User prefs** (ADR 0187 W2-v1): tabella `user_prefs` vocabolario CHIUSO (lang/tone/reply_length/units); executor leggono `${RUNTIME:pref_<chiave>}`; UI /admin/users; set-da-chat e Finalizer = v2.
- **Igiene filiera proposte** (ADR 0180): generatori specialize/generalize RITIRATI (introvertiva=solo dedupe); adapter attivi telos (cluster-head)/introvertiva/synt/user_feedback; **accept di una pipeline = eseguirla una volta** in scheduled-scope; killer `layer_overlap` nell'auto-evaluator.
- **Data piping**: `from_step: int` + `{{stepN.field}}` per scalari.
- **Intent extractor**: LLM middle (~370ms), fallback bag-of-words, bypass deterministico per undo; compound → `actions=[{verb,object}]` per clausola (routing pool per-clausola).
- **Universal helpers**: `classify_entries`, `filter_entries`, `extract_entries`, `undo_last_turn` sempre; `describe_entries` SOLO se intent.verb non è d'azione; `extract_entries` = testo non strutturato→record tipizzati (date ISO 8601), confine §2.2.
- **Reverse patterns**: `runtime/reverse_patterns.py` — 5 entry deterministiche (§2.3). Gap noto: il ramo gw dei creatori multi-provider non è pattern-undoable (`delete_created_paths` non copre ids).
- **Platform policy**: `runtime/platform_policy.py` — system files cross-mount-safe + protected paths host-aware.
- **Messaggi**: `runtime/messages.py` — dizionario unico code→template `ERR_*/WARN_*/MSG_*/LOG_*`; mai stringhe duplicate negli executor. Norma i18n: §7.13 (parte invariante).

## 12. Fasi di sviluppo

- **Fasi 1-5 chiuse** (POC / test framework / synt 5 stadi / reality check+Telegram / vaglio+sandbox+dispatcher).
- **Fase 6** voce — STANDBY. **Fase 7** topic 1 (client Rust executor remoti): MVP fatto, C7 mutanti in corso; topic 2+ (multi-OS/multi-user/robustezza) DA COMPLETARE. **Fase 8** stress logico — DOPO fase 7.

## 14. HTTP API

Server `runtime.metnos_http_server` porta **8770** (8765=pairing). aiohttp bare: ROUTES tuple list, `_error()`, `auth_middleware`; ruoli anonymous/user/admin (admin key `~/.config/metnos/admin.key`, 0600). Endpoint `/agent/{health,turn,devices/me}` + `/.well-known/metnos.json` + `/admin/{,changes,executors,executors/stats,runs,safety,turns,caches/*/flush}`. Negotiation HTML (htmx+Jinja2+uPlot) vs JSON; ETag su collezioni admin; SSE su `/agent/turn`. ADR 0078.
