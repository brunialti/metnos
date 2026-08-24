# CONV-001 — confine delle richieste senza azione

## Verifica di attualita'

I turni storici indicati nel TODO descrivono ancora un difetto reale del
fallback: un intent senza verbo e oggetto apre l'intero catalogo e il proposer
puo' scegliere un executor non pertinente. La parte relativa al manuale non e'
piu' attuale: il Tutor viene eseguito prima del planner, usa il catalogo firmato
di documentazione pubblica e viste live autorizzate, conserva le fonti e
gestisce in modo esplicito indisponibilita' e lacune. Aggiungere un executor
documentale parallelo duplicherebbe fonte, autorizzazione e presentazione.

Resta quindi un solo cambiamento necessario: rappresentare in modo tipizzato
l'assenza di un'azione e impedire che quel caso acquisti per fallback l'intero
catalogo operativo.

## Contratto

L'estrattore restituisce una union chiusa, indipendente dalla lingua:

- `action`: osservazione di dati reali o effetto richiesto; richiede almeno un
  verbo od oggetto canonico e conserva l'attuale decomposizione composta;
- `conversation`: interazione sociale o discorsiva senza operazioni;
- `metnos_help`: domanda stabile su capacita', funzionamento o uso di Metnos;
- `unknown`: classificazione non sufficientemente affidabile.

La presenza di un'azione canonica prevale sulle altre etichette. Gli output
legacy con verbo od oggetto vengono interpretati come `action`, rendendo
graduale l'attivazione dei prompt materializzati. `unknown` conserva il
fallback completo: un guasto o un dubbio del classificatore non deve far
sparire una richiesta operativa.

`conversation` e `metnos_help` producono un pool operativo vuoto. La grammar
del framework aggiunge comunque il solo strumento virtuale `final_answer`, che
non ha effetti. Il Tutor intercetta normalmente `metnos_help` prima del
runtime; il confine nel routing serve come protezione quando il pre-gate
declina. Non vengono introdotti sinonimi, parole chiave o mappe per executor.

## Invarianti e rollback

1. Una query `action`, incluse quelle miste con cortesia, vede lo stesso pool
   che vedeva prima.
2. `unknown` vede lo stesso catalogo completo di prima.
3. `conversation` e `metnos_help` non vedono executor reali.
4. La classificazione vive nei prompt IT/EN versionati; gli identificatori
   restano invarianti e language-neutral.
5. Il rollback consiste nel rimuovere il campo `kind` e il gate iniziale del
   pool; nessun dato persistito o manifest viene migrato.
