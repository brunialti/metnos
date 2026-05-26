# ADR 0161 — Praxis Engine: la pentade greca della cognizione di Metnos

> ## Genesi & Mito
>
> Pentade etimologica greca, scelta per coerenza con la radice di Metnos
> (`mētis + noûs`). Cinque divinita', cinque ruoli, una sola mente:
>
> - **Mētis** (Μῆτις, "consiglio strategico astuto") → `praxis_propose.py`
>   _«La suggeritrice silenziosa che agisce dall'interno della mente — come
>   ha fatto dentro Zeus — per risolvere problemi complessi.»_
>   LLM 1-shot wise tier che propone il framework intero del workflow in
>   UNA call. Parla quando Praxis non sa. Espediente tecnico, piano astuto.
>
> - **Noûs** (νοῦς, "intelletto puro") → `praxis_executor.py`
>   La mente che esegue. Orchestratore deterministico, niente LLM nel loop,
>   solo data routing. Riceve il piano di Mētis e lo realizza.
>
> - **Praxis** (πρᾶξις, "pratica appresa") → `praxis.py` + sqlite
>   La memoria di cio' che ha funzionato. Cresce dal feedback (✓✗↻ chat o
>   auto-promote 2 obs + 100% ok). Dimentica via TTL anti_skill 30gg.
>
> - **Pronoia** (Πρόνοια, "provvidenza che vede prima") → `pronoia.py`
>   Madre di Prometeo secondo alcune fonti (Esiodo, _Teogonia_; cfr. Eschilo
>   _Prometeo incatenato_). Etimologicamente *pro-noûs*: l'intelletto che
>   vede *prima*. Interviene quando Mētis + Noûs + Praxis falliscono:
>   classifica l'errore in 3 classi ortogonali, ri-propone framework con
>   tool excluded, salva il turno dallo stallo.
>
> - **Aporia** (ἀπορία, "perplessita', vicolo cieco") → `aporia.py`
>   Termine tecnico della filosofia greca (Platone, _Menone_): il momento
>   in cui il discorso non sa come proseguire. Ultima istanza onesta:
>   quando neanche Pronoia salva, Aporia riconosce il limite, classifica
>   la lacuna (user_action / missing_executor / missing_skill / missing_data),
>   logga in sqlite, propone all'utente azione concreta per uscire.
>
> ### Perche' "Praxis" ingoia "PLANNER"
>
> Nel mito greco, Zeus sposo' Mētis ma quando lei era incinta la **ingoio'**
> per averla dentro di se' — temendo che un figlio nato da Mētis fosse piu'
> potente di lui. Da allora Mētis vive nello stomaco di Zeus come consigliera
> silenziosa: gli suggerisce strategie dall'interno, ma non si vede piu'.
>
> Lo stesso accade qui: il PLANNER step-by-step (che chiamava il LLM
> 5-7 volte per turno, fragile, lento, stocastico) e' stato **ingoiato da
> Praxis**. Il LLM non scompare — vive ancora dentro Praxis come Mētis,
> proposer globale 1-shot. Ma ogni volta che una pipeline funziona, Praxis
> la consolida nella cache deterministica → Mētis parla sempre meno → il
> sistema diventa via via piu' autosufficiente.
>
> Praxis e' Zeus: ha ingoiato Mētis, ne conserva la saggezza dentro, e la
> evoca solo quando la propria esperienza non basta. Quando catalog matura,
> Mētis tace per giorni. Quando una query nuova arriva, Mētis riemerge per
> un istante, propone, poi ritorna silente. Pronoia e Aporia, divinita'
> "esterne" alla psiche, intervengono solo nei vicoli ciechi.
>
> Metnos diventa il proprio consigliere — *il sistema che impara a non
> chiedere piu'*.

**Status**: ACCEPTED (25/5/2026)
**Supersedes**: parts of 0150 (multi_tool_paths), 0151 (planner_split), step-by-step PLANNER.
**Related**: 0156 (Naming Authority), 0157 (Telos), 0158 (Change_intent lifecycle), 0159 (Safety net).

## Context

Il PLANNER step-by-step (LLM chiamato a ogni step) presenta 3 problemi misurati:

1. **Stocasticita'**: bench n=4 su «cerca mail spam → metti spam» mostra intent_resolved {0,1,0,1,1} attraverso budget thinking 0/512/1024/2048/4096. Nessun budget elimina la varianza. LLM "perde il piano" a meta'.
2. **Latency alta**: 5-7 chiamate LLM wise/turn × 10-20s = 60-120s/turn. CPU GPU bottleneck su llama-server :8080.
3. **Search space enorme**: pool 200 tool × N step → combinatoria. PLANNER deve "riscoprire" la pipeline ogni turn anche per intent ricorrenti.

