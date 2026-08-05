---
id: 0192
title: Gate E2E su due esecuzioni consecutive e matrice stabile
date: 2026-07-15
status: accepted
area: test
related: [0154, 0159, 0189]
---

## Context

Una singola suite verde non distingue una correzione stabile da una convergenza
casuale di LLM, rete o servizi esterni. Inoltre il corpus storico classificava
come successo ogni `final_kind=answer`, includendo rifiuti, dialoghi incompleti
e operazioni mai eseguite. Il primo baseline completo ha raccolto 283 test con
13 failure; il successivo slow ne ha raccolti 287 con 12 failure.

## Decision

Il gate canonico e' `tests/e2e/run.sh --quality-gate`. L'obiettivo e' raggiunto solo
con due run consecutivi che rispettano `tests/e2e/quality_targets.json`: zero failure,
zero error, success rate 100%, almeno 95% dei test eseguiti, almeno 280 test
raccolti e fingerprint identico della matrice raccolta/eseguita.

Il corpus viene congelato una volta prima dei due run. Un esito storico e'
inferito positivo solo se almeno un executor produttivo ha `result.ok=true` e
nessun errore o handoff; il solo testo finale non e' evidenza. I replay positivi
sono read-only. Le fixture realistiche copiano database piccoli e usano symlink
read-only per corpus e indici voluminosi. Il server E2E esegue il worktree con
l'interprete della distribuzione Metnos, non con un Python di sistema casuale.

## Alternatives considered

- Un solo run verde: piu' rapido, ma non rileva flakiness o divergenza LLM.
- Retry automatici fino al verde: nascondono le cause e non misurano stabilita'.
- Considerare `final_kind=answer` un successo: semplice, ma semanticamente falso.

## Consequences

Il gate completo costa due esecuzioni slow e usa servizi reali configurati, ma
produce metriche confrontabili e un report JSON verificabile. Gli skip restano
ammessi entro il 5% per dipendenze realmente indisponibili; ogni variazione della
matrice tra i due run invalida comunque l'obiettivo.
