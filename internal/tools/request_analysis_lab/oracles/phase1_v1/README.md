# Oracle Phase 1 v1

Audit indipendente e oracolo tipizzato per i 34 controlli mirati sulla
posizione corrente. È stato costruito senza chiamare il server e senza leggere
output V26.3. La fixture originale
`../../question_focus_controls_v1.json` non è stata modificata.

## Verdetto congelato

- binding diretto invariato: 9 positivi e 25 negativi;
- 31 proiezioni semanticamente esatte;
- 3 ambiguità tipizzate con alternative obbligatorie;
- verifiche separate per binding diretto, dipendenza, copertura, sicurezza e
  prove;
- i 34 controlli non certificano da soli la copertura multi-clausola,
  multi-azione o multi-dominio.

Il vecchio confronto esatto sui sette campi resta diagnostico: include campi
ridondanti o non oggettivi e non è un criterio di accettazione.

## Artefatti

| File | SHA-256 |
|---|---|
| `metnos_phase1_oracle_audit_v1.md` | `538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a` |
| `metnos_phase1_oracle_audit_v1.json` | `b8443ad2e27a2d773b971147c1b3d37ee19beb1091b1d357c0a84e6aa0c98a77` |
| `metnos_phase1_typed_oracle_v1.schema.json` | `4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2` |
| `metnos_phase1_typed_oracle_v1.overlay.json` | `e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af` |
| `metnos_phase1_oracle_audit_v1.freeze.json` | `f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f` |

Le cinque copie sono byte-identiche agli artefatti `/tmp` originari. Il file
freeze conserva intenzionalmente i percorsi `/tmp` per mantenere questa
identità; per la ripresa durevole risolvere i nomi nella directory corrente e
verificare gli SHA della tabella.

## Uso

Un successore deve congelare il proprio valutatore contro schema e overlay
prima di qualunque inferenza. Un risultato riceve credito soltanto se pubblica
separatamente tutti i controlli definiti nel report. Non modificare il gold
originale e non usare l'overlay da runtime, prompt o projector.
