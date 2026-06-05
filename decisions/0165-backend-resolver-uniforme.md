# ADR 0165 — Backend resolver uniforme — il provider è configurazione, non intento

**Date**: 2026-06-01
**Status**: accepted
**Related**: ADR 0155 (planner choice > runtime override), ADR 0130 (backend tree per object), ADR 0136 (provider qualifier `_<provider>`), ADR 0123 (skill gmail/drive/calendar + `delete_<object>_by_id`), ADR 0137 (persons vs contacts), ADR 0087 (CIFS/SMB canonicalize), §2.2 vocab compositivo, §7.3 universalità, §7.9 codice deterministico > LLM
**Complements**: ADR 0155 (aggiunge la 4ª eccezione disciplinata; chiarisce che 0155 governa forma/flusso, non valori di configurazione)

## Context

Durante il loop E2E del 31/5/2026 riemerge il **bug calendar #8**: un evento creato non era cancellabile. Root cause: `create_events` finiva su un backend (es. `local` ICS) e `delete_events` su un altro (es. `google_workspace`), perché in entrambi i casi era l'**LLM a emettere l'arg `client`** e lo emetteva in modo incoerente fra i due step dello stesso turno. L'id restituito dalla create non esisteva nel backend interrogato dalla delete → `deleted=False` ma final_message "cancellato" (anche una violazione §2.8, trattata a parte).

La causa profonda non è calendar-specifica. È la **lesson A3/B1** (`internal/reports/lessons_learned.md`): la selezione del provider è **configurazione, non intento**. Quale backend serve una richiesta dipende da cosa è autenticato/configurato sull'istanza, non da una decisione "intelligente". Ogni arg `client`/`account`/`provider` esposto all'LLM è un punto dove il **bias verso il pattern (enum)** vince sul colloquiale: il modello ha visto in training che i tool calendar hanno un `client`, quindi lo emette — spesso sbagliato — anche quando la query non nomina alcun backend.

Il problema si ripropone **ovunque** esistano backend multipli (insight Roberto 31/5, tabella `lessons_learned.md §B2`):

