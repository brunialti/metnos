# Il fine come STRUTTURA — analisi prima di codificare (7/8/2026)

> Chiesto da Roberto: «prendi tempo per analizzare prima di codificare».
> Questo documento non propone codice da scrivere subito: dice che cosa ho
> trovato guardando il codice vero, quale sia il difetto di fondo, e quali
> decisioni restano a lui. La consegna della notte 6→7/8 lo elencava come
> «il prossimo pezzo grosso», con il disegno mai consegnato.

## 0. In breve

L'argomento `action` di `act_sites` è una stringa che ha bisogno di **sei
regole prescrittive** nel manifest per essere scritta bene. Quelle sei regole
non sono documentazione: sono **campi mancanti descritti in prosa**. Il
sistema comprime una struttura in testo, la perde per strada, e poi la
ricostruisce con cinque euristiche lessicali — due delle quali esistono solo
per rimediare alla perdita.

Il rimedio non è un nuovo oggetto tipizzato da progettare in astratto: è
**finire un lavoro già cominciato**. `done_when`, aggiunto il 7/8 mattina, è
il primo campo estratto da quella stringa, e ha funzionato. Gli altri si
estraggono allo stesso modo, uno per volta, ciascuno con la sua misura.

## 1. Il sintomo

Dal manifest firmato di `act_sites`, argomento `action`, sei regole:

1. DEVI nominare la COSA da raggiungere, non solo il verbo
2. DEVI dire «mie/miei» quando la cosa sta nell'area personale
3. DEVI aggiungere il tratto che distingue: «passate», «future», un anno
4. NON DEVI nominare il sito
5. NON DEVI mettere due fini in una chiamata
6. (`done_when`) DEVI descrivere il contenuto atteso, non l'aspetto

Quando un argomento di tipo stringa richiede sei regole per essere prodotto,
il tipo sta mentendo sul contenuto. E nessuna delle sei è verificabile: se il
modello locale medio ne sbaglia una, non se ne accorge nessuno finché il
pilota non sceglie male. Un fallimento silenzioso a monte diventa un misroute
a valle — che è esattamente il difetto che ha aperto la sessione di ieri.

## 2. La prova, sul codice

### 2.1 Le euristiche che ri-estraggono a mano

In `action_resolver.py` ci sono ~15 funzioni che interrogano il TESTO del
fine. Quattro ricostruiscono informazioni che il planner aveva già:

| Funzione | Che cosa ricava dal testo | Campo che sta ricostruendo |
|---|---|---|
| `_is_personal_goal` | cerca «mie/miei» con regex | ambito: personale / pubblico |
| `goal_is_exhaustive` | cerca i quantificatori («tutte») | portata: campione / esaustivo |
| `goal_tokens(navigation=True)` | **scarta le cifre**, perché «date e importi sono filtri differiti, non nomi di menu» | separa la COSA dal FILTRO |
| `goal_discriminates_on_site` | verifica che il fine non coincida col nome del sito | conseguenza della regola 4 |

La terza è la più eloquente: la funzione butta via le cifre perché dentro la
stessa stringa convivono due cose che servono a due meccanismi diversi — dove
andare, e come filtrare quando si è arrivati. La separazione avviene a
posteriori, per tipo di carattere.

### 2.2 Il giro con perdita

Il fine attraversa oggi cinque stazioni:

1. l'utente dice «accedi a booking.com e mostrami le mie prenotazioni»;
2. il **planner** (modello) produce `action` seguendo le sei regole in prosa;
3. il **riduttore** `_reduce_site_goal` (un secondo modello locale) accorcia
   al «contenitore da raggiungere» — e nel farlo perde possesso ed esaustività;
4. `preserve_goal_qualifiers` **ri-inietta** i marcatori persi, ricopiandoli
   dalla query originale (il suo commento lo dice: «restore navigation
   semantics that a goal reducer may discard»);
5. il **pilota** ri-estrae il resto con le regex del §2.1.

Struttura → testo → testo ridotto → testo ri-arricchito → struttura. Due
passaggi di modello e cinque euristiche per trasportare informazioni che
esistevano già, intere, al passo 2.

Che il giro non regga è già scritto nel codice, in `session_broker.py`:

> «Keep its complete natural target extractively: a second model pass can
> otherwise erase a status/year facet such as *passate*.»

Cioè il riduttore è stato **disattivato** per il caso più comune, perché
cancellava una faccetta. Non è una svista: è l'ammissione che il testo non
regge le faccette.

