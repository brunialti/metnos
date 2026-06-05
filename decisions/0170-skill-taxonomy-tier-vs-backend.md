# ADR 0170 — Tassonomia delle skill (3 tier) e confine skill ↔ backend

**Date**: 2026-06-05
**Status**: accepted
**Related**: ADR 0130 (backend tree per OBJECT), ADR 0165 (backend resolver uniforme — provider=config), ADR 0136 (provider qualifier `_<provider>`), ADR 0159 (safety-net 7-layer skill imported), ADR 0160 (locale-aware skill bundle), ADR 0123 (skill importer 5-stage), §2.2 vocab compositivo, §10.3 self-hosted default
**Complements**: ADR 0136/0165 (governano l'asse *backend/provider*; questo ADR governa l'asse *skill/attivazione* e ne fissa l'ortogonalità)

## Context

Il rilascio pubblico (iniziativa 5/6/2026) ha reso urgente decidere **cosa è "builtin" e cosa è "importato"** in Metnos. Oggi "skill" indica **tre cose diverse e non unificate**:

1. **Classificazione first-party** (`runtime/skills_catalog.py`): raggruppa executor *già nel repo* in capacità logiche (github/photos/mail/web/geo/calendar/frontier + core) via pattern-match. Non sono pacchetti: è uno strato di gating sopra executor esistenti. Dormant se manca la dipendenza.
2. **Bundle in-repo** (`executors/skills/<name>/` + SKILL.md, ADR 0160): pacchetti veri con script/manifest propri (es. `it_locale`).
3. **Importate/esterne** (importer ADR 0123/0159 + 7-layer net): skill da ecosistemi terzi mappate nel modello verificato/sandboxed.

L'anomalia che ha forzato la decisione: **google-workspace** era un bundle di terzi installato in user-data ma con wrapper first-party nel repo, funzionalmente core. È il sintomo che mancava una **regola di confine**.

Domanda parallela emersa (Roberto, 5/6): «c'è sovrapposizione fra skill e backend?». Sì, parziale — vanno separati esplicitamente.

## Decision

### 1. Tre tier di skill

| Tier | Cos'è | Trust | Dove vive | Attivazione |
|------|-------|-------|-----------|-------------|
| **1 — CORE** | planner/engine, file locali, dir, processi, tempo, scheduler locale, persone, credenziali, firme, proposte, helper scratchpad (filter/sort/group/compute/describe/extract) | massimo | repo, firmato, multilang | sempre on |
| **2 — FIRST-PARTY SKILL** | capacità spedite con Metnos, stesso standard del core, ma **dormant** finché manca la dipendenza (credenziale/backend/hw): github, photos, mail, web, geo, calendar, frontier, **google-workspace** | massimo | repo (bundle versionato + executor firmati) | auto, dormant se non configurata |
| **3 — IMPORTED / COMMUNITY** | codice di terzi NON auditato/vendorizzato (agentskills.io, ecc.) | basso | user-data, provenance | opt-in, **sandbox + 7-layer net** (ADR 0159) |

### 2. Regola di confine (litmus) builtin vs imported

Una skill è **Tier 2 (builtin)** se — *indipendentemente da chi l'ha scritta per primo* — noi la **vendorizziamo, auditiamo, versioniamo e manteniamo** allo stesso standard del core (manifest firmato §2.5, undo dove fattibile §2.3, i18n). È **Tier 3 (imported)** se esegue codice terzo **non** auditato/vendorizzato → resta sandboxed con provenance.

Promozione Tier 3 → Tier 2 = 4 sì congiunti:
1. utile in generale (non nicchia di un solo utente);
2. licenza compatibile (MIT/Apache/AGPL);
3. siamo disposti a mantenerla (undo, i18n, re-sign);
4. sostiene una capacità già nel catalogo.

google-workspace: 4/4 → vendorizzata in `executors/skills/google-workspace/`, origine cancellata (ridisegnata), `tier: first_party`.

### 3. Skill ↔ backend: assi ORTOGONALI (anti-duplicazione)

| | **Backend / provider** (ADR 0130/0165) | **Skill** (questo ADR) |
|---|---|---|
| Risponde a | *COME* eseguo `verb_object` contro un servizio | *SE/QUALI* capacità sono sbloccate/fidate/spedite |
| Asse | OBJECT × provider | dipendenza esterna (cred/backend/hw) |
| Visibilità | invisibile all'LLM, risolto da config (`backend_resolver`) | user-facing (enable/disable, dormancy, sandbox, packaging) |

Si **toccano** solo quando una skill è **mono-provider** (google-workspace, github, geo, mail ≈ nome user-facing dei backend di quel provider). **Divergono** quando la skill ha più backend (calendar = local-ICS / google / CalDAV) o nessun provider esterno (photos, core). Mappa **molti-a-molti**: un provider (google_workspace) serve più skill (mail/calendar/drive/docs/sheets).

**Regola anti-duplicazione**: la dipendenza esterna (credenziale/endpoint/hw) si dichiara **UNA volta, al backend/provider**. La **skill la AGGREGA** (union dei requisiti dei suoi backend), non la ridichiara. La dormancy della skill è **derivata** dai backend.

### 4. Evoluzione mono → multi provider (es. github → +gitlab)

Aggiungere un provider è una **promozione**, non uno sdoppiamento:

1. **Executor canonico provider-agnostico**: `find_issues`/`read_issues`/`find_pulls` (dispatcher sottile), NON `find_issues_github` + `find_issues_gitlab`. L'oggetto (`issues`/`pulls`, §2.2) è l'astrazione stabile.
2. **Backend tree per provider** (ADR 0130): `runtime/backends/{issues,pulls}/{github,gitlab}.py`. Il `backend_resolver` (ADR 0165) sceglie il provider **da config** (mappa repo→provider o per-account).
3. **Una skill PER PROVIDER**: skill `github` (PAT) + skill `gitlab` (token), dormancy indipendente — perché credenziale/fiducia sono il confine dove serve granularità ("abilita gitlab" senza toccare github). NON una skill unica che le ingloba.
4. Il **qualifier** `_github`/`_gitlab` (ADR 0136) resta per il **pin esplicito** dell'utente ("apri la issue *su gitlab*"); il default è agnostico.

Costo di un provider nuovo = **+1 file backend + +1 skill (se nuova credenziale), 0 nuovi executor**. Spostare il provider dal nome dell'executor (`_github`) al resolver è il percorso che calendar/files hanno già preso.

#### 4.bis — «Distribuisco con github, domani importo gitlab»: cosa succede

Due esiti distinti (il §4 sopra descrive il percorso «lo aggiungiamo NOI»; qui il percorso «lo IMPORTI TU»):

| Cosa vuoi | Meccanismo | Lavoro manuale |
|---|---|---|
| gitlab **funziona**, capacità separata | importer ADR 0123/0159 → executor **Tier 3 sandboxed** (es. `*_gitlab`), NON sotto il dispatcher canonico | **0** (importi e basta) |
| gitlab = **provider intercambiabile** sotto `find_issues` (resolver sceglie github/gitlab da config) | scrivere `backends/issues/gitlab.py` + registrare skill `gitlab` | **manuale** (oggi) |

Il **gap**: l'import grezzo produce *executor standalone*, non *backend sotto il canonico*. L'unificazione provider è manuale.

**Roadmap «import esterno robusto»** (iniziativa rilascio pubblico): l'importer deve **riconoscere** che una skill importata fornisce un OBJECT già canonico (`issues`/`pulls`) e **offrire di registrarla come backend** (`backends/issues/gitlab.py` + skill `gitlab`) invece che come executor standalone → il secondo esito diventa **quasi-automatico** (auto-detect oggetto → conferma utente → wiring + mapping vocab). Finché non c'è, vale la tabella sopra.

### 5. Campo `tier` nei metadata

`skills_catalog.py` e `SKILL.md` dichiarano `tier ∈ {core, first_party, imported}`. Unifica i 3 meccanismi sotto un modello solo. Rilascio pubblico: **Tier 1+2 spediti** nel repo; **Tier 3 no** (li installa l'utente). La tesi-sicurezza del README ("non fidarti del pacchetto") si applica **esattamente al Tier 3**.

## Consequences

- google-workspace promossa a Tier 2, vendorizzata, de-origine, undo write_files_doc verificato LIVE.
- Quando arriverà gitlab/altri: percorso §4 prescritto, niente sdoppiamento di executor.
- `skills_catalog` e SKILL.md guadagnano `tier`; gating e packaging pubblico ne derivano.
- Resta aperto: vendoring futuro di altre Tier-2 oggi solo "classificate" (i loro backend sono già in repo; mancano eventuali script esterni come per gws).
