# V26.5.6.7 — runner K1/34 per il contratto clause-owned, checkpoint OFFLINE

Status: **preflight eseguito e PASS; gate esaurito**. Self-test del
bundle **92/92 PASS** (66 ereditati + 26 nuovi) fino al momento in cui il gate
è stato creato — vedi la sezione sul gate. Zero rete, zero chiamate modello,
zero trasporto, nessun live, `inference_allowed=false`. Gold, overlay, fixture,
registro, validator e i bundle V26566/V26565/V26564 non sono stati toccati:
verificati per hash prima e dopo.

Il bundle contiene **runner e valutatore offline**, cioè i punti 1 e 2 della
lista dell'handover. I due gate operativi (preflight ed esterno) **non
esistono**, quindi un live è impossibile per costruzione.

Replay:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26567/metnos_v26567_offline_selftest.py
```

Circa 30 s, nessuna scrittura fuori da directory temporanee.

## Da dove viene

I byte del trasporto, della macchina dei gate, dell'ancoraggio dell'output, del
budget di codifica canonica e del contratto di errore della CLI sono quelli di
**V26.5.6.4**, che aveva ottenuto uno STATIC PASS indipendente. Sono stati
modificati **solo** dove il contratto di richiesta li tocca. Non è una
riscrittura: è un re-pin, e l'eredità è verificabile con un diff contro
`../v26564/metnos_v26564_k1_runner.py`.

Predecessore: `6fb5785d…b146a`. Questo runner: `fd149655…0553`.

## Cosa cambia, e perché

**Il contratto è V26.5.6.6.** Prompt, schema di risposta, espansore e condotta
di valutazione sono ripinnati per SHA-256 su quel bundle
(freeze `a2d88bc1…1f1f3e`). Lo schema **derivato a runtime dal registro
congelato deve essere uguale** a quello materializzato: prompt e schema nascono
dallo stesso modulo pinnato, quindi non possono divergere. La verifica avviene
**prima** di qualunque trasporto: un contratto rotto costa zero chiamate al
modello, non 34.

**Ogni record porta l'impronta strutturale** (S4), valido o no — inclusi il
caso il cui contenuto non era JSON e l'eventuale caso di trasporto fallito, che
ricevono l'impronta «senza frame» (`well_formed=false`). Il live V26.5.6.4 fu
indiagnosticabile proprio perché un caso fallito non lasciava traccia
strutturale.

**Ogni record porta i codici di tutti e tre gli stadi** (S3), più
`validator_schema_censored`, `validator_semantic_measured` e
`validator_semantic_codes`: un fallimento di schema non nasconde più la
superficie semantica.

**Il frame espanso è persistito per ogni frame schema-valido**, non solo per
quelli semanticamente validi. È esattamente il punto cieco di V26.5.6.4, dove
33 casi su 34 morirono senza lasciare nulla da leggere. Resta query-free per
costruzione: in un frame schema-valido **ogni posizione di stringa è un enum o
una const** del registro congelato, quindi non esiste stringa a forma libera.
Il self-test lo verifica su ogni espansione persistita contro un vocabolario
**derivato** (chiavi della forma normale congelata + chiavi del contratto +
enum del registro), non contro una lista scritta a mano.

**I percorsi di errore dello schema sono sanificati.** Un percorso d'errore
JSON-Schema è costruito con le chiavi del frame, e le chiavi le sceglie il
modello: qui sopravvivono solo gli indici di array e i nomi di proprietà che il
contratto dichiara; tutto il resto diventa `?` ed è **contato**. È la stessa
disciplina dell'impronta, applicata a un secondo canale che altrimenti
trasportava materiale di richiesta in un batch dichiarato query-free.

## Quello che NON è stato ereditato, dichiarato

Il **worker sigillato V26.5.5 non è usato**. Esegue l'adapter compatto V26.5,
che non parla questo contratto, e pinna il proprio manifest dentro i propri
byte congelati: ricostruirlo per V26.5.6.6 avrebbe creato nuova superficie di
sicurezza non revisionata. La proprietà che dava è conservata in altro modo e
si dice com'è: **questo runner non esegue mai dati provenienti dal modello**.
Gli unici `compile/exec` caricano byte del repository pinnati per SHA, l'uscita
del modello è decodificata con un decodificatore JSON stretto in dati semplici,
e la condotta che li tratta è Python puro su quei dati.

## Eredità del self-test: perché V26.5.6.3 e non V26.5.6.4

La catena dei self-test eredita **eseguendo** quello del predecessore. Quello
di V26.5.6.4 **non può più passare**: asserisce un invariante di author-time —
«entrambi i gate operativi devono essere assenti» — che la sua stessa esecuzione
live del 9 agosto ha falsificato, perché quei gate sono stati creati, verificati
e consumati. L'ultimo antenato ancora vero è V26.5.6.3, e si eredita quello
(66 controlli). I controlli che V26.5.6.4 aveva aggiunto e che riguardano il
**runner** — e non il suo valutatore — sono reimplementati qui contro questo
bundle.

## I 26 controlli nuovi

| Gruppo | Cosa prova |
|---|---|
| freeze e pre-gate | freeze `PRE_INFRA_REVIEW`, entrambi i gate assenti, pre-gate che nega inferenza, trasporto e live |
| raggiungibilità dei gate | con un gate sintetico presente, i verificatori arrivano ai byte e falliscono sull'envelope; la politica di assenza resta separata |
| contratto | schema derivato **uguale** al materializzato, prompt derivato uguale al pinnato, corpo di richiesta che porta esattamente quei due, `temperature=0`, seme fisso, thinking spento |
| trasporto | solo loopback letterale con porta; doppia apertura, proxy e redirect falliscono chiusi con i contatori esatti |
| batch | 34 record, **1 GET + 34 POST = 35 aperture**, zero retry, envelope che non rivendica accuratezza |
| gold | **0 letture** di qualunque prova gold-side durante l'intero batch (verificato con un audit hook) |
| query-free | un modello ostile che pianta un marcatore in valori, chiavi inventate e famiglie di prova: **0 occorrenze** nel batch, contate come fuori vocabolario |
| percorsi d'errore | una chiave inventata dal modello non entra mai in un percorso persistito |
| forma del record | impronta e tre stadi su ogni record; espansione persistita **solo** se schema-valida e con **0** stringhe a forma libera |
| censura | sul frame da 24 atomi il validator si censura, e la semantica è misurata lo stesso |
| contatori | ogni stadio è contabilizzato (32 schema-validi, 33 misurati, 30 validi, 4 invalidi) |
| output | dirfd ancorato che sopravvive allo scambio del genitore; produttore preflight che esige il gate e non sovrascrive; live bloccato dal gate esterno assente |
| valutatore | accetta il batch finto; **9 batch falsificati respinti con 0 letture gold** |
| CLI | runner e valutatore: errori sanificati su una riga, nessun traceback |
| pin | manifest delle dipendenze e i cinque artefatti del contratto falliscono chiusi se derivano |
| igiene | cap del batch prima della codifica grande, nessun bytecode nel bundle |

Osservazione non deterministica dichiarata: `batch_serialized_bytes_with_newline`
varia di pochi byte fra esecuzioni, perché il batch finto contiene latenze in
virgola mobile. Ogni **controllo asserito** è deterministico.

## Review d'autore (NON indipendente)

Su richiesta esplicita dell'utente — nessun agente indipendente disponibile — la
review l'ha fatta l'autore, e i file lo dichiarano ovunque:
`metnos_v26567_author_static_review.{md,json}` più la sonda deterministica.
Ha trovato **due difetti veri**, entrambi chiusi:

- i codici semantici recuperati dietro un validator censurato non erano
  ricalcolati;
- l'impronta non era mai confrontata con l'espansione — la stessa categoria del
  digest del corpo di risposta che V26.5.6.3 aveva tolto: un'osservazione fatta
  passare per prova.

Sotto il primo c'era un difetto più profondo. Per un frame **schema-invalido**
l'espansione non si salva (query-freedom), quindi quei codici **nessuno** può
ri-derivarli. Ora ogni record **dichiara** `semantic_codes_reproducible` e il
valutatore impone la dichiarazione: vera → ricalcola e confronta; falsa → il
record non deve portare un'espansione e il conteggio finisce nel risultato
(`unreproducible_semantic_records`); incoerente con `schema_ok` → respinta prima
del gold. In una riga: **chi rivendica un successo porta prove ri-derivabili,
chi dichiara un fallimento porta osservazioni e lo scrive.**

Sonda dopo le correzioni: 10 prove, **0 difetti residui**.

## Gate preflight: creato e verificato, **non consumato**

Autorità: **umana**. Nessun agente indipendente era disponibile; Roberto ha
autorizzato il 10 agosto («autorizzo, considera STATIC_PASS come passato»). Gli
artefatti lo dicono in chiaro: `verdict_authority: "user"`,
`independent_review_performed: false`, e la stringa di autorità nel gate è
`user_authorized_v26567_preflight_gate_verifier` — non «independent».

Il gate autorizza **una** cosa e nient'altro:

| Campo | Valore |
|---|---|
| metodo e percorso | `GET /v1/models` |
| endpoint | `http://127.0.0.1:8080` |
| corpo della richiesta | 0 byte |
| tentativi di trasporto | 1 |
| chiamate di inferenza | 0 |
| output | `/tmp/metnos_v26567_transport_preflight.json`, deve essere assente, no-clobber |

