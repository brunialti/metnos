---
id: 0194
title: Il linguaggio naturale precede lo schema executor
date: 2026-07-16
status: accepted
area: executor | interaction
related: [0039, 0040, 0042, 0045, 0189, 0193]
---

# 0194 - Il linguaggio naturale precede lo schema executor

## Contesto

La standardizzazione degli executor riduce ambiguita' tecniche, ma puo'
produrre l'effetto opposto se nomi canonici, argomenti ed enum diventano una
sintassi che l'utente deve imparare. Metnos deve comprendere richieste naturali,
non trasformare la chat in una CLI mascherata.

Allo stesso modo, un executor intelligente deve mantenere un mandato e un
contratto verificabili senza essere ridotto a una sequenza rigida incapace di
adattarsi a pagine, documenti o provider diversi.

## Decisione

Lo standard executor si applica **dopo** l'interpretazione linguistica. Nomi,
argomenti tipizzati, enum e schema di output sono il protocollo interno fra
planner ed executor; non sono un linguaggio pubblico.

Il planner e i normalizzatori condivisi devono accettare parafrasi naturali
materialmente diverse quando esprimono lo stesso scopo. Affinity, descrizioni ed
esempi non sono whitelist di frasi. La conformita' richiede test di
orchestrazione con parafrasi e non puo' essere provata soltanto invocando
direttamente l'executor con JSON canonico.

Per un executor intelligente, lo standard vincola mandato, autorita', budget,
azioni ammesse, osservabilita' e postcondizione. Non prescrive un unico percorso
interno: l'executor puo' scegliere e correggere il percorso entro quei limiti.

## Conseguenze

- La migrazione legacy non deve introdurre comandi o ordinamenti obbligatori.
- Gli enum interni devono essere risolti da linguaggio naturale e i18n, non
  esposti come parole magiche.
- I test di nascita verificano il contratto tecnico; un corpus di parafrasi
  verifica separatamente la comprensione del sistema.
- Un executor puo' diventare piu' verificabile senza diventare meno adattivo.

