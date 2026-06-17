---
id: 0172
title: Promozione github-issues a backend-resolver — design + risoluzione collisione di nome
date: 2026-06-15
status: accepted
area: runtime
related:
  - 0136  # provider qualifier _<provider>
  - 0141  # issues/pulls github (skill provider-baked)
  - 0155  # planner choice > runtime override (4ª eccezione disciplinata)
  - 0156  # naming grammar (qualifier _similar, descriptor)
complements:
  - 0165  # estende OBJECT_BACKENDS a issues/pulls
  - 0170  # istanzia §8 (promozione mono→multi) per github + risolve la
          #   collisione di nome che §8 aveva dato per scontata
---

<!-- Questa ADR ESTENDE 0165 (nuovi object nel registry) e 0170 (rende concreto
il §8 «promozione frontier una-tantum» per il caso github-issues, aggiungendo il
nodo che §8 non aveva visto: i nomi canonici sono già occupati). Additiva: nessun
codice cambia finché il trigger non scatta. -->


## Context

Il README (riga 166) e `docs/*/architecture/skills_backends.html` presentano
`find_issues` come l'**esempio** dell'esecutore provider-agnostico con backend per
provider + resolver. Verifica sul codice (15/6): la forma è reale e funzionante,
ma **solo per `events`/`files`/`contacts`** (`runtime/backend_resolver.py`,
`OBJECT_BACKENDS`, backend in `runtime/backends/{events,files,contacts}/`). Per
`issues` **non esiste**: niente `backends/issues/`, il registry non conosce
`issues`, e le issue GitHub si prendono con la skill **provider-baked**
`find_issues_github` (`provider_suffix: _github`, `~/.local/share/metnos/skills/
github/`), che il planner vede e che ADR 0170 §8 classifica come stato «baked-in».

ADR 0170 §8 ha già definito il **meccanismo** di promozione (frontier una-tantum
per il solo refactoring, bordi deterministici, gate umano, firma manuale §7.10) e
il **trigger** (arrivo del 2° provider: object-overlap + provider mismatch). La
decisione 5/6 (`project_session_5_6_2026.md`) ha fissato la politica: «github
LASCIARE cotto; **promozione progettata e una-tantum se servirà**; mai skill in 2
versioni». Questa ADR è la parte **«progettata»** di quella decisione — il design,
non il build.

**Il nodo che 0170 §8 non ha visto.** §8 dà per scontato che promuovere significhi
«fondere `*_github` in `find_issues` + backend». Ma i nomi canonici **sono già
occupati**, da tutt'altro concern:

| canonico       | executor reale                                | concern                |
|----------------|-----------------------------------------------|------------------------|
| `find_issues`  | dedup semantico (BGE-M3 su `github_issue_qa`) | **memoria QA locale**  |
| `read_issues`  | rilegge record di trattamento dal db locale   | **memoria QA locale**  |
| `write_issues` | persiste record di trattamento nel db locale  | **memoria QA locale**  |

Sono i mattoni del **flusso maintenance** (memoria interna delle issue già
trattate), non fetcher remoti. La promozione collide frontalmente con loro. La
stessa ambiguità inquina il README: riga 166 `find_issues`=esempio
provider-agnostico, riga 247 `find_issues`=dedup locale — **stesso nome, due
significati**.

## Decision

Progettare la promozione di `issues`/`pulls` alla forma backend-resolver,
risolvendo prima la collisione di nome. Quattro punti; **nessun codice cambia
finché il trigger 0170 §8 non scatta** (2° provider reale).

## Aggiornamento 17/6/2026 — direzione UNIVERSAL-EXECUTORS (gate RILASCIATO)

Decisione Roberto (17/6): **gate del «2° provider» RILASCIATO** — il mis-route
(«le issue di brunialti/metnos» → `read_issues` LOCALE invece di
`find_issues_github`) è un bug REALE ora, non ipotetico. Causa scoperta: l'aging
notturno aveva deprecato l'intero bundle skill github → fuori dal pool → ripiego
sul locale (fix universale: skill esenti da aging, `source='skill'`, commit
`a6931b1`).

