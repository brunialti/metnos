# F4-EPOCA-01 · B1 — classificazione e adeguamento dei 12 legami vivi

> Agente B, 31 agosto 2026. Ancora: `80b5037c`.
> Perimetro: classificazione dei 12 legami di O15 e disegno del loro
> adeguamento. Il nucleo del protocollo di transizione appartiene all'agente A;
> questo documento non lo definisce e non lo anticipa.
>
> Fonte dei legami: `internal/design/diagnosi_avvio_nascita_31_8_2026.md`, O15.
> Strumento: `internal/tools/classifica_legami_epoca.py` (prove in
> `internal/tools/prova_classifica_legami_epoca.py`).
> Nessuna modifica al sistema in funzione: tutto qui è lettura.

## 1. Esito in cinque righe

I 12 legami vivi sono **sei fatti sotto due rappresentazioni**: sei nascite
concluse il 30 agosto, ciascuna registrata due volte — come ricevuta di
ammissione nel negozio dei contratti e come ricevuta di produttore nello stato
di nascita. Tutti e dodici vanno classificati **`epoca_storica`**: restano
legati all'epoca sotto cui sono avvenuti. **Zero ripuntamenti**, e non perché
sia comodo: perché ripuntarli sarebbe falso.

## 2. Che cosa sono, misurato

Il classificatore acquisisce il contesto corrente dalla radice di nascita e
attribuisce ogni legame per **regola chiusa**. Sul dato reale:

```
contesto corrente: sha256:f90abe9d64282390ae518b88e4bc9d879ca982e89c1c4b3fe73ed24c6a47f3ec
legami esaminati : 12
buste non interpretabili e non attribuibili a questo contesto: 2

epoca_storica              12
nuova_epoca                 0
cessa_di_essere_corrente    0
non_classificato            0
```

I sei contratti coinvolti sono tutti builtin: `write_entries`,
`read_tasks_history`, `set_skills`, `start_lre`, `set_tasks`,
`set_preferences`.

### Il fatto che decide la classificazione

Ogni ricevuta copre una generazione che **non è più corrente**. E non lo è per
un motivo preciso, che la ricevuta stessa registra: la nascita aveva reso
corrente quella generazione, e oggi il puntatore è tornato su quella
precedente.

| contratto | resa corrente dalla nascita | corrente oggi | esito |
|---|---|---|---|
| `read_tasks_history` | `b3683edf…` | `65f50c70…` | ritornata alla precedente |
| `set_preferences` | `2bc4deb8…` | `cc4058ed…` | ritornata alla precedente |
| `set_skills` | `7ca8c99f…` | `51b488b6…` | ritornata alla precedente |
| `set_tasks` | `bba4b66d…` | `d6504964…` | ritornata alla precedente |
| `start_lre` | `ac969abb…` | `2daef102…` | ritornata alla precedente |
| `write_entries` | `418f0ea4…` | `ac6bc2e9…` | ritornata alla precedente |

Sei su sei. **L'effetto di quelle nascite è stato annullato**: i dodici legami
attestano atti conclusi il cui risultato non è più in vigore.

### Un fatto trasversale che il perimetro A deve conoscere

Misurato sull'intero negozio, non solo sui sei:

```
pubblicazioni totali                        : 123
con almeno una ricevuta di ammissione       :  21
la cui GENERAZIONE CORRENTE ha una ricevuta :   0
```

**Nessun contratto oggi in vigore è stato ammesso dalla porta di nascita.** Le
21 ricevute esistenti (su due contesti distinti) coprono tutte generazioni
superate. È un fatto sul dominio, non una proposta: lo consegno al perimetro A
perché tocca la condizione d'ingresso della transizione, che non è mia.

## 3. La regola di classificazione

Tre futuri ammessi e un rifiuto. La regola è chiusa e non ha un valore di
difetto: ciò che non sa decidere **blocca**.

| classe | condizione | che cosa si fa |
|---|---|---|
| `epoca_storica` | la generazione coperta non è corrente | **niente**: il legame resta com'è |
| `nuova_epoca` | la generazione coperta È corrente | va **riattestata** sotto la nuova epoca prima della transizione |
| `cessa_di_essere_corrente` | la generazione coperta è corrente ma il contratto è in ritiro | la generazione smette di essere corrente; il legame non si riscrive |
| `non_classificato` | qualunque altro caso | **F4 non può essere dichiarata** |

Il rifiuto non è una cortesia. Una dipendenza non classificata è esattamente il
caso in cui dichiarare F4 significherebbe affermare qualcosa che nessuno ha
verificato, ed è per questo che ha un codice d'uscita proprio (`2`).

Una ricevuta di produttore **non è un fatto indipendente**: è il lato nascita
dello stesso commit di cui la ricevuta di ammissione è il lato contratto. Il suo
futuro è quindi quello di quel commit, e lo strumento lo deriva dal gemello
invece di inventare una seconda regola. Se il gemello non si trova, il legame
resta `non_classificato` — non viene indovinato.

## 4. Perché nessun ripuntamento è lecito (la prova richiesta)

