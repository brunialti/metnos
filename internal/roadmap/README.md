# Roadmap Metnos

Questa directory contiene progetti futuri riconosciuti come utili e meritevoli
di conservazione, ma non ancora completamente implementati. È una classe
documentale distinta da analisi, rapporti, specifiche correnti, TODO e ADR.

## Autorità e significato

- Una roadmap esprime una direzione progettuale consolidata, non lo stato del
  codice.
- Codice, manifest caricati, test e ADR implementate restano le fonti dello
  stato corrente.
- L'inclusione nella roadmap non autorizza da sola l'implementazione né modifica
  i principi invarianti di `CLAUDE.md`.
- Quando inizia l'implementazione, la roadmap resta il quadro dei requisiti e
  viene collegata alle specifiche, alle ADR e alle prove prodotte.

## Conservazione

Una roadmap con stato non terminale è **persistente**:

- non viene rimossa perché vecchia, inattiva o non pianificata;
- non rientra nella pulizia periodica di handoff, report o specifiche concluse;
- non viene assorbita in un altro documento perdendo identità e criteri di
  accettazione;
- può essere cancellata soltanto su indicazione esplicita di Roberto;
- esce dallo stato attivo soltanto quando l'implementazione è dimostrata oppure
  quando viene cancellata esplicitamente.

Il completamento non implica la cancellazione: il documento passa a
`implemented` e conserva i riferimenti alle prove. Un'eventuale archiviazione o
rimozione successiva richiede una decisione esplicita.

## Stati ammessi

| Stato | Significato |
|---|---|
| `active` | direzione accettata, progettazione o priorità ancora aperte |
| `ready` | confini e criteri sufficienti per iniziare l'implementazione |
| `in_progress` | implementazione iniziata e tracciata nel documento |
| `implemented` | criteri di uscita soddisfatti con prove referenziate |
| `cancelled` | cancellazione della direzione decisa esplicitamente da Roberto |

Solo `implemented` e `cancelled` sono terminali. Inattività, data del file,
assenza da un TODO o implementazione parziale non sono stati terminali.

## Metadati minimi

Ogni documento deve dichiarare in testa:

- identificatore stabile `RM-NNNN`;
- stato;
- data di creazione e ultima revisione;
- decisione di conservazione;
- stato reale dell'implementazione;
- documenti di origine e, quando presenti, ADR e prove.

Ogni roadmap deve inoltre separare chiaramente:

1. obiettivo e valore per l'utente;
2. stato del codice verificato;
3. proposta futura;
4. invarianti e non-obiettivi;
5. rischi e misure anti-regressione;
6. fasi e criteri misurabili di completamento.

## Indice

| ID | Titolo | Stato | Implementazione | Ultima revisione |
|---|---|---|---|---|
| [RM-0001](RM-0001-conoscenza-utente-locale.md) | Conoscenza utente locale: memoria forte, semplice e automatica | `ready` | design F0-F6 finalizzato; implementazione non iniziata | 2026-07-26 |
| [RM-0002](RM-0002-linter-manifest-multilingue.md) | Linter multilingue dei manifest executor | `active` | non iniziata | 2026-07-23 |
| [RM-0003](RM-0003-tutor-integrato.md) | Tutor integrato: guida operativa intelligente | `closed` | F2/F3/F4 implementate, certificate e distribuite | 2026-07-30 |
| [RM-0004](RM-0004-motore-workload-durevoli.md) | Motore generico per lavori lunghi, persistenti e paralleli | `active` | F0-F2 completate nel nucleo interno inattivo; F3 claim/lease/fencing non iniziata | 2026-08-20 |
