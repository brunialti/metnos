# Redacted prompt anti-contamination audit

Offline only; no model/server call. Query text, prompt excerpts and matched n-grams are omitted. Fingerprints are SHA-256.

| Prompt | Surface/mixed lines | 109 exact / >=4 | 34 exact / >=4 | 70 exact / >=4 | Max-objective credit |
|---|---:|---:|---:|---:|---|
| v25 | 8 | 0 / 0 | 0 / 0 | 0 / 0 | no |
| v25.1-none-first | 8 | 0 / 0 | 0 / 0 | 0 / 0 | no |
| v25.1-active-first | 8 | 0 / 0 | 0 / 0 | 0 / 0 | no |
| v25.2-relation-goal | 8 | 0 / 0 | 0 / 0 | 0 / 0 | no |
| v26 | N/A | N/A | N/A | N/A | no (missing) |

Decision: current prompts remain useful experimental measurements, but none may receive maximum-objective acceptance credit while source-language examples or lexical mappings remain in prompt prose. Technical ontology labels/definitions are reported separately and are not, by themselves, contamination.

No V26 artifact was present. V25.2 relation-goal is audited under its real version and is not silently relabelled V26.

## Aggiornamento V26.2

L'ultima frase sopra descrive il checkpoint precedente e non va applicata al
freeze successivo. V26.2 minimal Phase 1 è ora presente e ha un audit frozen:

- prompt SHA-256
  `dad168f1975a3ab96b3e70303efce939aaab464ba71f567ff9c691d11c862a60`;
- 0 righe surface/miste;
- 0 query complete e 0 overlap di almeno tre token contro 109+34+70;
- nessuna action ontology, route o object di prodotto;
- eleggibile per il run dell'obiettivo massimo, ma non ancora per il cutover.

Audit completo in `/tmp/metnos_prompt_contamination_audit.json`, legato al
freeze dal gate e dal verifier elencati nel §16 del handover principale.

## Aggiornamento V26.4

V26.4 typed relational è congelato offline con prompt SHA-256
`2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f`.
Il suo audit frozen conferma:

- 0 righe surface/mixed;
- 0 query intere;
- 0 overlap contiguo di almeno tre token su 109 + 34 + 70;
- nessun esempio, sinonimo o mapping della lingua sorgente;
- chiamate server/modello: 0.

Audit SHA-256
`6845ef5ab1a3b7f7871909a285b070c292cfe97b2cf923471ebfeee99af78ff9`,
archiviato in `internal/tools/request_analysis_lab/candidates/v264/`. Questo
esito abilita soltanto il credito anti-contaminazione: il gate esterno è
ancora assente e nessun run è autorizzato.
