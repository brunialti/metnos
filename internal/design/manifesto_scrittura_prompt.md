# Manifesto: come si scrive un prompt in Metnos

> Stato: **proposta di standard**, 10/8/2026. Raccoglie i risultati misurati su
> V23lite (10/8) e quelli già ratificati nei giri precedenti (Tutor 24-26/7,
> routing, fast path). **Il §1 e' stato SMENTITO dalla sua stessa prova decisiva:
> non propagarlo.** I §2-§6 reggono. Il §7 e' un **task prioritario di verifica**:
> osservazioni che toccano `CLAUDE.md` §6 e §2.5 e che nessuno ha ancora testato —
> e con la caduta del §1 vanno considerate senza fondamento finche' non misurate.
> La parte invariante la cambia solo Roberto.

Campo di applicazione: ogni testo letto da un modello — `runtime/prompts/<lang>/*.j2`,
capitolo `[description]` dei manifest, istruzioni di sistema degli analizzatori,
prompt degli stadi di sintesi. **Non** si applica alla documentazione per le
persone: questo file, per esempio, viola le sue stesse regole ed è corretto così.

---

## 1. ~~La legge: afferma, non negare~~ — SMENTITA DALLA PROVA DECISIVA

> **Attenzione: questa sezione è stata falsificata dalla misura che doveva
> confermarla (ciclo c6, 10/8 sera). Non propagare come standard.** Il testo
> resta a documentare l'ipotesi e come è caduta. Il dato solido è al §2.

L'ipotesi era: *ciò che il prompt afferma il modello lo esegue; ciò che nega
nominandolo, lo rende disponibile* — perché in decodifica vincolata su
vocabolario chiuso essere in contesto significa essere più probabile.

**La prova.** `TIE_BREAK_REFINEMENTS` riscritto in forma **affermativa**, stesso
contenuto, nessuna rotta nominata per escluderla. Previsione: 40/40 se contava
la negazione, 39/40 se contava la ridondanza. **Risultato: 37/40** — peggio di
entrambe le ipotesi.

**Cosa dice davvero il dato.** Rimettere quel blocco, *in qualunque forma*,
riporta i due fallimenti di collisione d'àncora che la sua rimozione aveva
risanato ([17] «conta i file **e** le directory», [20] «crea un riepilogo **e**
un foglio»). Non è la negazione: è che **altro testo di assegnazione di rotte
spinge il modello a decomporre più finemente**, e la decomposizione più fine
sbatte contro il vincolo d'àncora che la coordinazione rende insoddisfacibile
(§3). La correlazione rho −0,73 del §4 resta vera come misura e **non spiegata
come causa**: è verosimilmente confusa con questo effetto.

Da rifare prima di riproporre qualcosa del genere: separare «quantità di regole
di rotta» da «forma affermativa o negativa», che in questo esperimento variavano
insieme.

Misura (10/8, ablazione uno-a-uno dei dieci blocchi del prompt di V23lite,
40 query reali):

| costo del blocco (query perse togliendolo) correlato con | rho |
|---|---:|
| quota di frasi che negano **nominando una rotta canonica** | **−0,73** |
| quota di frasi negative in generale | −0,62 |
| **lunghezza in caratteri** | **−0,10** |

