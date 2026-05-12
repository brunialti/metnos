---
id: 0128
title: Importer verb-as-data boundary (skill agentskills.io → vocab Metnos §2.2)
date: 2026-05-12
status: accepted
area: synt | naming | importer
related:
  - 0045  # naming convention closed vocabulary
  - 0114  # synth admission policy 4 layers
  - 0123  # skill importer agentskills.io
  - 0126  # L7 admission imported skills
  - 0127  # qualifier _empty
extends:
  - 0123
modifies:
  - 0123  # `actions` flat ora ha priorita' bassa rispetto a `contextual`
---

## Context

L'importer skill agentskills.io (ADR 0123) produce executor importati da
SKILL.md remoti, applicando una mapping `skill_vocab_map.json` per
tradurre `(provider, action)` → `(verb, object, qualifier)` del vocab
chiuso §2.2. La tabella originale era piatta: chiavi = action stringa
(es. `update`, `share`, `create`), valore = `metnos_verb`.

L'audit verb del 12/5/2026 (task #19) sull'import live `google-workspace`
(24 executor) ha trovato **8+ cluster di drift sistemico**:

- **get→read drift (5 cluster)**: Google API verb `get` mappato a Metnos
  `get`, ma §2.2 distingue `get` (lookup snapshot / metadata) da `read`
  (id sorgente → contenuto). Esempi: `gmail get MESSAGE_ID` ritorna il
  contenuto del messaggio, quindi e' `read_messages` non `get_messages`.
  `drive download FILE_ID` idem (Sprint S commit `140c8da` ha gia'
  chiuso H1-H4).
- **change overloaded (4 cluster)**: `docs append`, `sheets update`
  (cells), `gmail modify` (labels) tutti mappati a `change_*`. Ma §2.2
  riserva `change` a forma/parametri (resize/rotate/convert): un'immagine
  resta un'immagine. `append` su un Doc modifica il BODY → `write`.
  Aggiornare CELLE di uno sheet modifica il body strutturato → `write`.
  Modificare labels di un'email = upsert idempotente di stato → `set`
  (non `change`).
- **set overloaded**: `drive share` mappato a `set_files`, ma share crea
  un permission/ACL grant remoto (outbound consent), non un upsert
  idempotente di stato interno al record. `docs create` / `sheets create`
  / `calendar create` mappati a `set_*` ma sono creazioni terminali, non
  upsert (l'ID e' assegnato dal server, non dal client).

**Risk**: synth e PLANNER LLM si confondono sulla mapping verb→behavior.
Query nuove allucinano routing: «condividi il foglio con X@mail.com» va
a `set_files` invece di esistere un `share_files`, oppure il PLANNER
sceglie un synth fresco (`request_new_executor`) bypassando il
binding canonical. Il bug live 11/5 (`read_appointments` synth con 119s
sprecati) e' uno dei sintomi del problema piu' generale.

**Stato pre-decisione**: 1424 PASS / 0 FAIL post `140c8da`. 21 executor
importati google-workspace (post Sprint S rinomine triviali H1-H4).
Cluster sistemici H5-H10 ancora aperti.

## Decision

**Il vocab §2.2 si applica integralmente anche agli executor importati.**
Niente "dialetto importer". Nessuna deroga di compatibilita' con la
nomenclatura API del provider.

### 1. Tabella di mapping contestuale (`skill_vocab_map.json::contextual`)

Aggiungiamo accanto al mapping piatto `actions` (LEGACY) una tabella
**contestuale** indicizzata da `<domain>:<action>` con tripla canonica
`(target_kind, side_effect, verb)`:

```json
"contextual": {
  "calendar:list":  {"target_kind":"content",  "side_effect":"fetch_content", "verb":"read"},
  "calendar:update":{"target_kind":"state_labels","side_effect":"state_update","verb":"set"},
  "calendar:create":{"target_kind":"create_only","side_effect":"resource_create","verb":"create"},
  "drive:share":    {"target_kind":"share_access","side_effect":"acl_grant",  "verb":"share"},
  "drive:upload":   {"target_kind":"body",     "side_effect":"body_modify",   "verb":"write"},
  "docs:append":    {"target_kind":"body",     "side_effect":"body_modify",   "verb":"write"},
  "sheets:update":  {"target_kind":"state_labels","side_effect":"state_update","verb":"set"},
  "gmail:modify":   {"target_kind":"state_labels","side_effect":"state_update","verb":"set"},
  ...
}
```