La domanda posta a B1 è se un ripuntamento meccanico possa ridurre le guardie
esistenti. La risposta è sì, e per tre ragioni distinte e verificabili.

**(a) Riscriverebbe una provenienza.** Una ricevuta di ammissione dichiara sotto
quale contesto i sette controlli sono stati eseguiti — `approval`,
`authoring_install_journal_v1`, `dependency_closure`, `manifest_lint`,
`manifest_standard`, `properties`, `semantic_review`. Cambiarle il contesto
affermerebbe che quei controlli sono avvenuti sotto la nuova epoca, quando sono
avvenuti sotto la vecchia. Non è una migrazione: è la fabbricazione di una
provenienza. Il rapporto del gruppo 2 lo dice già in un altro punto — «una
differenza dopo l'installazione è un errore, non un invito a riprovare con altri
byte» (§8.2).

**(b) Sarebbe una sostituzione, che il disegno vieta.** Le ricevute vivono nel
negozio dei contratti come documenti finali. §8.1 del rapporto del gruppo 2:
«Non esiste sostituzione di una destinazione finale». Un ripuntamento è
esattamente una sostituzione.

**(c) Non servirebbe a nulla, ed è la ragione che chiude la questione.** Le sei
nascite sono state annullate: nessuna delle generazioni che hanno ammesso è
oggi corrente. Ripuntarle collegherebbe la nuova epoca a sei atti il cui effetto
non esiste più. Si pagherebbe il costo di una falsificazione per un beneficio
nullo.

Il verso opposto vale altrettanto: **conservare** i dodici legami non riduce
nessuna guardia. Sono terminali (`committed`, scaduti il 30 agosto alle 12:17),
non sono consumabili una seconda volta, e non sostengono alcuna generazione in
vigore. Restano ciò che devono essere: la registrazione di che cosa è successo.

## 5. Adeguamento: che cosa fare, per ciascuno dei 12

| # | legame | classe | azione |
|---|---|---|---|
| 1-6 | ricevute di ammissione dei sei contratti, in `contract-publications/v1/*/admission-receipts/` | `epoca_storica` | nessuna: restano immutate |
| 7-12 | righe `birth_producer_receipts` 18-23 in `birth/producer_receipts.sqlite` | `epoca_storica` | nessuna: restano immutate |

**L'adeguamento dei 12 legami è quindi: non toccarli.** Il lavoro non è
spostarli, è **provare** che non vanno spostati — e renderlo verificabile a
ogni esecuzione, invece che deciso una volta a mano.

Il che sposta la domanda dove deve stare: non «come adeguo i dodici legami», ma
**«come verifico, prima di ogni transizione, che non esista un legame che
richiede un'azione»**. Il classificatore è quella verifica, e ha un codice
d'uscita per ciascuna risposta:

```
0  ogni legame resta storico: nessuna azione prima di F4
2  almeno un legame non classificato: F4 NON dichiarabile
3  classificazione completa, ma qualche legame richiede un'azione
1  lo strumento non ha potuto girare
```

## 6. Prove

`internal/tools/prova_classifica_legami_epoca.py`, otto casi su fixture: zero
dipendenze; una su generazione superata; una sulla generazione corrente; un
contratto in ritiro; molte dipendenze miste con classi diverse nello stesso
giro; ricevuta di produttore senza gemello, che deve **bloccare**; busta
illeggibile, che deve bloccare invece di essere ignorata; contesto diverso, che
non deve essere raccolto.

**Un difetto trovato dalle prove, e vale la pena registrarlo.** La prima
versione dello strumento attribuiva una ricevuta di produttore al contesto
cercandone il testo nei byte grezzi della busta. Sul dato reale funzionava — ma
per caso, perché altri campi ripetono il contesto in chiaro, mentre la ricevuta
vera è in base64 e quel testo lì non c'è. Su una fixture minima il filtro non
trovava nulla e sei legami sparivano. Ora l'autorità è la ricevuta decodificata,
il confronto letterale resta solo come rete per una busta che non si riesce a
interpretare, e le buste non interpretabili né attribuibili vengono **contate e
dichiarate** invece di essere scartate in silenzio (sul dato reale ce ne sono 2,
le due ricevute `rejected`).

## 7. Che cosa questo documento NON stabilisce

- Il protocollo della nuova epoca: stati, proprietario normativo, ingresso,
  ripresa, ripetibilità, conservazione della precedente e ritorno controllato.
  È del perimetro A.
- Se e quando la transizione debba avvenire.
- Che cosa fare delle 21 ricevute complessive sugli **altri** contesti: la
  regola le classificherebbe allo stesso modo, ma non le ho misurate una per
  una e non le dichiaro.

---

## 8. Secondo giro — le quattro garanzie chieste dall'agente A

Revisione `MODIFICHE_RICHIESTE` di A su `937ba594`
(`internal/design/revisione_a_adeguamento_legami_epoca_31_8_2026.md`). Tutti e
quattro i rilievi sono accolti. **Il disegno dei tre futuri e la conclusione sui
12 oggetti non cambiano**, come A richiedeva: cambia ciò su cui poggiano.