Il caso limite è un esperimento naturale: `ONTOLOGY_REFINEMENTS` e
`TIE_BREAK_REFINEMENTS` dicono **le stesse sei cose**. Il primo afferma
(*"Aggregate inspection of directories uses find/dirs"*), il secondo arbitra
nominando il perdente (*"…never get/dirs"*, *"list/persons is not a canonical
intent"*, *"It is not set/images"*). Tolto il primo si perdono 4 query; tolto il
secondo **se ne guadagna 1**.

**Regola.** Ogni regola si scrive come affermazione di cosa una cosa È. Se un
confine va difeso, si definisce **anche l'alternativa in positivo**: non
«è `get/files`, non `set/images`» ma «i metadati descrittivi di una foto sono
`get/files`; cambiare i pixel è `set/images`».

Restano in piedi, misurate ciascuna per conto suo e **non** unificate da questa
ipotesi: gli esempi letterali tolti dal Tutor (25/7: 103→106/134) e il divieto di
liste di sinonimi nel prompt. Erano tre misure che sembravano lo stesso fenomeno;
la prova decisiva dice che l'unificazione era prematura.

## 2. La lunghezza non è la leva

La resa di un blocco non è proporzionale al testo: 445 caratteri
(`GLOSS_REFINEMENTS`) valgono 7 query su 40; 2.253 (`ONTOLOGY_REFINEMENTS`) ne
valgono 4; 2.910 (`TAGGED_ARGUMENT_GRAPH`) ne valgono 0.

**Correzione a un risultato precedente.** L'analisi del 10/8 mattina concludeva
che «la ripetizione non è ridondanza, è rinforzo», dopo che una riduzione del
−41% era costata 3 query. L'ablazione mostra che la spiegazione era sbagliata:
la lunghezza non predice il carico (rho −0,10). Quella riduzione ha fatto danno
perché ha tolto **contenuto affermativo denso**, non perché ha tolto ripetizioni.
Una riduzione si giudica per **cosa** toglie, mai per quanto.

**Non si accorcia un prompt per la latenza.** L'ingresso è quasi gratis (il
server riusa il prefisso comune); un token generato costa ~13,4 ms. La leva
sulla latenza è la **forma dell'uscita**, non la lunghezza dell'ingresso.

## 3. Cosa non va nel prompt: le procedure condizionali

Una regola nella forma «quando A e B collidono, allora C» **perde contro i prior
del modello**, in qualunque punto la si scriva.

Misura: la stessa regola sull'àncora, scritta in due modi opposti — frase di
chiusura in un blocco lontano, e divieto esplicito nel punto in cui il campo
viene definito, con un ripiego calcolabile — è costata **−2** e **−3**. Il
modello ha continuato a fare di testa sua, e aveva ragione: la grammatica non
offriva la risposta che la regola chiedeva.

**Regola.** Se una regola può essere espressa **solo** nominando lo stato
sbagliato o la risposta sbagliata, non appartiene al prompt: appartiene al
**codice deterministico** (§7.9). Il modello etichetta, il codice taglia.

Corollario già in vigore, ora spiegato: la contaminazione di routing si risolve
con una **funzione deterministica + boundary come sorgente di verità**, mai con
liste nel prompt; le varianti di frase le impara la cache semantica L0, non si
cablano nel fast path.

## 4. Il prompt è il contratto del validatore

Dove prompt e validatore si contraddicono, **il modello obbedisce al prompt** e
il validatore boccia il frame. Sembra un errore del modello: non lo è.

Misura: il validatore imponeva che un record non-`request` avesse verbo e
oggetto entrambi `none`; il prompt chiedeva «usa la coppia canonica più vicina
quando è chiara». Sulle richieste con divieti espliciti («non scaricare, non
modificare») la coppia È chiara, il modello obbediva, il frame veniva bocciato.
Allineata la frase al contratto: **+2 query**.

**Regola.** Ogni vincolo che il codice verifica va scritto nel prompt **nella
stessa forma in cui il codice lo verifica**, entrambi i rami. Nessuna licenza
(«quando è chiaro», «se possibile», «altrimenti `none`») accanto a un campo che
il validatore controlla in modo assoluto: la licenza viene usata esattamente
dove è vietata.

## 5. Forma delle regole