**§1 SUPERSEDED** (niente rinomina dei tool locali): la memoria QA locale NON
diventa tool issue-specifici rinominati, ma si accede coi **CRUD UNIVERSALI**
(`find_entries`/`write_entries`/`delete_entries`, skill `sqldatabase`, backend
sempre sqlite) su uno **store registrato** (`store_bootstrap`, nome
`github_issue_qa`). La **similarità** (ex `find_issues_db`) diventa l'executor
universale **`compare_entries`** (distanza semantica `reference`↔candidati,
cosine BGE-M3, riusabile ovunque: dedup issue, foto↔testo, RAG). → il flusso
issue è **pura composizione di universali**, zero executor di dominio per il
local store.

**Ritiro** (sostituiti): `find_issues_db` → `compare_entries`; `read_issues` /
`write_issues` → `*_entries`. Canonici `find/read/create/set/delete_issues`
liberati → github via backend_resolver (§2/§4 sotto, invariati).

**Pipeline detect VETTORIALE** (no iterazione per-item, §2.1): `find_issues`
(github) → `filter_entries`/`compare_entries` (dedup vs store) → `write_entries`
(new) → `send_messages`. Poi promozione **L1** (riuso, non ri-composizione).

**Stato fasi**:
- **1.0 FATTO** (commit `af64e98`): `compare_entries` universale (inproc, 9 test
  + integrazione embedder reale).
- **1.1 FATTO** (commit `e0109b0`): store `github_issue_qa` registrato
  (`store_bootstrap`) → `*_entries` attivi sui dati reali (#47 leggibile).
- **1.2 DA FARE**: `OBJECT_BACKENDS["issues"]` (§2) + esecutori canonici
  provider-agnostici (§4) + ritiro dei 3 tool locali.
- **1.3 DA FARE**: pipeline detect vettoriale + promozione L1.

Il resto dell'ADR (§1-§4 sotto) resta come razionale di design; §1 è storicizzato
dall'aggiornamento qui sopra (rinomina → ritiro+universali).

### 1. La memoria QA locale NON è un backend di `issues` (perché va rinominata)

Tentazione: modellare lo store locale come provider `local`/`memory` di `issues`
sotto il canonico, lasciando github come secondo provider. **Rifiutata**: viola la
premessa di ADR 0165 — «il provider è configurazione, non intento». Per `events`,
`local` vs `google_workspace` è lo **stesso intento** («la mia agenda») su store
diversi → il resolver può sceglierlo da config. Per `issues`, la memoria QA vs
github **NON è lo stesso intento**: il flusso maintenance vuole DELIBERATAMENTE
due operazioni diverse — `find_issues_github` = lista issue *vive* dal repo;
`find_issues` locale = *similarity search* sulle issue già risposte (dedup). Un
resolver che scegliesse «per configurazione» fra le due rompereebbe il flusso. Sono
**concern diversi**, non host-variant dello stesso oggetto.

→ I tre tool locali vanno **rinominati** per liberare i nomi canonici. Caso
chiaro: `find_issues` (similarity) → **`find_issues_similar`** (qualifier modalità
`_similar` §2.2, grammaticale per `naming_grammar`). Per `read_issues`/
`write_issues` locali (CRUD sulla memoria di trattamento) la scelta è APERTA e
human-gated: o la famiglia `_similar`/qualifier coerente, **oppure** una escalation
vocab §2.2 per un concetto «memoria-QA-issue» dedicato (3 criteri: necessario,
generale, comprensibile). Questa ADR non chiude il punto vocab — lo segnala come
decisione di Roberto (§2.2 escalation), perché tocca il vocabolario chiuso.

### 2. Estendere `OBJECT_BACKENDS` (ADR 0165)

Liberati i nomi, aggiungere al registry `runtime/backend_resolver.py`:

```
"issues": {"arg": "client", "providers": ["github", "gitlab"],
           "available": lambda p: _has_pat(p), "aliases": {
               "github": ("github", "su github", "on github"),
               "gitlab": ("gitlab", "su gitlab", "on gitlab")}},
"pulls":  {"arg": "client", "providers": ["github"], … },
```

`object_of()` riconosce già `verbo_oggetto`; `resolve_backend_arg` inietta `client`
allo stesso hook unico (`engine/executor.py`, dopo i placeholder runtime). Default
= primo provider con PAT configurato; esplicito in query vince. **L'LLM non vede né
sceglie** — niente più `_github` nel pool, niente più bias provider (il problema
originario di 0165/0170).

### 3. Backend concreti da `github_api.py`

`runtime/backends/issues/github.py` + `runtime/backends/pulls/github.py`, derivati
dal wrapper esistente `~/.local/share/metnos/skills/github/scripts/github_api.py`
(REST v3 + PAT, già provato live, con `_resolve_repo` config-driven). Stessa forma
dei backend `events/google_workspace.py`. Il default-repo (cred `github.repo`,
memoria) resta config-driven §7.11.

### 4. Esecutori canonici provider-agnostici

`find_issues`, `read_issues`, `create_issues`, `set_issues`, `delete_issues`,
`find_pulls`, `read_pulls`, `set_pulls`, `change_pulls` — generati dalla
**passata frontier una-tantum** (0170 §8) a partire dai `*_github` + dai backend,
con **review + firma umana** §7.10 (auto-firmare codice frontier romperebbe la
fiducia). Transizione **one-way**: i `*_github` vengono ritirati, non affiancati
(«mai skill in 2 versioni», 5/6).

### Trigger di BUILD (invariato da 0170 §8 / 5/6)

Costruire SOLO all'arrivo di un 2° provider reale (tipicamente l'import di una
skill che mappa su `issues`/`pulls` già coperti solo da `*_github`). Fino ad
allora github resta cotto: questa ADR è il piano, non l'esecuzione.