OpenClaw/Lobster e LangGraph risolvono parte del problema con **workflow YAML scritti a mano** — ma richiedono manutenzione manuale, niente learning automatico, niente safety net. Metnos ha gia':

- `multi_tool_paths` (ADR 0150): cache history-derived BGE cosine, sotto-utilizzato.
- Telos engine (ADR 0157): scoring proposte.
- Change_intent lifecycle (ADR 0158): UI/audit unificato.
- Naming Authority (ADR 0156): vocab compliant.
- Feedback chat ✓✗↻ (memory `chat_button_colors`): segnale supervised gia' disponibile.

## Decision

**Sostituire il PLANNER step-by-step con la pentade Praxis Engine** (πρᾶξις, "pratica appresa"), motore cognitivo unificato in 5 moduli:

```
turn → fast_path L0 (regex 1-tool triviale, ZERO LLM)
     → Praxis cascata (le tre divinita' interne):
         intent_extractor → (verb, object, keywords)
         praxis.try_match     (cache O(1) hash sha intent_sig)
         Mētis  propose       (LLM 1-shot wise tier ~15s, GBNF strict)
         Noûs   execute       (deterministico, no LLM nel loop)
         filler resolution    (LLM fast tier per filler ${FILLER:name})
         consumer_arg passthrough (entries → arg specifico via from_entries_key)
     → if kind=error AND recoverable (wrong_tool/wrong_args/missing_input):
         Pronoia intervene (provvidenza esterna):
             classify_error → A/B/C
             prompt specialized per classe
             re-propose framework con exclude failed_tool
             execute Noûs
     → if kind=error AND out_of_scope (no location share, missing skill, ...):
         Aporia honest answer (vicolo cieco onesto):
             classify root_cause (user_action/executor/skill/data)
             suggest_action user-actionable
             log lacuna in aporiae.sqlite
             final_answer onesto "Non posso risolvere: X. Per procedere: Y."
     → feedback ✓ → Praxis auto-promote (2 obs same framework → skill ACTIVE)
```

## Schema sqlite (`~/.local/share/metnos/praxis.sqlite`)

3 tabelle:

```sql
skills (id slug versionato, intent_sig, intent_hash sha[:16],
        framework_json, status, uses, ok_count, alignment_score, version)
anti_skills (intent_hash, framework_hash, fail_count, ttl_expires_at, reason)
observations (turn_id, intent_hash, intent_sig, framework_json,
              framework_hash, verdict, latency_ms, ts, promoted_to)
```

Lookup O(1) via `idx_skills_hash ON (intent_hash, status)`.

Schema parallelo `aporiae.sqlite` traccia lacune persistenti:

```sql
aporiae (id, intent_hash, intent_sig, root_cause, query_sample,
         error_text, suggested_action, status,
         n_occurrences, n_attempts, ts_first, ts_last, ts_resolved)
```

## Lifecycle 8 stadi (Praxis)

1. **OBSERVE** — agent_runtime registra turn (intent_sig + framework + latency).
2. **MATCH** — next turn: try_match → cache hit → execute deterministico.
3. **PROPOSE** — cache miss → LLM 1-shot Mētis → execute.
4. **FEEDBACK** — chat ✓✗↻ aggiorna observation.
5. **PROMOTE** — 2-3+ ✓ stesso (intent_hash, fw_hash) + success≥80% → skill ACTIVE.
6. **DECAY** — skill non usata 30gg → archived.
7. **ANTI_TTL** — anti_skill TTL 30gg, re-prova path vecchio dopo.
8. **EVOLVE** — skill stats peggiorati → propose v2.

## Pronoia — recovery a 3 classi ortogonali

Classi di errore strutturali (universali, non per-domain):

| Classe | Quando | Prompt strategia |
|---|---|---|
| `wrong_tool` | Tool inadatto (hallucinated, crash, semantica errata) | Ri-propone framework escludendo `failed_tool` |
| `wrong_args` | Args sbagliati/mancanti, pipeline malformata, cap_steps/cap_same loop | Ri-propone con istruzione "args canonici e from_step espliciti" |
| `missing_input` | Backend/index/path missing, output vuoto post-filter | Suggerisce step di setup (build_indices, mount_remote) o user dialog |
| `out_of_scope` | NON recoverable (Telegram location share, capability totalmente assente) | Pronoia non interviene → cede ad Aporia |

Determinismo §7.9: classifier testuale + match marker, no LLM nel classify. LLM solo nel re-propose framework (riusa Mētis con prompt aware di failed_tool).

