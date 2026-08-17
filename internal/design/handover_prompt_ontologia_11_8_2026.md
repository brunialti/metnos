# Consegna — analisi della richiesta V23lite, misura su campione esteso (11/8/2026)

> Documento **autosufficiente**: scritto perché un agente che non ha visto la sessione
> possa riprendere il lavoro senza rileggere niente altro.
> Lavoro **ombra**: nessuna scrittura sul runtime, **niente committato**.

---

## 0. L'OBIETTIVO, che non è il punteggio di un banco

> Migliorare la fase del **proposer** lavorando sull'**estrazione di intento** (o
> prima), **per eliminare codice che si porta dietro dizionari di parole e che è
> per costruzione non conforme all'i18n**, e farlo **senza degradazione di
> qualità**.

Tutto il resto di questo documento è derivato. Se chi legge deve scegliere fra
alzare un numero e cancellare una lista di parole legata alla lingua, sceglie la
seconda. Il numero serve solo a dimostrare che cancellandola non si perde niente.

**Stato rispetto all'obiettivo: NON raggiunto**, e mancano due cose precise —
vedi §12. La strada è dimostrata praticabile, la sostituzione no.

## 1. Che cosa si sta facendo

`V23lite` è un analizzatore di richieste: un LLM locale legge la frase dell'utente e
compila una struttura JSON vincolata da schema (temperatura 0, seme 42, thinking
spento). Un validatore deterministico dice valida/non valida. Il lavoro consiste nel
cambiare il **prompt** e contare le frasi valide.

È lavoro **ombra**: non è cablato nel runtime, non tocca gold, gate o cache.

## 2. Stato in una riga

Il salto di ieri (36→40 su 40) **non esisteva**: misurato su 120 frasi mai usate, tutto
ciò che era una modifica di *scrittura* perde o è neutro. Le due sole cose che
guadagnano sono un tetto d'uscita alzato e **sei righe di codice deterministico**.

**Base migliore dimostrata prima del taglio: 116/120**, cioè prompt di riferimento
con `riparo_ruolo.py`. I 117/120 dei tagli corretti sono il massimo numerico
osservato, ma il +1 è dentro il rumore e non costituisce una nuova base promossa.

**Aggiornamento del 12/8, che prevale sul vecchio verdetto del §14:** la replica
del taglio dei campi è stata rifatta sulla base corretta `A + riparo_ruolo.py`.
I controlli fanno 116/120 e `c10`/`c11` 117/120; validità e qualità di rotta del
taglio restano **INDECISE**. V23lite non è promuovibile perché non può
rappresentare `undo`, ma questo difetto respinge la direzione di sostituzione,
non discrimina `c10`/`c11` dalla loro base. Dettaglio corrente nel §15.

## 3. Dove sono i file e come si esegue

Tutto sta in `internal/tools/request_analysis_lab/misure_11_8/` (non committato).

| file | cosa fa |
|---|---|
| `../unified_query_bench_v23_checkpoint.py` | **banco congelato. NON modificarlo mai.** Ogni toppa sostituisce un attributo del modulo a runtime |
| `prova_cieca.py` | modulo comune: costruisce il campione, carica il banco, applica le toppe, esegue. Gli altri script lo importano |
| `patch_corrente.py` | toppe storiche selezionabili con `SET_PATCH` |
| `riparo_ruolo.py` | **il vincitore**: contratto di ruolo spostato dal prompt al codice |
| `riparo_ancore.py` | riparo deterministico delle collisioni di àncora (scritto, mai misurato in questa serie) |
| `campo_arco.py` | corregge il campo dell'arco fantasma (§7) |
| `ontologia_incisiva.py` | riscrittura E, prosa incisiva |
| `ontologia_sei.py` | riscrittura H, forma §6 |
| `perimetro.py` | quante frasi del campione appartengono al Tutor e non al motore |
| `prova_*.py` | un braccio ciascuno; ognuno esegue due configurazioni **di seguito** sulle stesse frasi |
| `prova_*.json`, `*.log` | risultati grezzi |

Esecuzione, dalla directory `misure_11_8`:

```bash
python3 prova_ruolo.py > prova_ruolo.log 2>&1      # ~24 min, due configurazioni x 120
```

Serve `llama-server` vivo su `127.0.0.1:8080`. Verifica: `curl -s -m 5 http://127.0.0.1:8080/health`.

**Il campione**: 120 frasi estratte dai turni reali (`~/.local/share/metnos/turns/*.jsonl`),
seme `HELDOUT_SEED = 20260811`, escludendo le 40 del seme `20260810` usate per la messa a
punto. Include di proposito **tutte** le 19 frasi su `persons` e **tutte** le 4 sullo
spostamento di posta, che nelle 40 erano **zero**. Budget d'uscita 4000 token.

## 4. Il contratto che il validatore impone

Da leggere prima di toccare qualunque cosa. Righe del banco congelato.

- **Ruolo** (1303-1305): se `role == "request"`, verbo e oggetto **devono** essere
  entrambi diversi da `none`; per **ogni altro ruolo** devono essere entrambi `none`.
  Fallimenti: `predicate_N_incomplete_request` e `predicate_N_nonrequest_executable`.
- **Ruoli ammessi** (581-586): `request`, `forbid`, `condition`, `description`, `quote`.
  Riga 580: «Return one record for every predicate that could otherwise be mistaken for
  an operation» — quindi una relativa **produce** un record, con ruolo `description`.
- **Arco dei dati** (1284-1288): `source_predicate_id` intero con `0 <= id < indice`, e
  `input_from_predicate_id` **uguale** a quello. È un **numero di record**.
- **Àncore**: una per record, crescenti, uniche, dentro l'intervallo dei token.

## 5. Tutte le misure, stesse 120 frasi

| braccio | che cos'è | valide /120 |
|---|---|---|
| **A** | prompt attuale, solo tetto d'uscita alzato a 4000 | **112 / 113** |
| B | A + contratto di ruolo, ramo «non-richiesta» per primo | 110 |
| C | B + `TIE_BREAK_REFINEMENTS` rimosso | 112 |
| D | contratto riscritto, ramo «richiesta» per primo, in positivo | 108 |
| G | `TIE_BREAK` rimosso **senza** contratto | **107** |
| E | C + `ONTOLOGY` riscritta in prosa incisiva | 108 |
| H | C + `ONTOLOGY` riscritta in forma §6 | 109 |
| **I** | **A + `riparo_ruolo.py`** | **116** |

A è stato misurato tre volte: 112, 113, 113. **La banda di rumore è ±1.** Differenze di
1 non significano nulla; da 3 in su significano.

### 5.1 Il quadro 2×2, che è il risultato meno intuitivo

|  | `TIE_BREAK` presente | `TIE_BREAK` rimosso |
|---|---|---|
| **senza contratto** | **113** | **107** |
| **con contratto** | 110 | 112 |

Gli effetti **non si sommano, interagiscono**. Rimuovere `TIE_BREAK` da solo costa **6
frasi**; rimuoverlo dopo aver aggiunto il contratto ne rende 2. Ieri, sulle 40 frasi di
messa a punto, la rimozione sembrava valere +1: era adattamento.

**Conseguenza operativa: `TIE_BREAK_REFINEMENTS` resta dov'è.** L'ablazione che lo
dichiarava «dannoso» era misurata su un campione che non conteneva **nessuna** delle
famiglie coperte dalle sue regole più lunghe.

### 5.2 La stessa regola scritta in quattro modi

| il contratto di ruolo, scritto come… | valide |
|---|---|
| frase, ramo «niente» per primo (B) | 110 |
| frase, ramo «richiesta» per primo (D) | 108 |
| **assente dal prompt** (A) | 112-113 |
| **codice deterministico** (I) | **116** |

Il meccanismo è leggibile nei motivi di caduta: la frase azzera i 3
`nonrequest_executable` **e in cambio** porta `incomplete_request` da 1-2 a 5. Il modello
ha un budget di «niente» che, spinto sui record non-richiesta, sborda su quelli di
richiesta. **Riordinare i rami non li separa.** Il riparo in codice azzera i 3 senza
poter sbordare, perché non parla mai al modello: `nonrequest_exe 3→0`, `incomplete 2→2`,
**0 frasi rotte**.

### 5.3 Le riscritture di `ONTOLOGY`

E (prosa incisiva, 2.392 char) e H (forma §6, 6.015 char) fanno 108 e 109 sulla stessa
base. **Differenza dentro la banda di rumore: sono indistinguibili**, a 2,7× la
lunghezza per la forma §6. Ed entrambe stanno **sotto** il blocco originale sulla stessa
base (112). Cioè: riscrivere quel blocco, in qualunque forma, **costa**.

## 6. Che cosa ha funzionato e che cosa no

**Ha funzionato (2 cose, entrambe dimostrabili dal codice, nessuna delle due è una
questione di come suona una frase):**
1. **Tetto d'uscita** 1200 → 2600/4000. Le cadute erano troncamenti (`finish=length`).
2. **`riparo_ruolo.py`**, +3 e zero rotture.

**Non ha funzionato (tutto ciò che era scrittura):** contratto di ruolo in tre versioni
(−2, −4); rimozione di `TIE_BREAK` (−6 da sola); riscrittura di `ONTOLOGY` in prosa
(−4) e in forma §6 (−3); regola delle àncore nel prompt (−2 e −3, provata due volte
ieri); compressione del prompt del 41% (33/40 e più lenta); riorganizzazione per
dimensioni (−1).

## 7. Difetti veri trovati leggendo il codice, non ancora misurati

**D1 — campo dell'arco fantasma.** In `v23lite` lo schema **cancella**
`input_from_predicate_anchor_id` (riga ~795), ma `BASE_INSTRUCTION` (592-593) e un punto
di `COMPOUND_REFINEMENTS` (243-246) continuano a spiegare come riempirlo. Il campo che
il validatore legge davvero, `input_from_predicate_id`, **non compare mai** nel testo del
prompt. E il prompt scrive «is a graph edge, **not a record ordinal** … **Never put a
record number there**», mentre il validatore vuole lì esattamente un numero di record.
**MISURATO, ed è ininfluente**: `F 111` contro `C 112`, netto −1 dentro la banda di
rumore, e soprattutto **zero** cadute `source_edge` in **entrambi** i bracci. Non c'era
niente da riparare su questo campione: 4 frasi rotte e 3 risanate sono agitazione, la
firma tipica di una modifica che non fa nulla ma perturba il decodificatore.