`target_kind` ∈ {`content`, `metadata`, `body`, `shape`, `state_labels`,
`share_access`, `create_only`, `snapshot_only`}.
`side_effect` ∈ {`fetch_content`, `fetch_metadata`, `body_modify`,
`shape_modify`, `state_update`, `acl_grant`, `resource_create`,
`resource_delete`}.

**Regole derivative** (tabella `verb_by_target_side`):
- `(content, fetch_content)` → `read` (fetch contenuto).
- `(metadata, fetch_metadata)` → `get` o `read` per liste — vedi nota.
- `(body, body_modify)` → `write` (append/upload/cells = body modify).
- `(shape, shape_modify)` → `change` (resize/rotate/convert).
- `(state_labels, state_update)` → `set` (idempotent upsert di labels).
- `(share_access, acl_grant)` → `share` (NUOVO verbo).
- `(create_only, resource_create)` → `create`.
- `(create_only, resource_delete)` → `delete`.

`resolve_name()` di `skill_translator.py` consulta PRIMA `contextual`; in
assenza, fallback su `actions` flat (legacy ADR 0123). Cosi' lo stesso
provider-verb (`gmail.modify`, `drive.share`) puo' mappare a verbi
Metnos diversi in domini diversi senza ambiguita'.

### 2. Verifier deterministico (`runtime/importer_verb_verify.py`)

Nuovo modulo per Layer 6.bis di synth admission (estende ADR 0114):
- `check_plan(plan, *, domain, action)` → `Verdict` con `aligned: bool`,
  `chosen_verb`, `expected_verb`, `mismatch_kind` ∈ {`get_drift`,
  `change_overload`, `set_overload`, `share_drift`, `share_collapse`,
  `unknown`, `vocab_map_internal_inconsistency`}, e ragione.
- `audit_existing_imports(root)` → lista `[(name, Verdict)]` per scan
  periodico dei manifest gia' importati e individuazione drift legacy.

Determinismo §7.9: zero LLM. Tutta la decisione e' tabella + lookup.
Costo O(1) per `check_plan`. Wired alla pipeline di import: import
respinge il plan con `Verdict.aligned=False`, registrando audit JSONL.

### 3. Nuovo verbo `share` (vocab.ACTIONS 22 → 23)

`share` aggiunto a `vocab.ACTIONS`, `ACTION_CATEGORIES`,
`ACTION_MAPPING` (boundary IT+EN), `DESTRUCTIVE_VERBS` (side-effect
remoto, ACL grant), e `skill_translator._METNOS_VERBS`.

**Definizione canonica** (§2.2):
> **share** = OUTBOUND CONSENT. Grant access a un'entita' senza spostarla
> o duplicarla. Crea un permission/ACL grant remoto. Distinto da `send`
> (outbound copy o notifica: il destinatario riceve un OGGETTO, es. mail)
> e da `set` (upsert idempotente di valori/labels/metadata interni al
> record). Reversibile via revoke (`delete_<obj>_permissions_by_id`).

### 4. Rinomine cluster H5-H10 (Sprint M)

Sei executor importati google-workspace rinominati per aderire alla
nuova policy:

| Old name              | source_subcommand | New name                          | Cluster |
|-----------------------|-------------------|-----------------------------------|---------|
| `change_files_text`   | docs append       | `write_files_text`                | H5 (change→write+body) |
| `change_files_xlsx`   | sheets update     | `set_files_xlsx`                  | H6 (change→set+state) |
| `change_messages`     | gmail modify      | `set_messages`                    | H7 (change→set+labels) |
| `set_files`           | drive share       | `share_files_google_workspace`    | H8 (set→share+access) |
| `set_files_text`      | docs create       | `create_files_text`               | H9 (set→create+terminal) |
| `set_files_xlsx`      | sheets create     | `create_files_xlsx`               | H10 (set→create+terminal) |
| `set_events`          | calendar create   | `create_events`                   | H10' (set→create+terminal) |

Cambia il `name` del manifest, il file `.py`, il `digest` (re-firma
§7.10), e tutti i riferimenti nelle planner sections IT+EN
(`prompts/{it,en}/planner/sections/{calendar,mail}.j2`).

`change_files_xlsx → set_files_xlsx`: motivata dalla semantica «update
delle celle = upsert idempotente di state strutturato». In alternativa
si potrebbe argomentare `write_files_xlsx` (body modify) — la chiamata
si gioca sull'idempotenza. Scelto `set` perche' l'API google-sheets
`update` accetta payload e sovrascrive la cella senza memoria di prima:
operazione idempotente. `write` resta riservata a operazioni che
producono diff con stato precedente (append).

### 5. CLAUDE.md §2.2 aggiornato