**R1 — identità composta.** L'indice del gemello era la sola generazione, mentre
il contratto F4 usa già `(contract_id, generation_id)`. Due contratti con lo
stesso digest potevano prendersi il gemello a vicenda. Ora la chiave è la coppia,
e l'identità viene confrontata in **tutti e tre i posti** in cui compare —
percorso, documento e busta: una divergenza è un rifiuto, non qualcosa da
riconciliare preferendo una fonte.

**R2 — nessuna autorità dal chiamante.** `--ritirati` è stato **rimosso**. Il
ritiro proviene ora dalla lapide autenticata del contratto
(`ContractRetirement`), che è una decisione autorevole e non un dato
diagnostico. Una prova verifica anche che l'argomento non sia più accettato.

**R3 — fatti autenticati.** Inventario e generazione corrente arrivano dalle
primitive produttive del negozio — `inventory_store_manifests` e
`current_contract` — invece che da letture dirette di `binding.json` e
`current`. Sul dato reale: **122 contratti su 122 autenticati**, con la radice
d'installazione reale. Un contratto il cui stato corrente non è autenticabile
non riceve una classe: **blocca**.

> **Limite dichiarato, non taciuto.** La firma delle ricevute di ammissione non
> viene verificata: il verificatore appartiene all'autorità di nascita
> sigillata, che durante la transizione non è attiva. Lo strumento lo stampa
> a ogni esecuzione, invece di lasciar credere di aver verificato più di quanto
> abbia verificato.

**R4 — regola terminale chiusa.** Le due buste reali non erano illeggibili:
sono righe `rejected` con `admission_receipt` a `null` e un codice di rifiuto —
rifiuti conclusi correttamente, che non hanno ammesso niente e quindi **non sono
dipendenze**. Ora sono escluse per stato e forma, contate a parte e nominate col
loro codice. Al contrario, una conclusione `committed` con busta invalida che
non ripeta il contesto in chiaro **prima passava in silenzio**: oggi blocca. E un
rifiuto senza codice — cioè senza una ragione — blocca anch'esso.

### Esito reale dopo le quattro correzioni

```
contratti autenticati dall'inventario produttivo: 122
legami esaminati  : 12
rifiuti terminali validi (non sono legami): 2

epoca_storica              12
nuova_epoca                 0
cessa_di_essere_corrente    0
non_classificato            0
```

Undici prove mirate, tutte verdi, fra cui le cinque che A chiedeva per nome:
due contratti con lo stesso digest che non condividono il gemello; identità
discorde fra percorso e documento; stato corrente non autenticabile; rifiuto
terminale valido; conclusione invalida senza testo del contesto.

**La conclusione regge, e ora regge su qualcosa.** Prima era riproducibile sui
byte osservati; adesso poggia su inventario e generazioni autenticati, su
un'identità composta confrontata in tre punti, su un'autorità che il chiamante
non può fabbricare e su una regola terminale senza casi ignorati.

---

## 9. Terzo giro — l'autorità non si legge più da un JSON

Rilievi 5, 6 e 7 di A, tutti accolti. Il difetto comune era uno: **dichiarare un
limite non rende probante un verdetto**. Le chiavi pubbliche erano già
nell'insieme preparato; non usarle era una scelta, non un vincolo.

**R5 — contesto e verificatori dall'insieme.** Una sola acquisizione in sola
lettura (`open_prepared_root_session_v1` + `load_prepared_set_v1` + registro
pubblico, sotto lucchetto condiviso) dà il contesto, la chiave di ammissione e
gli undici registri di produttore, **senza ricostruire da `PATH_RUNTIME`** —
quindi senza dipendere dal terzo ostacolo. Ogni ricevuta passa ora da
`verify_admission_receipt`. Il limite dichiarato al §8 **è ritirato**: non era un
limite, era una lacuna.

**R6 — entrambe le prove della riga.** La riga porta la ricevuta di produttore
firmata in `encoded` e la firma della busta in `terminal_auth`; leggevo solo
`state` e la busta, quindi una riga modificata nel database poteva ancora
passare per conclusione valida. Ora si verificano entrambe, e richiesta, stato,
contratto, generazione e byte dell'ammissione devono concordare fra riga, busta
e gemello verificato.

**R7 — il negozio si attraversa dall'inventario.** Si parte dai riferimenti che
l'inventario produttivo accetta, si pretende **zero problemi**, e la directory
si raggiunge con la primitiva del negozio. Una directory che l'inventario non
possiede blocca, anche se non contiene ricevute del contesto cercato.

### Tre errori miei, trovati eseguendo

- **La scadenza andava verificata al momento giusto.** Passavo l'ora corrente a
  `verify_producer_receipt`, e 23 ricevute concluse il 30 agosto risultavano
  `producer_receipt_expired`. La domanda non è se siano ancora spendibili, ma se
  fossero valide quando sono state usate: l'istante è ora la colonna durevole
  `registered_at`.
