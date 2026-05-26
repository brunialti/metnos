# it_locale — bundle locale italiano per Metnos

Bundle skill builtin (`trust: metnos-official`) che raggruppa tutte le
feature dipendenti dal locale italiano (utility/telecom, fiscalita',
formati nativi). Pattern bundle-per-locale (ADR 0160): un solo namespace
`it_locale/` con N sub-feature, anziche' N skill granulari.

## Layout

```
it_locale/
├── SKILL.md           frontmatter (lang, trust, auto_enable, feature_modules)
├── README.md          questo file
├── vendors.json       config bills_extractor — vendor IT whitelist
├── scripts/
│   └── bills_extract.py    pipeline bollette (deterministic §7.9)
└── docs/              riferimenti per future feature
```

## Aggiungere una nuova feature

Esempio aggiunta `codice_fiscale`:

1. `scripts/codice_fiscale.py` — implementazione deterministica §7.9.
2. Frontmatter `SKILL.md`: `feature_modules: [bills_extractor, codice_fiscale]`.
3. Sezione "Scope" in SKILL.md: linea breve cosa fa.
4. Test in `runtime/tests/test_it_locale_<feature>.py`.
5. Doc di riferimento opzionale in `docs/codice_fiscale.md` (algoritmo,
   tabella codici comuni, edge case omocodia).

## Non e' un'executor

`it_locale/` NON contiene `manifest.toml` al livello root: e' un
helper-library bundle, non un executor singolo. Gli executor che usano
queste feature (`get_files`, future `parse_codice_fiscale`, etc.)
importano i moduli sotto `scripts/` via Python path.

## Estensione ad altri locale

Lo stesso pattern si riusa per:
- `fr_locale/` — bills FR (EDF, Orange, Free), SIRET, IBAN FR.
- `de_locale/` — bills DE (E.ON, Vodafone DE), Steuer-ID, IBAN DE.
- `es_locale/` — bills ES (Iberdrola, Movistar), NIF/NIE, IBAN ES.

Pattern §7.3 generale: niente codice hardcoded `bills_extractor_it`,
solo `it_locale/scripts/bills_extract.py`. Il "locale" e' nel parent dir.