Verificatore: `metnos_v26567_transport_preflight_gate_verifier.py`, offline.
Deriva da sé i byte esatti dei tre artefatti e li confronta; poi prova a
romperli: **75 mutazioni**
(25 sul gate, tutte respinte anche dal
runner, 27 sull'autorizzazione,
23 sulla verifica),
**0 fallite**. Rete 0, chiamate al server 0, il
trasporto finto conferma 1 GET, 0 POST, 0 byte di corpo.

**Gate consumato il 10 agosto.** Il preflight è stato eseguito: **PASS**, un
solo socket, HTTP 200, 1 documento JSON decodificato, **0 chiamate di
inferenza**, corpo della richiesta 0 byte, latenza 10.4 ms. Output
`/tmp/metnos_v26567_transport_preflight.json`, 1632 byte,
sha256 `67f7c284615967f38ed39e9920c961da9636061a8206ce17f3588c936dd6cf72`. Da adesso il gate è **esaurito**: il suo contratto esigeva
`output_must_be_absent`, e l'output esiste.

Conseguenza dichiarata, identica a quella di V26.5.6.4: da quando il gate
esiste, il self-test del bundle **non passa più in loco**, perché il suo
controllo `author_freeze_pass_both_operational_gates_absent` asserisce
l'assenza dei gate. Il risultato archiviato
(`metnos_v26567_author_selftest_result.json`, 92/92) è l'ultimo stato in cui
quell'asserzione era vera. Non è un test rotto: è un invariante di author-time
che il passo successivo, per definizione, supera.