- **Non verificabile ≠ invalido.** Il negozio contiene anche ricevute di epoche
  precedenti, firmate da portachiavi che questo insieme non ha. Sono **fuori
  ambito** e vengono contate; diventano un rifiuto solo se il loro contenuto non
  verificato **pretende il nostro contesto**, che è qualcuno che afferma questa
  epoca senza averne l'autorità.
- **Due radici, non una.** I moduli di nascita esistono solo nella linea
  RM-0008; i contratti sono stati pubblicati dall'installazione. Confonderle
  faceva fallire quattro contratti per il solo motivo della radice sbagliata.

E un difetto di robustezza: una ricevuta con byte corrotti faceva **crollare** il
censimento invece di bloccarlo. Ora il dubbio cade dalla parte del rifiuto.

### Esito reale, e una divergenza da dichiarare

```
legami esaminati  : 12          epoca_storica              12
rifiuti terminali autenticati: 0    nuova_epoca                 0
ricevute di altre epoche    : 32    cessa_di_essere_corrente    0
anomalie del negozio        : 2     non_classificato            0
```

**Divergenza dai numeri attesi da A**: A si aspettava «due rifiuti terminali
autenticati». Ne trovo **zero**, e la ragione è verificabile: le due righe
`rejected` non portano alcuna ricevuta di ammissione e sono registrate alle
10:48 e 10:58 del 30 agosto, cioè **prima** che questo insieme esistesse
(13:17). Non sono rifiuti di questa epoca: sono fuori ambito. L'attesa nasceva
dalla lettura non autenticata, dove il contesto compariva altrove nella busta.

**E il censimento oggi BLOCCA**, non per i 12 legami ma per il negozio: la
directory `4e2feabf…` è una **pubblicazione interrotta** del 30 agosto alle
12:48 — nessun `binding.json`, nessun `current`, `generations` vuota, un
`writer.lock`. L'inventario produttivo la segnala come `binding_invalid`. È una
precondizione di F4 che nessuno aveva notato, ed è esattamente ciò che il
rilievo 7 chiedeva di far emergere.

Dieci prove mirate verdi, con finzioni **firmate**: firma di ammissione guasta,
contesto alterato, generazione discorde fra percorso e ricevuta firmata, firma
di produttore guasta, richiesta discorde fra riga e busta, `terminal_auth`
guasta, busta con identità estranea al gemello, directory inattesa senza
ricevute, generazione corrente.

---

## 10. Il censimento legge anche il percorso V2

Rilievo mio (B2 della revisione su `d1c25395`), chiuso da me perché il
censimento è il mio perimetro. Il protocollo di A introduce
`admission-receipts-v2/<generazione>/<contesto>.json` e stabilisce che da quella
transizione in avanti **anche le ammissioni ordinarie** lo usino. Un censimento
che legge solo V1 continuerebbe a rispondere «nulla da fare» mentre la scrittura
avviene dove non guarda: è il solo modo in cui questo strumento può mentire
restando verde.

Ora legge entrambi i tracciati. Nel V2 il percorso porta **due** identità —
generazione e contesto — e nessuna delle due viene creduta: si confrontano con
la ricevuta firmata, e una ricevuta riposta sotto un contesto diverso da quello
che firma è una divergenza, non la ricevuta di un'altra epoca.

**Una conseguenza da dichiarare**: con V1 e V2 sulla stessa generazione, due
ricevute condividono l'identità composta. Sovrascrivere la chiave avrebbe scelto
in silenzio l'ultima arrivata; ora l'accordo è **richiesto**, e un disaccordo
sullo stato della generazione blocca invece di risolversi a caso.

Resta aperta una domanda che non è mia: se una stessa generazione possa avere
insieme una ricevuta V1 storica e una V2 corrente. Il censimento oggi le tratta
come due locazioni distinte dello stesso fatto e non presume la risposta; quando
il disegno la darà, la chiave di identità potrà stringersi.

Quindici prove mirate, verdi. Fra le nuove: ricevuta V2 vista, contesto discorde
nel percorso che blocca, V1 e V2 sulla stessa generazione, seconda esecuzione
identica senza deriva, e molte dipendenze correnti che chiedono tutte la nuova
epoca.

---

## 11. Quarto giro — la catena, il tempo firmato, la tripla

Rilievi 8, 9, 10 e quinto giro di A. Il più grave era il primo, e **l'ho
riprodotto prima di accettarlo**: con firme di ammissione e produttore entrambe
valide, ma con `producer_receipt_hash` inventato e richieste discordanti, il
classificatore rispondeva `{'epoca_storica': 2}`. Falso verde, riproducibile.

**R8 — i tre atti sono una conclusione, o non lo sono.** Autenticavo ricevuta di
ammissione, ricevuta di produttore e busta terminale **separatamente**, e poi le
accoppiavo sull'identità. Ora sono legate: `producer_receipt_hash` deve essere
l'hash reale dei byte Producer della riga, `birth_request_id`, `request_id`
della busta e colonna durevole devono coincidere, e `receipt_hash`,
`issuer_id`, `objective_hash`, `candidate_source_id`, `executor_origin`,
`revision_authorship`, `expires_at` e `result_binding` della riga devono
coincidere con ciò che è firmato. Hash e legame canonico vengono dalle
primitive del prodotto, non ricalcolati qui.

