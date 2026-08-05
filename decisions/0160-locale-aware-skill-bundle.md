---
id: 0160
title: Locale-aware skill bundle (it_locale / fr_locale / ...) + rename _imports → skills
date: 2026-05-24
status: accepted
area: skills | executor | naming
related:
  - 0123  # skill importer
  - 0136  # provider qualifier + dormancy
  - 0159  # safety net 7-layer per skill imported
complements:
  - 0123  # estende structure delle skill: builtin + bundle-per-locale
---


## Context

Dopo l'introduzione dello skill importer (ADR 0123) e del safety net 7-layer
per skill imported third-party (ADR 0159), il filesystem aveva tre concetti
mescolati:

1. **Skill imported third-party** (agentskills.io, Anthropic Hub) →
   `~/.local/share/metnos/executors/_imports/<skill>/<executor>/manifest.toml`.
2. **Skill builtin** (`google-workspace`, `github`) → handcrafted come
   `executors/<name>/` direttamente, senza dir parent "skill" — confondevano
   "executor singolo" e "bundle di funzionalita' coerenti".
3. **Helper di dominio specifico** (24/5/2026: pipeline estrazione bollette
   italiane, `runtime/bills_extract.py` + `runtime/bills_vendors.json`) →
   buttato a runtime root senza nessuna struttura, non versionato come
   "skill", non disabilitabile, non installabile da hub.

Per il caso bills la prima istanza naturale era una skill granulare
`bills-extractor-it/`, ma sarebbe stato l'inizio di una frammentazione:
`bills-extractor-it`, `codice-fiscale-it`, `iban-it`, `partita-iva-it`,
`date-it`, `tariffe-it`, ... → N skill per N feature italiane, senza un
namespace coerente.

L'osservazione: il fattore comune NON e' "bills" ma il **locale**. Tutte
queste feature dipendono dal mercato/cultura/normativa IT e sono utili
solo per un utente che vive in Italia. Lo stesso ragionamento vale per
fr_locale, de_locale, es_locale.

## Decision

### A. Rename `_imports/` → `skills/`

Tutte le skill (imported + builtin) vivono sotto `executors/skills/<name>/`.
Path canonici:
- `<install>/executors/skills/<name>/` — skill builtin shippate con Metnos
  (`trust: metnos-official`).
- `<user_data>/executors/skills/<name>/` — skill imported third-party
  (canonical WRITE post-ADR 0160).
- `<user_data>/executors/_imports/<name>/` — back-compat READ-ONLY per
  installazioni legacy.

Loader scansiona tutti e tre i root, write nuovi vanno SEMPRE in
`PATH_SKILLS_USER`. Helper centralizzato: `runtime/skills_paths.py`.

### B. Skill bundle pattern bundle-per-locale

Una skill puo' contenere N feature correlate, raggruppate dal locale (o
da qualsiasi altro asse coerente). Esempio canonico:

```
executors/skills/it_locale/
├── SKILL.md           (name, lang, trust, distribution, feature_modules)
├── README.md          (scope + come aggiungere feature)
├── vendors.json       (config bills_extractor)
├── scripts/
│   └── bills_extract.py
└── docs/
```

Frontmatter SKILL.md nuovi campi:
- `lang: it` — locale primario della skill (it/en/fr/de/es/...).
- `trust: metnos-official | community | unverified` — chi ha pubblicato.
- `auto_enable: true | false` — abilitata di default al boot.
- `distribution: hub-installable | bundled | community` — `bundled` =
  inclusa nella distribuzione base, `hub-installable` = disponibile nel
  hub ufficiale Metnos ma opt-in, `community` = third-party.
- `feature_modules: [name1, name2, ...]` — sub-feature attive dentro il
  bundle (rispecchiate da `scripts/<name>.py`).

Aggiungere nuova feature al bundle:
1. `scripts/<feature>.py` (deterministic §7.9).
2. Append a `feature_modules:` nel frontmatter.
3. Sezione "Scope" in SKILL.md.
4. NO nuova SKILL.md, NO dir separata.

Estendibile a `fr_locale/`, `de_locale/`, `es_locale/`, ... — pattern §7.3
generale, niente codice hardcoded "bills-extractor-it" o simili.

### C. Conseguenze di runtime

- `it_locale/` NON ha `manifest.toml` al root: e' un helper-library bundle,
  non un executor. Loader skip dir senza manifest. Gli executor che usano
  queste feature importano via `sys.path` (es. e2e test).
- `vendors.json` caricato lazy via `Path(__file__).parent.parent / "vendors.json"`
  (path resolution §7.11). Override env `METNOS_BILLS_VENDORS_PATH` per test.
- `auto_enable: true` + `trust: metnos-official` → gate `skill_registry.is_skill_enabled`
  ritorna True senza azione esplicita dell'utente.

## Alternatives considered

**Skill granulari `bills-extractor-it/`, `codice-fiscale-it/`, ...** —
scartato: N skill per N feature dello stesso locale frammenta il namespace,
duplica SKILL.md/README.md/license, costringe a iterare N volte per il
setup di un utente italiano. Bisogno reale: "voglio Metnos in italiano,
con le feature italiane" → un solo enable di `it_locale`, non 6+.

**Helper sotto `runtime/` (status quo pre-ADR)** — scartato: i moduli
runtime sono "core engine", non feature di prodotto installabili.
`bills_extract.py` non e' un componente del runtime ma una feature per
utenti IT. Tenerlo a runtime root viola il principio di modularita' e
impedisce di vederlo come skill installabile da hub.

**Mega-skill `localization` con sub-dir per locale** —
`localization/it/scripts/bills_extract.py`, `localization/fr/scripts/...`.
Scartato: complica il filesystem (3 livelli) senza guadagno; preferiamo
N skill sibling al pari livello `executors/skills/`. Permette enable/disable
indipendente per locale (un utente francese non scarica it_locale).

## Consequences

**Becomes easier**:
- Aggiungere nuove feature italiane (codice fiscale, IBAN, P.IVA) senza
  creare nuova skill ogni volta.
- Replicare il pattern per altri locale (fr_locale, de_locale).
- Un solo enable per attivare tutto il pacchetto italiano.

**Becomes more expensive**:
- Niente — la struttura e' piu' semplice del path precedente
  (runtime root frammentato + skill granulari ipotetiche).

**Spawned work items**:
- Migrare future feature dal runtime root al bundle locale appropriato.
- Eventuale CLI `metnos-skills enable it_locale` (se distribution !=
  bundled).
- Documentare il pattern bundle-per-locale nei doc architettura
  (`docs/it/architecture/skills.html`).

**Baseline 24/5/2026**:
- `runtime/bills_extract.py` (447 LOC) + `runtime/bills_vendors.json` (47 LOC)
  → spostati a `executors/skills/it_locale/scripts/bills_extract.py` +
  `executors/skills/it_locale/vendors.json`.
- E2E test `tests/e2e/scenarios/test_bills_pipeline_real.py` aggiornato:
  sys.path centralizzato a module level, niente hardcoded `/opt/metnos`.
- Creati `SKILL.md` + `README.md` + `docs/` placeholder.
