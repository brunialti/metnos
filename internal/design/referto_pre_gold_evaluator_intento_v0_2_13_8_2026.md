# Referto pre-gold — evaluator intent-shadow 0.2 (13/8/2026)

## Esito

**PASS pre-gold. Valutazione non eseguita. Oracolo non aperto.**

La scelta A approvata da Roberto è implementata soltanto nel laboratorio
`candidate_v0_1`, come nuova versione affiancata 0.2. Nessun file 0.1 è stato
sovrascritto. Produzione, runtime, batch live, risposta raw, journal, sigillo,
marker, manifest, protocollo originario, expected dell'oracolo e risultati del
modello sono rimasti invariati.

## Regola applicata

Nel confronto tra estrazione salvata e ricalcolata è ammessa una differenza
soltanto al puntatore JSON
`adapter_metadata.implicit_actions_ignored`, soltanto se il campo esiste da
entrambe le parti e i due valori sono booleani JSON esatti.

L'eccezione vale soltanto per il record con indice interno zero-based 80,
richiesta 81, campione 40, pannello canonico, braccio A. L'identità è legata
anche a identificativo caso, posizione nel pannello, hash della query e hash
della richiesta. Tutti i campi semantici e ogni altro metadata devono
coincidere esattamente per tipo e valore.

Il campo non viene eliminato dal referto. Con `PYTHONHASHSEED=0` il confronto
registra `saved=false`, `replay=true` e motivo deterministico
`approved_boolean_diagnostic_variance_at_fixed_record`. Con
`PYTHONHASHSEED=2` le estrazioni coincidono tutte.

## Risultati pre-gold

- batch, raw, journal, checkpoint, marker, sigillo del batch, freeze storico e
  freeze 0.2: integri;
- 316/316 record verificati e ricalcolati in ciascuno dei due processi;
- seed 0: 1 differenza autorizzata, 0 differenze inattese;
- seed 2: 0 differenze autorizzate, 0 differenze inattese;
- 12/12 prove finite superate;
- rifiutati: campo assente, tipo non booleano, altro metadata modificato,
  campo semantico modificato, due differenze e indice diverso;
- accettati: uguaglianza totale, `false -> true` e `true -> false` soltanto
  nel puntatore e record autorizzati;
- una prova strumentata conferma che il fallimento del controllo 0.2 precede
  sia l'apertura dell'oracolo tipizzato sia quella del gold Phase-1.

I pannelli restano distinti: 120 casi canonici, 4 controlli tipizzati e 34
controlli legacy. Restano invariati anche le nove colonne senza regressione,
il requisito 4/4 per i controlli speciali e il miglioramento minimo di 3/120.

## Artefatti e impronte

- `live_replay_gate_v0_2.py`:
  `cf4b832b3e8eb951f80298dc902bbaf2d1162d95759fc3726f27f2462c4d0bdf`;
- `live_evaluator_v0_2.py`:
  `be49b085e22a089dda3c82a89dacddda218099d174b934adf4074bc0f71ee85c`;
- `build_live_replay_v0_2_freeze.py`:
  `135e19d95a154a3b6f8059b1cb20eb0dba4382705e5765ac29216ffea39302d8`;
- `test_live_replay_v0_2.py`:
  `6a31b2cee5baf250bee3b4914aa514581592b3b30b54c948f1a997542dbcf362`;
- `live_replay_v0_2.freeze.json`:
  `2e4608c8924790ebd1c4faafaa99fbeb905b52dc82ff276d3cc4ac5a398fceef`;
- `live_replay_pre_gold_report_v0_2.json`:
  `03edefc1986e21dd764a0ce993551d33749b9b4f4cc4f15a77612116fb33f576`.

Il freeze usa un insieme chiuso di 41 fonti più tre file 0.2 auto-improntati.
Non contiene fonti gold. Il report dipende dal freeze; il freeze non dipende
dal report o da questo referto, evitando una catena circolare.

## Revisione avversariale

La revisione ha verificato in particolare che l'eccezione non sia globale per
nome campo: è legata a un solo indice e alla sua identità completa. Azzerare
quel booleano nelle due copie serve soltanto al confronto; i valori originali
restano nel report. Un secondo scarto, anche nello stesso oggetto, fa fallire
il controllo. L'uguaglianza usa JSON canonico e quindi non confonde booleani e
interi.

Il valutatore 0.2 resta post-sigillo. Importarlo non apre il gold; l'accesso è
successivo al PASS del controllo 0.2. Il comando seguente è soltanto il passo
offline previsto e **non è stato eseguito**:

```bash
PYTHONHASHSEED=0 python3 -B internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluator_v0_2.py --batch internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_sealed_batch_v0_1.json --seal internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_sealed_batch_v0_1.freeze.json --freeze internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_replay_v0_2.freeze.json > internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_2.json
```

Non sono state usate rete, GPU, endpoint o servizi in questa sott fase. Nessun
commit è stato creato. Non è emersa una nuova scelta di policy.
