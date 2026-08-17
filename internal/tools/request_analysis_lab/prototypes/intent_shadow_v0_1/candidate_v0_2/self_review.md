# Revisione avversariale — candidate intent IR 0.2

## Verdetto provvisorio

Il disegno elimina dal testo generato le porte arbitrarie che hanno dominato
il fallimento del candidato precedente. Non dimostra ancora che il modello
sceglierà la rotta corretta: questa fase è volutamente offline e senza GPU.

## Attacchi eseguiti

1. **Chiavi duplicate.** Il decodificatore le rifiuta prima della validazione.
2. **Numeri ostili.** `NaN`, infinito e overflow non diventano valori Python
   accettabili. `bool` e `float` non passano per ordinali interi.
3. **Campi inventati.** Ogni oggetto è chiuso. Il modello non può fornire
   `input`, `output`, `path`, `ordinal`, `outcome` o `continuation`.
4. **Autorità alterata.** Registro, fonti e liste uniche sono canonici e legati
   a impronte. Il controllo contro la sorgente respinge rotte tolte o aggiunte.
5. **Dipendenze illegali.** Self-reference, riferimenti futuri e valori di un
   ramo usati fuori dal ramo falliscono chiusi. L'ordinamento non semantico
   delle sorgenti viene normalizzato.
6. **Barriera aperta.** Il modello non può creare casi o esiti. Il compilatore
   possiede l'espansione `approved/rejected`; il rifiuto è sempre vuoto.
7. **Ripresa modificata.** La continuazione include impronte di registro,
   documento, percorso, esito e corpo tipizzato. Una variazione la invalida.
8. **Contaminazione.** Schema, prompt e proiezione non contengono testi, ID o
   impronte dei 124 casi. L'adattatore dell'oracolo vive soltanto nei test.
9. **Registro rinominato.** Una rotta sintetica nuova funziona dopo la modifica
   del registro; il vecchio nome viene rifiutato. Il compilatore non contiene
   eccezioni per le rotte del banco.
10. **Finta seconda opinione.** Il codice espone un segnale e una richiesta per
    il critico, ma non chiama un modello né fonde due risposte. Il massimo resta
    due e il percorso normale usa una chiamata.
11. **Trasporto impostore.** Il primo ciclo accettava un oggetto che si
    autodichiarava offline. Ora il trasporto è una classe sigillata costruita
    internamente dal client; imitazioni, costruzione esterna e sottoclassi sono
    respinte prima che il loro metodo `send` possa essere chiamato.
12. **Ordine falso delle chiavi.** Il primo ciclo richiedeva `kind` come prima
    chiave nel validatore, cosa che JSON Schema non può esprimere. La regola di
    validità è stata rimossa: `kind` primo o ultimo compila nello stesso oggetto
    e con la stessa impronta. Il serializzatore model-facing continua a
    presentare `kind` per primo, senza attribuire significato all'ordine.
13. **Lingua ignorata o binaria.** La lingua ora è un tag BCP47 normalizzato,
    inserito esplicitamente nell'unico prompt neutro. Non esiste allowlist,
    branch `it/en` o fallback implicito; un tag malformato fallisce pre-send.
14. **Overfit al campione.** Uno scan sui sorgenti candidato e adapter respinge
    testi, frammenti significativi, ID e hash del banco. Rotte e dipendenze
    restano derivate dal registro e provate anche su sintetici rinominati.

## Limiti onesti

- La forma compatta rappresenta una sola continuazione non vuota per barriera.
  Una barriera con più rami operativi richiede una decisione nuova e una nuova
  versione; non viene generalizzata in silenzio.
- Alcuni executor esistono soltanto nello snapshot storico e offrono una
  descrizione italiana non strutturata. La proiezione conserva il testo pieno;
  il prompt usa una frase limitata. Non viene inventata una traduzione inglese.
- Il sottoinsieme JSON Schema dovrà essere provato col modello soltanto in una
  futura misura autorizzata. Qui il trasporto è esclusivamente finto.
- L'interfaccia del critico non stabilisce ancora come produrre un segnale
  semantico affidabile. Implementarlo ora avrebbe introdotto una nuova policy.
- Il giro sull'oracolo prova equivalenza di rappresentazione, non apprendimento:
  la conversione è isolata nei test e non è accessibile al percorso `analyze`.
- I dizionari sono confinati al laboratorio e agli artefatti JSON. Un porting
  nel core Metnos richiede tipi immutabili; non autorizza modelli dati `dict`.

## Stato di sicurezza

Nessun file di produzione, banco, oracolo, batch, diario, sigillo o risultato
storico viene scritto. Nessun servizio, rete o GPU viene usato.