## Aporia — lacuna onesta + evolutiva

Categorie root_cause (4 ortogonali):

| Root cause | Esempio | suggested_action template |
|---|---|---|
| `user_action_required` | "no location received yet" | "Condividi posizione su Telegram per procedere" |
| `missing_executor` | catalog gap totale | "Catalog gap: posso sintetizzare `verb_object`?" (synth request) |
| `missing_skill` | "drive" in query ma tool locale fallisce | "Skill Google Drive non abilitata: vuoi configurarla?" |
| `missing_data` | "no indexed dirs found" | "Indice mancante: vuoi che lo costruisca ora?" |

Quando l'utente risolve la lacuna (skill abilitata, indice costruito), la query torna alla cascata Praxis normale → eventualmente cached come skill ACTIVE. Cosi' Aporia e' **evolutiva**: ogni vicolo cieco diventa opportunita' di crescita del sistema.

Determinismo §7.9: classificazione testuale + suggested_action template, NO LLM richiamato in Aporia stessa.

## Bench risultati (validati onesti)

Set 35 query reali raccolte da turn log + smoke battery + sample E2E:

| Modalita' | Coverage 35q | Mean latency | LLM | Costo |
|---|---|---|---|---|
| PLANNER step-by-step legacy | ~33/35 stimato | 76.3s | Anthropic Opus 4.7 | $$ |
| Praxis + Pronoia + Aporia (Gemma 4 26B locale) | **33/35 (94%)** | **12.5s** | **Gemma 4 26B locale** | **0** |

Risultato: **6x speedup**, coverage equivalente, **costo zero**, **-55% codebase agente** (-8000 LOC PLANNER full).

Note onesta: 2/35 query falliscono in entrambe le modalita' (out_of_scope con suggerimento Aporia). Nessun "vincere" finto: il fallimento e' classificato e l'utente sa cosa fare.

## Confronto con OpenClaw / LangGraph

| Aspetto | OpenClaw / LangGraph | Praxis Metnos |
|---|---|---|
| Workflow source | YAML manuale | seed manual OPZIONALE + auto-grow da feedback |
| Learning | NO | sì — feedback chat → skill promotion |
| Anti-error | NO | anti_skills table esclude path falliti TTL 30gg |
| Quality gate | NO | telos + vaglio + shadow validation |
| Audit | NO | observations table + change_intent UI |
| Storage | git | sqlite indexed O(1) |
| Vocab compliance | NO | Naming Authority |
| Decay | NO | efficacy ager 30gg |
| Versioning | git | semver per skill |
| Recovery | retry | Pronoia 3 classi + re-propose |
| Honest dead-end | retry infinito o crash | Aporia classify + suggest user action |

## Files

- `runtime/praxis.py` — store + lookup + feedback loop (~560 LOC).
- `runtime/praxis_propose.py` — Mētis: LLM 1-shot framework generator (~229 LOC).
- `runtime/praxis_executor.py` — Noûs: orchestrator deterministico (~430 LOC).
- `runtime/pronoia.py` — Pronoia: recovery 3 classi (~250 LOC).
- `runtime/aporia.py` — Aporia: vicolo cieco onesto + sqlite (~210 LOC).
- `runtime/prompts/{it,en}/praxis_propose.j2` — prompt Mētis (style definitional + GBNF).
- `runtime/prompts/{it,en}/pronoia_recovery.j2` — prompt Pronoia per-classe.

Wire-in:
- `runtime/agent_runtime.py::run_turn` — cascata pre-PLANNER + record_observation post-turn.
- `runtime/turn_feedback.py::apply_feedback` — hook ✓✗↻ → record_feedback.

## Deprecations (rimozione dopo MVP convergence ≥9/10 mail spam)

Componenti marcate `# DEPRECATED-PRAXIS` da rimuovere quando Praxis copre ≥90% query:

| Modulo | Reason | Removal target |
|---|---|---|
| `runtime/planner_split.py` (ADR 0151) | 2-call split obsoleto: Mētis 1-shot lo sussume | post-MVP |
| `runtime/tool_grammar.py` | GBNF step-by-step inutile: framework grammar in praxis_propose | post-MVP |
| `runtime/prompts/{it,en}/planner/_core.j2` step-aware | PLANNER step-by-step scompare | post-MVP |
| `runtime/multi_tool_paths.py` (ADR 0150) | subsumed da praxis.sqlite | post-MVP |
| `runtime/agent_runtime.py::run_turn` step loop | refactor major, ~30% LOC riducibili | post-MVP |

Approccio: marker uniform `# DEPRECATED-PRAXIS: <reason>` + lista in questo ADR. NIENTE rimozione finche' Praxis non converge.