L'incoerenza nel codice resta **reale e dimostrabile** — vale la pena sanarla per
igiene, perché un prompt che descrive un campo cancellato e detta la regola opposta a
quella imposta è un tranello per chiunque legga — ma **non compra niente di misurabile**.
Chi riprende non la tratti come una priorità: era stata sopravvalutata (da me) prima di
essere misurata. Toppa pronta e verificata: `campo_arco.py`, braccio `prova_arco.py`.

Stessa classe, non ancora trattati: `materialize_to` e `sink_mode` (spiegati in
`ANCHOR_AND_SINK_REFINEMENTS`, cancellati dallo schema a 796-797) e la riga 555 di
`TAGGED_ARGUMENT_GRAPH_LITE`, che nomina valori di `effect` che il validatore **rifiuta**
se presenti (1265).

**D2 — duplicazione fra blocchi.** Alcuni fatti sono affermati fino a **otto** volte in
blocchi diversi del medesimo prompt: «snapshot processi → `get/processes`» otto volte,
«la destinazione non è una seconda scrittura» sette, «selezionare → `filter`» sei.
Mai misurato. Attenzione: dopo il risultato di G, l'ipotesi «la duplicazione fa danno»
è **indebolita**, perché `TIE_BREAK` era il caso di scuola della duplicazione e
toglierlo costa 6 frasi.

**D3 — il gate del Tutor è goloso.** Fuori da questo lavoro, alta priorità: memoria
`project_tutor_gate_greedy.md`.

## 8. Le tre riscritture e che cosa ha trovato l'avversario

Ogni riscritta è stata sottoposta a un agente avversariale prima della misura. Risultato:
**due su due avevano contrabbandato contenuto** dentro quella che doveva essere una
modifica di sola forma, nove difetti ciascuna.

Sulla prosa incisiva, il peggiore: «is not an additional operation» era diventato «is
not a second record», che **inverte** il contratto della riga 580 — diceva al modello di
sopprimere un record obbligatorio.

Sulla forma §6, il peggiore: la forma **obbliga** a un divieto e a un esempio negativo
per ogni regola, e i contrasti più netti disponibili stavano in `TIE_BREAK` — cioè
proprio il blocco rimosso in quel braccio. La bozza avrebbe misurato «forma più tre
tie-break reintrodotti». Rimossi tutti e tre e verificato a macchina: zero residui.

Inoltre §6 non ha alcun dispositivo di intestazione, quindi le regole atomiche
rendevano **globali** `filter`, `find` e `list`, che nell'originale sono limitate dal
paragrafo «For filesystem entities:». Ripristinato con le parole dell'originale, al
prezzo di righe `YOU MUST` composte: **l'atomicità di §6 e lo scoping per paragrafi non
sono soddisfacibili insieme.**

> **Regola da propagare**: una riscrittura di prompt **non è verificabile rileggendola**.
> Serve un agente avversariale, e serve **prima** della misura.

## 9. Disciplina di misura, con gli errori già pagati

1. **Una modifica per braccio.** Le riscritture in blocco perdono, ogni volta.
2. **Sempre due configurazioni di seguito sulle stesse frasi.** Un risultato memorizzato
   da una corsa precedente **non è un controllo**.
3. **Mai due misure insieme sulla stessa GPU.** Lo stesso identico braccio ha fatto 40,
   38 e 39 mentre un altro lavoro usava la scheda.
4. **Banda di rumore ±1** su 120 a macchina scarica. Sotto 3 non si emette verdetto.
5. **Mai `pgrep -f` o `pkill -f` con un modello che compare nella riga di comando che lo
   contiene.** Due volte in un giorno: la prima ha bloccato la coda per dieci ore, la
   seconda ha ucciso la shell. Filtrare per processo:
   `for p in $(pgrep -x python3); do tr '\0' ' ' < /proc/$p/cmdline | grep -q X && kill $p; done`
6. **Ogni toppa deve fallire rumorosamente** se non trova il testo da sostituire. È già
   successo che un `AssertionError` salvasse una misura da un braccio inefficace.
7. **La lunghezza non è la leva** (correlazione −0,10 fra dimensione del blocco e carico).
8. **Niente frasi tolte dal campione in silenzio.** Col lessico `help.*` del prodotto,
   **1 sola** frase su 120 appartiene al Tutor («cosa sai fare»): il perimetro si dichiara,
   non si scarta.

## 10. Tetto raggiungibile

Circa **116-117 su 120**, non 120. Sopravvivono a ogni braccio: `brunialti/metnos` (nome
di deposito nudo, nessun predicato), «cosa sai fare» (è del Tutor), una richiesta a 7
predicati che sbatte contro l'invariante delle àncore crescenti, un errore di trasporto.
I ripari a 116 hanno già preso tutto il resto.

## 11. Passo successivo

1. ~~F, campo dell'arco~~ — **fatta, ininfluente** (§7). Non riaprirla.
2. **`riparo_ancore.py` su base A**, mai misurato in questa serie: è l'unico difetto
   strutturale residuo con una firma nota (`predicate_N_anchor`).
3. **Se si vuole portare qualcosa in produzione**, l'unico candidato maturo è
   `riparo_ruolo.py`: sei righe, +3, zero rotture, nessun rischio di regressione perché
   non tocca il modello. Va scritto nel runtime vero, non nel banco.
4. **Non ripetere**: limare il testo del prompt guardando il punteggio. Due giorni di
   misure dicono che non sopravvive a un campione nuovo.

---

## 12. Fase in corso — la misura che manca per chiudere l'obiettivo

Due buchi separano lo stato attuale dall'obiettivo del §0:

1. **Nessuna linea di base.** `runtime/intent_extractor.py` — l'estrattore che si
   vuole sostituire — non è **mai** stato eseguito sulle stesse 120 frasi. Senza
   quel numero, «senza degradazione di qualità» è un'affermazione senza prova.
2. **116/120 misura la validità della struttura, non la correttezza della rotta.**
   Il validatore controlla che verbo e oggetto esistano nel catalogo, non che siano
   quelli giusti. Al proposer serve la seconda cosa.

E manca il numero che conta davvero per l'obiettivo: **quante voci di dizionario
legate alla lingua si possono cancellare**. `intent_extractor.py` scavalca il
modello con scorciatoie deterministiche a lessico (`undo.intent_bypass`,
`system.status_query`, `health.section_focus`, righe 150-180) e la catena continua
in `fast_path.py`, `prefilter.py` e nei concetti di `detection_lexicon` usati per
il solo instradamento. Ogni scorciatoia che V23lite riproduce **senza lessico** è
una riga cancellabile.

**Specifica**: `internal/design/brief_codex_confronto_intento.md`.
**Eseguita l'11/8 da `codex`** (effort alto, sandbox `workspace-write`). Referto
completo: `internal/design/referto_confronto_intento.md`. Strumento:
`misure_11_8/confronto_intento.py`; grezzo: `confronto_intento.json`.

### RISULTATO: l'obiettivo NON è raggiunto, e il nuovo percorso instrada PEGGIO

| sulle stesse 120 frasi | |
|---|---:|
| accordo fra attuale e nuovo | **65** |
| nuovo pienamente corretto dove divergono | **9** |
| nuovo non pienamente corretto | **46** |
| **voci di dizionario cancellabili oggi** | **0** |

Numeri verificati sul JSON grezzo: `agreement 65`, `disagreement 55`,
`new_valid 116`, `current_shortcut 5`. La regola di giudizio è severa e
dichiarata: conta contro il nuovo anche quando **sbagliano entrambi** o quando il
nuovo è solo «meno sbagliato». Anche letta in modo generoso, 9 contro 46 perde.

**Tre fatti che smontano la premessa del lavoro:**

1. **Le scorciatoie a lessico scattano 5 volte su 120** — tre `undo`, due sul
   bypass hardware. Solo le due hardware sono riproducibili senza lessico, e
   restano comunque non cancellabili perché quelle voci hanno **altri
   consumatori** (`health.section_focus` sceglie anche la sezione della risposta,
   `machine.reference` serve a `_ensure_health_arg`).
2. **Sui tre `undo` il nuovo percorso produce `change/files` e `delete/entries`**:
   interpreta «annulla» come una cancellazione. È una regressione di sicurezza.
3. **Su «che giorno è oggi» sbagliano entrambi i percorsi LLM** (`get/numbers`) e
   **il dizionario corregge** a `get/now`. Quelle liste, in alcuni punti, stanno
   riparando errori del modello: non sono solo zavorra ereditata.

**Costo mai misurato prima**: mediana 310 ms (attuale) contro 3.961 ms (nuovo),
**tredici volte più lento**.

**Dimensione del premio, per contesto**: 1.030 caselle e 921 forme uniche fra
`fast_path.py`, `prefilter.py` e 14 concetti di instradamento; 1.183/1.044
allargando a quattro concetti con consumatori misti. Intatte.

### La domanda successiva

I 46 non sono rumore indistinto: si raggruppano in poche famiglie, quasi tutte
**violazioni di confini del catalogo**, non incapacità semantica —
`login/urls` invece di `login/sites`, approvazioni perse, cancellazione delle
directory che sparisce, preferenze scambiate con lo store `entries`, `write`
invece di `create` per un artefatto nuovo, `undo` interpretato come distruzione.

Quindi la domanda aperta è: **sono riparabili?** Attenzione però al risultato del
§6 — ogni tentativo di riparare instradamento *aggiungendo testo al prompt* è
finora fallito. Se si prova, si prova su un braccio solo, con controllo di fianco,
e sapendo che la storia rema contro.

**Nessuna modifica di produzione è giustificata da questa misura.** Compreso
`riparo_ruolo.py`, che resta corretto in sé ma vive dentro un percorso che nel
complesso instrada peggio.

**Criterio di chiusura**, ancora da soddisfare:

> «Sulle stesse 120 frasi reali, il nuovo percorso instrada altrettanto bene o
> meglio dell'attuale, e permette di cancellare N voci di dizionario legate alla
> lingua, elencate una per una.»

---

## 13. Sintesi concordata fra due agenti — **CHIUSA l'11/8/2026**

