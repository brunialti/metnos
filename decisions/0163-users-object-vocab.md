# ADR 0163 — persons aggregator + `${RUNTIME:*}` placeholder + verb `read` per identità

**Date**: 2026-05-26
**Status**: accepted
**Supersedes**: nessuna (sostituisce draft "users object")
**Related**: ADR 0083 (multi-user host/guest), ADR 0113 (persons registry), ADR 0161 (Praxis Engine), §2.2 vocab compositional, §7.3 universalità

## Context

Query live 26/5 "chi sono io" → Mētis sceglieva `get_persons() → describe_entries` → output "Le 4 entry analizzate: nessun contenuto rilevante". User ✗.

Root cause: §2.2 vocab ha `persons` (enrollment biometrico, ADR 0113) ma stessa persona vive in 3 storage diversi senza visione unificante:
- `persons.sqlite`: volto enrollato (ArcFace + n_examples + image_paths)
- `users.db`: account Metnos paired (ADR 0083: host/guest, role, autonomy, channels)
- `contacts` (futuro): email/phone/anagrafe

Tre OBJECTS distinti = LLM deve sapere QUALE interrogare per QUALE attributo → frizione + bug. La sessione 26/5 ha esplorato 4 alternative:

A. **Migrare persons.sqlite a master** con role/autonomy columns → rottura ADR 0083, schema change risky → SCARTATA
B. **Layered `get` vs `read`** sfruttando §2.2 PRODUCER_VERBS già esistenti → SCELTA
C. **Nuovo OBJECT `users` separato + cross-link slug** → vocab churn 19→20, "stessa persona" sparsa → SCARTATA
D. **Decomposizione qualifier `get_persons_account/_contact/_self`** → estensione QUALIFIERS richiesta + surface 3-4 executor → SCARTATA

## Decision

### 1. `persons` come master logical entity in vocab §2.2

`runtime/vocab.py::OBJECTS` resta 19 (no nuovi). `users`/`contacts` sono runtime-internal (backend storage), NON visibili al PLANNER come OBJECTS distinti.

Commento aggiunto in vocab.py§2.2 dopo `"entries"`:
```
# NB §2.2 (26/5/2026, ADR 0163): `users` NON è OBJECT vocab. L'account
# Metnos paired (host/guest, ADR 0083) è runtime-internal, esposto al
# PLANNER come ATTRIBUTI di `persons` via `read_persons` aggregator.
```

### 2. `get` vs `read` per `persons` (sfrutta §2.2 PRODUCER_VERBS già esistente)

§2.2 vocabolario chiuso: `get` = id noti o snapshot; `read` = id sorgente → contenuto.

| Verb | Executor | Output | Scope storage |
|---|---|---|---|
| `get` | `get_persons` (invariato) | Scheda registro `{slug, name, n_examples}` | persons.sqlite only |
| `read` | `read_persons` (NUOVO) | Profilo completo `{slug, name, role, autonomy, examples, channels, is_self}` | persons.sqlite + users.db JOIN |

Mētis sceglie naturalmente in base al semantic della query:
- "chi è registrato" / "lista persone" / "quante foto di Matteo" → `get` (lookup snapshot)
- "chi sono io" / "mio profilo" / "dimmi tutto su X" / "lista guest paired" → `read` (contenuto profilo)

### 3. Placeholder `${RUNTIME:key}` (nuovo pattern universale)

Sintassi `${RUNTIME:key}` in args di executor, risolto da `runtime/praxis_executor.py::_resolve_runtime_placeholders` al turno corrente. Whitelist chiusa §7.9:

| Key | Valore | Note |
|---|---|---|
| `actor` | nome user corrente | Generic "host"/"guest" risolto a `display_name` reale via `users.db` |
| `lang` | "it"/"en" | Lingua corrente |
| `channel` | "telegram"/"http"/... | Canale invocazione |

Pattern in Mētis prompt: `read_persons(name="${RUNTIME:actor}")` per "chi sono io". Il resolver:
1. Sostituisce stringa-encoded `${RUNTIME:actor}` con valore (es. "Roberto").
2. Inietta `_actor`/`_lang`/`_channel` come args "hidden" (prefix `_` = runtime, mai emesso da LLM) per executor che vogliono accedere al contesto programmaticamente (es. `is_self` flag).

Parallelo a `${FILLER:name}` (ADR 0090) e `${stepN.field}` (ADR 0161). Pattern §7.3 universale: ogni nuovo runtime value diventa una key whitelist + resolver lookup, mai estensione manifest §2.5 con `self=true` booleani ad-hoc.

### 4. Pattern `PROFILO_IDENTITARIO` in praxis_propose.j2 IT

Nuova sezione MATCH/NO_MATCH/PIPELINE/OK/ERRORE con esempi:

```
PROFILO_IDENTITARIO

MATCH: "io"/"chi sono"/"mio profilo"/"dimmi tutto su X"/"lista guest"
NO MATCH: "chi è registrato" → get_persons; "trova foto X" → find_persons_indices

PIPELINE
- step1: read_persons(name="${RUNTIME:actor}" | name=X | role=Y)
- step2: final_answer
- ZERO compute_entries, ZERO describe_entries (output entry singola arricchita)

OK: {"tool":"read_persons","args":{"name":"${RUNTIME:actor}"}} per "chi sono io"
OK: {"tool":"read_persons","args":{"name":"Lucia"}} per "dimmi tutto su Lucia"
OK: {"tool":"read_persons","args":{"role":"guest"}} per "lista guest paired"
ERRORE: get_persons per "chi sono io" — ritorna scheda registro, manca role/autonomy

OUTPUT
final_message: "Sei ${step1.entries.0.name} (${step1.entries.0.role}, ...)"
```