**R9 — il tempo viene dal campo firmato.** Verificavo la scadenza usando
`registered_at`, una colonna **modificabile**: chi può scrivere nel database
decideva se una firma fosse temporalmente valida, e un valore illeggibile
ripiegava in silenzio sull'ora corrente. Ora l'istante è `issued_at` **dentro la
ricevuta firmata**, e un `registered_at` durevole invalido blocca. I percorsi
usano `lstat`: un collegamento a una directory inventariata non può più evitare
il blocco risolvendosi su un percorso posseduto.

**Quinto giro — identità a tripla.** A ha congelato la coesistenza: V1 storica e
V2 corrente sono **atti distinti**, identificati da
`(contract_id, generation_id, admission_context_id)`. Usavo la coppia e
pretendevo che V1 e V2 concordassero: era la fusione di due fatti. Ora solo una
ripetizione della **stessa** tripla deve concordare, e deve concordare byte per
byte. La prova che chiamavo «V1 storica e V2 corrente» non esercitava il caso —
usava lo stesso contesto per entrambe — ed è stata rifatta con due contesti.

**Un difetto mio, due volte lo stesso.** Leggevo `contract_id` con `str()` su un
oggetto `ContractId`, ottenendo il suo repr: nessuna chiave combaciava mai. È lo
stesso errore che avevo già corretto sul lato negozio, ricomparso sul lato
produttore.

Esito reale: **12 legami, tutti `epoca_storica`, zero non classificati**. Il
blocco resta quello del negozio. Quindici prove verdi, con finzioni che ora
costruiscono una catena coerente invece di valori segnaposto.

---

## 12. Sesto giro — i quattro falsi verdi

Rilievi 11-14 di A, con quattro casi indipendenti. Tutti riprodotti, tutti
chiusi.

**R11 — la ricevuta nella busta non era il gemello.** Ricevevo i byte e non li
usavo: due ricevute firmate con la stessa tripla ma byte diversi passavano come
due fatti coerenti. Ora i byte della busta devono essere **quelli** del gemello
autenticato, e le tre richieste devono **esistere** e coincidere, non soltanto
non discordare quando presenti.

**R12 — un campo assente veniva accettato.** I confronti erano condizionali, e
la fixture confermava il difetto *per costruzione*: usava una tabella ridotta che
non conteneva le colonne su cui il controllo doveva cadere. Ora si pretende lo
schema produttivo e l'uguaglianza esatta; la fixture usa la tabella vera. E il
rifiuto non usa più `rejection_code` **oppure** `error_code`: devono esistere
entrambi e coincidere, altrimenti due codici diversi contavano come un solo
rifiuto valido.

**R13 — i percorsi delle ricevute.** `lstat` copriva solo il primo livello del
negozio; le ricevute si leggevano con `glob` e `read_bytes`, quindi una ricevuta
spostata fuori e raggiunta per collegamento passava come storica. Ora ogni
directory V2 e ogni file ricevuta devono essere oggetti regolari, raggiunti
senza seguire collegamenti.

**R14 — il contenuto non verificato decideva il perimetro.** Una ricevuta non
verificabile, posta sulla generazione **corrente** ma con un altro contesto
scritto nei byte, finiva fuori ambito. Ora solo lo stato corrente autenticato
può dimostrare che un oggetto precedente non riduce il lavoro F4: se la
generazione del percorso è quella corrente, il dubbio blocca.

**Un errore di misura mio**, che vale la pena registrare: la prima riproduzione
del caso 1 falliva perché le due ricevute che credevo diverse erano **byte
identiche** — il costruttore di finzioni è deterministico. Non era il difetto a
resistere: era la mia prova a non esercitarlo. Rifatta con due produttori
davvero distinti, il caso cade come deve.

Esito reale invariato: 12 legami, tutti `epoca_storica`, zero non classificati.
Quindici prove verdi più le quattro variazioni indipendenti.

**Resta aperto** dal R13: la verifica terminale usa `json.loads` e la firma, non
la decodifica canonica V2 del prodotto, e la fixture emette ancora
`schema_version: 1`. Serve la primitiva pubblica di decodifica; non la
sostituisco con una seconda implementazione.

---

## 13. Settimo giro — sparire non è bloccare

**R15 — il rilievo più acuto finora.** Avevo corretto «segue il collegamento» in
«non lo censisce», e credevo di aver chiuso. Ma un oggetto saltato **sparisce
lo stesso**: la forma del difetto era cambiata, non il difetto. Ora ogni anomalia
dentro le radici possedute — collegamento, oggetto non regolare, componente non
esadecimale, nome inatteso, `lstat` fallita — viene **riportata e blocca**.

**R16 — `result_binding` dipende dallo stato.** Lo pretendevo sempre, mentre il
Producer store lo impone su `committed` e lo vieta su `rejected`. Conseguenza:
un rifiuto terminale **autentico** veniva dichiarato ignoto. Ora il confronto
segue lo stato, e un `committed` con codice di rifiuto o un `rejected` con
legame bloccano entrambi.

