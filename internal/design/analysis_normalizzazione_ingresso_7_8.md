# Una normalizzazione della richiesta all'ingresso — misura e verdetto

> 7-8/8/2026. Punto 0 della consegna `handover_7_8_2026.md`, idea di Roberto.
> Questo documento riporta cinque misure e il verdetto che ne segue. La tesi di
> partenza non regge nella forma in cui era scritta; regge il problema che la
> motivava, e si e' chiuso per la parte che le misure hanno dimostrato.

## 0. In due righe

Chiedere al modello di RISCRIVERE la richiesta non funziona (misurato: tiene i
verbi, riordina, a volte non fa nulla). Chiedergli di ETICHETTARE ogni parola
funziona benissimo (60/60), ma **non batte cio' che c'e' gia'** dove serviva:
perde le faccette e inquina il fine con le altre clausole. Il rumore vero era
un altro, e si toglie con due famiglie di parole nel lessico: misurato,
13,9% → 9,7%. Per strada e' uscito un difetto che valeva piu' di tutto il
resto: **il fine scritto come lo chiede il manifest veniva rifiutato**.

## 1. Che cosa chiedeva il punto 0

Una sola passata all'ingresso che chiede al modello di (a) togliere articoli e
preposizioni, (b) portare i verbi all'infinito, (c) sostituire i verbi ambigui
con una forma canonica. Da li' in poi «tutti i consumatori lavorano su una
richiesta gia' pulita, e i filtri per-dominio smettono di esistere invece di
essere accorpati».

## 2. Le cinque misure

Banco: 13 richieste di controllo + 60 richieste reali estratte dal corpus
(`data/prefilter_corpus_snapshot.jsonl`, seme fisso). Tier fast, temperatura 0.

| # | forma chiesta al modello | esito |
|---|---|---|
| 1 | riscrittura, prompt in prosa | tiene il verbo con cui si chiede in 9 casi su 14 |
| 2 | riscrittura, prompt prescrittivo §6 | il caso di punta torna **identico**; un riordino; articoli e preposizioni restano in 4 su 10 |
| 3 | etichettatura, parole numerate, una riga per parola | **11/11 complete**; riconosce «accedi», che il lessico non conosce |
| 4 | etichettatura compatta, una lettera per parola | **1/13**: non sa contare le parole, e copia l'esempio |
| 5 | forma canonica **contro il riduttore di oggi**, 8 richieste composte | perde le faccette («prossime», «last»), inquina con le altre clausole («mail») |

Due fatti collaterali, utili a chi verra' dopo:

- **il tier non e' una leva**: `fast` e `middle` hanno prodotto uscite identiche
  parola per parola su tutti i casi. Oggi condividono modello e policy; scegliere
  «il tier piu' alto» per un compito linguistico non compra niente.
- **la forma compatta e' un ricordo dell'esempio**: alla richiesta di una lettera
  per parola il modello ha emesso `CGRLCGRLCGRL...`, cioe' l'esempio ripetuto.
  Conferma la regola nota: esempi a soli segnaposto, e mai una forma copiabile.

## 3. Perche' la misura 5 e' quella che decide

Il riduttore che gia' esiste (`_reduce_site_goal`, workload `sites.goal_reduce`)
fa **due** cose: pulisce e SCEGLIE il contenitore, con validazione estrattiva
(≤6 token, tutti presenti nella richiesta). La forma canonica fa solo la prima.
Su «apri booking.com, mostrami le mie prenotazioni e mandami una mail con
l'elenco» il riduttore consegna `mie prenotazioni`; la canonica consegna anche
`mail`, che su una pagina di prenotazioni e' un controllo vero da premere.

E su «Vai sul sito booking.com fai login ... dimmi le prossime prenotazioni» la
canonica **perde `prossime`**, perche' il modello la etichetta come grammatica:
esattamente la faccetta per cui il 7/8 e' stato speso mezzo pomeriggio.

Quindi: la passata unica non sostituisce il riduttore. Al massimo lo alimenta —
e alimentarlo costa una chiamata in piu' per guadagnare, sui casi misurati,
niente che il lessico non desse gia'.

## 4. Che cosa il rumore era davvero

