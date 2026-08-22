# RM-0004 — verifica privata F14 (2026-08-22)

Stato: **criterio di uscita F14 superato**.
Questo rapporto è interno: non va pubblicato, incluso nel catalogo Tutor o
usato come documentazione rivolta all'utente.

## Esito sintetico

F14 ha eliminato il profilo come prerequisito per una singola azione lunga
compatibile. Il normale motore finalizza il piano e, immediatamente prima
dell'esecuzione, applica un unico controllo comune a scorciatoie, cache e
ripresa. Una durata dichiarata inferiore a 600 secondi conserva il percorso
interattivo; da 600 secondi in poi il runtime costruisce e accoda il piano LRE
minimo quando il contratto può essere congelato in modo verificabile.

Il percorso è generale: non contiene nomi di domini o executor. I piani
registrati restano un'ottimizzazione per grafi noti; non sono più una
condizione di ammissione. Dal momento in cui viene riconosciuta un'azione
intrinsecamente lunga, un errore di contratto, disponibilità o idempotenza
produce un rifiuto localizzato e non permette l'esecuzione in linea.

## Confini implementati

- Lo schema SQLite è alla versione 7; `stage_placements` congela server o
  dispositivo senza riscrivere le fasi esistenti.
- Gli argomenti finalizzati sono riferimenti `literal` JSON canonici,
  convalidati contro lo schema chiuso dell'executor e compresi nel digest.
- Il registro dei runner viene derivato una volta per processo dal catalogo
  verificato. Non esiste un profilo per executor.
- Sono ammessi automaticamente soltanto executor attivi, firmati,
  deterministici, non in-process, con effetto dichiarato, schema chiuso e
  collocazione risolvibile.
- Le risorse modello `llm` e `vlm`, gli executor intelligenti senza binding
  completamente congelato, gli argomenti risolti a runtime e gli effetti
  sconosciuti o interattivi falliscono chiusi.
- `read_only` usa semantica `pure` con un massimo di tre tentativi;
  `create_only`, `reversible` e `mutating` usano `manual_only` con un solo
  tentativo. Nessun effetto ambiguo viene ritentato automaticamente.
- `admission.submit_candidate` è l'unica sequenza bozza-ammissione-coda e
  conserva la convergenza per chiave e digest.
- `engine.dispatch` contiene un solo call site statico di `Executor.run()`;
  tutti i sette ingressi correnti attraversano prima lo stesso callback LRE.
- Approvazioni antecedenti vengono eseguite da sole; alla ripresa il piano
  finalizzato viene classificato di nuovo.

## Correzioni emerse dalla revisione

La revisione multidimensionale ha individuato e corretto una divergenza fra
collocazione persistita e collocazione effettiva. Un executor vincolato al
server, compresi i binding provider attivi, viene ora congelato sul server; un
executor vincolato a un dispositivo senza destinazione esatta viene respinto.
Non sono possibili né il ripiego locale di un dispositivo revocato né la scelta
implicita di un dispositivo dopo avere persistito `server`.

Sono inoltre respinti riferimenti `${...}`, `{{...}}`, `from_step` e
`from_steps`, anche annidati. In questo modo il worker non può reinterpretare
come dati letterali i collegamenti del piano interattivo.

## Verifiche riproducibili

| Verifica | Esito osservato |
|---|---|
| suite completa `tests/runtime/durable_workloads` | 324 passati in 39,54 s |
| ammissione LRE e nucleo del motore | 61 passati in 2,59 s |
| route amministrativa LRE | 3 passati in 0,15 s |
| chiavi i18n del repertorio iniziale | 18 passati e 1.118 subtest passati |
| documentazione, generatori e installazione | 40 passati in 2,09 s |
| inventario pubblico | 99 documenti HTML indicizzabili; lingue `it` ed `en` |
| catalogo Tutor da sorgenti locali | 4 schede, 3.402 unità, firma verificata; digest `sha256:4fe8ff4bfb25a8d6d98de4b78708de3e829b69ce94c8e4afe8d553a54432d892` |
| esclusione delle roadmap future dal Tutor | zero unità riferite a RM-0001, RM-0002 o RM-0005 |
| qualità delle differenze | `git diff --check` senza errori |

Il catalogo Tutor è stato compilato direttamente dai file locali del
repository, inclusa `docs/`; metnos.com non è stato contattato come sorgente.
Gli URL canonici sono soltanto citazioni. Nel contenitore di verifica la
directory dati dell'istanza non era scrivibile, quindi il catalogo candidato è
stato materializzato in una directory temporanea, firmato e verificato senza
sostituire il catalogo operativo.

Una prova HTTP basata su socket locale non ha prodotto un verdetto nel
contenitore, che vieta l'apertura della porta di ascolto. La stessa route è
coperta dalla prova unitaria senza rete ed è risultata verde. L'esclusione è
ambientale e non viene conteggiata come successo di integrazione HTTP.

## Limiti dichiarati

1. La prima ammissione dinamica copre una singola invocazione indipendente. Un
   piano composto non ancora rappresentabile viene rifiutato esplicitamente.
2. Un executor monolitico riprende al confine dell'unità, non da un progresso
   interno che il suo contratto non espone.
3. Un executor limitato a un dispositivo richiede una destinazione già
   risolta; LRE non sceglie una macchina leggendo di nuovo il testo utente.
4. Gli executor intelligenti entreranno nella copertura automatica soltanto
   quando modello, prompt, lingua e budget saranno completamente congelabili.
5. Non esiste un limite teorico di 98 o 980 elementi. Restano autorevoli i
   budget finiti e visibili della singola revisione e la granularità realmente
   esposta dal piano.

Conclusione: F14 soddisfa la matrice di accettazione di RM-0004. LRE è
automatico per le azioni lunghe compatibili quando il servizio dell'istanza è
abilitato, resta tracciabile nella console e non richiede conoscenze o scelte
architetturali da parte dell'utente.
