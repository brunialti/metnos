# La legge della negazione nel prompt di V23lite (10/8/2026)

Lavoro shadow, niente committato, nessuna scrittura sul runtime. Attrezzi in
`/tmp/claude-1000/-opt-metnos/5ab418a6-8fbc-493c-9965-0dffcbd8a6b1/scratchpad/`.

## 1. Il risultato in una riga

Il carico di un blocco di prompt **non dipende dalla sua lunghezza** (rho −0,10).
Regge la misura; **non regge la spiegazione**: l'ipotesi «conta quanto il blocco
nega nominando la rotta che rifiuta» (rho −0,73) e' stata **smentita dalla prova
decisiva** — vedi §5-bis. Il titolo di questo file resta per continuita' di
riferimento, ma la legge della negazione **non e' stata confermata**.

## 2. La corsa a cicli, misurata

Campione: 40 query reali, seme 20260810. Ogni ciclo = 3 passate, per separare i
fallimenti stabili dagli instabili.

| ciclo | valide | intervento | esito |
|---|---:|---|---|
| base | 36/36/36 | — | 4 fallimenti stabili |
| c1 | 37/37/37 | tetto d'uscita 1200 → 2600 | i 4 erano **troncamenti**, `finish=length` |
| c2 | 39/39/39 | contratto di ruolo riallineato al validatore | **+2** |
| c3 | 37/37/37 | regola àncora come frase di chiusura in blocco lontano | **−2, scartata** |
| c4 | 36/40 | stessa regola come divieto nel punto di definizione | **−3, scartata** |
| c5 | in corso | rimozione di `TIE_BREAK_REFINEMENTS` | atteso 40/40 |

**Banda di rumore = 0**: 240 chiamate, 0 query instabili per validità e per
rotta. Il confronto fra prompt è quindi leggibile a una passata sola. Unica
eccezione misurata: la **stessa identica richiesta ripetuta di fila** può dare
frame diversi (riuso della cache KV del prompt intero) — vale per i ritenta­tivi,
non per il campione.

## 3. I due difetti veri trovati

**(a) Il prompt istruiva ciò che il validatore rifiuta.** Il validatore
(`unified_query_bench_v23_checkpoint.py:1301-1305`) impone: `role == request` →
verbo e oggetto entrambi diversi da `none`; **ogni altro ruolo** → entrambi
`none`. Il prompt diceva l'opposto: *"For non-request records use the closest
canonical action/object when clear"*. Sulle query con divieti espliciti («non
scaricare, non modificare») la coppia È chiara, il modello obbediva al prompt e
il frame veniva bocciato. Allineato il testo al contratto: +2 query.

**(b) L'àncora è insoddisfacibile sulla coordinazione.** «**crea** un riepilogo
**e** un foglio» e «**conta** i file **e** le directory» sono due operazioni con
**un solo** token-verbo, mentre il validatore vuole un'àncora unica e crescente
per record. Due tentativi di insegnarlo al modello sono costati −2 e −3: il
prior «àncora = token del verbo» non cede, e ha ragione a non cedere, perché la
grammatica non offre un secondo token.

In v23lite l'àncora **non è una chiave d'arco**: lo schema rimuove
`input_from_predicate_anchor_id` (righe 735, 795) e porta gli archi per
`predicate_id`; V24.1 la nomina una sola volta, in una fixture. La crescita
stretta è un invariante **ereditato** dalle varianti v21/v22, dove serviva da
chiave. Rimedio scritto e verificato a freddo: `riparo_ancore.py`, riparazione
deterministica pre-validazione (§7.9 — il modello etichetta, il codice taglia),
che spara solo sulla collisione ed è inerte altrove.

## 4. L'ablazione: quale blocco regge cosa

Uno-a-uno sui dieci blocchi, stesse 40 query, riferimento 39/40.

| blocco tolto | costo | char | frasi che negano nominando una rotta |
|---|---:|---:|---:|
| `GLOSS_REFINEMENTS` | **+7** | 445 | **0%** |
| `ONTOLOGY_REFINEMENTS` | +4 | 2.253 | **0%** |
| `STRICT_COMPOUND_REFINEMENTS` | +2 | 1.674 | 4% |
| `LEMMA_LAYER_REFINEMENTS` | +2 | 1.117 | 0% |
| `STRUCTURE_REFINEMENTS` | +1 | 938 | 0% |
| `COMPOUND_REFINEMENTS` | +1 | 1.912 | 5% |
| `ANCHOR_AND_SINK_REFINEMENTS` | +1 | 914 | 0% |
| `SCOPE_REFINEMENTS` | 0 | 537 | 0% |
| `TAGGED_ARGUMENT_GRAPH_LITE` | 0 | 2.910 | 3% |
| `TIE_BREAK_REFINEMENTS` | **−1** | 1.151 | **31%** |

