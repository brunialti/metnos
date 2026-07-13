---
id: 0189
title: Executor intelligenti come agenti a mandato ristretto
date: 2026-07-11
status: accepted
area: runtime
related: [0001, 0045, 0071, 0159, 0188]
---

# 0189 — Executor intelligenti a mandato ristretto

## Contesto

Un executor tradizionale applica una procedura nota a input tipizzati. Alcuni
compiti hanno invece uno scopo preciso ma un percorso non noto a priori: login
multi-stadio, wizard, UI variabili, risposte API che richiedono recupero. Portare
ogni variazione nel planner irrigidisce il linguaggio e gli attribuisce dettagli
e autorita' che appartengono al dominio operativo.

## Decisione

Metnos ammette **executor intelligenti**. Operativamente sono agenti: osservano,
scelgono un passo, agiscono e verificano. Architetturalmente restano executor:
il loro mandato, contratto e autorita' sono chiusi.

- Il contratto pubblico non cambia. Il planner passa gli stessi argomenti e
  riceve lo stesso schema di output di una implementazione non agentica.
- Lo scopo deriva dal nome e dalla descrizione dell'executor; il modello interno
  non puo' sostituirlo, ampliarlo o creare un secondo piano generale.
- Il ciclo interno e' bounded per passi e tempo:
  `observe -> resolve -> verify -> gate -> execute -> verify`.
- Resolver deterministici e controlli strutturali hanno precedenza. Il modello
  gestisce soltanto l'incertezza residua e sceglie entro un insieme enumerato e
  fissato dal runtime.
- Capability, sandbox, origine dei dati, gate e policy del normale executor
  restano invariati. L'executor non puo' auto-approvarsi o estendere la propria
  autorita'.
- Il successo richiede una postcondizione osservabile. Budget esaurito, stato
  instabile o postcondizione assente producono un errore tipizzato, mai un
  successo inferito.
- Quando l'ostacolo richiede autorita' o informazione umana, l'executor produce
  un handoff esplicito conservando soltanto lo stato necessario e sicuro.

Il pattern e' ortogonale ai domini e non introduce un nuovo suffisso di naming,
un nuovo tipo di pipeline o una sintassi utente. Non ogni executor deve essere
intelligente: la procedura diretta resta preferibile quando lo stato e' noto.

## Criteri di adozione

Un executor puo' adottare il pattern quando esistono tutti questi elementi:

1. scopo stretto e non ambiguo;
2. spazio d'azioni limitato;
3. progresso riosservabile;
4. postcondizione verificabile;
5. budget finito;
6. autorita' separata dall'eventuale modello.

Ricerca aperta, strategia multi-dominio e creazione senza criterio di
completamento restano responsabilita' del planner.

## Catalogo

Il concetto di executor intelligente e' separato dal censimento per dominio.
Il catalogo pubblico e' generato deterministicamente dai manifest firmati sotto
`executors/` e raggruppato secondo l'oggetto canonico del nome. Gli executor
installati o sintetizzati appartengono al catalogo runtime della singola
istanza. Nessuna lista manuale e nessuna mappa per dominio ad hoc.

## Prima applicazione

ADR 0188 applica il pattern a `login_sites` e alla navigazione bounded di
`act_sites`: il planner conosce soltanto input e output, mentre il broker risolve
gli stati intermedi con autorita' e verifiche specifiche del compito.

## Conseguenze

L'adattamento alle variazioni rimane vicino alla competenza che sa verificarlo,
senza trasformare la CLI in un linguaggio o il planner in un automa di
micro-passaggi. Il costo e' che ogni executor intelligente deve avere test sugli
stati, sui budget, sugli handoff e sulle postcondizioni, non soltanto sul caso
felice.

