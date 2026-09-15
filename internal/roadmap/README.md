# Roadmap Metnos

Questa directory conserva i progetti riconosciuti come utili e meritevoli di
identità stabile, inclusi quelli già implementati o chiusi. È una classe
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
`implemented` quando supera i criteri tecnici e a `closed` quando anche il
closeout dichiarato (documentazione, distribuzione o altri gate richiesti)
risulta verificato. In entrambi i casi conserva i riferimenti alle prove.
Un'eventuale archiviazione o rimozione successiva richiede una decisione
esplicita.

## Stati ammessi

| Stato | Significato |
|---|---|
| `active` | direzione accettata, progettazione o priorità ancora aperte |
| `ready` | confini e criteri sufficienti per iniziare l'implementazione |
| `in_progress` | implementazione iniziata e tracciata nel documento |
| `implemented` | criteri tecnici di uscita soddisfatti con prove referenziate; closeout ancora non dichiarato o non richiesto |
| `closed` | implementazione e closeout dichiarato completati; nessuna attività residua nella roadmap |
| `cancelled` | cancellazione della direzione decisa esplicitamente da Roberto |

Solo `closed` e `cancelled` sono terminali. Inattività, data del file, assenza
da un TODO, implementazione parziale o solo completamento tecnico non sono
stati terminali.

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
| [RM-0009](RM-0009-crescita-allineata-delle-capacita.md) | Crescita allineata delle capacità | `active` | revisione 8; base RM-0008 1c308922 verificata, inventari e piano candidato aggiornati; contratti G0 ancora aperti, codice F0-F6 non iniziato | 2026-09-15 |
| [RM-0008](RM-0008-porta-unica-nascita-executor.md) | Porta unica di nascita e ciclo controllato degli executor sintetizzati | `in_progress` | F4: transizione produttiva e turni reali verificati; requisiti di preesercizio e chiusura F5-F6 ancora distinti | 2026-09-08 |
| [RM-0007](RM-0007-pubblicazione-verificata-contratti.md) | Pubblicazione verificata delle varianti linguistiche dei contratti | `closed` | M0-M4, cutover, due cicli operativi e distribuzione certificati | 2026-08-25 |
| [RM-0006](RM-0006-certificazione-logica-e2e.md) | Certificazione logica da capo a fondo | `implemented` | C0-C6 completate; cinque sonde reali e certificazione finale 96/96 | 2026-08-23 |
| [RM-0005](RM-0005-multilinguismo-full-auto-localizzante.md) | Multilinguismo full e auto-localizzazione dell’istanza | `closed` | consolidamento lessicale completo; 21 contratti Birth firmati e CI Linux/Windows verde | 2026-08-30 |
| [RM-0004](RM-0004-motore-workload-durevoli.md) | Motore generico per lavori lunghi, persistenti e paralleli | `implemented` | F0-F14 completate; ammissione automatica centralizzata, senza profilo obbligatorio | 2026-08-22 |
| [RM-0003](RM-0003-tutor-integrato.md) | Tutor integrato: guida operativa intelligente | `closed` | F2/F3/F4 implementate, certificate e distribuite | 2026-07-30 |
| [RM-0002](RM-0002-linter-manifest-multilingue.md) | Controllo multilingue dei manifest executor | `closed` | L0-L6 certificate, provate live e distribuite | 2026-08-25 |
| [RM-0001](RM-0001-conoscenza-utente-locale.md) | Conoscenza utente locale: memoria forte, semplice e automatica | `ready` | design F0-F6 finalizzato; implementazione non iniziata | 2026-07-28 |