**R17 — le prove devono stare nel commit.** Le quattro variazioni del sesto giro
le avevo eseguite fuori dal file versionato: il checkpoint non era riproducibile
dal solo commit. Ora sono dentro, insieme al caso del rifiuto coerente. **Venti
casi**, tutti verdi.

Esito reale invariato: 12 legami, tutti `epoca_storica`, zero non classificati.

**Resta aperto**, dichiarato: la busta terminale è ancora letta con `json.loads`
più la firma invece della decodifica canonica V2, e le fixture non usano le API
del Producer store (`register`/`claim`/`finalize`). A indica la via — riusare
`_decode_terminal_envelope` con una `BirthRequest` legata alla riga — e resta il
prossimo passo del mio perimetro.

---

## 14. Ottavo giro — la busta la decodifica il prodotto

I due punti strutturali che avevo lasciato aperti sono chiusi.

**Il decoder è quello del prodotto.** La busta terminale non passa più da un
`json.loads` permissivo ma da `_decode_terminal_envelope`, che impone versione
di schema, byte canonici e legame con la richiesta — tre cose che un lettore
indulgente non nota. La `BirthRequest` è legata alla **riga durevole** e al
contratto che la **catena di emissione** nomina (`birth_producer_issuance`), non
a un valore scelto qui. Verificato sul dato reale: tutte le buste sono canoniche
V2 e la misura non cambia.

**Le finzioni emettono la busta produttiva.** `_terminal_envelope` legge dal
nucleo un solo attributo, quindi una controfigura di tre righe basta: le prove
costruiscono ora buste canoniche vere, sia per il commit sia per il rifiuto,
invece di una forma ridotta parallela. Una prova che passa su un formato che il
prodotto non emette non dimostra il contratto dichiarato.

Commenti del nuovo file di prova tradotti in inglese.

**Venti casi verdi; dato reale invariato**: 12 legami, tutti `epoca_storica`,
zero non classificati, blocco sul negozio.

Con questo, per dichiarazione di A, il perimetro B è pronto per B1.

**Un difetto di metodo mio, due volte nello stesso giro.** Ho modificato il
codice due volte con sostituzioni per indice di riga e con espressioni regolari
generalizzate, e due volte ho corrotto un file che ho poi dovuto riparare o
ripristinare. Su codice si sostituisce testo esatto, non forme.

---

## 15. Nono giro — la catena durevole legata per intero

**R18 — la finzione attraversa ora le API vere.** `riga()` costruiva a mano le
due tabelle, senza chiavi, vincoli, versione di schema né migrazione, e
inseriva perfino una forma che lo schema produttivo vieta. Nominare le colonne
non è attraversare le API. Ora il percorso positivo passa da
`get_or_issue_and_claim_producer_receipt` e `finalize_producer_receipt`, che
creano insieme ricevuta, emissione, claim e conclusione; un caso negativo altera
**una sola colonna** del database che quelle API hanno prodotto.

Due dettagli che la finzione ha dovuto imparare dal prodotto: l'istante deve
stare **dentro la finestra di validità** delle ricevute e avere la precisione
che il negozio impone; e una ricevuta corrotta va emessa **valida** e guastata
**dopo**, perché le API rifiutano di emetterne una invalida — che è
esattamente ciò che devono fare.

**R19 — l'emissione lega tutto, non solo il contratto.** Cercavo l'emissione per
`receipt_id` e ne leggevo il solo contratto: la catena durevole era presente ma
non legata, e alterare la sola richiesta dell'emissione passava ancora come
conclusione autenticata. Ora si legge **esattamente una** riga di emissione e si
pretende uguaglianza esatta di `request_id`, `issuer_id`, `objective_hash`,
`candidate_source_id` e byte.

Venti casi verdi; `git diff --check` pulito; dato reale invariato: 12 legami,
tutti `epoca_storica`, zero non classificati.

---

## 16. Decimo giro — la proprietà entra nel commit

Tre adeguamenti, tutti sulla stessa idea: **una proprietà provata da una prova
temporanea non è una proprietà del commit.**

1. **Il caso è ora versionato.** La modifica della sola
   `birth_producer_issuance.request_id`, dopo una costruzione integralmente
   fatta con le API, è il ventunesimo caso: un solo ignoto, zero rifiuti.
   Verificato non vacuo — togliendo il confronto della richiesta, la prova
   diventa rossa con `{'epoca_storica': 2}`.
2. **L'assenza non è accordo.** `birth_producer_issuance.encoded` mancante era
   trattato come «niente da confrontare»; lo schema produttivo lo vieta, e ora
   la sua assenza è una discordanza.
3. **Una mutazione che non avviene rende rossa la prova.** Le alterazioni
   negative inghiottivano `sqlite3.Error`, quindi un caso negativo che non
   riusciva a mutare restava verde sul caso positivo. Ora ogni mutazione
   pretende di aver toccato esattamente una riga.