### 5. Renderer extension: `entries.N.field` list index

`praxis_executor::_resolve_dotted` esteso per supportare index numerico list:
- `entries.0.name` → `result["entries"][0]["name"]`
- Universal §7.3, non solo per persons.

Era limitato a dict-only traversal. Ora gestisce list.isdigit() come index.

### 6. read_persons code: cross-link bidirezionale persons↔users

`executors/read_persons/read_persons.py` JOIN via `slugify(name)` + token-anywhere fallback su display_name (PersonsRegistry.resolve_name). Risolve discrepanza naming:
- `persons.slug="roberto_brunialti"` vs `users.name="roberto"` → match via token "roberto" sussiste in entrambi
- Generic actor "host"/"guest" → resolved a `display_name` reale via `users.db` in `_build_runtime_resolvers`

### 7. intent_extractor.j2 IT few-shot

Aggiunti 9 few-shot per disambiguare get vs read su persons:
```
"chi sono io" → {"verb": "read", "object": "persons"}
"who am I" → {"verb": "read", "object": "persons"}
"mio profilo" → {"verb": "read", "object": "persons"}
"dimmi tutto su Lucia" → {"verb": "read", "object": "persons"}
"lista guest paired" → {"verb": "read", "object": "persons"}
"chi e' registrato" → {"verb": "get", "object": "persons"}
"elenca persone" → {"verb": "get", "object": "persons"}
"quante foto di Matteo" → {"verb": "get", "object": "persons"}
```

## Consequences

### Pro

- LLM Gemma 26B vede UN concetto persons + 2 verbi distinti (get/read) — pattern già familiare (`get_messages`/`read_messages`, `get_files`/`read_files_pdf`)
- ZERO modifiche vocab §2.2 (OBJECTS resta 19; estensione `users` SCARTATA)
- ZERO migrazione storage (persons.sqlite + users.db restano separati; JOIN runtime)
- Pattern `${RUNTIME:*}` riusabile per ogni runtime value futuro (no proliferazione args `self=true`/`lang=current`/etc.)
- Renderer extension `entries.N.field` utile cross-domain, non solo persons
- Validato live 26/5: 3 query in 3-8s ciascuna, output corretti

### Contro

- `${RUNTIME:*}` aggiunge un nuovo placeholder convention. Mētis deve impararlo (1-2 esempi few-shot bastano)
- ClusterLLM mette in stesso cluster "chi sono io"/"dimmi tutto su X"/"lista guest" → riusa stessa skill cross-query. Mitigazione: pulire skill cache dopo prima esecuzione errata, OR arricchire `intent_sig` con dominant arg semantic (TODO)
- Template `PROFILO_IDENTITARIO` hardcoded "Sei X..." → wrong per query non-self. Mētis dovrebbe variare template per intent (TODO)
- Cross-link slug fragile per nomi con accenti/varianti: token-anywhere resolution mitiga ma non garantisce 100% match

### Neutro

- Bridge enrollement→web (query "foto sul web simile a enrollement di Roberto") NON risolto da questo ADR. Pipeline ora 2-step corretta semanticamente (`find_persons_indices → find_images_web`) ma usa face-match corpus 3381 entries invece delle 4 foto enrollate. Necessario read_persons + projection nested `examples.*.image_path` come reference_images. Scope futuro.

## Implementation summary (validato live 26/5)

| Component | File | LOC |
|---|---|---|
| Resolver `${RUNTIME:*}` | `runtime/praxis_executor.py` | +50 |
| Wire-in runtime_ctx | `runtime/agent_runtime.py` | +5 |
| Renderer list index | `runtime/praxis_executor.py::_resolve_dotted` | +5 |
| Executor read_persons | `executors/read_persons/{manifest.toml, read_persons.py}` | 220 + manifest 100 |
| Pattern PROFILO_IDENTITARIO | `runtime/prompts/it/praxis_propose.j2` | +30 |
| Intent few-shot | `runtime/prompts/it/intent_extractor.j2` | +9 |
| Vocab note | `runtime/vocab.py` | +7 commento |

Test 35/35 verdi. Bench live:
- "chi sono io" → `read_persons(name="${RUNTIME:actor}")` → "Sei Roberto Brunialti (host, autonomy: full)" — 3.2s
- "dimmi tutto su Silvia" → `read_persons(name="Silvia")` → "Sei Silvia Buffa (guest, autonomy: restricted)" — 8.5s
- "lista guest paired" → `read_persons(role="guest")` → "1 guest paired" — 7.9s

## Open follow-ups

1. EN parity: `praxis_propose.j2` + `intent_extractor.j2` EN ancora minimali. Deferred via `METNOS_LANG_DEFER=en` al commit.
2. Template variant in PROFILO_IDENTITARIO: "Sei" funziona per self; per "dimmi tutto su X" serve "X è ...". Mētis dovrebbe variare template.
3. Cluster differentiation: ClusterLLM riusa stessa skill cross-query persons. Arricchire `intent_sig` con dominant arg (self vs name vs role).
4. Bridge enrollement→web: usare `read_persons(name=X).entries.0.examples.*.image_path` come reference_images per find_images_web (richiede projection nested in renderer).
5. CLAUDE.md §5 (vincoli di dominio): aggiungere mapping query identità → read_persons.