| Artefatto del gate | SHA-256 |
|---|---|
| `metnos_v26567_transport_preflight_gate_verifier.py` | `e39ae6cc4e89466b134a74cab5589e3fdf21316f9cf9d82b4216e5c4ca47efd2` |
| `metnos_v26567_transport_preflight_authorization_review.json` | `a7344c57e7fa0612e461438bb7e90fa2630ce662935618ef332618d43701239d` |
| `metnos_v26567_transport_preflight_gate_verification.json` | `ce7602b3e438290aff94868f501685341d2ff9982f54884f3e2cf7cdb3fb412a` |
| `metnos_v26567_preflight_gate.lock.json` | `053f97743856440f301efb5a7d8235cff38c0dc8a96430feb6270b44e93081c9` |

## Limiti dichiarati

- Nessun live, nessun gate, nessun trasporto: `inference_allowed=false`.
- Né il contratto V26.5.6.6 né questo bundle hanno una **review indipendente**:
  la prima è stata saltata dall'utente il 9 agosto, la seconda l'ha fatta
  l'autore su sua richiesta. Le due review indipendenti precedenti trovarono
  ogni volta difetti che l'autore non vedeva: la garanzia mancante è quella.
- Il registro resta **Phase-1 a 10 relazioni**: nulla qui certifica i 109.
- 92/92 offline misura infrastruttura e forma del record, non accuratezza
  semantica del modello.
- Il tetto `max_tokens=2200` è quello ereditato; il frame gold più grande in
  questo contratto occupa **1.770 byte**, cioè circa un quarto del budget.

## Il valutatore

È un comando separato: legge un batch, lo valida **per intero prima** di aprire
qualunque artefatto gold, poi delega il punteggio allo scorer Phase-1
congelato, riusato invariato.

Oltre ai controlli ereditati da V26.5.6.4 verifica, prima del gold:

- l'impronta di **ogni** record, e che ogni sua stringa sia una denotazione
  chiusa — vocabolario **derivato** da registro, contratto e forma normale;
- che un record schema-valido porti la sua espansione, e che **i codici del
  validator siano riproducibili**: li ricalcola e li confronta, quindi un batch
  non può mentire sui propri esiti;
- che nessuna espansione sia persistita per un frame schema-invalido;
- il censimento esatto dei tipi su ogni foglia scalare, contatori riconciliati,
  corpo di richiesta ricostruito dai pin.

Misura: **9 batch falsificati** (stato forgiato, impronta rimossa o alterata,
testo libero nell'impronta, codici forgiati, espansione dove non deve stare,
stadi e contatori forgiati, query piantata in un codice) vengono **tutti
respinti con 0 letture gold**.

## Prossimo passo

I due gate operativi nuovi — preflight ed esterno — e solo dopo il live.

## Hash

| Artifact | SHA-256 |
|---|---|
| `metnos_v26567_k1_runner.py` | `4af76aaa874c46c890ac248b896a20d443f5cd260fb8e73e431b380e5ae57c23` |
| `metnos_v26567_offline_evaluator.py` | `6c6a71ce6fad44ae1d1a4e6e397016787c46ab68efeec45663a746765ec18dcc` |
| `metnos_v26567_offline_selftest.py` | `7cb62ef5acc73a1bb10a35e8a5de25dbdd70388c984a3b92a586986cf24a8ed6` |
| `metnos_v26567_author_pre_gate.json` | `de5b931c02d4842982bdf01a14fb04343884adbab4d77b4801a03e490ada3e5c` |
| `metnos_v26567_author.freeze.json` | `79ecf2c9d130767a58ddaafa037de3b48ee4dd84e6e65c282bc84bd70873852e` |

Il freeze pinna anche i 19 artefatti di runtime e post-batch, il probe del
corpo di richiesta e il cap del batch; il risultato del self-test è pinnato
dentro il freeze.
