# Quiescenza proposte introvertive — design 3/5/2026 sera

Vedi `runtime/proposals_state.py` per implementazione, e
`runtime/scheduler.py::task_introvertiva_propose` per l'integrazione.

## Stati

- `pending`: proposta nuova o riemersione recente. Visibile in `get_proposals`.
- `dormant`: proposta che è stata generata per N notti consecutive (default 3)
  senza accept/reject. Non visibile in `get_proposals` di default.
- (transitorio) re-emersione: una `dormant` torna `pending` se il dato
  sottostante sale di +30% (es. `uses` o `dominance`).

## Soglie default (configurabili via env)

| Env var | Default | Significato |
|---|---|---|
| `METNOS_PROPOSALS_DORMANCY_NIGHTS` | 3 | notti consecutive di silenzio prima di → dormant |
| `METNOS_PROPOSALS_REEMERGE_FACTOR` | 1.30 | fattore di rinforzo per uscire da dormant (uses_now / uses_at_dormant) |
| `METNOS_PROPOSALS_DEDUPE_KEY` | (canonical) | come identificare «la stessa proposta» (vedi `_canonical_key`) |

## Schema SQLite (`~/.local/state/metnos/proposals_state.db`)

```sql
CREATE TABLE proposals_state (
    sig_key       TEXT PRIMARY KEY,           -- canonical key, JSON-serialized
    kind          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    first_seen    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_uses     INTEGER NOT NULL DEFAULT 0,
    n_seen        INTEGER NOT NULL DEFAULT 1,
    dormant_since TEXT,
    dormant_uses  INTEGER,                    -- snapshot di uses al momento di → dormant
    last_action   TEXT,                       -- 'approve' | 'reject' | 'block' (futuro)
    last_action_at TEXT
);
```

## Flusso al fire del task notturno

```
for each candidate in introvertiva.run_all():
    sig = canonical_key(candidate.kind, candidate.payload)
    row = proposals_state.find_by_sig(sig)

    if row is None:
        # Nuova proposta
        proposals_state.insert(sig, kind, state='pending', last_uses=...)
        continue

    if row.state == 'pending':
        # Aggiorna last_seen, n_seen
        proposals_state.touch(sig, last_uses=...)
        if row.n_seen >= DORMANCY_NIGHTS and last_action is None:
            proposals_state.go_dormant(sig, dormant_uses=row.last_uses)

    elif row.state == 'dormant':
        # Verifica re-emersione
        if last_uses > row.dormant_uses * REEMERGE_FACTOR:
            proposals_state.reawaken(sig)
```

## Filtraggio in `get_proposals`

Default: filtra `state in ('pending')`. Se l'utente chiede esplicitamente
le dormant («mostra anche le proposte sopite»), passare `include_dormant=true`.

## Integrazione con set_proposals (futuro)

Quando esisterà `set_proposals(action="approve|reject|block")`,
l'azione aggiorna `last_action` + `last_action_at` e fa transizione di
stato:
- approve → state='applied' (uscita dal flusso pending/dormant)
- reject → state='dormant' immediato
- block → state='blocked', mai più riproposta
