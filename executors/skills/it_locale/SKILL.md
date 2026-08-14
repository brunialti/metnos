---
name: it_locale
description: "Bundle di funzionalità localizzate per il mercato italiano: bills_extractor (utility/telecom), in futuro codice fiscale, IBAN IT, P.IVA, parse date italiane, tariffe italiane."
version: 0.1.0
tier: first_party
lang: it
trust: metnos-official
auto_enable: true
distribution: hub-installable
author: metnos
license: MIT
platforms: [linux]
feature_modules: [bills_extractor]
metadata:
  metnos:
    tags: [locale, italia, utility, telecom, fiscal]
    locale_code: it_IT
---

# it_locale — Bundle Italian Locale

Skill bundle pattern bundle-per-locale (ADR 0160): raggruppa N feature
localizzate per il mercato italiano in un unico namespace, anziche'
frammentare in N skill granulari (`bills-extractor-it`, `codice-fiscale-it`,
`iban-it`, ...).

## Scope

- **bills_extractor** (attivo): pipeline universale estrazione bollette
  utility/telecom IT da mailbox (Eni Plenitude, Enel, Fastweb, Iliad,
  Vodafone, WindTre, Acea, AMA, ...). Schema stabile vendor + amount_eur +
  due_date + bill_number + customer_id + period + consumption.
- **codice_fiscale** (planned): validazione + parse codice fiscale (16 char,
  derivazione data nascita / sesso / comune).
- **iban_it** (planned): validazione + parse IBAN IT (27 char, ABI/CAB).
- **partita_iva** (planned): validazione P.IVA (11 cifre, checksum Luhn-IT).
- **date_it** (planned): parse date naturali italiane ("lunedi prossimo",
  "tra una settimana", "il 15 del mese").
- **tariffe_it** (planned): catalogo tariffe utility IT (fasce orarie F1/F2/F3,
  tariffe sociali, agevolazioni ISEE).

## Feature add policy

Aggiungere nuova feature al bundle:
1. Implementare in `scripts/<feature>.py`.
2. Aggiungere nome modulo a `feature_modules:` nel frontmatter.
3. Aggiornare scope sopra con linea descrittiva.
4. Niente nuova SKILL.md, niente dir separata. La feature vive nel bundle.

Pattern generale §7.3 — riusabile per altri locale (`fr_locale/`,
`de_locale/`, `es_locale/`, ...).

## Trust + Distribution

- `trust: metnos-official` — shippato da Metnos, non third-party imported
  (no L2/L6 affinity verify; vedi ADR 0159).
- `auto_enable: true` — abilitato di default al boot tramite skill_registry.
- `distribution: hub-installable` — disponibile nel hub ufficiale Metnos
  (non incluso nella distribuzione base; install opt-in per chi vive
  in Italia).
