---
id: 0008
title: Microarchitettura precedente marcata UNTRUSTED, triade v1.1 come canonici
date: 2026-04-24
status: accepted
area: documentation
related:
  - 0007
---

## Contesto

A fine aprile 2026 la cartella `/opt/myclaw/docs/it/architecture/`
conteneva 22 documenti di microprogettazione (più l'index): scritti nei
mesi precedenti, allineati a un'ontologia in cui le componenti si
chiamavano "neuroni" e le loro relazioni "sinapsi". Nel frattempo erano
successe due cose grosse: il Dialogo sugli executor (22-23 aprile) aveva
ribattezzato i neuroni in "executor" e introdotto la famiglia mnest /
mnestoma / proto-mnest / ager (vedi ADR 0009 e 0010); l'Architettura era
stata bumpata a v1.1 con tre assi di sicurezza distinti, sei principi
guida, e una nuova topologia su `.33` (ADR 0007).

I 22 doc esistenti contenevano quindi termini, riferimenti incrociati e
scelte di design che non erano più allineate all'Architettura
corrente. Lasciarli online senza marker rischiava di fuorviare un
lettore che ci atterrasse direttamente da un motore di ricerca.
Rimuoverli avrebbe perso la tracciabilità del percorso di pensiero. La
domanda concreta era: come segnalare lo stato senza distruggere la
storia, e in che ordine ricostruire i canonici nuovi.

## Decisione

I 22 microdesign precedenti sono stati marcati **UNTRUSTED** in modo
visibile: banner rosso "DOCUMENTO UNTRUSTED (24 aprile 2026)" subito dopo
`<body>`, `<meta name="robots">` cambiato in `noindex, follow` per
toglierli dai risultati dei motori senza romperne i link interni. Lo
stato è leggibile programmaticamente: nel sorgente HTML compare la
stringa `UNTRUSTED-MARKER 2026-04-24`. Il banner contiene rinvii
all'Architettura v1.1, ai due dialoghi galileiani, e all'index della
microprogettazione.

In parallelo è stata pubblicata la prima triade di canonici v1.1, banner
verde "UNDER APPROVAL", bilingue IT+EN dall'origine: `executor.html` (che
sostituisce `neuron.html`), `mnest.html` (che sostituisce `synapse.html`),
`mnestoma.html` (IT) / `mnestome.html` (EN, suffisso *-ome*). Il
glossario è stato aggiornato perché le entry storiche `neurone` e
`sinapsi` puntino come canonical ai nomi nuovi. L'index della
microprogettazione (IT+EN) ha una sezione di testa "v1.1 — canonici nuovi"
che evidenzia la triade.

Il principio operativo che governa le riscritture future è:

- quando un microdesign UNTRUSTED viene riscritto, va promosso a v1.1,
  bilingue, banner verde "UNDER APPROVAL" o "APPROVED" se il POC l'ha
  validato (vedi ADR 0033 sul pattern POC-valida-microdesign);
- l'ordine di riscrittura non è deciso a priori; una proposta sensata è
  prima i tre strati centrali (gateway, policy, sandbox), poi telos e
  vaglio (già trattati nei dialoghi), poi tool/executor (rinominato), poi
  mnest/mnestoma (ex synapse, ex memory), poi il resto;
- finché un canonico non è scritto, il vecchio doc UNTRUSTED resta come
  tracciabilità ma non è autoritativo.

## Alternative considerate

**Cancellare i 22 doc obsoleti.** Pro: pulizia. Contro: si perde la
storia delle scelte; chi torna fra mesi e si chiede "perché abbiamo
scartato X" non trova il ragionamento. Anche per il SEO il problema dei
link rotti sarebbe più costoso del banner. Scartata.

**Riscrivere tutti e 22 in blocco prima di marcarli.** Pro: nessuna fase
intermedia di disallineamento. Contro: avrebbe richiesto settimane di
lavoro coordinato durante cui il sito sarebbe stato congelato; e molti
di quei doc avranno bisogno del codice che non c'è ancora per essere
riscritti correttamente. Scartata: meglio la fase intermedia con marker
chiaro.

**Solo `noindex` senza banner visibile.** Pro: meno disturbo visivo per
chi conosce l'architettura. Contro: chi atterra da un link interno o
condiviso vede la pagina senza capire che è obsoleta. Il banner serve
proprio a chi non ha il contesto. Scartata.

**Promuovere a "UNDER APPROVAL" anche i doc UNTRUSTED come passo
intermedio.** Pro: meno strappo visivo. Contro: la differenza fra "non
allineato" (UNTRUSTED) e "in attesa di firma" (UNDER APPROVAL) è
sostanziale. Mescolare i due stati confonde. Scartata.

## Conseguenze

I 22 doc obsoleti restano consultabili ma non indicizzati. La triade
v1.1 diventa il riferimento; ogni nuovo microdesign si scrive in IT+EN
dal primo giorno. Il pattern dei tre stati visivi (UNTRUSTED rosso /
UNDER APPROVAL verde tenue / APPROVED verde pieno) si è consolidato come
linguaggio del corpus.

Resta come disciplina ricorrente, alleata della memoria sull'allineamento
doc-codice (vedi ADR sull'allineamento), che ogni promozione di un
microdesign da v1.0 obsoleto a v1.1 canonico richieda anche
l'aggiornamento di: cap. 17 dell'Architettura (Roadmap & approfondimenti),
banner "Aggiornamento post-POC" nell'index, card "Continua a leggere",
meta description / og: / twitter: dell'index, tabella "v1.1 — canonici"
dell'index, barra fasi, banner di stato dei doc canonici toccati. È una
catena lunga ma ogni anello conta.