**Ventuno casi verdi, `git diff --check` pulito, misura reale invariata**: 12
legami, tutti `epoca_storica`, zero non classificati, blocco sul negozio.

Con questo, per i criteri che A ha elencato, il perimetro B è pronto per B1.

---

## 17. Dopo B1 — la primitiva di recupero del contenitore incompleto

Primo lavoro sbloccato del perimetro B, e l'unico che non dipende dall'addendum
di roadmap: il contenitore di prima pubblicazione incompleto è una precondizione
**distinta** dalla transizione di epoca.

`runtime/executor_birth_publication_recovery.py`. La sicurezza sta in ciò che
**rifiuta**:

- il chiamante nomina un **contratto**, mai un percorso, e il contenitore è
  indirizzato dalla chiave di quell'identità: un contenitore che nessun
  contratto rivendica non è raggiungibile affatto;
- forma esatta e nient'altro: niente `binding.json`, niente `current`,
  `generations/` ordinaria e **vuota**, al più un `writer.lock` ordinario, e
  ogni passo letto con `lstat`. Qualunque differenza è un rifiuto, non un caso
  da gestire;
- unica postcondizione ammessa: la rimozione del contenitore vuoto e il `fsync`
  della radice.

**Un difetto trovato scrivendola, e vale la pena registrarlo.** Avevo messo la
verifica **dentro** il lucchetto di scrittura. Ma il lucchetto produttivo
**crea** le directory del contratto: la primitiva avrebbe fabbricato un
contenitore per qualunque contratto e poi rimosso ciò che aveva appena fatto.
Una primitiva di recupero che può creare il proprio soggetto non è un recupero.
Ora la forma si verifica **prima** di ogni lucchetto e si **rilegge** sotto,
perché ciò che era vero un istante fa deve esserlo mentre nessun altro può
cambiarlo.

Dieci prove su copia, verdi, verificate non vacue: forma esatta con e senza
applicazione, `binding.json` presente, `current` presente, `generations` non
vuota, oggetto inatteso, collegamento al posto del contenitore, `generations`
collegata, contenitore assente, e un percorso passato al posto di un'identità.

### Il contenitore reale è raggiungibile, e appartiene a un contratto ritirato

Misurato sull'inventario autoriale (123 manifest, zero problemi): la chiave
`4e2feabf…` è rivendicata da **`retired:reply_messages/manifest.toml`**. La
prima pubblicazione interrotta del 30 agosto riguarda quindi un executor
**ritirato**, e la primitiva può raggiungerla per identità come il disegno
prevede — la proprietà di sicurezza è soddisfacibile, non solo enunciata.

L'uso sul negozio reale resta una decisione operativa separata, fuori
dall'esecuzione automatica F4 e non presa qui.

---

## 18. La primitiva riscritta — cinque proprietà che non aveva

A ha reso riproducibili cinque difetti della prima versione. Erano tutti veri, e
uno era pericoloso.

**R1 — il lucchetto globale stava sul negozio sbagliato.** Passavo `store_root`
al lucchetto del contratto e non a quello di catalogo: i due non serializzavano
lo stesso negozio, e il globale finiva su quello che diceva la configurazione.

**R2 — osservare scriveva.** La modalità dichiarata osservativa prendeva il
lucchetto di scrittura, che **crea** il file: dichiarava «non ho rimosso nulla»
dopo aver creato qualcosa. Ora ispezione e rimozione sono **due ingressi** con
due postcondizioni, e l'ispezione non prende lucchetti.

**R3 — `ContractId` prova la sintassi, non la provenienza.** Il costruttore è
pubblico: chiunque poteva fabbricare un'identità valida, preparare la forma
ammessa e farla rimuovere. Ora serve un'**autorizzazione** che solo
l'inventario autoriale produce.

**R4 — un errore tardivo lasciava uno stato peggiore.** Svuotavo e poi rimuovevo:
un arresto sull'ultimo passo lasciava un contenitore mezzo vuoto che il
controllo di forma avrebbe rifiutato **per sempre**. Ora c'è un solo punto
d'impegno — una rinomina senza sostituzione verso un nome di ritiro — e un
arresto lascia una forma che il tentativo successivo riconosce.

**R5 — il più grave: `lstat` e poi operazioni per nome.** Una sostituzione
sincronizzata fra i due passaggi faceva atterrare le rimozioni **dentro una
directory estranea**. Ora ogni controllo e ogni rimozione passano da descrittori
aperti con `O_NOFOLLOW`, relativi al padre, e l'identità `(st_dev, st_ino)` è
confrontata prima e dopo. Windows è rifiutato esplicitamente prima di ogni
effetto, perché `O_DIRECTORY` non esiste lì.

Quindici prove versionate verdi, comprese le cinque proprietà che A aveva
dimostrato mancanti — portate nel mio file invece di dipendere dal suo, così il
prossimo checkpoint è riproducibile dal solo commit. Non vacue: ripristinando la
risoluzione per nome, la prova del collegamento torna rossa.

---

## 19. Secondo giro sulla primitiva — cinque proprietà nel punto d'impegno