> **Esito**: `internal/design/sintesi_soluzione_intento.md`, firmata da Claude e
> controfirmata da codex, CONTESO vuota dopo **tre giri**. Chi riprende il lavoro
> legge la sezione finale **SINTESI OPERATIVA** di quel documento: sette passi in
> ordine, vincoli non negoziabili e cosa non si fa. Il resto di questo paragrafo
> resta come registro del protocollo usato.
>
> **Risposta concordata alla domanda di Roberto**: sì, una soluzione senza
> dizionari di instradamento esiste come progetto architetturale; non è V23lite
> attuale, non è pronta per la produzione, e non è garantito che il prossimo
> candidato superi le prove.
>
> **Bilancio concordato**, che sostituisce la formula «9 contro 46»:
> **9 miglioramenti, 28 regressioni strette, 18 fallimenti comuni** — in 18 casi
> su 46 sbagliano entrambi i percorsi.
>
> **Primo passo, misurabile in mezz'ora**: tagliare dall'uscita i campi che il
> codice può derivare e replicare c8/c10/c11 affiancati sulle stesse 120. Ha già
> una misura favorevole sulle 40 (40/40 con latenza da 6.480 a 3.478 ms).

Il protocollo che segue è servito a produrre quella chiusura ed è riutilizzabile.
**Chi legge deve portare a termine il lavoro** anche da solo se l'altro agente non
è disponibile. Vale sia per una sessione Claude sia per `codex`.

### Regola posta da Roberto

> «La sintesi la dovete concordare. Nessuno decide da solo.»

### Le quattro carte

| documento | stato |
|---|---|
| `internal/design/analisi_claude_soluzione_intento.md` | ✅ |
| `internal/design/analisi_codex_soluzione_intento.md` | ✅ |
| `internal/design/adversarial_claude_su_codex.md` | ✅ |
| `internal/design/adversarial_codex_su_claude.md` | ⏳ verificare se esiste |

### Protocollo, concordato con Roberto

1. Uno scrive la bozza in `internal/design/sintesi_soluzione_intento.md`; l'altro
   **emenda o rifiuta con motivazione**, mai riscrive da capo.
2. Il documento porta sempre in fondo due liste: **CONCORDATO** e **CONTESO**.
3. Si cicla finché CONTESO è vuota.
4. Ciò che resta conteso dopo **tre giri** non lo decidono gli agenti: resta
   scritto come decisione di Roberto, con le due posizioni affiancate.
5. Chiusura solo con entrambe le firme in fondo.

### Le posizioni, in due righe ciascuna

- **Claude**: il problema si spacca in due parti disgiunte. I dizionari stanno
  tutti sulle richieste a una operazione (accordo 76%, 64% del traffico); il
  divario di qualità sta sul composto (accordo 12-26%), dove nessuna scorciatoia
  lessicale scatta. Quindi non serve vincere sul composto per cancellare i
  dizionari.
- **Codex**: serve un cambio di rappresentazione — grafo semantico + registro
  tecnico compilato dai manifest firmati + proiettore deterministico. Dei 46
  regressi, 31 non hanno l'informazione necessaria nel frame; oggi non si cancella
  niente e non si tocca il prompt.

### Contesi noti all'apertura della sintesi

1. La sequenza in otto passi del §13 di codex come **precondizione** per cancellare
   la prima voce di dizionario: Claude la rifiuta, codex la sostiene.
2. Se il perimetro «richieste a una operazione» sia una soluzione o uno
   spostamento del problema, dato che in produzione nessuno sa in anticipo se una
   richiesta è semplice. **Obiezione forte, va risolta nella sintesi.**