Misura del RESIDUO (parola che il filtro deterministico TIENE e il modello dice
grammatica o verbo) e della PERDITA (l'inverso), su 60 richieste reali, 548
parole giudicate. Strumento riusabile: `internal/tools/misura_residuo_fine.py`.

| stato | residuo | perdita |
|---|---|---|
| prima | 13,9% | 3,5% |
| + famiglia degli ausiliari, + numeri di una cifra | 11,1% | 1,8% |
| + famiglia degli interrogativi | **9,7%** | 4,4% (vedi nota) |

*Nota sulla perdita che risale*: il giudice etichetta gli interrogativi come
`Q` (quantita') o `P` (possesso), quindi toglierli gli risulta una perdita. Le
due sole voci `C` (contenuto) del referto sono `cosa` in «di cosa dicono» e
`Che` in «Che ora e'»: interrogativi, non contenuto. Il giudice e' un modello,
non un oracolo; le percentuali si leggono come tendenza fra un prima e un dopo,
mai come voto assoluto — ed e' scritto nello strumento.

Che cosa resta nel 9,7%: 23 verbi (di cui **9 gia' noti** al vocabolario
canonico multilingue `vocab.ACTION_MAPPING`, gli altri 14 nemmeno canonici:
«proponi», «ricevuto») e 30 giudizi di grammatica, in buona parte discutibili
(«piu'», «frequenti», «dopo»). Il prossimo passo, se si vuole, vale **1,6
punti**: far consultare al filtro del fine il SoT dei verbi del progetto invece
delle sole 5 primitive del dominio. Va misurato prima, perche' la stessa
funzione taglia anche i nomi dei controlli di pagina.

## 5. Il difetto trovato per strada, che valeva piu' del resto

Turno reale `0cde68a46a7a426d`: piano corretto in ogni passo, ultimo passo morto
con `unsupported_action`. L'azione era `"le mie prenotazioni"`.

Il manifest di `act_sites` chiede al pianificatore **esattamente** quella forma:

> «DEVI nominare la COSA da raggiungere, non solo il verbo: 'le mie
> prenotazioni', non 'vai'» — e l'esempio canonico e' `action="fatture 2026"`.

Il resolver pretendeva un verbo per scegliere la primitiva, quindi rifiutava
l'esempio del proprio manifest. Verificato differenzialmente su `git worktree`:
il difetto e' su HEAD, non l'ho introdotto io stanotte. E' lo stesso filo del
punto 0 — **il verbo con cui si chiede non puo' essere insieme rumore e
requisito**: il dominio lo toglie ovunque (`_goal_noise`) e poi lo esigeva qui.

Proprieta' introdotta: **un fine e' un NOME**. Un testo senza verbo che nomina
qualcosa e' un fine (primitiva `search`); il vocabolario resta chiuso
dall'altro lato, perche' cio' che non nomina niente — un'espressione, un
selettore, un frammento di codice — resta rifiutato. Il riscontro di sicurezza
che pretendeva il rifiuto di `esegui javascript alert(1)` continua a valere,
verbatim.

## 6. Verdetto e cosa farne

1. **La passata di riscrittura all'ingresso non si fa.** Non regge la misura, e
   il posto dove doveva rendere e' gia' occupato da qualcosa che fa di piu'.
2. **La forma etichettata resta in cassetta**, con il suo banco. Guadagna il suo
   posto quando: arriva una terza lingua (il lessico va riseminato, il modello
   no), oppure una misura mostra che la coda dei verbi non canonici pesa. In
   quel caso si compone: **il lessico decide sulle parole che conosce, il
   modello sulle altre** — mai il contrario, perche' e' proprio sulle parole che
   distinguono («prossime») che il modello sbaglia.
3. **L'accorpamento chiesto come ripiego e' fatto per la parte che rendeva**: le
   due famiglie mancanti sono nel lessico, generali (`text.*`, non `sites.*`) e
   consultate da un punto solo; le forme ambigue con un sostantivo restano
   fuori per scelta misurata («stato», «done»).

Resta aperto — e non e' questo il documento che lo chiude — il fine come
STRUTTURA (`analysis_goal_come_struttura_7_8.md`): li' il verbo, l'ambito, la
portata e la faccetta smettono di essere parole da filtrare e diventano campi.
E' la strada per cui i filtri «smettono di esistere» davvero, ed e' l'altra
meta' dell'idea del punto 0.