**R6 — la rinomina «senza sostituzione» sostituiva.** Controllavo che il nome
fosse libero e poi usavo `os.rename`, che su POSIX **sostituisce**. Fra i due
passi una directory vuota veniva sovrascritta. Ora si usa `renameat2` con
`RENAME_NOREPLACE`, relativa allo stesso descrittore padre: una collisione
lascia entrambi gli oggetti intatti, e il rifiuto è atomico invece che sperato.

**R7 — il punto d'impegno non era durevole né ripreso.** Nessun `fsync` dopo la
rinomina, e un nuovo tentativo cercava sempre il nome originale, quindi finiva
con «contenitore assente» invece di riprendere. Ora una **ricevuta durevole**
precede il punto d'impegno, la radice è sincronizzata subito dopo, e la matrice
distingue i cinque stati: solo originale, solo ritirato, entrambi, nessuno,
collisione. Un ritirato si riprende **solo con provenienza completa** — un nome
deterministico non prova nulla da sé.

**R8 — la pulizia cancellava una voce mai autorizzata.** Iterava e rimuoveva
tutto ciò che non fosse `generations`: un file comparso dopo la verifica veniva
eliminato e la funzione dichiarava successo. Ora il contenitore ritirato è
riverificato attraverso lo stesso descrittore e la pulizia **nomina** solo
`generations` e l'eventuale `writer.lock`; qualunque altra voce blocca e resta
intatta.

**R9 — l'autorizzazione era fabbricabile.** Il sigillo era un attributo del
modulo: chiunque importasse poteva scrivere `R._TOKEN`. Ora vive in una
chiusura che non è legata ad alcun attributo, e l'autorizzazione porta
l'**identità della radice** contro cui è stata emessa — perché la stessa chiave
seleziona lo stesso contenitore in qualunque radice il chiamante passi. La porta
di prova è separata e nominata.

**R10 — la seconda esecuzione non era idempotente.** Dopo un successo non
restava nulla che distinguesse «già recuperato» da «mai esistito». Ora la
ricevuta lo distingue: una ripetizione restituisce lo stesso esito, e un
contenitore mai esistito resta un rifiuto.

**Un difetto trovato dalle prove, non dal codice.** La ricevuta era indirizzata
per sola chiave, quindi due negozi diversi se la contendevano — e nella suite,
dove gli inode di directory temporanee si riciclano, un caso leggeva la
provenienza di un altro. Ora il nome include l'identità della radice.

**E una prova che non provava.** Il caso della collisione creava il nome
occupato *prima* della chiamata: esercitava il controllo anticipato, non
l'atomicità. Reso sincronizzato — la collisione compare fra la verifica e il
punto d'impegno — diventa rosso se si toglie `RENAME_NOREPLACE`.

Ventitré prove versionate verdi, `git diff --check` pulito.

---

## 20. Terzo giro sulla primitiva — l'autorità, la ripresa, la ricevuta

**R11 — il sigillo era ancora raggiungibile.** Avevo messo il segreto in una
chiusura, ma **l'emettitore** restava un attributo del modulo, e c'era pure una
seconda porta per le finzioni. Un'autorizzazione che chiunque importi il modulo
può fabbricare non è un'autorizzazione. Ora la funzione produttiva **chiude sul
sigillo ed è l'unica** che lo emette; il modulo non esporta né la zecca né una
porta di prova, e le prove sostituiscono **l'inventario**, non la porta.

**R12 — una sospensione durante la pulizia non era riprendibile.** Dopo il punto
d'impegno pretendevo ancora la forma completa, quindi un arresto fra le due
rimozioni rendeva il contenitore irrecuperabile per sempre — l'opposto della
proprietà che il disegno dichiara. Ora dopo l'impegno valgono solo gli stati
**monotoni** realmente raggiungibili: forma completa, forma senza lucchetto,
contenitore vuoto.

**R13 — la ricevuta non identificava il contenitore.** Registrava contratto,
chiave e radice, ma non l'inode: una ricevuta preparata per l'originale
autorizzava la rimozione di un contenitore **diverso** che avesse occupato il
nome di ritiro. Ora l'identità è durevole prima della rinomina e confrontata a
ogni ripresa.

**R14 — «preparata» e «impegnata» erano lo stesso documento.** Con entrambi i
nomi assenti leggevo la ricevuta come successo, anche quando la rinomina era
**fallita**. Ora gli stati sono due: `prepared` prima del punto d'impegno,
`committed` dopo la rinomina e il `fsync`. L'idempotenza vale **solo da
`committed`**; un `prepared` con entrambi i nomi assenti è un fallimento, e
viene detto tale.

Ventisette prove versionate verdi, `git diff --check` pulito. Non vacue:
riammettendo `prepared` come successo, il caso R14 torna rosso.

**E un errore di metodo, il terzo dello stesso tipo.** Ho di nuovo tagliato
codice con uno slicing per indice invece che con una sostituzione di testo
esatto, cancellando tre definizioni che ho poi dovuto ricostruire. È la terza
volta in questa unità: il rimedio non è stare più attento, è non usare più
quello strumento su codice.