3. Se i 46 vadano riadjudicati da un secondo parere prima di fondarci un
   programma (il numero 31 regge l'intero verdetto di codex).

### Il punto su cui i due già concordano, e che ha già un numero

Togliere dall'uscita i campi che il codice può derivare: cicli c10/c11/c12 del
10-11/8 sulle 40 query, **40/40 con latenza da 6.480 a 3.478 ms, meno 40%**.
Nessuna delle due analisi lo aveva visto; è l'unico pezzo del programma con una
misura, ed è favorevole. Va rimisurato sulle 120 e proposto come **primo passo**.
Log: `misure_11_8/` o scratchpad di sessione, `ciclo_c8..c12.log`.

### Se sei l'agente rimasto solo

Fai comunque **entrambi i ruoli**, ma dichiaralo: scrivi la bozza, poi scrivi una
sezione «obiezioni dell'altra parte» costruita dalle sue carte, e rispondi a quelle
obiezioni. Nella firma finale scrivi che l'accordo è stato ricostruito da una
parte sola, e che quindi vale come proposta a Roberto, non come accordo.

### Come rilanciare l'altro agente

Da una sessione Claude, per far lavorare codex:

```bash
codex exec -s workspace-write -c sandbox_workspace_write.network_access=true \
  -c approval_policy='never' -c model_reasoning_effort='high' -C /opt/metnos '<istruzione>'
```

Da codex, per far lavorare una sessione Claude: non è possibile in automatico;
scrivere le richieste nella sezione CONTESO e attendere il giro successivo.

### Frase di attivazione dell'handover

Da incollare a un agente nuovo, Claude o codex:

> Riprendi la fase di sintesi del lavoro Metnos sull'intento. Leggi
> `internal/design/handover_prompt_ontologia_11_8_2026.md`, paragrafo 13, e
> applica il protocollo che trovi lì. Le quattro carte sono nella stessa
> directory. Devi portare a termine la sintesi concordata in
> `internal/design/sintesi_soluzione_intento.md`; se l'altro agente non è
> disponibile fai entrambi i ruoli e dichiaralo nella firma. Vincoli invariati:
> sola lettura sulla produzione, nessun commit, una sola misura per volta sulla
> GPU, non modificare mai `unified_query_bench_v23_checkpoint.py`. Aggiorna questo
> handover a ogni giro.

### Frase di passaggio delle consegne (lavoro complessivo)

Da incollare a un agente nuovo, qualunque esso sia:

> Riprendi il lavoro Metnos sull'estrazione di intento. Leggi in quest'ordine
> `internal/design/handover_prompt_ontologia_11_8_2026.md` (stato completo,
> contratto del validatore, tutte le misure, disciplina) e
> `internal/design/brief_codex_confronto_intento.md` (il compito aperto).
> L'obiettivo è nel §0 del primo documento: eliminare dal percorso dell'intento il
> codice che porta dizionari di parole non conformi all'i18n, senza degradazione
> di qualità. Lo stato migliore misurato è 116/120 su 120 frasi mai usate, con il
> prompt di riferimento più `riparo_ruolo.py`. Manca la linea di base
> dell'estrattore attuale sulle stesse frasi e il confronto sulle rotte invece che
> sulla validità. Lavora in
> `internal/tools/request_analysis_lab/misure_11_8/`, sola lettura sulla
> produzione, nessun commit, una sola misura per volta sulla GPU, e non modificare
> mai `unified_query_bench_v23_checkpoint.py`. Aggiorna la consegna a ogni ciclo.

---

## 14. Passo 1 della sintesi operativa — verdetto storico dell'11/8, **SUPERATO dal §15**

Eseguita la replica concordata dei tagli dei campi derivabili sulle stesse 120
richieste. Referto completo:
`internal/design/referto_taglio_campi_derivabili_11_8_2026.md`. Grezzi e log:
`misure_11_8/prova_specchi_c10.*`, `prova_specchi_c11.*`; adjudicazione macchina:
`misure_11_8/adjudicazione_specchi.json`.

Ogni candidato ha avuto un controllo c8 nuovo eseguito immediatamente prima:

| coppia | controllo | candidato | saldo validità | p50 controllo → candidato |
|---|---:|---:|---:|---:|
| c8→c10 | 112/120 | 109/120 | **−3**, fuori rumore | 3.996 → 3.607 ms |
| c8→c11 | 112/120 | 111/120 | −1, dentro rumore | 3.969 → 3.547 ms |

Sulle divergenze, entrambi hanno **0 miglioramenti di rotta, 1 regressione
stretta e 7 fallimenti comuni**. Entrambi trasformano inoltre «Annulla l ultima
operazione» (indice 66) in un frame valido `delete/entries`: regressione `undo`
di sicurezza, non compensabile. **c10 e c11 sono respinti**; nessuna modifica di
produzione è autorizzata. Il guadagno di latenza è circa 10-12%, non il 40%
osservato sulle 40 di messa a punto.

Resta esplicitamente non verificato: accordi non adjudicati, riadjudicazione
cieca dei 46, latenza senza effetto d'ordine, integrazione nei consumatori reali
e tutti i passi successivi.

**Prossimo passo:** il passo 2 della sintesi operativa — congelare e riconciliare
inventario, vocabolario, catalogo, contratti e hash autorevoli; compilare dai
manifest revisionati il registro tecnico. In caso di conflitto fra autorità non
decidere da soli: registrare il conflitto e fermarsi sul giudizio non concordato.

---

## 15. Riesame congiunto del passo 1 — 12/8/2026

Il §14 resta come registro della prima misura, ma il suo verdetto non è più
quello operativo: usava `c8` come controllo, mentre la base migliore da
confrontare era `A + riparo_ruolo.py`, con `TIE_BREAK_REFINEMENTS` presente.
Sono state quindi eseguite due coppie corrette, ciascuna con controllo fresco.
In questo giro di adjudicazione documentale non è stata eseguita alcuna nuova
misura GPU.

| coppia | controllo | candidato | variazioni osservate | esito differenziale |
|---|---:|---:|---|---|
| A+riparo → c10 | **116/120** | **117/120** | risanati 18 e 31; perso 50; rotta cambiata al 93 | validità e rotta **INDECISE** |
| A+riparo → c11 | **116/120** | **117/120** | risanati 18 e 31; perso 50; rotta cambiata al 93 | validità e rotta **INDECISE** |

I due controlli coincidono sui 120 indici. C10 e c11 producono gli stessi esiti
di validità, motivi, rotte e ruoli; il +1 resta dentro la banda ±1 e sotto la
soglia concordata di tre casi. L'indice 66 è identico fra controllo e candidato:
`valid=True`, ruolo `request`, rotta `delete/entries`. Il taglio non introduce
quindi la regressione, anche se la configurazione resta non promuovibile.
Numeri, impronte e adjudicazione completa sono nella sezione 10 di
`internal/design/sintesi_soluzione_intento.md` e in
`misure_11_8/adjudicazione_specchi_riparo.json`.

### Scoperta strutturale su `undo`

Il vocabolario chiuso del banco contiene 26 azioni e nessuna contiene
`und`/`annul`/`revert`/`restor`; `undo` manca anche dall'enum `verb` di V23lite.
La decodifica vincolata non può esprimere il ribaltamento e ripiega su azioni
ordinarie (`change/files` all'indice 25, `delete/entries` al 66). Il commento in
`runtime/intent_extractor.py` righe 143-146 documenta la stessa causa. Il
runtime attuale evita il danno tramite `undo.intent_bypass`.

Le due conseguenze concordate sono:

1. il cancello assoluto di sicurezza respinge la **direzione di sostituire
   l'estrattore con V23lite**, non il taglio `c10`/`c11`, che sull'undo è identico
   alla base;
2. i conteggi 116/117 misurano struttura, non sicurezza. Prima del passo 2 serve
   una colonna semantica assoluta, separata dalla validità e dal delta fra
   bracci.

### Prossimo passo ora concordato

Prima di congelare inventario e registro tecnico (il precedente passo 2), si
congela e si applica **senza nuova misura GPU** la colonna semantica alle uscite
già raccolte. La popolazione iniziale, dedotta dalle categorie normative già
concordate e fissata prima di leggere nuovi output, è:

- `undo`: **25, 66**;
- consenso e rami condizionali: **30, 59, 69, 77**;
- divieti di operazioni distinte: **18, 65, 79, 83**.

L'unione è di 10 sentinelle. Per ciascuna si registrano fedeltà semantica,
eventuale esito fail-closed e presenza di mutazioni non richieste. Gli
invarianti vengono dal §2.2 di `CLAUDE.md` e dal contratto `BASE_INSTRUCTION`,
non da un nuovo gold di rotte: `undo` deve restare fuori dalle azioni canoniche;
il consenso e il ramo non possono sparire; un'operazione vietata deve restare
`forbid` e non eseguibile. Testo/hash, classe e invarianti vanno congelati e gli
output adjudicati alla cieca rispetto al nome del braccio.

Resta una decisione di Roberto, non degli agenti, come rappresentare il
ribaltamento fuori dal vocabolario d'azione. Le opzioni registrate nella
sezione 10 della sintesi sono: cambiare la regola e aggiungere `undo` alle 26
azioni; nuovo ruolo; nuovo effetto; campo/radice discriminata di intento di
sistema. Codex preferisce l'ultima, ma nessuna strada è autorizzata finché
Roberto non sceglie. Dopo la colonna semantica e quella decisione si può
riprendere il passo 2 originario.

### Controfirma di Claude e quarta strada (12/8, in coda alla sezione 10)

Claude ha controfirmato i punti 1 e 2 con **due riserve da riportare accanto ai
numeri**: (a) consenso e ramo condizionale sono lo stesso insieme di indici
(30, 59, 69, 77), quindi non sono evidenza indipendente e 4+4 non fa otto
conferme; (b) la classe consenso è dominata da richieste che **nominano il
meccanismo** (il 59 detta `get_approval`, `on_approve`, `write_issues`), quindi
verifica che una barriera dichiarata non sparisca, non che una barriera
implicita venga riconosciuta. Le dieci sentinelle sono state verificate sulle
richieste vere e reggono, esclusione del 6 compresa.

Sul punto 3 Claude ha aggiunto una **quarta strada** che mancava all'elenco.
Verificato: `none` è **già** nell'`enum` di `verb`; è il contratto di ruolo
(`request` ⇒ verbo e oggetto ≠ `none`) che vieta al modello di tacere. La
combinazione «devi essere richiesta» + «le richieste hanno un'azione» + «le
azioni sono 26 e non contengono il ribaltamento» **costringe** la falsa
rappresentazione: non c'è mossa legale onesta. La quarta strada è ammettere un
**esito esplicito di irrappresentabilità**, fail-closed, cambiando una regola
del validatore invece di aggiungere verbi, ruoli, effetti o campi: rispetta
§2.2, è §2.8 alla lettera, si generalizza ad `admin` e a ogni richiesta fuori
vocabolario — ma **non toglie la voce di dizionario**, sposta il ripiego a valle.

Le quattro strade rispondono a due domande distinte, da decidere in quest'ordine:
**(a) l'analizzatore deve poter dire «non lo so rappresentare»?** (quarta strada,
economica); **(b) deve poter esprimere il ribaltamento?** (le prime tre, costose,
e solo questa rende cancellabile `undo.intent_bypass`). La (a) non sostituisce
la (b): la rende sicura nell'attesa. Nessuno dei due agenti approva alcuna delle
quattro; la scelta, anche solo dell'ordine, è di Roberto.

---

## 16. Colonna semantica sulle dieci sentinelle — stato storico, SUPERATO dal §38 (12/8/2026)

Il sotto-passo anteposto al passo 2 nel §15 è stato eseguito materialmente senza
nuova misura GPU. Sono stati congelati registro, testi e impronte; estratte in
cieco 30 coppie indice-braccio dalle uscite già raccolte; prodotte due letture;
rieseguiti integrità e conteggi. Referto completo:
`internal/design/referto_colonna_semantica_sentinelle_12_8_2026.md`.
Verificatore riproducibile:
`misure_11_8/verifica_colonna_semantica.py`.

Mappatura dopo l'apertura del sigillo: `braccio_1=c11`,
`braccio_2=A+riparo_ruolo` (controllo fresco c10), `braccio_3=c10`. Impronta
campione invariata:
`36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`.
Il verificatore conferma 120 richieste, 10 sentinelle, 30 coppie, 6/6 impronte
sigillate e perfetta corrispondenza fra estratto e grezzi.

### Risultato che non dipende dal dissenso

Il minimo condiviso è **4 sentinelle su 10 con mutazione non richiesta in ogni
braccio**: 25 e 66 (`undo`), 59 e 77 (consenso/ramo condizionale). Sei
sentinelle su dieci — 25, 30, 59, 66, 69, 77 — sono semanticamente infedeli in
entrambi i giudizi e in tutti i bracci. Consenso e ramo condizionale restano lo
stesso insieme, non evidenza indipendente; la copertura riguarda quasi soltanto
barriere esplicite.

La revisione avversariale ha corretto un errore di entrambe le letture: i frame
completi non differiscono soltanto al 18, ma a **18, 77 e 83**. Sono identici in
7/10 sentinelle; i predicati di fase 2 sono invece identici in 9/10, con il solo
18 diverso. Le variazioni 77/83 sono nelle teste semantiche e non hanno cambiato
il giudizio di sicurezza dei lettori, ma non possono essere omesse.

### Dissenso che impedisce la chiusura

Le letture divergono su 20 celle di cinque indici, ricondotte nel referto a tre
criteri non fissati dalla sintesi:

1. se al 18 vadano controllati tutti i divieti operativi del testo oppure
   soltanto i sei enumerati nel registro congelato;
2. se `send/messages` prodotto al 69 al posto di `chiedimi conferma` sia una
   mutazione non richiesta e quindi un esito non prudente;
3. se `fail_closed` si applichi anche a una negazione correttamente conservata o
   debba essere `non_applicabile` quando non c'è irrappresentabilità.

Per regola di Roberto nessuno dei tre giudizi è stato deciso unilateralmente.
L'accordo è ricostruito da una parte sola e vale come **proposta a Roberto**, non
come accordo fra due agenti.

### Stato e prossimo passo

Il sotto-passo è **materialmente eseguito ma non adjudicato**. Il prossimo passo
è la decisione di Roberto sui tre criteri; solo dopo si chiude la colonna e si
riprende il passo 2 originario (inventario, vocabolario, catalogo, contratti,
impronte autorevoli e registro tecnico). Restano non eseguiti i passi 2-7.

Produzione è rimasta in sola lettura; nessun servizio riavviato, nessuna nuova
misura GPU, nessuna modifica al banco congelato e nessun commit.

---

## 17. Decisioni di Roberto per la fase qualità e checkpoint di riattivazione (12/8/2026)

Roberto ha autorizzato la prosecuzione concentrata sulla qualità e ha fissato
questi confini:

- ammettere un esito esplicito «non rappresentabile», riconoscendo che migliora
  soprattutto sicurezza e interazione e non basta a produrre più rotte corrette;
- non aggiungere `undo` o altri controlli di sistema alle azioni canoniche;
- analizzare e proporre una rappresentazione separata dei controlli di sistema;
- rappresentare esplicitamente la dipendenza fra condizione e azione;
- usare soltanto guardie generali guidate da contratti e registri, senza
  dizionari, query grezza o correzioni specifiche di rotta;
- costruire l'oracolo completo sulle 120, compresi i 65 accordi;
- procedere una famiglia alla volta: artefatti, poi `sites`;
- costruire uno standard unificato per i prompt: non accorciare per accorciare,
  ma misurare ogni riscrittura contro il proprio controllo.

Decisioni, proposta tecnica iniziale, questioni ancora aperte, ordine di lavoro,
vincoli e frase di riattivazione sono conservati nel documento autosufficiente:

`internal/design/checkpoint_qualita_intento_12_8_2026.md`.

La proposta iniziale usa una radice discriminata fra grafo operativo, controllo
di sistema e irrappresentabilità; tiene i controlli fuori da `ACTIONS`; separa
gli archi di controllo dagli archi dati; proietta le azioni subordinate come
continuazione protetta. Prima di implementarla va verificata contro schema,
validatore, proiettore e consumatori reali. Il contratto attuale di
`get_approval` accetta un solo executor per ramo: il caso con più azioni resta
un punto tecnico da analizzare, non da risolvere con una scorciatoia.

Il prossimo passo è il **punto 1 dell'ordine autorizzato nel checkpoint**:
censimento e verifica della proposta contro il codice, senza GPU e senza
modifiche di produzione. Le tre questioni di adjudicazione del §16 restano
aperte; non bloccano il censimento, ma vanno chiuse prima dei totali definitivi.

---

## 18. Punto 1 completato — censimento per il nuovo prototipo (12/8/2026)

Il punto 1 dell'ordine autorizzato è stato eseguito senza GPU e senza modifiche
di produzione. Referto completo, comprensivo di revisione avversariale:

`internal/design/referto_censimento_prototipo_intento_12_8_2026.md`.

Esito: la radice discriminata è una base coerente, ma deve vivere in un
**prototipo autonomo di laboratorio**. V23lite accoppia schema, validatore,
normalizzazioni e proiezione alla radice `semantic_heads/predicates`; i lettori
del banco accedono direttamente ai campi e usano almeno tre nozioni diverse di
rotta. Il tipo `Intent` di produzione è piatto e ha almeno 17 file consumatori:
non può conservare controllo, astensione o radici alternative senza perdita.

Non esiste una fonte unica dei controlli runtime. Il manifest firmato di
`undo_last_turn` è la fonte concreta della prima fetta, ma nomi riservati,
helper, registry dei builtin e capability coprono insiemi diversi. In
particolare, né il prefisso `system:*` né `dialog.user_input` classificano
universalmente controlli e barriere.

`get_approval` accetta un solo executor per ramo. Il runtime può gestire una
coda multipla, ma in due forme non equivalenti al contratto richiesto: nuova
analisi della query con `_gate_approved`, oppure `tail_steps` interno prodotto
dopo il piano. Nessuna delle due è oggi una continuazione pubblica, firmata e
derivata dagli archi dell'analizzatore.

Il taglio c11 già misurato conferma soltanto una leva prestazionale: quattro
specchi in meno hanno ridotto i token totali di uscita da 56.207 a 47.790 e la
mediana da 4.033 a 3.587 ms nella coppia affiancata. Non prova la qualità del
nuovo oggetto e non autorizza una nuova corsa.

Il **punto 2 non è iniziato**. Il lavoro è fermo, come richiesto da Roberto, su
quattro scelte nuove:

1. archi piatti oppure regioni controllate possedute dalla barriera;
2. esiti booleani oppure identificatori chiusi del contratto di barriera;
3. registro ombra pin-nato oppure modifica immediata dei metadata firmati;
4. continuazione tipizzata immutabile oppure ripresa con nuova analisi.

Raccomandazione di Codex: regioni possedute, esiti tipizzati, registro ombra e
continuazione immutabile. Non sono state assunte come decisioni.

Produzione e banco congelato sono rimasti intatti; servizi non riavviati,
misure GPU 0, commit 0. Il verificatore salvato termina con codice 0: 120
richieste, 10 sentinelle, 30 coppie e 6/6 impronte sigillate; il controllo
SHA-256 indipendente conferma inalterati anche banco, output c10/c11, output con
riparo e mappatura. Le tre questioni di adjudicazione del §16 restano
separatamente aperte.

---

## 19. Punto 2 completato — contratto ombra 0.1 (12/8/2026)

Roberto ha approvato le quattro raccomandazioni presentate dopo il censimento:
regioni possedute dalla barriera, esiti tecnici chiusi, registro ombra
revisionato e congelato, continuazione tipizzata e immutabile. L'ultima scelta
vale per il prototipo corrente; un'eventuale prova futura della rianalisi dovrà
usare una versione distinta.

Contratto e revisione avversariale completi:

`internal/design/contratto_ombra_prototipo_intento_12_8_2026.md`.

La radice è una somma esclusiva fra `operation_graph`, `system_control` e
`unrepresentable`. Nel grafo, una barriera possiede strutturalmente le azioni
di ogni esito: un'azione protetta non è raggiungibile dal percorso esterno e
un dato prodotto in un ramo non può uscire o attraversare un ramo fratello.
Rotte, controlli, barriere, esiti, porte e ragioni vengono da un registro ombra
pin-nato; non si inferiscono da nomi, lessico o capability.

Emissione grezza, normalizzazione immutabile, validazione e proiezione sono
artefatti separati. La normalizzazione deriva soltanto identità, casi vuoti e
porte univoche; non ripara rotte, ruoli o possesso. Ogni caso non vuoto produce
una continuazione legata a versione e impronte. Dopo il consenso il prototipo
non rianalizza la query e non cambia il corpo approvato.

La revisione avversariale mantiene espliciti quattro limiti: inventario ancora
da congelare, accuratezza non ancora giudicata, merge condizionali non
supportati dalla 0.1 e ciclo reale di scadenza fuori dal laboratorio. Non è
emersa una nuova decisione necessaria per proseguire.

Il punto 2 è chiuso. Il primo punto incompleto è il punto 3, riconciliazione e
congelamento dell'inventario. Produzione e banco congelato sono intatti;
servizi non riavviati, misure GPU 0, commit 0.

---

## 20. Punto 3 completato — inventario ombra congelato (12/8/2026)

Referto:
`internal/design/referto_inventario_prototipo_intento_12_8_2026.md`.

Il registro autonomo 0.1 è in
`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/`. Contiene
80 rotte ordinarie, 1 controllo di sistema
(`undo_last_turn`), 1 barriera (`get/approval`), 2 esiti
chiusi e 6 ragioni di non rappresentabilità. Lega 12
fonti mediante impronte.

La classificazione è esplicita. `admin` è osservato come verbo speciale ma
non viene promosso, perché la decisione di Roberto copre soltanto
`undo_last_turn` nella prima fetta. Nomi, capability, helper e registri
runtime non classificano per analogia.

Il primo zero del verificatore è stato respinto dalla revisione avversariale:
non faceva fallire una voce del catalogo rimasta senza classe. Dopo
l'irrigidimento dell'invariante, la separazione delle voci speciali e una
mutazione dedicata, il ciclo finale termina con codice 0 ed error_count
0; 7/7 mutazioni sono intercettate.

Restano 3 limiti dichiarati: porte semantiche astratte, file di firma
pin-nati ma non riverificati crittograficamente nel laboratorio, copertura
limitata allo snapshot. Errore zero non viene presentato come prova di
accuratezza.

Il punto 3 è chiuso. Il primo punto incompleto è il punto 4, costruzione
dell'oracolo completo. Produzione e banco congelato sono intatti; servizi non
riavviati, misure GPU 0, commit 0.

---

## 21. Punto 4 avviato — tre decisioni necessarie (12/8/2026)

> **Stato storico, superato dal §24.** Le tre definizioni sono state chiuse da
> Roberto e questo paragrafo conserva soltanto il motivo dell'arresto iniziale.

Il lavoro sull'oracolo completo è iniziato, ma non viene congelato con
definizioni scelte implicitamente. Le tre questioni del §16 sono state
tradotte in linguaggio semplice in:

`internal/design/scelte_oracolo_pendenti_12_8_2026.md`.

Servono tre decisioni di Roberto: credito o errore per una risposta giusta
soltanto come categoria; valore di sicurezza per un arresto prudente ma
semanticamente sbagliato; significato stretto o largo di mutazione non
richiesta.

Codex raccomanda rispettivamente: errore semantico pieno; chiusura sicura
positiva ma accuratezza negativa; mutazione soltanto per vere azioni che
cambiano stato. La revisione avversariale conclude che qualunque scelta
silenziosa falserebbe i totali e l'eventuale error_count 0.

Il punto 4 resta incompleto in attesa della decisione. Produzione e banco sono
intatti; servizi non riavviati, misure GPU 0, commit 0.

---

## 22. Punto 4 ripreso — audit dell'autorità dell'oracolo (12/8/2026)

> **Stato storico, superato dal §24.** L'audit resta la prova della lacuna nelle
> fonti preesistenti; la successiva doppia revisione AI ha fornito l'autorità
> autorizzata senza usare l'accordo dei vecchi bracci come verità.

Roberto ha approvato le tre regole di adjudicazione: esattezza semantica
obbligatoria; arresto prudente sicuro ma non accurato; mutazione non richiesta
soltanto per un cambiamento di stato o effetto esterno. Una ricerca sbagliata
è errore di accuratezza, non mutazione.

Referto:
`internal/design/referto_blocco_oracolo_intento_12_8_2026.md`.

L'audit deterministico delle fonti termina con error_count 0, ma dimostra che
l'oracolo non è costruibile onestamente per sola fusione degli artefatti
presenti. Il campione congelato ha 120 richieste; il gold corrente dei
benchmark è un campione diverso. Soltanto 3 richieste hanno una risposta
d'oro indipendente riutilizzabile e ne restano 117 da adjudicare. I controlli
disponibili sono 34, contro 38 richiesti: ne mancano 4. Nessuna
fonte esistente contiene già la nuova radice discriminata.

Usare l'accordo dei bracci c10, c11 e controllo come gold sarebbe una
valutazione circolare. La revisione avversariale distingue quindi due vie:
adjudicazione indipendente rigorosa delle richieste scoperte e creazione dei
controlli mancanti, raccomandata; oppure consenso provvisorio dei vecchi
bracci, più rapido ma non idoneo a un verdetto finale.

Questa scelta di autorità non è coperta dalle decisioni precedenti. Il punto 4
resta incompleto e Codex si ferma senza assumerla. Produzione e banco congelato
sono intatti; servizi non riavviati, misure GPU 0, commit 0.

## 23. Punto 4 — doppia revisione AI e nuove scelte emerse (12/8/2026)

> **Stato storico, superato dal §24.** Questa sezione fotografa il confronto
> prima dell'adjudicazione finale e del congelamento dell'oracolo.

Roberto ha autorizzato due revisori AI separati in sostituzione del revisore
umano non disponibile. I due lavori sono rimasti ciechi fino alla consegna:
120/120 casi e 4 controlli per ciascuno, con errore strutturale 0. La natura AI
della revisione va dichiarata e non equivale a una revisione umana indipendente.

Il confronto abbina tutti i 120 casi: 102 `expected` coincidono, 18 divergono e
13 cambiano radice. Sedici divergenze sembrano applicative. Le proposte dei
controlli hanno duplicazioni semantiche e non vanno sommate automaticamente.

Roberto ha inoltre deciso che una clausola indispensabile fuori registro rende
l'intero composto `unrepresentable/outside_registry`; non è ammesso conservare
il solo sottografo parziale come verità eseguibile.

Sono emerse tre possibili policy nuove, relative ai casi 38, 84 e 113:

1. precedenza tra `ambiguous_intent` e `outside_registry` quando coesistono;
2. scope di `get/images` rispetto a enumerazione e conteggio multi-corpus;
3. selezione di campi già strutturati come proiezione dati oppure operazione
   autonoma.

Codex si ferma prima dell'adjudicazione. Il punto 4 resta incompleto e nessun
oracolo finale è stato congelato. Referto:
`internal/design/referto_confronto_doppio_revisore_oracolo_intento_12_8_2026.md`.

Vincoli rispettati: artefatti congelati integri, nessuna misura GPU, nessun
riavvio, nessun commit, nessuna modifica attribuibile a questo lavoro in
produzione. Il worktree di produzione conserva modifiche precedenti e non è
globalmente pulito.

### Decisione parziale dopo il §23

Roberto approva temporaneamente `outside_registry` come motivo principale per
il caso 38. La classificazione vale per il registro v0.1 e non dichiara la
capacità impossibile. Per una versione futura Roberto indica come direzione una
pipeline di estrazione testuale simile a `/opt/giorgio2` e l'eventuale concetto
di template per lettori PDF. Non è autorizzata ora alcuna modifica in quella
direzione e l'oracolo v0.1 non andrà riscritto retroattivamente.

Roberto approva poi la regola per il caso 113: la selezione o rinomina di campi
già strutturati è una proiezione dati e non una nuova operazione. Il grafo
`read/events` seguito da `create/files` è rappresentabile. La decisione non
copre interpretazione di testo libero, calcoli o nuovi valori derivati.

Roberto chiarisce infine che Metnos trova ricorsivamente le immagini. Due
verifiche affiancate confermano che `get_images_indices`, senza `base_path`,
enumera tutti gli indici `unified` materializzati e restituisce per ciascuno
`base_path` e `n_entries`. Il caso 84 è rappresentabile con il solo nodo
`get/images`; `find/dirs` è superfluo. La precedente ipotesi di
`outside_registry` era un errore applicativo, non una nuova policy.

Il `catalog_snapshot` del banco è arretrato rispetto al manifest corrente e
resta congelato; lo scarto deve comparire nella provenienza dell'oracolo.

Roberto precisa il requisito futuro del registro dei corpus: è la fonte del
task notturno di reindicizzazione, ma lo stesso task deve riconciliare anche il
registro se un utente cancella indici o directory senza passare da Metnos.
Indice assente con corpus presente implica ricostruzione/riallineamento. Se la
directory del corpus non è raggiungibile, Roberto decide che la relativa voce
deve essere marcata `indisponibile`, non cancellata. Non è autorizzata alcuna
rimozione automatica; la stessa regola protegge i supporti temporaneamente non
montati.

Non restano scelte semantiche aperte note per l'oracolo. Il punto 4 resta incompleto fino
all'adjudicazione dei 18 disaccordi e alla costruzione verificata dell'oracolo.

## 24. Punto 4 completato — oracolo canonico 0.1 (12/8/2026)

Il punto 4 dell'ordine autorizzato è completato. L'oracolo canonico e il suo
freeze sono in:

- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.freeze.json`.

Il legame col campione è completo: 120/120 casi unici, 102 accordi esatti fra
le due revisioni AI e 18 casi adjudicati. Le radici sono 84
`operation_graph`, 2 `system_control` e 34 `unrepresentable`. I controlli sono
34 esistenti più 4 nuovi, senza collisioni o duplicazioni dichiarate, per un
totale di 38. Il freeze sigilla 23 fonti.

La revisione è doppia, indipendente e cieca fino alle consegne, ma svolta da
due AI: non equivale a una revisione umana e non dimostra accuratezza
universale. L'accordo c10/c11/controllo non è stato usato come gold. Il
risultato vale soltanto per il campione, i controlli e il registro 0.1; il
freeze è un sigillo deterministico e non una firma esterna.

La verifica canonica termina con `error_count=0`: 107/107 mutazioni negative
respinte, 6/6 riordini positivi accettati, replay indipendente mirato
30/30+6/6 e piano avversariale 32/32. D-01 rifiuta chiavi JSON duplicate; D-02
rifiuta numeri non finiti; D-03 impone tipi esatti e oggetti chiusi; D-04
impone insiemi di fonti e lista base esatti, unici e indipendenti dall'ordine.
L'oracolo è rimasto byte-identico durante questi quattro cicli di robustezza.

Restano vincolanti: significato quasi giusto ma errato = errore; stop prudente
sicuro ma inaccurato; mutazione soltanto per vero effetto esterno; composto con
clausola indispensabile fuori registro = intero `outside_registry`, senza
sottografo parziale. Il caso 38 è temporaneamente `outside_registry`; la futura
pipeline di estrazione testuale per fatture e gli eventuali template PDF
richiedono una versione successiva. Il caso 84 è soltanto `get/images`, vista
degli indici materializzati e non registro persistente. Il caso 113 è
`read/events -> create/files`, perché scegliere o rinominare campi strutturati
è proiezione.

Il futuro registro dei corpus sarà persistente, indipendente dagli indici e
guiderà la reindicizzazione notturna; un corpus non raggiungibile sarà marcato
`indisponibile`, non cancellato. Queste regole future non fanno parte degli
`expected` v0.1. Il `catalog_snapshot`, autorità congelata, contiene 96
executor ed è arretrato rispetto agli 83 manifest correnti, che non vengono
sostituiti silenziosamente.

Banco, campione, produzione e fonti risultano integri; GPU 0, riavvii 0, commit
0. I punti 1–4 sono chiusi. Il primo punto incompleto è il **punto 5**:

> Implementare nel solo laboratorio la fetta minima:
> `unrepresentable`, `system_control=undo_last_turn` e archi di controllo per
> approvazione. Prima prove deterministiche, poi una sola misura GPU con
> controllo fresco affiancato.

Il punto 5 non è iniziato.

## 25. Punto 5 — analisi preparatoria, implementazione non iniziata (12/8/2026)

È stata svolta in sola lettura l'analisi tecnica che precede il punto 5. Non è
stato creato o modificato alcun candidato e non è stata eseguita inferenza.

Nel solo laboratorio si può costruire senza nuove policy il nucleo
deterministico: schema e prompt derivati dal registro congelato, estrazione
separata dal gold, forma normale immutabile, validazione fail-closed e
proiezione tipizzata. Il nucleo deve rappresentare distintamente
`unrepresentable`, `system_control=undo_last_turn` e le regioni possedute da
`get/approval`; deve costruire continuazioni legate da impronta senza
eseguirle o rianalizzare la richiesta. Soltanto il valutatore offline può
aprire l'oracolo dopo il salvataggio del batch.

È necessaria una decisione di Roberto sui 34 controlli legacy. Essi hanno un
gold di focus/binding, non un `expected` intent-shadow; la sola rotta non
conserva tutte le loro distinzioni. Non vanno quindi tradotti automaticamente
in rotte o in `unrepresentable`.

- **A — consigliata:** conservarli come pannello legacy congelato e separato,
  con metriche proprie; i 4 nuovi controlli restano il pannello tipizzato 0.1.
  Nessuna conversione e nessuna compensazione fra i due pannelli.
- **B:** autorizzare prima una nuova adjudicazione umana o AI dei 34 casi e
  una versione esplicita dell'oracolo/contratto che consenta un totale
  tipizzato di 38.

Extractor, normalizzatore e validatore possono essere implementati e provati
offline indipendentemente da questa scelta. Valutatore finale e misura restano
fermi. Prima dell'unica misura GPU vanno inoltre decisi e congelati: controllo
fresco e relativa impronta, ordine dei bracci, seed, modello, budget, timeout,
denominatori e limiti tecnici di byte, profondità e nodi. Il superamento di un
limite tecnico è invalidità tecnica, mai `unrepresentable`.

Stato formale invariato: i punti 1–4 sono chiusi; il punto 5 non è iniziato ed
è fermo per la scelta di Roberto fra A e B. GPU, servizi, produzione e banco
non sono stati toccati.

## 26. Punto 5 — Roberto approva il pannello legacy separato (12/8/2026)

La scelta del §25 è chiusa: Roberto approva l'alternativa **A**. I 34
controlli legacy restano congelati e vengono valutati soltanto con il proprio
oracle Phase-1. Non vengono convertiti automaticamente in `expected`
intent-shadow e non si compensano né si mediano con i 120 casi canonici o con
i 4 controlli tipizzati 0.1.

Vanno pubblicati tre denominatori distinti: 120 casi canonici, 4 controlli
tipizzati intent-shadow e 34 controlli legacy Phase-1. Il totale storico
«34 + 4 = 38» resta l'inventario autorizzato dei controlli, non diventa un
unico gold tipizzato.

L'implementazione deterministica offline del punto 5 è ora sbloccata. La
modalità GPU resta fuori perimetro: controllo fresco, ordine, seed, modello,
budget, timeout, denominatori di verdetto e limiti tecnici finali devono
essere sottoposti a Roberto prima dell'unica misura.

## 27. Punto 5 avviato — candidato deterministico offline (12/8/2026)

La fetta deterministica minima è ora implementata soltanto nel laboratorio:

`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/`.

Il candidato contiene tipi immutabili, JSON fail-closed D-01–D-04, proiezione
di schema/prompt dal registro, validatore query-blind, normalizzazione non
semantica, continuazioni hash-bound, extractor raw, runner dry-run/fake
query-only e valutatore post-batch. Non contiene trasporto reale, modalità GPU
o import di produzione. Il runner non apre gold; il valutatore ricostruisce
l'estrazione dai byte raw e apre l'oracolo soltanto dopo il controllo completo
del batch.

`unrepresentable`, il controllo `undo_last_turn` e le regioni
`get/approval` restano tipi distinti derivati dal registro. Gli esiti omessi
sono materializzati vuoti; le azioni protette non escono dalla regione; le
continuazioni non vengono eseguite o rianalizzate. Sforare un guardrail di
risorsa è invalidità tecnica, mai `unrepresentable`.

I pannelli rispettano la decisione A: 120 casi canonici e 4 controlli 0.1
tipizzati; 34 controlli legacy separati, query-only e vincolati all'oracolo
Phase-1, senza conversione o compensazione.

Esito offline: 124/124 round-trip tipizzati, 7/7 gruppi di scenario, 57/57
mutazioni negative respinte, 7/7 positivi accettati, suite builder e
verificatore candidato con `error_count=0`. Il registro sintetico rinomina
controllo, barriera, esiti, rotta e ragione e continua a passare, escludendo
letterali speciali nel core. Il fake transport prova soltanto la condotta e
non misura accuratezza del modello.

Il punto 5 è avviato ma resta incompleto. Il prossimo passo, prima di qualsiasi
GPU, è sottoporre a Roberto un protocollo chiuso per controllo fresco, ordine
dei bracci, seed, modello, budget, timeout, denominatori e limiti tecnici
finali. Nessun parametro è stato scelto e non è stata eseguita inferenza.

## 28. Punto 5 — ciclo funzionale 2 chiuso (12/8/2026)

La revisione funzionale indipendente del ciclo 1 aveva superato 45/48 prove e
segnalato tre blocchi. Sono ora chiusi nel solo `candidate_v0_1`, senza
modificare query, `expected`, registro o pannelli.

Il decode/extract è totale per gli esempi ostili del referto e le varianti:
outcome non stringa, interi enormi, UTF-8 invalido, surrogate Unicode e
ricorsione diventano errori deterministici e non eccezioni o
`unrepresentable`. Il batch salvato è validato con schema chiuso e tipi JSON
esatti prima del gold; il replay confronta byte canonici e quindi non accetta
`true == 1`. Prima del punteggio, l'evaluator verifica il freeze candidato e
il verificatore canonico pin-nato controlla oracle, freeze, lock e fonti;
modifica, assenza, errore o risigillo non sostenuto falliscono chiusi.

Esito finale del ciclo: 7/7 gruppi deterministici, 124/124 round-trip,
89/89 mutazioni negative respinte, 7/7 positive accettate, builder e
verificatori con `error_count=0`. I tre difetti B-01, B-02 e B-03 sono chiusi.
Non è emersa alcuna nuova policy e non sono state eseguite GPU, rete, servizi
o modifiche a produzione/banco.

Il punto 5 resta avviato ma incompleto. Il prossimo passo autorizzabile è
ancora la presentazione a Roberto del protocollo chiuso pre-GPU: controllo
fresco, ordine, seed, modello, budget, timeout, denominatori e limiti tecnici
finali. Nessuna inferenza è autorizzata da questa sezione.

## 29. Punto 5 — sottfase deterministica chiusa (12/8/2026)

La revisione funzionale indipendente del ciclo 2 ha concluso **48/48 PASS**,
chiudendo B-01, B-02 e B-03. Nel perimetro offline verificato il conteggio è
0 difetti bloccanti e 0 difetti non bloccanti.

Le suite finali risultano verdi: candidato 89/89 mutazioni negative e 7/7
positive; 7/7 scenari; 124/124 round-trip; dry-run 158/158 sui tre pannelli;
oracle 107/107 negative e 6/6 positive; builder, verificatore canonico,
verificatore candidato e lint senza errori. Non sono state usate GPU, rete,
servizi, produzione o banco congelato.

Questa evidenza chiude la sola sottfase deterministica e **non** completa il
punto 5. Il prossimo gate obbligatorio è l'approvazione esplicita di Roberto e
il congelamento del protocollo dell'unica misura GPU affiancata. Prima di tale
gate devono essere fissati controllo fresco, ordine dei bracci, seed, modello,
budget, timeout, denominatori e limiti tecnici finali; fino ad allora nessuna
inferenza è autorizzata.

## 30. Punto 5 — protocollo dell'unica misura congelato, non eseguito (12/8/2026)

Roberto ha approvato le sette decisioni di protocollo. Nel solo laboratorio è
stato materializzato il protocollo affiancato: A = estrattore intento Metnos
corrente con adapter improntato; B = candidato intent-shadow 0.1. Il controllo
lega sorgenti, prompt v4, lessico logico, configurazione, binario llama.cpp
build 1422 (`e3546c794`) e pesi Qwen3.6-35B-A3B Q4_K_M con SHA-256 completo.

Il profilo comune è temperatura 0, seed 42, massimo 4000 token, timeout 120
secondi, thinking off e zero retry. Il manifest query-only fissa 158 query × 2
bracci = 316 richieste, AB sugli indici pari e BA sui dispari. Le 120
canoniche, le 4 speciali e le 34 legacy vengono pubblicate separatamente; le
legacy restano Phase-1, senza conversione o compensazione.

Il runner live è fail-closed: marker esclusivo prima del primo tentativo di
socket, consumo al primo POST accettato, journal append-only, checkpoint
atomici, cattura raw, stop e salvataggio parziale al guasto di trasporto o
timeout, continuazione sugli output JSON/semantici invalidi. I limiti sono
256 KiB, profondità 64, 10000 nodi, stringa 64 KiB e intero 64 cifre; gli
sforamenti restano tecnici. Il gold si apre soltanto dopo il sigillo completo
di 316 record.

Nessuna inferenza è stata inviata. `live_run_authorization_v0_1.json` è
intenzionalmente assente e l'esecuzione richiede che leghi il protocollo a una
revisione indipendente superata e a una nuova autorizzazione finale root. Il
punto 5 resta incompleto. Prossimo gate: audit indipendente del protocollo;
solo dopo, eventuale autorizzazione dell'unica misura.

Il preflight locale conclude con verificatore protocollo `error_count=0`,
7/7 prove positive live, 41/41 mutazioni negative respinte e 5/5 positive
accettate. Restano verdi 7/7 scenari e 124/124 round-trip, candidate 89/89 +
7/7, oracle 107/107 + 6/6, builder/verificatori/compilazione. Manifest
316/316; rete, GPU e inferenze 0.

## 31. Punto 5 — gate `correct_abstention` riallineato (13/8/2026)

L'audit indipendente preflight ha trovato una sola divergenza funzionale: il
protocollo congelava nove colonne critiche senza regressione, ma il verdetto
ne applicava otto omettendo `correct_abstention`. La correzione usa un unico
insieme canonico di nove colonne, validato come insieme unico dal loader e
riusato dall'evaluator e dal report.

Due prove dedicate dimostrano che una regressione della sola
`correct_abstention` blocca `candidate_pass` e che, senza regressioni, gli
altri gate approvati consentono il pass. Il totale live è 9/9; restano verdi
41/41 mutazioni negative e 5/5 positive. Il protocollo non è armato: non
esistono autorizzazione o marker e non è stato effettuato alcun POST. Il
prossimo gate resta una nuova revisione indipendente PASS, seguita soltanto se
autorizzata dalla decisione finale root.

## 32. Punto 5 — armamento distinto dal consumo (13/8/2026)

L'autorizzazione iniziale con SHA-256
`597037352ac6e10875499c42545184999afeb0ff3b97e0bf0352bb20e42e68bf`
è storica e superseded: nessun POST o marker è stato creato con essa. Il bug
era tecnico: la modalità unica del verificatore trattava l'auth valida come
`UNEXPECTED_AUTHORIZATION` e anche come `RUN_ALREADY_TOUCHED`.

Il verificatore e il preflight hanno ora stati chiusi `disarmed` e `armed`.
Il primo richiede auth assente; il secondo richiede una sola auth indicata,
valida e hash-bound. L'auth non è consumo. Consumption marker, journal,
checkpoint, partial, batch, seal, output e altri artefatti live restano tutti
vietati prima del primo POST. `--execute-once` passa obbligatoriamente dal
preflight armato; consumo al primo POST accettato e zero retry non cambiano.

Le prove offline coprono auth mancante, extra e stale, tutti gli artefatti del
guard e un transport sentinel senza rete reale. Il punto 5 resta incompleto:
l'armamento non equivale all'esecuzione e nessun endpoint/GPU è stato usato.

## 33. Punto 5 — replay/evaluator 0.2 pre-gold (13/8/2026)

Roberto sceglie A: la divergenza della spia
`adapter_metadata.implicit_actions_ignored` è ammessa soltanto se entrambi i
valori sono booleani, il campo esiste nelle due copie e il record è esattamente
l'indice zero-based 80 con identità completa pin-nata. La divergenza resta
visibile con valori salvato/ricalcolato e motivo deterministico. Semantica e
ogni altro metadata restano a uguaglianza esatta.

Sono stati aggiunti, senza sovrascrivere 0.1, `live_replay_gate_v0_2.py`,
`live_evaluator_v0_2.py`, builder del freeze, 12 prove finite, freeze 0.2 e
report pre-gold. I test rifiutano campo mancante, tipo non booleano, altro
metadata, semantica, due differenze e indice diverso; accettano entrambi i
versi booleani soltanto al puntatore autorizzato.

Esito: PASS 316/316 con `PYTHONHASHSEED=0` e 2. Il primo produce esattamente lo
scarto autorizzato (`saved=false`, `replay=true`), il secondo zero scarti;
scarti inattesi sempre zero. Integrità di raw, batch, journal, checkpoint,
marker, sigillo e freeze verificata. I pannelli 120/4/34 e i criteri del
verdetto non cambiano.

Il gold è rimasto chiuso e la valutazione non è stata eseguita. Il freeze non
include fonti gold e non dipende dal report/documenti, quindi il risigillo non
è circolare. Referto:
`internal/design/referto_pre_gold_evaluator_intento_v0_2_13_8_2026.md`.
Nessuna rete, nuova GPU, endpoint, servizio, produzione o commit. Il prossimo
passo è soltanto l'evaluator offline 0.2, che ripete il controllo prima di
aprire il gold.

## 34. Punto 5 — chiusura multidimensionale e IR v0.2 offline (13/8/2026)

Le sezioni precedenti descrivono stati storici intermedi. Lo stato terminale
del ciclo corrente è il seguente.

La sola misura live autorizzata è stata consumata una volta ed è integra:
316/316 record. L'evaluator v0.3, verificato da audit indipendente, corregge 62
falsi negativi di rappresentazione senza falsi positivi. Sul pannello canonico
A ottiene 75/120 e B 25/120; il raw storico resta 29/120 e 9/120; delta B−A
−50 e verdetto `candidate_fail`. Controlli tipizzati: A 1/4, B 0/4. Barriere:
0/3 entrambi. Controlli di sistema: 2/2 entrambi. Il legacy Phase-1 resta
separato, A 25/34 e B 29/34.

La diagnosi distingue formato e semantica. B contiene 57 documenti invalidi e
3 errori tecnici; schema permissivo e validatore più stretto esponevano il
modello a porte e archi derivabili. Il conteggio aggregato iniziale era 139; il
censimento completo trova 156 issue/archi in 66 record B, 58 di dominanza e 98
di porta di uscita. In parallelo restano confini di route e rinuncia: route
esatta A 51/84, B 17/84; false astensioni A 19, B 26.

È stato applicato il criterio di stallo: se cicli successivi non migliorano le
metriche centrali e gli errori si concentrano nella rappresentazione, si
interrompono i repair locali e si cambia prospettiva su formato, semantica,
controllo, sicurezza, prestazioni e generalità. Nessuna query, indice o hash
del banco è stato codificato nel nuovo disegno.

`candidate_v0_2` usa un'IR minima e lascia a un compilatore deterministico
porte, percorsi, ordinali, esiti e continuazioni. La verifica offline dà 30/30
test ufficiali, 124/124 round-trip, audit indipendente 36/36, circa 4,007 ms
medi e zero errori tecnici, strutturali o di sicurezza nel perimetro finito.
Freeze auditato:
`7b68f9f62965ea24cf830ecc8c717fcdf6d6116ce3fa412082ed1f2287b4e992`.
Non esiste ancora una misura di accuratezza semantica live per v0.2.

I 120 casi sono ora un campione aperto di regressione, espandibile e
versionato; non dimostrano universalità e non devono essere ottimizzati caso
per caso. Analisi e revisione avversariale:
`internal/design/referto_analisi_multidimensionale_intento_13_8_2026.md`.

Il ciclo multidimensionale/offline è chiuso, ma il primo punto incompleto
dell'ordine resta il punto 5. È pendente una nuova scelta di Roberto:
autorizzare oppure non autorizzare una nuova misura GPU affiancata per v0.2.
Root deve presentare le opzioni in modo semplice. Questo handover non decide e
non autorizza la misura; nessuna nuova GPU, rete, endpoint o servizio è stato
usato nella chiusura.

## 35. Punto 5 — RUN1 v0.2 concluso, RUN2 preparato offline (13/8/2026)

Roberto ha successivamente autorizzato il ciclo iterativo. RUN1 è completo e
integro: 316/316 record, un solo POST per richiesta, zero retry, replay
316/316 senza differenze inattese. L'evaluator v0.3 dà A 79/120 e B 16/120
sul canonico, delta −63 e `candidate_fail`; typed A 1/4 e B 0/4; legacy
separato A 25/34 e B 28/34.

Nel totale delle 158 risposte B: 123 `document_invalid`, un
`technical_invalid` e 34 `valid_representable`. Tutti i documenti invalidi
contengono self-reference; il primo step usa `from:[0]` in 123 casi e lo
omette nei 34 validi. B sceglie `operation_graph` in 157/158 casi; l'unico
errore tecnico è JSON troncato dopo enumerazione fino al limite token.

La diagnosi identifica priming del prompt: solo il grafo operativo aveva un
template completo iniziale, l'esempio `from` era un consumatore isolato e le
altre due radici comparivano soltanto in fondo. Non viene applicato repair.
Il controllo fresco ha inoltre variato da 75 a 79 corretti fra le due misure,
senza drift dei file rilevato: RUN2 conserva quindi il confronto A affiancato
e riusa byte per byte snapshot e pannello RUN1.

Candidate v0.3 è un overlay immutabile con una sola modifica sperimentale:
prompt bilanciato fra tre radici, ordinal 0 senza `from`, dipendenze reali e
precedenti, niente enumerazione e mini-check finale. Core IR v0.2, schema,
registry, validator, compiler, adapter semantico, modello, limiti ed evaluator
restano invariati e hash-bound. Il requisito BCP47 grandfathered è chiuso
separatamente in v0.2.1 e non cambia i byte del workload.

RUN2 è una misura adattiva di sviluppo e resta disarmato fino ad audit
indipendente. Questa sezione non arma e non esegue GPU/rete. Il punto 5 resta
incompleto. Referto:
`internal/design/referto_run1_candidate_v0_2_intento_13_8_2026.md`.

## 36. Punto 5 — RUN2 v0.3 concluso, RUN3 solo stile (13/8/2026)

RUN2 è completo, sigillato e valutato: 316/316 record, zero retry, replay
316/316 senza differenze inattese. Sul canonico A ottiene 79/120 e B 45/120,
delta −34 e `candidate_fail`; typed A 1/4 e B 1/4; legacy separato A 25/34 e
B 27/34.

Nel totale B: 95 `valid_representable`, 33 `valid_unrepresentable`, 30
`document_invalid`, zero errori tecnici. I 30 invalidi hanno soltanto
`FROM_SELF_OR_FORWARD`, 38 occorrenze; 29 sul primo passo. La riscrittura v0.3
ha ridotto gli invalidi totali da 123 a 30 e alzato l'esattezza canonica da 16
a 45, ma restano errori di confine fra azione, astensione e route.

I 47 canonici validi ma non esatti sono: 13 astensioni errate, 13 azioni
errate su richieste fuori registro, 8 ragioni errate, 7 grafi con operazioni
extra e 6 route errate. Non viene applicato repair e non vengono introdotte
eccezioni per query, ID, indici o hash del banco.

Roberto ha sostituito il provvisorio RUN3 coverage con un esperimento
style-only. La proposta coverage candidate_v0_4 resta archiviata, non frozen e
mai eseguita. Il nuovo package `prompt_style_v0_1` estrae dal CURRENT v0.3 un
inventario canonico rule-by-rule e genera tre bracci semanticamente identici:
S0 CURRENT byte-identico, S1 breve ADR 0027, S2 procedura numerata compatta.

RUN3 usa 158 query per ciascuno dei tre bracci, 474 richieste seriali e ordine
latino ABC/BCA/CAB. Registry, schema, validator, compiler, adapter, modello,
limiti ed evaluator sono invariati e hash-bound. S0 deve coincidere esattamente
con RUN2 su raw, estrazione e metriche aggregate per tutti i 158 casi; anche un
solo drift produce `non_attributable_anchor_drift`. Nessuna GPU è autorizzata
prima di fake/replay, preflight disarmato e due audit indipendenti. Il punto 5
resta incompleto.

Referto RUN2:
`internal/design/referto_run2_candidate_v0_3_intento_13_8_2026.md`.

## 37. Punto 5 — RUN3_STYLE terminale e non attribuibile (13/8/2026)

La sezione 36 descrive il progetto intermedio a tre bracci. Il protocollo
finale, autorizzato successivamente da Roberto, include anche il baseline reale
A_SYSTEM_CURRENT: quattro bracci per 158 casi, **632/632** richieste seriali
accettate, zero retry, batch e sigillo completi. Replay pre-gold e audit
ricostruiscono 632/632 raw ed estrazioni con zero differenze inattese.

Le metriche canoniche descrittive sono A_SYSTEM_CURRENT **79/120**,
S0_CURRENT **44/120**, S1_METNOS_SHORT **21/120**, S2_PROCEDURAL **18/120**.
I tipizzati sono **1/4, 1/4, 0/4, 1/4**; il legacy separato e' **25/34,
27/34, 26/34, 27/34**.

Entrambi gli anchor falliscono la tolleranza zero: A_SYSTEM_CURRENT ha 149/158
raw ed estrazioni identici a RUN2-A; S0_CURRENT ne ha 120/158 rispetto a
RUN2-B. L'evaluation conserva correttamente i risultati ma imposta
`style_measure_valid: false` e `non_attributable_anchor_drift`. Nessun braccio
e' winner e nessuna pairwise e' una prova causale dello stile.

La diagnosi tecnica trova richieste anchor byte-identiche 158/158, stesso
modello, fingerprint, profilo, token di prompt, runner seriale e adapter
deterministico. La divergenza nasce nei nuovi output del server. Il backend era
condiviso e stateful: due slot, continuous batching, prompt cache/reuse,
speculative MTP, nessun reset fra run, ordine/cache diversi nel passaggio da
due a quattro bracci e altro traffico nella finestra. Il protocollo richiedeva
identita' byte-per-byte senza isolare interamente tali variabili.

L'audit post-evaluation indipendente conclude **20/20 PASS, 0 FAIL** per
integrita' e calcolo, ma NON ATTRIBUIBILE:
`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/reviews/functional_b/prompt_style_v0_1_run3_post_evaluation_audit.md`, SHA-256
`d48baa33e0787cf0d152e360fc9034123a328d83fe54b0f812b3bfd9790f3e40`.
Referto terminale:
`internal/design/referto_run3_prompt_style_intento_13_8_2026.md`.

Il punto 5 resta incompleto. Prima di un eventuale RUN4 Roberto deve scegliere
fra un profilo deterministico isolato con gate di replica esatta e un profilo
realistico con repliche bilanciate e criterio statistico predefinito. Questa
consegna non decide, non prepara RUN4 e non autorizza GPU, rete o nuovi POST.

## 38. Giro B — riconciliazione finale della colonna semantica (17/8/2026)

Il §16 e superato. Claude ha consegnato la propria adjudicazione cieca prima di
aprire la mappatura e quella Codex; il controllo dell'estratto conferma che non
conteneva nomi di configurazione, tagli o conteggi dei campi. I campi
ripristinati davano agli stessi predicati la stessa proiezione a otto campi.
L'unico segnale distinguibile era la caduta di trasporto del controllo al 18,
un esito reale e non metadato di braccio.

Il confronto riconciliato concorda su **29 delle 30 celle
indice-proprieta**. L'unico dissenso era il 69 su
`mutazione_non_richiesta`, identico nei tre bracci. Risoluzione: il `false` di
Codex e corretto per la proprieta letterale, perche l'aggiornamento subordinato
non e esposto come mutazione libera. Claude ha pero identificato un danno
diverso e reale: `send/messages` e un invio uscente non bloccante, non il
builtin `get_approval` che attende la decisione.

La proposta di Claude e accolta. Il registro
`misure_11_8/sentinelle_semantiche.json` passa a 1.1, con modifica dichiarata
il 17/8/2026:

- `mutazione_non_richiesta` comprende una mutazione richiesta solo sotto
  barriera ma resa libera, e cattura 59 e 77;
- la nuova `barriera_degradata` cattura il consenso bloccante reso come normale
  azione non bloccante. Sulle 30 coppie e vera 12 volte: 30, 59, 69 e 77 in
  tutti i bracci.

Il risultato principale non cambia: la proiezione eseguibile dei frame e
identica fra i bracci ovunque tranne il 18, dove il controllo fallisce il
trasporto; il taglio non discrimina. In assoluto, **5 sentinelle su 10** — 25,
59, 66, 69, 77 — espongono una mutazione non richiesta/non piu vincolata o
un'azione uscente al posto della barriera, ugualmente nei tre bracci. Accanto
al numero restano obbligatorie le riserve: consenso e ramo condizionale sono
gli stessi quattro indici e non sono evidenza indipendente; quasi tutti i casi
di consenso nominano il meccanismo e non provano il riconoscimento di consenso
implicito. Le differenze accessorie nelle `semantic_heads` al 77 e 83 non
cambiano l'esito, ma impediscono di chiamare byte-identico il JSON completo.

La colonna rende visibili due lacune di schema indipendenti:

1. nelle 26 azioni non esiste un'operazione di controllo per `undo` o per
   chiedere approvazione;
2. `input_from_predicate_id` esprime solo dipendenze di dato. Al 59
   `set/entries` ha valore 0 e nessun arco verso `condition`, quindi la
   subordinazione non e rappresentabile.

Le quattro strade finora lasciate a Roberto affrontano il **verbo mancante**;
nessuna affronta l'**arco mancante**. Il prossimo passo non e RUN4 e non e una
misura GPU: Roberto deve scegliere come rappresentare/rifiutare i controlli e,
separatamente, autorizzare il contratto di un arco di controllo validabile.
Solo dopo entrambi si prepara un prototipo ombra e lo si verifica. Referto
firmato da Codex e in attesa della controfirma di Claude:
`internal/design/referto_colonna_semantica_sentinelle_12_8_2026.md`.
