# Audit indipendente post-run/post-evaluation — prompt_style_v0_1 / RUN3

- Data: 2026-08-13
- Revisore: `reviewer_b_independent`
- Perimetro: 20 controlli finiti; sola lettura e ricalcolo in memoria. L'evaluator non è stato rilanciato.
- Esito numerico e d'integrità: **PASS DESCRITTIVO — 20/20 PASS, 0 FAIL**.
- Esito causale: **NON ATTRIBUIBILE — `style_measure_valid: false`**.
- Vincolo conseguente: **nessun winner, nessun claim di superiorità e nessun riuso delle pairwise come misura causale dello stile**.

## Artefatti auditati

| Artefatto | SHA-256 |
|---|---|
| evaluation RUN3_STYLE | `66ff0dc6204cd7d923a49ccfef47bc7004458f5e84eba7c31f239ae7186e9fde` |
| sealed batch 632 | `8f43a12831f2820cda3d2695e7435eb4e270bffb59b2a12f048eaca4afbe05dc` |
| journal | `e9339da6d715d8dd796165a6def2b4beb652c34fdb490a70b99b53a0c905d2a3` |
| seal | `e01d19ca4c8d4aa379d8cf76dedc8539430df50d44766400dcd043d6f6a10f61` |
| checkpoint | `5a429121b75fc13b5365668838ba00cf9bf138884d76c78800311a00249d855d` |
| consumption marker | `a79444836466ea6a6f57bf081fc1eabb38e4b5ecb10e1f1df9a17d9bff9ee3cc` |
| authorization | `a92b4663585cd922745086d53fe77c04fb89eaf4cc74f1449b48acb875a70350` |
| protocol freeze | `2dec09e3edce00b13e833c908df389a4425ff1c00db345ff26a014e41212dcad` |

Gli hash di batch, journal, seal, checkpoint, marker, authorization e freeze coincidono con quelli osservati prima dell'apertura gold. L'evaluation lega il batch esatto e il sorgente dell'evaluator v0.3 RUN2.

## Checklist finita 20/20

| N. | Controllo | Esito | Evidenza |
|---:|---|:---:|---|
| 1 | Schema top-level evaluation chiuso | PASS | 23 campi esatti; nessun campo estraneo. |
| 2 | Identità evaluator/run/status | PASS | Versione RUN3 attesa, run ID corretto, `status: ok`, evaluation eseguita. |
| 3 | Binding evaluation → sealed batch | PASS | `batch_sha256` coincide con `8f43a128…e05dc`. |
| 4 | Binding metrica v0.3 | PASS | Source SHA dell'evaluator RUN2 coincide; row/aggregate sono quelli congelati. |
| 5 | Gate pre-gold conservato | PASS | 632 record, replay 632, unexpected 0, allowed `[]`, `oracle_opened: false` durante il gate. |
| 6 | Catena batch/journal/seal/checkpoint/marker | PASS | Conteggi 632, hash e binding interni esatti; nessun partial. |
| 7 | Raw/body/content/extraction replay | PASS | 632/632 body hash, wrapper-content, content hash ed extraction replay esatti. |
| 8 | Copertura righe canonical+typed | PASS | 496 righe univoche = `(120 + 4) × 4`. |
| 9 | Copertura legacy | PASS | 136 righe univoche = `34 × 4`; pannello separato e senza compensazione. |
| 10 | Aggregati canonical ricalcolati | PASS | Tutti i campi, status, latency e token usage dei quattro bracci coincidono byte per byte. |
| 11 | Aggregati typed ricalcolati | PASS | Tutti gli aggregati 4×4 coincidono byte per byte. |
| 12 | Aggregati legacy ricalcolati | PASS | Total e direct-binding dei quattro bracci coincidono. |
| 13 | Anchor A ricalcolato | PASS | A_SYSTEM→RUN2-A: raw 149/158, full extraction 149/158; aggregati esatti; `exact: false`. |
| 14 | Anchor S0 ricalcolato | PASS | S0→RUN2-B: raw 120/158, full extraction 120/158; sette drift aggregati; `exact: false`. |
| 15 | Mapping e drift indices | PASS | Mapping A→A/S0→B corretto; liste raw ed extraction coincidono con l'output. |
| 16 | Sei pairwise ricalcolate | PASS | Delta bidirezionali, safety regression e struttura delle sei coppie coincidono. |
| 17 | Fail-closed attribuzione | PASS | `both_exact: false`, `style_measure_valid: false`, verdict `non_attributable_anchor_drift`. |
| 18 | Divieto winner | PASS | Tutte le 6 coppie hanno `left_style_pass: false`, `right_style_pass: false` e interpretation non-attributable. |
| 19 | Nessun punteggio/claim globale | PASS | `combined_score: null` globalmente e per coppia; assenti `winner`, `best_arm`, `global_winner`, `left_better`, `right_better`. |
| 20 | Nessuna mutazione storica | PASS | Gli hash live pre-gold restano invariati; audit senza scritture su evaluation, batch, seal, oracle o produzione. |

## Metriche descrittive ricalcolate

| Braccio | Canonical exact /120 | Root exact /120 | Technical valid /120 | False action avoided /120 | Typed exact /4 | Legacy direct /34 |
|---|---:|---:|---:|---:|---:|---:|
| A_SYSTEM_CURRENT | 79 | 94 | 120 | 107 | 1 | 25 |
| S0_CURRENT | 44 | 67 | 120 | 96 | 1 | 27 |
| S1_METNOS_SHORT | 21 | 25 | 115 | 119 | 0 | 26 |
| S2_PROCEDURAL | 18 | 28 | 117 | 117 | 1 | 27 |

Status canonical:

- A_SYSTEM_CURRENT: 74 valid representable, 46 valid unrepresentable;
- S0_CURRENT: 68 valid representable, 23 valid unrepresentable, 29 document invalid;
- S1_METNOS_SHORT: 22 valid representable, 5 valid unrepresentable, 88 document invalid, 5 technical invalid;
- S2_PROCEDURAL: 21 valid representable, 16 valid unrepresentable, 80 document invalid, 3 technical invalid.

Questi sono risultati descrittivi conservati dal protocollo; non costituiscono ranking o vincitore.

## Anchor drift e diagnosi limitata

- A_SYSTEM_CURRENT ha 9 drift raw/full-extraction: sample index `64, 79, 91, 95, 96, 113, 120, 121, 153`.
- S0_CURRENT ha 38 drift raw/full-extraction: sample index `5, 9, 16, 19, 30, 33, 34, 38, 39, 45, 46, 48, 65, 66, 67, 70, 75, 77, 79, 81, 83, 88, 90, 96, 97, 101, 102, 120, 125, 131, 135, 136, 137, 139, 141, 150, 151, 156`.
- Request hash, query identity, modello, fingerprint, prompt-token count e profilo coincidono 158/158 per entrambi gli anchor; adapter replay RUN3 è 632/632 esatto.
- La divergenza è quindi nei nuovi output del modello, non nella mappatura, nel metodo hash, nell'adapter o in una corruzione della catena run. Il protocollo aveva precommesso che anche un solo drift annulla l'attribuibilità pur conservando i risultati.

## Verdetto

L'evaluation è **numericamente e strutturalmente corretta come output descrittivo**. Non è una misura valida per attribuire differenze allo stile: entrambi gli anchor falliscono la tolleranza zero. Nessun numero o delta può essere promosso a winner, miglioramento causale o preferenza fra prompt. È consentita soltanto la conservazione storica del pannello descrittivo con l'etichetta `non_attributable_anchor_drift`.