445 caratteri che valgono 7 query contro 2.253 che ne valgono 4: la resa non è
proporzionale al testo.

## 5. Il razionale

`TIE_BREAK` e `ONTOLOGY` dicono **le stesse sei cose** (persons/registro,
move/messages, read/files con destinazione, filter vs delete, find/dirs,
get/files su metadati foto). Cambia il **modo**:

- `ONTOLOGY` **afferma**: *"Aggregate inspection of directories uses find/dirs"*;
- `TIE_BREAK` **arbitra nominando il perdente**: *"…never get/dirs"*,
  *"list/persons is not a canonical intent"*, *"It is not set/images"*.

Il primo, tolto, costa 4 query. Il secondo, tolto, ne **rende** 1.

> **Ciò che il prompt afferma, il modello lo esegue. Ciò che il prompt nega
> nominandolo, il modello lo rende disponibile.** In decodifica vincolata su
> vocabolario chiuso, essere in contesto è essere più probabile: la rotta citata
> per escluderla viene emessa proprio dove la regola la vieta.

Negare in astratto è quasi gratis (rho −0,62 contro −0,73): quello che pesa è
**nominare la rotta canonica**.

### 5-bis. La prova decisiva l'ha smentita (ciclo c6)

`TIE_BREAK` riscritto in forma **affermativa**, stesso contenuto, nessuna rotta
nominata per escluderla. Previsione dichiarata prima della misura: 40/40 se
contava la negazione, 39/40 se contava la ridondanza. **Risultato: 37/40**, peggio
di entrambe. Rimettere quel blocco *in qualunque forma* riporta i fallimenti di
collisione d'ancora che la sua rimozione aveva risanato: non e' la forma, e'
che **piu' regole di rotta spingono il modello a decomporre piu' finemente**, e
piu' pezzi significa piu' collisioni sul vincolo d'ancora (§3b). Nell'esperimento
«quante regole» e «in che forma» variavano insieme, e ho attribuito alla forma
cio' che era della quantita'. Il rho −0,73 resta una correlazione non spiegata.

Restano valide per conto loro, non unificate da questa ipotesi: gli esempi
letterali tolti dal Tutor (103→106/134) e il divieto di liste di sinonimi.

### Conseguenze operative

1. ~~Una regola si scrive solo come affermazione di cosa una cosa È.~~ Caduta
   con la legge: da rimisurare separando quantita' e forma.
2. Se una regola può essere espressa **solo** nominando la risposta sbagliata,
   non appartiene al prompt: appartiene al **codice deterministico** (§7.9).
   È il caso dell'àncora, e spiega i −2/−3 dei cicli 3 e 4.
3. L'unico intervento sul prompt che ha reso (+2) faceva la cosa opposta:
   **toglieva** una licenza che nominava `none` come valore ammesso.
4. ~~Sospetto sul capitolo `NON:` dei manifest.~~ Poggiava sulla legge caduta:
   senza fondamento finche' non misurato per conto suo.

## 6. Misure in coda (lanciate, esito da leggere)

- **c5** — `TIE_BREAK` rimosso, 3 passate: conferma del 40/40 → `ciclo_c5.log`.
- **c6** — `TIE_BREAK` **riscritto in forma affermativa**, stesso contenuto,
  nessuna rotta nominata per escluderla. È la **previsione** che separa la legge
  dalla semplice ridondanza: se vale la legge → 40/40; se il danno era ripetere
  ciò che `ONTOLOGY` già dice → 39/40. → `ciclo_c6.log`.
- **c7** — `TIE_BREAK` rimosso + riparo deterministico delle àncore, per
  verificare che il riparo sia inerte quando non serve. → `ciclo_c7.log`.

Fine coda segnalata da `coda_fatta.txt`.

## 7. Aperto, da decidere