### 2.3 Il difetto #4 della consegna di ieri

«Un fine che coincide col nome del sito non discrimina»: «le mie
prenotazioni» si riduce al token `booking`, che su booking.com sta ovunque.
Il rimedio è stato una regola in prosa (la 4) più un canale strutturale di
ripiego. Con un campo che dice *quale cosa* e un campo che dice *in quale
ambito*, il problema non nasce: il nome del sito non entra in un campo che
non è fatto per contenerlo.

## 3. Che cosa NON è il problema

Da escludere prima di progettare, per non risolvere la cosa sbagliata:

- **Non è la qualità del riduttore.** Migliorare il prompt del riduttore
  sposta l'errore, non lo toglie: qualunque riduzione a testo perde le
  faccette, e infatti il rimedio adottato è stato spegnerlo.
- **Non è il vocabolario del lessico.** Gli alias funzionano; il difetto è
  che una sola stringa deve servire quattro meccanismi che vogliono cose
  diverse, e quelli si contendono i token.
- **Non è il budget di passi.** Quello era il difetto separato chiuso oggi
  (§6): passi che si contano sul progresso e identità stabile dei posti. Con
  quattro passi *veri* il pilota arriva; ma arriva **guidato dalle parole**,
  e le parole sono ciò che questo documento mette in discussione.

## 4. I campi che la stringa nasconde

Dalle euristiche del §2.1, letti dal codice e non inventati:

| Campo | A quale meccanismo parla | Oggi si ricava da |
|---|---|---|
| **cosa** | classifica dei controlli di navigazione | i token non-cifra, tolto il rumore |
| **ambito** (personale / pubblico) | quale canale: menu dell'account o ricerca in pagina | regex su «mie/miei» |
| **filtro** (passate, future, 2026) | verifica dell'arrivo e lettura, **non** la scelta del menu | le cifre scartate + gli alias di stato |
| **portata** (campione / tutto) | quante continuazioni e quanti scorrimenti | regex sui quantificatori |
| **riconoscimento** | condizione d'arrivo e taglio del testo letto | ✅ **già estratto**: `done_when` |

Cinque campi, quattro ancora dentro la stringa.

## 5. Il disegno proposto: incrementale, non un oggetto unico

**Raccomandazione: non introdurre un oggetto `goal` annidato in un colpo
solo.** Estrarre un campo per volta, piatto, come è stato fatto con
`done_when`. Motivi:

1. **Il precedente ha funzionato ed è misurato.** `done_when` è nato piatto,
   facoltativo, con ripiego sul comportamento precedente («senza, si
   riconosce dal fine stesso»). Nessuna regressione, e il flusso Booking è
   rimasto verde. Lo stesso stampo vale per gli altri quattro.
2. **Ogni campo ha una sua misura.** `ambito` si misura sui siti con area
   personale; `portata` sulle collezioni impaginate; `filtro` sui fini con
   una faccetta di stato. Un oggetto unico li rende indistinguibili: se il
   turno peggiora non si sa quale campo l'ha rotto.
3. **Un campo per volta è ritirabile.** Un oggetto unico è un cambio di
   contratto che non si annulla a metà.
4. **Il ripiego resta possibile.** Finché un campo non è dichiarato, vale
   l'euristica di oggi. Il codice del §2.1 non si cancella: **diventa il
   ripiego** di un campo assente, che è ciò che è già oggi per `done_when`.

Ordine consigliato, dal più redditizio:

1. **`ambito`** — è il campo che governa il canale strutturale, cioè la mossa
   che ieri notte ha richiesto tre correzioni distinte (difetti #4, #5, #6
   della consegna). È anche l'unico dove l'euristica è un singolo marcatore
   di possesso, quindi fragile per costruzione in una lingua che il possesso
   lo esprime in molti modi.
2. **`filtro`** — toglie le cifre e le faccette di stato dalla classifica dei
   menu senza doverle scartare per tipo di carattere, e le porta dove
   servono: la verifica dell'arrivo.
3. **`portata`** — governa continuazioni e scorrimenti; oggi un quantificatore
   in una lingua diversa dalle due seminate non arriva.
4. **`cosa`** — per ultimo: è ciò che resta della stringa quando gli altri
   tre sono usciti, quindi si ottiene quasi gratis ed è il meno rischioso.

## 6. Fatti verificati che rendono la strada percorribile

