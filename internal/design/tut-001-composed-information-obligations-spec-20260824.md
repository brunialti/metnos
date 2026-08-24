# TUT-001 — obblighi informativi composti

Stato: specifica verificabile approvata dal mandato operativo del 24/8/2026.
Questa specifica non amplia l'autorita' del Tutor: riguarda soltanto la
selezione di fonti gia' ammesse dopo il confine `EXPLAIN`.

## 1. Difetto osservato e causa

Il turno `8805975d9728461e` chiedeva un confronto fra i controlli applicati
alla lettura e alla modifica di un file. Il recupero corrente ha incorporato
l'intera frase in un solo vettore. Le unita' di `read_files`, ripetute per
manifest e argomenti, hanno occupato la testa e il budget; `write_files` era
pertinente ma oltre la selezione. In seguito `_coherent_manifest_scope` ha
escluso per progetto ogni manifest secondario. Il ledger ha quindi censito
soltanto le fonti gia' scelte e non poteva sapere che una seconda parte della
domanda era rimasta senza evidenza.

Il difetto e' ancora attuale al 24/8/2026. Aumentare `top_k` non lo risolve:
sposta soltanto il punto di saturazione, aumenta il contesto e conserva la
stessa competizione fra obblighi diversi.

## 2. Invarianti

1. Il classificatore di modalita' resta l'unico confine fra spiegazione,
   osservazione e azione. L'analisi degli obblighi avviene soltanto dopo un
   esito `EXPLAIN` e non puo' chiamare executor o sonde live.
2. Ogni obbligo nasce dalla domanda corrente. Le fonti selezionate non possono
   inventare retroattivamente una parte da coprire.
3. Lingua, sinonimi e anafore non sono risolti con liste per query, executor o
   dominio. L'uscita e' uno schema chiuso, neutrale rispetto alla lingua.
4. Ogni obbligo recupera fonti con gli stessi controlli di audience, soglia,
   catalogo firmato e scadenza del percorso singolo.
5. Il budget resta globale: al massimo sedici fonti, senza aumento implicito di
   token, tempo o autorita'. Una fonte compare una volta per identita' stabile.
6. Una parte priva di fonte ammessa produce un gap locale; non autorizza una
   fonte sotto soglia e non annulla le parti fondate.
7. Una domanda semplice usa il percorso corrente senza una seconda
   composizione, senza doppio recupero e senza cambi di ranking.

## 3. Alternative confrontate

### A. Aumento del solo budget

Respinta. Non rappresenta le parti della domanda, non garantisce equita' e
peggiora costo e latenza. Il numero corretto dipenderebbe inoltre dal corpus.

### B. Segmentazione soltanto su punteggiatura e connettori

Utile come pre-segnale, ma insufficiente come autorita'. Non risolve pronomi
come «modificarlo», coordina male liste e confronti e dipende dalla morfologia
della lingua. Puo' evitare una chiamata nei casi chiaramente semplici, ma non
deve produrre da sola il ledger semantico.

### C. Obblighi tipizzati e recupero bilanciato

Scelta. Un classificatore locale a uscita chiusa riceve domanda e lingua e
restituisce da uno a quattro obblighi ordinati. Ogni obbligo contiene soltanto
un identificatore posizionale e una formulazione autosufficiente destinata al
recupero. Il modello puo' risolvere un'anafora, ma non puo' dichiarare fonti,
executor, autorita', esiti o fatti. La struttura e i limiti sono validati dal
codice; uscita assente o malformata ricade nel singolo obbligo originale.

## 4. Contratto dati

Schema interno versionato:

```text
InformationObligations {
  version: 1,
  items: [
    {id: "q1".."q4", retrieval_query: string}
  ]
}
```

Vincoli: ordine della domanda, da uno a quattro elementi, testo non vuoto,
lunghezza complessiva non superiore alla domanda piu' un margine bounded,
nessun duplicato normalizzato. Il testo non viene persistito in telemetria.

Per una domanda semplice il contratto e' `{q1: domanda_originale}`. Quando il
pre-segnale strutturale rileva piu' parti, il classificatore puo' confermarle,
fonderle o risolverne i riferimenti. Non puo' trasformare `EXPLAIN` in un'altra
modalita'.

## 5. Algoritmo di recupero e budget

1. Caricare una sola snapshot atomica del catalogo.
2. Recuperare separatamente per ogni `retrieval_query`, con la soglia corrente
   e la stessa audience.
3. Selezionare il primario ammesso di ogni obbligo prima delle fonti di
   supporto. Un primario sotto soglia resta un gap.
4. Deduplicare per `(source_type, source_id)`.
5. Distribuire gli slot residui a rotazione, in ordine di punteggio entro
   ciascun obbligo, senza superare sedici fonti complessive.
6. Conservare il primario del primo obbligo come primario di presentazione;
   gli altri primari sono evidenza obbligatoria, non nuovi argomenti.
7. Applicare le espansioni strutturali soltanto dentro gli slot assegnati; un
   inventario o documento non puo' sfrattare il primario di un altro obbligo.

La composizione riceve un blocco interno `QUESTION_OBLIGATIONS` che associa
ogni obbligo alle sole fonti ammesse per esso. Il coverage ledger nasce prima
dagli obblighi e incorpora poi i dettagli strutturati delle fonti. La rilettura
segnala separatamente `unsupported` e `missing_in_answer`.

## 6. Errori, sicurezza e rollback

- classificatore non disponibile o risposta non valida: percorso singolo
  corrente, mai rifiuto dell'intero Tutor;
- recupero senza fonte per un obbligo: gap locale esplicito;
- fonte primaria non visibile: stesso segnale ristretto corrente, senza
  rivelare titolo o contenuto;
- deadline: nessuna chiamata aggiuntiva dopo il margine della fase; fallback
  al percorso singolo se non e' iniziata la composizione;
- contesto oltre il limite: esito insufficiente, nessun troncamento;
- rollback: un unico flag disabilita classificatore e bilanciamento e ripristina
  byte per byte il percorso singolo.

## 7. Gate quantitativi

- turno osservato: fonti ammesse sia per lettura sia per modifica, risposta con
  entrambe le parti e nessun falso gap;
- almeno dodici casi IT/EN: semplici, coordinate, comparative, condizionali,
  anaforiche e con una parte realmente non documentata;
- zero cambi di fonti e risposta nel corpus semplice quando il pre-segnale non
  apre il percorso composto;
- nessuna fonte sotto la soglia corrente; massimo sedici fonti e 30.000
  caratteri nel payload;
- p95 entro il budget Tutor deliberato e nessuna chiamata aggiuntiva sui casi
  semplici;
- due cicli consecutivi dell'intera certificazione Tutor senza regressioni.
