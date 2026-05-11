---
id: 0060
title: Reverse patterns — schema normalization + restore_blob_backup implementato
date: 2026-04-30
status: accepted
area: runtime, undo
related:
  - 0057  # synt-stage5-modular-verb-prompts
  - 0058  # intent extractor
modifies: []
---

## Context

Sera 29/4 → notte 30/4: l'undo system aveva tre bug che si combinavano per
rendere l'undo inaffidabile:

1. **Schema mismatch nested vs flat** (swap_src_dst):
   `move_messages` produceva `{src: {account, folder, uid}, dst: {...},
   message_id}`. Il pattern `_swap_src_dst_imap` aspettava
   `{account, src: folder, dst: folder, uid, message_id}` (flat). Sniff
   `if "account" in first` falliva → fallback a FS branch → `Path(p["dst"])`
   su un dict → behaviorally degraded a no_op.

2. **Undo marca "undone" anche on failure** (undo_last_turn.py:97):
   `log.append_undone()` chiamato indipendentemente da `rev_result.ok`.
   Le ops fallite uscivano dallo stack `latest_turn_done()` → impossibili
   da retentare anche dopo fix del pattern.

3. **restore_blob_backup STUB** (reverse_patterns.py:256):
   Ritornava `{"ok": False, "error": "not yet implemented"}`. Significa
   che `delete_files`/`write_files` con `revertible=true` produvano blob
   backup ma il runtime non sapeva ripristinarli.

Inoltre il prompt di stage 1 (NAMING) hardcodava `delete = revertible:false`
senza considerare il caso "delete con snapshot blob".

## Decision

### Schema normalization in swap_src_dst dispatcher

In `reverse_patterns._swap_src_dst`, sniff a 3 schemi:

  - FLAT IMAP:   `{account, src: folder, dst: folder, uid, message_id}`
  - NESTED IMAP: `{src: {account,folder,uid}, dst: {account,folder,uid}, message_id}`
  - FS:          `{src: "/path", dst: "/path"}`

Helper `_normalize_imap_pair(p)` converte nested → flat per il resto della
pipeline. `_dst_uid` conserva il nuovo UID per audit. Cosi' lo SCHEMA del
prompt move (nested, leggibile) e lo SCHEMA del runtime (flat, legacy)
co-esistono senza forzare uno dei due a cambiare.

Inoltre `_q_imap` per quoting dei nomi folder con spazi/non-ASCII (caso live:
`select Posta indesiderata` non quotato → folder fantasma `INBOX.Posta`
creata da Cyrus). E `M.uid("SEARCH", None, "HEADER", ...)` corretto (charset
None che mancava).

### Undo only marks done on success

In `undo_last_turn.py:97`, condizione di append:

```python
is_actually_undone = (rev_result.get("ok_count") or 0) > 0
if is_actually_undone:
    log.append_undone(rec["op_id"], rev_result)
```

Se il reverse non ha ribaltato NULLA, lasciamo l'op come "done not undone"
per poterla ritentare quando il bug viene fixato. Coerente con
`feedback_no_silent_failure`: meglio explicitly retry-able che silently
"undone" senza esserlo.

### restore_blob_backup — implementato

`_restore_blob_backup(plan, results)`:

  - Per ogni entry in `results.results`:
    - Trova `blob_path` (chiave esplicita) o lookup via
      `blob_sha256` in `$METNOS_HISTORY_DIR`.
    - Legge bytes dal blob.
    - Sniff: `path` → restore FS (`Path(p).write_bytes(raw)`).
    - `account+folder` → restore IMAP (`M.append(folder, raw)`).
  - Best-effort per-entry; connessioni IMAP riusate per account.

Convenzione storage blob (richiesta agli executor):
  `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`
  - `METNOS_HISTORY_DIR` default: `~/.local/share/metnos/_history`
  - `METNOS_TURN_ID` esposto da `agent_runtime.invoke_executor` al subprocess.
  - Naming sha256: deduplica blob identici fra entries.

Stage 1 NAMING aggiornato per riconoscere il caso "delete revertible con
backup": `delete/write con overwrite → revertible=true SOLO SE l'intent
menziona snapshot/backup/blob`. Altrimenti false (default sicuro per
operazioni distruttive).

### Universal helpers in tools_for_step

`agent_runtime` include sempre nei tool del planner:
  - `classify_entries`, `filter_entries` (data manipulation)
  - `undo_last_turn` (utente puo' annullare in qualunque momento)
  - `describe_entries` SOLO per verbi non-action (read/list/find/describe).
    Per verbi d'azione e' magnetic-tool che fa deviare il planner.

## Test

E2e validato:
  - `delete_files` synthesized con revertible=true + blob backup logic.
  - PRE: `/tmp/metnos_undo_e2e_test.txt` con contenuto.
  - DELETE: file rimosso, blob a `~/.local/share/metnos/_history/e2e-undo-test/blob/<sha>.bin`.
  - RESTORE: bit-perfect ripristino.

E2e su mail (live):
  - Move IMAP knowcastle+tiscali (17+7 mail) ✓
  - Undo knowcastle (17 mail ripristinate) ✓ (dopo fix schema)
  - Undo tiscali (7 mail ripristinate) ✓ (dopo fix _q + create+subscribe)

## Consequences

### Positive
- L'undo ora funziona per TUTTI i reverse_pattern del registry.
- Schema mismatch nested/flat tollerato in dispatcher → niente coupling
  fra prompt verbi e runtime.
- Failed undo retentabile dopo fix di bug a valle.
- IMAP folder quoting + create+subscribe per resilienza cross-provider.

### Negative
- restore_blob_backup richiede `blob_path` nei results: gli executor
  pre-existing senza questa logica non sono ribaltabili anche se
  `revertible=true` (no graceful migration).
- Storage blob cresce con l'uso. Necessita garbage collection (oltre il
  TTL del turn, blob rimangono finche' non si rimuovono manualmente).
- IMAP append non preserva flags originali (\Seen/\Flagged ecc.). Per
  ora best-effort: i messaggi tornano come "non letti".

### Mitigazioni & TODO
- TODO: GC storia blob (cron / flag --cleanup).
- TODO: preservare flags IMAP nel restore (FETCH FLAGS in append).
- TODO: estendere browsing degli undo disponibili e visualizzare
  lifecycle (pending/done/undone) come tool dedicato.
- TODO: support delete IMAP con blob backup (sintesi delete_messages
  con stage 1 che riconosce intent → revertible=true).

## Status

`accepted`. Implementato e validato e2e 30/4/2026 ore 00.