Controllati oggi sul codice, non assunti:

- **Lo schema regge le strutture annidate.** `get_inputs` dichiara properties
  annidate a tre livelli (`dialog.items.properties.schema.properties.kind`),
  `get_approval` a due. Non serve lavoro infrastrutturale, nemmeno se un
  giorno si volesse l'oggetto unico.
- **La grammatica sa vincolarle.** `tool_grammar.py` genera regole GBNF
  ricorsive per oggetti con properties tipizzate (marcate «B2 RECURSIVE»), e
  limita le enum ai valori dichiarati. È l'argomento decisivo rispetto a §7.9:
  un campo con enum è **vincolato alla produzione**; una regola in prosa può
  solo essere sperata.
- **Le cache non sono un ostacolo.** Ogni piano cachato porta `tools_sig` +
  `pool_sig` verificate a lettura (ADR 0182): un re-sign di `act_sites`
  invalida per costruzione, senza migrazioni.

## 7. Decisioni — PRESE da Roberto l'8/8/2026

> Tutte e quattro come raccomandato. In sintesi: **campi piatti uno per
> volta**; **`ambito` enum chiusa `personale`|`pubblico`**; i **nomi di
> argomento sono liberi** (§2.2 governa i nomi di executor, non gli
> argomenti); **solo `act_sites`**, non `login_sites`. La ricetta operativa
> del primo campo sta in `handover_7_8_2026.md`.

Le domande, per memoria di come si era arrivati a porle:

1. **Si procede a campi piatti incrementali** (raccomandato) **o si vuole
   l'oggetto `goal` unico** in un colpo solo? Il secondo è più pulito da
   guardare e più rischioso da consegnare.
2. **`ambito` come enum chiusa** (`personale` | `pubblico`) o come booleana
   (`personale = true/false`)? L'enum è più esplicita per il modello e più
   estensibile (esiste un terzo ambito? area di un'organizzazione?), la
   booleana è più corta da produrre.
3. **Il vocabolario §2.2**: questi sono nomi di *argomenti*, non di executor,
   quindi non toccano il vocabolario chiuso di azioni e oggetti. Confermare
   che l'interpretazione è giusta prima di scrivere i manifest.
4. **Fino a dove arriva il fine strutturato**: `act_sites` soltanto, o anche
   `login_sites` (che ha un fine implicito: «essere autenticati»)?

## 8. Come si misura che ha funzionato

Non «il turno Booking è ancora verde» — quello è il minimo, e oggi lo è già.
Le misure vere, per campo:

- **`ambito`**: un turno su un sito con area personale il cui fine **non
  contiene** marcatori di possesso in nessuna lingua seminata. Oggi
  l'euristica non può che fallire; col campo deve arrivare.
- **`filtro`**: un fine con faccetta di stato («le mie prenotazioni
  cancellate») deve scegliere il menu giusto e **non** filtrare il menu con
  la parola della faccetta.
- **`portata`**: una collezione impaginata con quantificatore esplicito deve
  esaurire le continuazioni; senza, deve fermarsi al campione.
- **Trasversale**: il numero di euristiche del §2.1 che restano *sulla strada
  principale* invece che come ripiego. È il conto che dice se il giro con
  perdita è stato tolto o solo spostato.

## 9. Nota operativa emersa oggi

Il contratto del sidecar è **derivato dal contenuto** dei moduli di confine e
congelato all'import di ciascun processo. Conseguenza verificata sul campo:
modificare un file sotto `runtime/playwright_sidecar/` **mentre un turno
gira** — anche solo l'allineamento di un commento — spezza l'accoppiamento a
metà turno. Il passo già avviato prosegue (il ciclo del fine sta dentro una
sola chiamata al broker), il passo successivo muore in 60 ms con
`sidecar_contract_mismatch`.

La trappola nota diceva «dopo ogni modifica, riavviare il sidecar». Va estesa:
**e non modificare quei file mentre un turno è in volo.**

---

**Riferimenti**: consegna 7/8 in `~/.claude/.../project_session_7_8_2026_sites_goal.md` ·
manifest `executors/act_sites/manifest.toml` · euristiche
`runtime/playwright_sidecar/action_resolver.py` · riduttore
`session_broker.py::_reduce_site_goal` · grammatica `runtime/tool_grammar.py` ·
quota di sessioni (lavoro separato) `internal/design/sites_todo_quota.md`.