- **`forbid` perde l'operazione vietata.** Col contratto attuale un record non
  `request` ha `none/none`: il frame registra che lì c'è un divieto, non **quale**
  operazione è vietata. Per «non cancellare nulla» a valle non resta
  l'informazione. È il contratto V24.1 congelato: rispettato, ma è una
  debolezza vera.
- **Àncora**: adottare `riparo_ancore.py` oppure rilassare l'invariante ereditato
  a non-decrescente. Nessuna delle due è stata applicata al codice di prodotto.
- Restano dai giri precedenti: tagli d'uscita approvati e in pausa
  (`uscita_ridotta.py`), le sei parole italiane in `render_boundaries`, il
  budget proporzionale alla lunghezza della query.

## 8. Chiusura dei cicli 8-12 (11/8, notte) — errore=0 raggiunto, e cosa non misura

| ciclo | cambio | valide | uscita p50 |
|---|---|---|---:|
| c8 | ripetizione di c5, stessa identica configurazione | **[40, 39, 40]** | 283 tok |
| c9 | tolto lo specchio dell'arco | 39/40 | 272 tok |
| c10 | tolti anche gli specchi di `predicate_id` e dell'ancora | 40/40 | 252 tok |
| c11 | tolto anche lo specchio di `role` | 40/40 | **244 tok** |
| **c12** | c10 + contratto di ruolo **nella grammatica** | **[40, 40, 40]** | 252 tok |

**Gli specchi.** Quattro valori erano chiesti due volte, in `semantic_heads` e in
`predicates`, col validatore a pretendere che coincidessero: informazione zero e
quattro occasioni per record di contraddirsi. Tolti dallo schema e ricomposti in
codice prima del validatore congelato. Uscita da 283 a 252 token (−11%), e due
fallimenti osservati ([31] sull'arco, [18] sull'ancora con ancore perfettamente
crescenti) diventano impossibili.

**Il contratto di ruolo passa dalla grammatica.** Il ciclo 2 lo aveva scritto nel
prompt in forma assoluta e aveva reso +2, ma la query booking lo violava ancora
circa una passata su tre. `role` viene emesso prima di `verb` e `object`, quindi
lo schema dei predicati diventa un'unione discriminata sul ruolo — la stessa
forma che lo schema congelato usa gia' per `carrier` e `sink`: `request` pesca
verbo e oggetto dagli enum **senza** `none`, ogni altro ruolo li ha `none` per
costruzione. `predicate_N_incomplete_request` e `predicate_N_nonrequest_executable`
smettono di essere possibili. Booking, ripetuta tre volte di fila (il caso che a
inizio giornata dava tre esiti diversi), da' tre volte `login/sites`.

Regola generale che ne esce: **quello che il prompt non riesce a garantire si
sposta dove sbagliarlo e' impossibile** — prima nel codice, poi nella grammatica.
E' la stessa lezione dei cicli 3 e 4, applicata bene.

### 8.1 Il limite del traguardo: il banco valida la FORMA, non la rotta

`valide 40/40` significa «frame ben formato», non «analisi giusta». Confrontando
le rotte fra passate identiche di c12:

- **5 query su 40 cambiano rotta** fra una passata e l'altra, restando valide:
  [3] `find|get|read/messages`, [9], [17] `files` contro `dirs`, [31], [37].
- Il caso grave e' **[9]**, «crea un task ricorrente che ogni 30 minuti…»: in una
  passata il corpo del task e' analizzato (`find/entries`, `classify/entries`,
  `write/entries`, `send/messages`), nelle altre due **sei record su sette
  collassano a `none/none`** — il corpo del task sparisce. Entrambe le letture
  passano il validatore.
- **Non e' causato dalla grammatica**: lo stesso collasso compare in c8 e c11.
  Le tre passate di c5 lo avevano semplicemente mancato.

Conseguenza: il collasso e' l'altra faccia della debolezza gia' segnalata al §7
(un record non-`request` non registra quale operazione fosse). Un ruolo sbagliato
non degrada l'analisi: la **cancella**, in silenzio e con esito valido.

**Quindi non serve continuare a ciclare su questa metrica.** Il prossimo passo e'
un criterio sulla rotta: come minimo la stabilita' della rotta fra passate (gia'
calcolata da `ciclo.py`, basta promuoverla a condizione di successo), e piu'
avanti un gold di routing. Fino ad allora «errore=0» va letto come «ben formato
zero errori», niente di piu'.