| OBJECT | arg | provider/i | stato |
|--------|-----|------------|-------|
| events | `client` | local ICS / google_workspace | **ATTIVO** (calendar #8) |
| messages | `account` | metnos_system / roberto / mykleos / knowcastle / tiscali / `all` | enum lista → bias |
| contacts/persons | provider | local / google_workspace | ADR 0137 |
| files/dirs | mount/path | local / CIFS-SMB | ADR 0087 |
| github | qualifier | first-party | ADR 0136/0141 |

Un primo tentativo di mitigazione — marcare l'arg `runtime_resolved` per **nasconderlo** dall'enum esposto al proposer (`runtime/engine/proposer.py:75-82`) — si è rivelato insufficiente (lesson B4, verificata 1/6): il proposer mostra solo la prima frase della description (`desc.split(".")[0]`, riga 70) e **l'LLM emette `client` comunque da training**. Test live: query senza "locale", LLM emette `client='local'`. Nascondere ≠ garantire.

## Decision

Un **resolver uniforme e deterministico** del backend, in `runtime/backend_resolver.py` (114 righe, nuovo), con tre fasi:

1. **DEFINE** — registry `OBJECT_BACKENDS`: per ogni object multi-backend dichiara `arg` (nome dell'arg di backend nel manifest), `providers` (ordine di preferenza), `available` (provider → bool da creds/config) e `aliases` (provider → token NL IT+EN per il match esplicito). Oggi: `events` (local/google_workspace), `files` (local), `contacts` (google_workspace).

2. **IDENTIFY** — `resolve(object, query)`: provider **esplicitamente nominato** nella query (match alias deterministico, es. "sul calendario locale" → `local`) → quello; altrimenti **default = primo provider DISPONIBILE** per ordine di preferenza (`available(p)` su creds). Fallback onesto: primo dichiarato — l'executor darà errore esplicito se non usabile.

3. **INJECT** — `resolve_backend_arg(tool_name, args, query)`: se il tool appartiene a un object del registry (`object_of()` sul nome `verbo_oggetto`), **sovrascrive** l'arg di backend col valore risolto. Hook UNICO al dispatch, `runtime/engine/executor.py:816-824`, subito dopo `_resolve_runtime_placeholders` (dopo from_step / stepref / fillers / runtime). No-op per tool non gestiti.

L'override è **incondizionato** (non condizionale al valore emesso dall'LLM): è questa la **garanzia** (lesson B4). Verificato: LLM emette `client='local'` + query senza "locale" → resolver corregge a `google_workspace`; il roundtrip create → `delete_events_by_id` con `deleted=True` prova la coerenza e chiude calendar #8.

### Rapporto con ADR 0155 (la 4ª eccezione disciplinata)

ADR 0155 vieta che il runtime **sovrascriva scelte deterministiche del planner**, elencando tre meccanismi disciplinati leciti (auto-remediation, vaglio costituzionale, fast-path pre-planner) e proibendo gli **interceptor** che fanno pattern-match su `(chosen_tool, predecessor_tool)`.

Il resolver **non viola** 0155 — lo **completa**, per due ragioni congiunte:

- **Il backend non è una scelta del planner.** È configurazione. Per definizione, l'arg è marcato `runtime_resolved = true` nel manifest e **non è esposto al proposer** (proposer.py:80-82) → non fa parte della superficie decisionale del planner. Far scegliere il backend all'LLM *era il bug*; il runtime che lo risolve è il **proprietario giusto** del valore, non un interceptor che contraddice una decisione. 0155 governa **forma/flusso** (quale tool, quale shape della pipeline), non i **valori di configurazione** su cui il planner non ha autorità.
- **È deterministico (§7.9), non un pattern-match linguistico hardcoded sulla query** del tipo vietato da 0155. Il match alias è esplicito e dichiarato nel registry, lang-agnostic per costruzione (token IT+EN), non un branch "il planner ha chiesto X ma faccio Y".

Si codifica quindi una **quarta eccezione disciplinata** alla regola 0155: *risoluzione deterministica di configurazione al dispatch*. Criterio di code review: lecita solo se (a) l'arg è `runtime_resolved` e nascosto al proposer, (b) la risoluzione è deterministica (zero LLM), (c) passa per il registry `OBJECT_BACKENDS`, non per un branch ad-hoc.

### Contratto

- **Manifest**: l'arg di backend (`client`/...) è marcato `runtime_resolved = true` in `[args.properties.<arg>]`; la sua description istruisce l'LLM a OMETTERLO (§6) ma la garanzia resta l'override, non il prompt. Verificato su `executors/{create,delete,read}_events/manifest.toml`.
- **Provider ben definito (lesson B3)**: ogni provider dichiara univocamente *sono disponibile?* via creds. Es. `runtime/backends/events/google_workspace.py:82::_has_creds()`. Niente backend "stub" ambigui: o è implementato e disponibile, o `available()` ritorna False.
- **Test (corollario)**: verificare l'**ESITO** (backend coerente fra create e delete, `deleted=True`), non il dettaglio interno (se l'LLM ha emesso o no l'arg).

## Alternatives considered

1. **Solo prompt-hiding** (`runtime_resolved` + omit dal proposer, senza override). *Rifiutata*: lesson B4 — l'LLM emette l'arg da training comunque; il proposer mostra solo la prima frase della desc. Nascondere non garantisce coerenza.
2. **`_default_client()` per-executor.** *Rifiutata*: logica duplicata ad-hoc in ogni executor, nessun punto unico per il match esplicito né per aggiungere un nuovo object multi-backend; non risolve l'incoerenza create/delete (ogni executor decide da solo).
3. **Migliorare il prompt** (`DEVI OMETTERE client`). *Rifiutata*: §7.3 — patch linguistica; A3 — il bias del pattern vince sul colloquiale, verificato che l'LLM ignora l'istruzione e emette comunque.
4. **Resolver dichiarato nel manifest TOML** (funzione/regola per-arg). *Rifiutata*: la logica creds-availability + alias non si esprime pulitamente in TOML; §7.9 — il posto giusto è il codice, con il manifest che dichiara solo il flag `runtime_resolved`.

## Consequences

- **Calendar #8 chiuso**: roundtrip create → delete-by-id coerente (`deleted=True` verificato live 1/6). Rispetta §2.3 (`reverse_pattern='delete_events_by_id'`).
- **Estensione a costo basso**: un nuovo object multi-backend = una entry in `OBJECT_BACKENDS`. Niente codice sparso.
- **Direzione: eliminare i `_default_client()` ad-hoc** duplicati per-executor (events fatto; gli altri quando migrano).
- **Lang-agnostic e future-proof**: il registry porta token IT+EN; nessun bias per lingua utente.

### Out of scope / aperti

- **messages (`account`)**: la selezione dell'account è **in parte intento** dell'utente ("dalla casella tiscali", "su tutte" = `all`) — non solo configurazione. Solo il *default quando non specificato* beneficerebbe del resolver. **Non migrato al 1/6**; da valutare in modo distinto (intento esplicito vs default).
- **urls**: non migrato.
- **files/dirs (mount/CIFS)**: asse diverso — è un *path* canonicalizzato (ADR 0087), non un enum di provider; resta separato.
- **github**: provider qualifier suffisso `_<provider>` (ADR 0136/0141), meccanismo di filtro pool grammar distinto dal resolver d'arg.

## Implementation status

Committato in `8208172` (sessione 31/5→1/6/2026): `runtime/backend_resolver.py` (+114), `runtime/engine/executor.py` (+98, hook riga 816-824), `runtime/engine/proposer.py` (+30, filtro `runtime_resolved`). Manifest `executors/{create,delete,read}_events` con `client` marcato `runtime_resolved = true` + description §6. Verificato live: create→google / "calendario locale"→local / delete coerente. Lessons in `internal/reports/lessons_learned.md §B`.
