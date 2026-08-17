# Referto risigillo post-apertura gold — 13/8/2026

## Esito

**PASS. Risigillo limitato al solo handover autorizzato da Roberto con scelta A.**

È stato modificato esclusivamente
`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.freeze.json`:

- impronta handover precedente:
  `71c7c6dbd9b25b5e85ff4331491ebfd8cebbf21351666cc1def8a7cb0ef94da4`;
- impronta handover corrente:
  `eb0fe7533eb85ff4906e82a21eea11ad9f1cd551743a3fb27277bb0bf0469bd8`;
- lock precedente:
  `82b033fcef187f132261eb43fc02789feb03ab53ac786b560d1127b93a3cb0d0`;
- lock corrente:
  `f04c69e03f211833dfc6faa3a6de26b0dc0d5298694b0485cc0b8989b09990dd`;
- impronta freeze precedente:
  `3dd123caf0516f0f553c85ee9d62689a94d5b740b87f05d3572d8be18b3f552e`;
- impronta freeze corrente:
  `eb9d051af8cee705536348ac602f7888595e5046937ec2ef09a632fddba63a03`.

## Prova di append-only

I primi 64.439 byte dell'handover corrente coincidono esattamente col vecchio
file sigillato e hanno la sua impronta SHA-256
`71c7c6dbd9b25b5e85ff4331491ebfd8cebbf21351666cc1def8a7cb0ef94da4`.
Il contenuto successivo è quindi una sola estensione in coda; il prefisso
storico non è stato riscritto.

## Verifiche

- verificatore canonico: `error_count=0`;
- fonti sigillate: 23/23;
- mutazioni negative: 107/107 bloccate;
- controlli positivi di riordino: 6/6 accettati;
- oracle JSON invariato:
  `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
- payload oracle invariato:
  `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`.

Il ricalcolo del lock usa lo stesso JSON canonico UTF-8, chiavi ordinate e
separatori compatti del verificatore, escludendo il solo campo
`lock_payload_sha256`.

Questo referto è stato creato soltanto dopo le verifiche, non appartiene alle
23 fonti sigillate e non modifica il freeze. Oracle JSON, expected, handover,
checkpoint, evaluator, batch, raw, journal, seal e ogni altra impronta sono
rimasti byte-identici. La seconda valutazione offline non è stata eseguita.
Nessuna rete, GPU, servizio, commit, produzione o banco è stata usata.