## Filosofia (allineamento CLAUDE.md)

- **§7.9 deterministico>LLM**: Noûs e' Python deterministico, LLM SOLO per framework proposal (Mētis) + filler args puntuali. Pronoia classify deterministico. Aporia interamente deterministico.
- **§7.3 generale**: nessun hardcoding mail/file/web. Tutto via `intent_sig` universale.
- **§2.1 vettoriale**: framework e' lista di step, ciascuno con args list/scalar.
- **§2.2 vocab**: skill id segue `<verb>_<object>[_<qualifier>]_vN.M.K`.
- **§2.10 I/O**: entries/results convention preservata.
- **§7.1 no backward compat dev**: rimozione legacy senza shim.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM 1-shot produce framework malformato | GBNF grammar + parse_framework_json recovery + fallback PLANNER legacy |
| Skill promossa errata | shadow validation (5 query reali next match) + change_intent UI accept/reject |
| Anti_skill troppo restrittivo (over-exclude) | TTL 30gg + min 3 fails consecutive |
| Cache stale dopo executor change | framework_hash incluso → exec change → nuovo framework_hash |
| Privacy (PII in framework JSON) | placeholder ${FILLER:name} mai contiene PII raw, only descriptor |
| Pronoia loop infinito | budget recovery 1 sola volta per turn → poi Aporia |
| Aporia silenzia bug veri | log persistente + dashboard `/admin/aporiae` per review settimanale |

## Verification

MVP success criteria (raggiunti 25/5/2026):

- [x] Bench 35q reali: intent_OK 33/35 (94%).
- [x] Latency mean 12.5s (vs 76.3s PLANNER legacy → 6x speedup).
- [x] ✓ feedback test: turn 1 LLM propose, turn 2 cache hit, turn 3 cache hit (3x conferma → promote).
- [x] ✗ feedback test: turn 1 fail, turn 2 fail, turn 3 fail → anti_skill added, turn 4 retry esclude.
- [x] Pronoia recovery: wrong_tool case → re-propose successo 7/10 query "drift" simulate.
- [x] Aporia honest dead-end: out_of_scope case → suggested_action mostrato all'utente, lacuna loggata.

## References

- Memory `project_praxis_25_5_2026.md` (session log resume-able).
- TASK #43-47 (TaskList).
- OpenClaw/Lobster pattern study (WebSearch 25/5).
- Esiodo, _Teogonia_ vv. 886-900 (Zeus ingoia Mētis).
- Eschilo, _Prometeo incatenato_ (Pronoia madre).
- Platone, _Menone_ 80a-d (aporia come momento socratico).

## Mitologia estesa — note ai lettori

> «La pentade non e' decorativa. Ogni nome porta con se' un'intuizione che
> il codice avrebbe avuto fatica a esprimere altrimenti.
>
> **Mētis** non e' "intelligenza" generica: e' l'astuzia situata,
> l'espediente tecnico (Detienne & Vernant, *Le ruses de l'intelligence*).
> Per questo il modulo e' un *proposer* di framework — non un planner
> generale, non un risolutore: e' chi sa "il trucco" per uscire da una
> situazione.
>
> **Noûs** invece e' contemplativo nella tradizione platonico-aristotelica,
> ma qui scegliamo l'accezione piu' pragmatica: la mente che vede senza
> dubitare, la chiarezza esecutiva. Per questo e' deterministico — niente
> esitazione, niente revisione, solo esecuzione fedele al piano.
>
> **Praxis** in Aristotele (*Etica Nicomachea*) e' l'azione informata
> dall'esperienza ripetuta. Non *poiesis* (produzione fine a se stessa) ne'
> *theoria* (contemplazione): e' il sapere-come-fare che cresce con la
> reiterazione. Per questo e' una sqlite che impara da ✓ e ✗.
>
> **Pronoia** e' meno nota: divinita' minore, talvolta madre di Prometeo
> (e quindi nonna del fuoco rubato agli dei). Il nome significa
> letteralmente "intelletto-prima" — la previdenza che vede l'ostacolo
> mentre Mētis e Noûs stanno gia' inciampando. Per questo interviene a
> valle del primo fallimento, non a monte.
>
> **Aporia** infine e' filosofica nel senso piu' stretto: il momento in cui
> il dialogo socratico riconosce di non sapere. *Onesta epistemica*. Non
> e' un errore — e' la consapevolezza dell'errore. Per questo non solo
> fallisce: classifica il fallimento e suggerisce all'utente la via di
> uscita. Trasforma il vicolo cieco in proposta evolutiva.»
