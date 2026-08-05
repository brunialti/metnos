---
id: 0190
title: Mandato credenziale persistente e inviluppo task subordinato
date: 2026-07-12
status: accepted
area: runtime
related: [0089, 0090, 0186, 0188, 0189]
---

# 0190 - Mandati credenziale per uso interattivo e schedulato

## Contesto

Un token di approvazione browser e' one-shot e dipende dal DOM corrente. Non
esprime come le credenziali possano essere usate nelle query future, siano esse
interattive o schedulate. Nei task il problema e' piu' evidente, perche' durante
il fire non c'e' un utente che possa rispondere a un dialogo.

## Decisione

Il mandato persistente appartiene al binding credenziale. Si applica dall'inizio
a ogni query che possa usare quel binding, indipendentemente dal canale e dalla
presenza di un task.

- Il binding credenziale conserva, cifrato insieme ai segreti, gli scope di
  utilizzo. `sites.read` permette login e navigazione automatizzata di sola
  lettura; non permette modifiche, invii, pagamenti, download o POST diversi
  dalla continuazione strettamente interna del login.
- Le nuove credenziali web non ricevono uno scope implicito: un form privo di
  segreti fa scegliere `interactive` oppure `sites.read`. Lo stesso form puo'
  aggiornare il mandato senza reinserire username o password.
- Una query interattiva usa il mandato come default. Puo' restringerlo, per
  esempio evitando il login, oppure chiedere un'azione piu' ampia; in questo
  secondo caso il mandato non si amplia e torna il consenso one-shot ordinario.
- Un task aggiunge un envelope subordinato con versione, actor, hash della query, host
  esatti, origini credenziali approvate e classi operative. Cancellazione,
  disabilitazione o modifica non riallineata della query invalidano l'envelope.
- Gli host aggiuntivi provengono soltanto dall'audit di sessioni realmente
  aperte e da transizioni esplicitamente approvate. Host solo osservati o
  bloccati non entrano nel mandato.
- Il run schedulato propaga al subprocess soltanto il nome opaco del task. Il
  broker ricarica il record persistente, verifica actor e hash e rilegge lo
  scope credenziale al momento dell'uso. Revocare `sites.read` revoca quindi
  subito tutti i task dipendenti.
- Un task non puo' aprire dialoghi interattivi durante il fire. Un host, submit
  o effetto fuori envelope produce `mandate_scope_exceeded` e fallisce chiuso;
  la configurazione si amplia solo con una nuova esecuzione interattiva.
- L'autorita' di un task e' l'intersezione fra mandato credenziale corrente,
  envelope del task e azione richiesta. Il task non crea mai autorita' propria.

Il testo utente resta linguaggio naturale. Il form e gli scope sono il
contratto interno; non viene introdotta una grammatica CLI obbligatoria.

## Conseguenze

La credenziale descrive **come** puo' essere usata in ogni modalita', la query
puo' solo restringere il default o richiedere un consenso interattivo ulteriore,
il task aggiunge **quando e per quale richiesta**, il broker verifica **dove e
con quali azioni osservate**. Un sito mai commissionato puo' richiedere un primo
run interattivo per verificare redirect e origine del form; dopo la commissione
sia le query interattive sia i task di lettura applicano il mandato persistente.

## Verifica

- `tests/runtime/infra/test_task_mandates.py`: default interattivo, estrazione host
  esatti, esclusione degli host solo osservati, integrita' query, revoca scope,
  redirect unattended, divieto POST e propagazione task.
- `tests/runtime/sites/test_sites_security.py`: gate one-shot, batch goal,
  continuazioni bounded e nessun ampliamento implicito della rete.
- Suite credenziali: form prima del salvataggio web, metadata-only update e
  preservazione dei segreti.