## Alternatives considered

- **Memoria QA locale come backend `local` di `issues`** (punto 1). Eviterebbe la
  rinomina. Rifiutata: viola ADR 0165 (config-not-intent) — dedup-memoria e
  fetch-vivo sono intenti diversi, non host dello stesso oggetto.
- **Generare `find_issues_github` + `find_issues_gitlab` e far scegliere al
  planner.** È l'opzione (a) dei drop-in/MCP-clone. Rifiutata da 0170: il planner
  LOCALE sviluppa bias provider (0165 §B1). Vale solo con un frontier al centro.
- **Lasciare github cotto per sempre + non promuovere mai.** Coerente con lo
  status quo ma lascia il README disonesto e blocca un eventuale 2° provider su un
  refactor non progettato. Questa ADR mantiene github cotto MA progetta l'uscita.
- **Build adesso (refactor completo subito).** Contro la decisione 5/6 («se
  servirà»): il 2° provider non c'è, il pairing github+gitlab è raro. Costo
  frontier + rischio senza beneficio attuale.

## Consequences

- **README va corretto SUBITO, a prescindere** (§9.1, separato da questa ADR):
  l'esempio provider-agnostico deve usare un object davvero virtualizzato
  (`events`/`contacts`) e `issues`/github va marcato «baked-in, promozione
  progettata (ADR 0172)». L'HTML `skills_backends.html` è già onesto («GitHub is
  currently in the baked-in state») — allineare il README a quello.
- **Collisione di nome documentata**: chiunque legga 0170 §8 e provi a promuovere
  troverà qui il nodo (`find_issues` occupato) e la via d'uscita.
- **Punto vocab APERTO** (human-gated): nomi dei tool memoria-QA locali
  (`read_issues`/`write_issues` → `_similar` vs nuovo concetto §2.2). Da decidere
  prima del build, non urgente.
- **Quando il build parte**: rinomina maintenance tools (+re-sign §7.10, +fix
  flusso che li chiama: `read_issues`→`send_messages_github` pipe), estensione
  registry, 2 backend, passata frontier sugli esecutori canonici, ritiro `*_github`
  one-way, bench routing + E2E sulle query reali ([[feedback-e2e-test-on-function-changes]]).