- **Una regola, una frase.** (La preferenza per la forma affermativa discendeva
  dal §1 ed e' caduta con lui: non e' una regola, e' da rimisurare.)
- **Solo segnaposto, mai esempi letterali** (misurato: 103→106/134 sul Tutor).
- **Niente liste di sinonimi**: il confine semantico sta nella sorgente di
  verità, non nel prompt.
- **Inglese** per i prompt indipendenti dalla lingua; italiano solo per ciò che
  è rivolto all'utente.
- **Niente attenuazioni**: `se possibile`, `preferibilmente`, `quando è chiaro`
  sono licenze, e la licenza viene usata.
- **Un prompt ha un budget saturo** (Tutor, 26/7): aggiungere una famiglia di
  voci costa più di quanto renda. Una variante nuova deve agire **fuori** dal
  prompt.

## 6. Come si misura un prompt

Nessuna modifica a un prompt si accetta senza misura. Il metodo che ha funzionato:

1. **Campione fisso** di query reali con seme dichiarato (40 query bastano a
   distinguere gli effetti visti qui).
2. **Banda di rumore prima di tutto.** Stesso prompt, 3 passate. Sul modello
   locale la banda e' risultata **0 su 240 chiamate** su quel campione e quei due
   prompt — ma il ciclo c7 (§9) mostra che **a prompt identico la decodifica puo'
   comunque differire**. Quindi: 3 passate per confermare, e una sola passata vale
   per scartare **solo** se lo scarto e' netto.
   *Eccezione:* la **stessa identica richiesta ripetuta di fila** può dare esiti
   diversi (riuso della cache del prompt intero). Vale per i ritentativi, non
   per un campione di query diverse. Si certifica **a macchina scarica**, e non
   si emette un verdetto sotto i 3 casi di differenza.
3. **Ablazione uno-a-uno** per sapere cosa regge cosa. Leggere il testo non
   basta: qui ha prodotto tre attribuzioni sbagliate di fila, tutte smentite
   dalla misura.
4. **Distinguere il fallimento tecnico da quello semantico.** Quattro
   «errori di comprensione» erano troncamenti dell'uscita (`finish=length`):
   si controlla sempre il motivo d'arresto prima di dare la colpa al prompt.
   Il tetto d'uscita non è un costo: alzarlo è gratis per chi non lo usa.
5. **Un cambio per volta**, e si tiene lo stato migliore misurato: due dei sei
   interventi di oggi hanno peggiorato, e senza il ritorno allo stato buono si
   sarebbe accumulato il danno.

## 7. TASK PRIORITARIO — osservazioni da verificare, non decisioni

I quattro punti qui sotto **non sono proposte di modifica**: sono osservazioni
che la legge del §1 suggerisce e che **vanno misurate prima di qualunque
decisione**. Nessuno di essi è stato testato: la legge è misurata su V23lite, non
sui prompt prescrittivi né sui manifest. Estenderla per analogia sarebbe
esattamente l'errore che questo lavoro ha smontato tre volte — leggere il testo e
attribuire, invece di misurare.

Ordine di lavoro proposto: prima il punto 4 (deterministico, senza decisioni),
poi 2 (ha già l'attrezzo di misura), poi 1 e 3.

La parte invariante di `CLAUDE.md` la cambia solo Roberto, e solo dopo la misura.

1. **§6 impone `NON DEVI:` in ogni regola prescrittiva.** È esattamente la forma
   che oggi risulta dannosa quando nomina il valore o la rotta da evitare.
   *Da misurare:* mantenere `DEVI` ed `ERRORE`, e ammettere `NON DEVI` **solo se non
   nomina un'alternativa canonica**; altrimenti riscrivere il confine come due
   affermazioni. Da misurare sui prompt prescrittivi veri prima di adottarla.
2. **§2.5 impone il capitolo `NON:` nei manifest**, con «anti-pattern +
   disambiguazione rispetto a tool simili» — cioè nominare i fratelli da non
   scegliere. Per questa legge è la parte rischiosa del manifest.
   *Da misurare:* con `scripts/bench_prefilter_corpus.py` su un
   sottoinsieme di manifest riscritti in forma affermativa, prima di toccare lo
   standard. Nessuna riscrittura di massa (§2.5 lo vieta già).
3. **§6.1 tipizza i prompt** in `prescriptive` / `definitional` / `few_shot`.
   Il dato di oggi dice che lo stile **definizionale è il più efficace** anche
   dove oggi imponiamo il prescrittivo. *Da misurare:* l'estensione dello
   stile definizionale ai prompt di analisi, con misura.
4. **`prompts_lint.py`** applica i controlli §6 solo allo stile prescrittivo.
   *Da misurare:* un controllo nuovo e trasversale — **segnalare ogni
   frase che nega nominando un termine del vocabolario chiuso** (`vocab.py`).
   È deterministico, si scrive in poche righe ed è la ricaduta più immediata di
   questo lavoro.

## 8. Le misure a cui questo manifesto si appoggia

- `internal/design/analysis_prompt_legge_negazione_10_8_2026.md` — ablazione,
  correlazioni, banda di rumore, i sei cicli.
- `internal/design/analysis_v23lite_prompt_10_8_2026.md` — composizione del
  prompt, costo per token, riduzione fallita (con la correzione del §2 qui).
- Tutor 24-26/7 (`CLAUDE.mutabile.md` §11, RM-0003) — prompt a soli segnaposto,
  budget saturo, certificazione a macchina scarica.
- Memorie: prompt a soli segnaposto; contaminazione = funzione, non prompt;
  niente liste di sinonimi; niente varianti cablate nel fast path.

## 9. Esito delle tre misure finali (10/8, sera)

| ciclo | prompt | valide | lettura |
|---|---|---|---|
| **c5** | contratto + `TIE_BREAK` **rimosso** | **40/40, 40/40, 40/40** | obiettivo raggiunto, 0 instabili, 0 troncamenti |
| **c6** | contratto + `TIE_BREAK` **affermativo** | 37/40 | **falsifica il §1**: peggio sia del rimosso sia dell'originale |
| **c7** | c5 + riparo deterministico àncore | 39/40 | il riparo **non** e' inerte come atteso: vedi sotto |

**c7 e' un risultato scomodo e va guardato.** Il riparo agisce solo **dopo** la
decodifica: non tocca il prompt, quindi il modello avrebbe dovuto produrre
esattamente l'uscita di c5. Invece una query ([31], sessione web) e' caduta con
`predicate_2_source_edge`, e le sue ancore [5, 15] non hanno collisioni — cioe'
il riparo non e' nemmeno scattato. Conclusione onesta: **a prompt identico la
decodifica puo' differire**, come gia' visto sulla query booking ripetuta di
fila. La banda misurata a 0 su 240 chiamate vale per quel campione e quei due
prompt, **non e' una proprieta' generale del motore**. Ogni verdetto su una
singola passata va riletto con questo limite.

## 10. La contesa di GPU invalida la misura (11/8, notte)

Rifatte a distanza di due ore, **le stesse misure si sono scambiate**: c5 da
40/40/40 a **38/38/39**, c7 da 39 a **40**. Nel frattempo la latenza mediana era
passata da 4.025 a 6.544 ms (+63%) perche' **una seconda sessione stava girando
i propri cicli sullo stesso llama-server**.

Sotto contesa la decodifica smette di essere ripetibile e la banda si apre a ~2
casi su 40 -- lo stesso valore gia' misurato ai tempi del Tutor, e la ragione
della regola "si certifica a macchina scarica".

**Conseguenze, da rispettare sempre:**

1. Un numero misurato mentre un altro processo usa la GPU **non e' un numero**.
   Prima di ogni corsa: `pgrep -f "python3 ciclo"` deve essere vuoto.
2. La latenza mediana della corsa e' la **spia di contaminazione**: se si scosta
   dalla corsa precedente, il confronto e' nullo, quali che siano i punteggi.
3. Il §9 va riletto con questo limite: la terna 40/40/40 di c5 fu misurata a
   macchina scarica, il 39 di c7 sotto carico. **Nessuno dei due e' un verdetto**,
   e l'anomalia di c7 -- una modifica post-decodifica che sembrava cambiare
   l'uscita del modello -- si spiega interamente cosi', senza difetti nel codice.
4. Due sessioni che lavorano nello stesso scratchpad devono usare **marcatori di
   fine distinti**: qui hanno condiviso `coda2_fatta.txt` e si sono sbloccate a
   vicenda.

## 11. Decisione di Roberto sullo standard unificato (12/8/2026)

Roberto ha chiarito l'obiettivo: **non accorciare un prompt per accorciarlo**, ma
costruire uno standard unificato a cui riferirsi nella creazione dei prompt.
Una riscrittura conforme allo standard non è corretta per definizione: deve
essere provata contro il prompt di controllo.

Questa decisione supera il divieto generale di nuove modifiche di prosa soltanto
nel seguente perimetro:

1. inventario dei fatti semantici prima e dopo la riscrittura;
2. coerenza campo per campo con schema e validatore;
3. una sola trasformazione per braccio;
4. revisione avversariale del contenuto prima della misura;
5. controllo e candidato affiancati sulle stesse 120 richieste;
6. validità, correttezza di rotta e sicurezza in colonne separate;
7. nessun verdetto sotto tre casi e nessuna compensazione delle regressioni di
   sicurezza.

Il §1 di questo manifesto resta falsificato e non torna in vigore. I §2-§6 sono
materiale probatorio per costruire lo standard, non una licenza a riscrivere in
massa. Le modifiche allo standard dei prompt restano separate da quelle di
schema e rappresentazione, così ogni risultato resta attribuibile.