ACTIONS da 22 a 23 (aggiunto `share`). Sezione «importer verb boundary»
con riferimento a questo ADR.

## Alternatives considered

1. **Drift libero, accettare confusione**. Lascia il PLANNER LLM
   risolvere caso per caso. Costo: ogni query ambigua spreca cycle.
   Test routing assertion (smoke L5 ADR 0114) gia' rosso su 3 query
   reali del corpus. **Rifiutata**: replica il bug 11/5.
2. **Boundary solo nel synt verifier stage 6** (ADR 0114 L6, LLM-based).
   Riusa l'LLM semantic verifier. Costo: ~5-10s per import call + non
   deterministico. **Rifiutata**: §7.9 codice deterministico > LLM
   quando equipotente. La tabella copre 100% dei casi noti.
3. **Regex/heuristic post-import**. Pattern-match sul `name` per
   rilevare `change_*` + body-modify-like flag. Costo: fragile, ad-hoc
   per provider. **Rifiutata**: §7.3 soluzioni generali, no hardcoded.
4. **Escalation manuale ogni nuovo skill**. Roberto rivede ogni import.
   Costo: non scala oltre 2-3 skill. **Rifiutata**: il senso dell'importer
   ADR 0123 e' automazione.
5. **Mapping table normalizzato `contextual`** (DECISA). Tabella chiusa
   esposta come dati JSON, verifier deterministico O(1), estendibile per
   provider futuri (microsoft-graph, slack, ...) aggiungendo righe.
   Costo: tabella da mantenere — circa 40 celle oggi (4 domain × 8
   action mediamente). Manutenibile.

## Consequences

### Codice
- `runtime/vocab.py`: `ACTIONS` 22→23 (aggiunto `share`).
  `ACTION_CATEGORIES`, `ACTION_MAPPING`, `DESTRUCTIVE_VERBS` aggiornati.
- `runtime/skill_translator.py`:
  - `_METNOS_VERBS` include `share`.
  - `resolve_name()` consulta PRIMA `contextual[<domain>:<action>]`, poi
    fallback su `actions` flat.
  - `resolve_context(domain, action)` nuovo helper per il verifier.
  - `resolve_reverse_pattern("share", obj)` → `(True,
    f"delete_{obj}_permissions_by_id")`.
  - `_OUTPUT_KIND_BY_VERB["share"] = "results"`.
- `runtime/skill_vocab_map.json`:
  - Nuova sezione `contextual` (39 celle).
  - Nuova sezione `verb_by_target_side` per il verifier.
  - `actions` flat ridimensionato (vincolante solo per fallback).
  - Eliminato il vecchio `"update":{"verb":"change"}` (drift): ora
    `update` flat fallback → `set`; `contextual` override per domain.
- `runtime/importer_verb_verify.py`: NUOVO modulo (~220 LOC).
  `check_plan()` + `audit_existing_imports()` + `Verdict` dataclass.

### Rinomine
7 executor importati google-workspace + .py + manifest + signature
(§7.10) + planner section refs IT+EN.

### Doc
- CLAUDE.md §2.2 aggiornata: «23 azioni» + nuova policy «importer verb
  boundary» citando questo ADR.
- §10.6 indice aggiunto (riga sintetica).

### Cosa diventa piu' facile
- Importare nuovi skill (microsoft-graph, slack, github, ...): basta
  aggiungere righe a `contextual` per i nuovi domain/action.
- Audit periodico: `audit_existing_imports()` lista drift legacy.
- PLANNER routing: i nomi degli executor importati sono ora semanticamente
  predicibili dal modello e dagli umani.

### Cosa diventa piu' costoso
- Mantenere la tabella `contextual`. Approssimativamente 8-10 righe
  nuove per ogni provider importato. Mitigato: la tabella e' dati, non
  codice. Modificarla NON richiede rebuild ne' re-firma.

### Lavoro spawnato
- **Sprint M (questa PR)**: rinomine H5-H10 + test policy + verifier
  module + ADR.
- **Sprint M.bis (TODO follow-up)**: re-validate audit del catalog
  importato attuale via `audit_existing_imports()` post-rinomine, atteso
  Verdict.aligned=True su tutti i 21 executor.
- **Sprint N (TODO)**: estendere `contextual` per microsoft-graph quando
  arrivera' il prossimo skill (template provato).

## Status

`accepted` — la decisione e' presa dopo audit live e ha conseguenze
immediate (rinomine + nuovo verbo). Le tabelle sono dati ispezionabili,
il verifier e' deterministico, l'estensione a provider futuri e' un
template ripetitivo.
