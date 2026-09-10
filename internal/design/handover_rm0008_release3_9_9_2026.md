# Consegna RM-0008 — Release 3 attraversata, e i due difetti che ha rivelato (9-10/9/2026)

> Aggiornato **il 10/9 verso mezzogiorno**. L'attraversamento e' **riuscito**:
> vedi **§6-sexies**, che e' ora la sezione da leggere per prima. Le sezioni
> §6-bis e §6-quinquies restano il racconto di *come* ci si e' arrivati e
> vanno lette solo per capire il perche' delle scelte.
>
> Subito dopo l'attraversamento sono emersi **due difetti veri, entrambi
> preesistenti e nessuno dei due causato dall'attraversamento**: i turni si
> bloccavano a intermittenza sul lucchetto del catalogo, e Telegram non
> riusciva ad avviare la sandbox. Cause trovate, correzioni scritte, in attesa
> del prossimo giro di rilascio (§6-sexies).

Documento per l'agente che subentra. Stato reale al momento della scrittura,
niente promesse: ogni affermazione qui sotto e' stata misurata, e dove non lo
e' stata c'e' scritto.

## 1. Dove siamo in una riga

La Release 3 e' **in esercizio**: attraversata il 10/9 alle 09:38 con
`CUTOVER_OK`, stato `PREFLIGHT_VERIFIED`, `metnos.target` **attivo** per la
prima volta dalle 14:21 del 9/9, tutti i servizi e i due timer su, tre head
pubblicate, nessuna rivendicazione pendente, interprete amministrativo
`/usr/bin/python3.12`. Le correzioni del login e dei cookie sono vive. Restano
due difetti trovati **provando davvero** subito dopo — contesa sul lucchetto
del catalogo e sandbox vietata a Telegram — corretti nel repository e in attesa
del prossimo ciclo di rilascio.

## 2. Ordini dell'utente in questa sessione, nell'ordine

1. riprendere da `handover_rm0008_stop_9_9_2026_1526.md`;
2. aggiungere l'uscita in avanti alla catena invece di tornare indietro;
3. eseguire l'abbandono della Release 2 in esercizio — **fatto alle 17:00**;
4. correggere i cookie in due modi «insieme» (contesti annidati + registrazione
   di cio' che si e' osservato);
5. «dopo passa alla versione 3»;
6. sul login: «correggi adesso», scegliendo esplicitamente di mettere login e
   cookie nella stessa versione, con un solo fermo dei servizi;
7. committare togliendo le due righe finali di attribuzione — **fatto su tutti
   i quattordici commit**;
8. questa consegna.

## 3. Il difetto del login: causa, correzione, prova

### Causa, misurata sulla pagina viva

Turno reale `ffe2a2d6`. Il vero collegamento di accesso di Telepass e':

```html
<a href="https://www.telepass.com/KTI/dashboard" target="_blank"
   aria-label="aria-label-middle_menu" ...><div><div>Accedi</div></div></a>
```

L'etichetta ARIA e' **una chiave di traduzione mai risolta**. Il nome
accessibile del collegamento era quindi `aria-label-middle_menu`, che non
somiglia a «login»: punteggio **0**. Gli unici nodi che dicevano «Accedi»
erano i due involucri grafici interni, senza `href`, identici fra loro:
punteggio 0,78 entrambi. Il risolutore vedeva due cose uguali senza
destinazione, dichiarava ambiguita' e si fermava. In chat: «non ho trovato un
modulo di login nella pagina», su una pagina che il modulo ce l'aveva.

La riapertura del menu non veniva ritentata perche' per quel bersaglio un
tentativo era gia' stato speso (`reveal_attempts`), ed e' giusto cosi'.

### Correzione (commit `d498cad6`)

Generale, deterministica, nessun nome di sito nel codice:

- `session_broker.py`, JS di enumerazione: ogni candidato porta ora anche
  `text`, il testo visibile, limitato a 160 caratteri come il ripiego
  `cursor:pointer` gia' esistente;
- `action_resolver.py`: nuovo `_candidate_names()` — nome accessibile,
  etichetta e testo visibile sono **tutti** nomi del controllo; entrano nel
  mucchio di confronto e nel confronto esatto. Nessuno nasconde l'altro;
- cio' che si mostra a una persona (richiesta di consenso, riga di registro)
  preferisce un'etichetta leggibile invece della chiave di traduzione.

### Prova

Sulla pagina viva, stessa sequenza della produzione: il collegamento passa da
**0 a 1,0**, ambiguita' sparita, e il clic apre davvero la pagina di accesso
con il campo password presente.

Un dettaglio che il prossimo agente deve conoscere: quel collegamento ha
`target="_blank"`, quindi l'accesso **si apre in una scheda nuova su
`login.telepass.com`**, che e' un host diverso da quello di partenza. Il
broker adotta le finestre nuove, ma per un host non ancora consentito prepara
un consenso di ampliamento (`_prepare_resource_expansion`). E' il confine
voluto, non un guasto: attendersi **un passaggio di consenso in piu'** al
primo accesso.

Prove nuove: `tests/runtime/sites/test_etichetta_sbagliata_non_nasconde_il_testo.py`
(4 deterministiche + 1 con browser vero, che si salta con onesta' se il
browser non c'e'). Confine conservato e provato: nomi diversi con destinazioni
diverse **restano ambigui**, e non li scioglie il risolutore.

## 4. I cookie

Stato: la precondizione semantica osserva anche i contesti annidati
(`MAX_OBSERVED_FRAMES = 8`, `MAX_OBSERVED_PANELS = 3`) e registra una riga di
osservazione con soli conteggi, mai testo.

Verificato oggi su una **copia fedele** del pannello che la produzione ha
mostrato nel turno `ed846e59` («Che biscotti vuoi?»): riconosciuto come
pannello cookie, premuto **«Solo necessari»**, pannello sparito.

Verificato anche il contrario, sul sito vero: quando al posto del banner
compare un riquadro pubblicitario, il codice lo vede, lo classifica «non
cookie» e **non lo tocca**.

Attenzione: sul sito vero, dal mio browser, **il banner dei cookie non
compare** — atteso 24 secondi, mai apparso. In produzione compare. Non ho
capito perche' e non ho indagato oltre; e' il motivo per cui la prova e' su
copia fedele e non sul sito.

Difetto **mio** trovato e corretto stasera (commit `d2346b34`): dopo aver
chiuso il pannello, il giro successivo non vedeva piu' nulla e azzerava i
conteggi, cosi' la registrazione non si scriveva proprio nel caso riuscito.
Ora i conteggi sono il massimo osservato nel flusso. **Questa correzione NON
e' nella Release 3**, che era gia' costruita: tocca solo un conteggio
diagnostico, mai la chiusura del pannello, e il caso fallito era gia'
registrato.

Sempre in `d2346b34`: riparata una prova che la mia modifica ai contesti
annidati aveva rotto e che gira **solo con browser vero**
(`METNOS_SITES_SIM=1`), quindi la suite ordinaria non la mostrava rossa. Da
oggi in poi conviene far girare `tests/runtime/sites` almeno una volta con
quella variabile: **454 verdi, 3 saltate**.

## 5. La Release 3

### Come e' stata costruita

L'esportazione e' la stessa proiezione del repository pubblico:

1. `bash scripts/export-public.sh <DEST>`
2. `python -I -S <DEST>/scripts/check_contract_boundary_policy.py`
3. `python internal/tools/rm0008_public_source_review.py public-fs-pin <DEST>
   <PIN_PUBBLICO> <CONTEGGIO> <PIN_PRIVATO>` — questo passo **riscrive** il
   riferimento dentro l'esportazione: senza, il candidato non e' coerente.
4. copia in un percorso stabile, **modi 644/755 e nessun bytecode**.

Due rifiuti incontrati, entrambi corretti e ora prevenuti dal costruttore:
il codice compilato lasciato dalle mie verifiche dentro le sorgenti, e i
permessi di gruppo del repository (il ricevitore accetta solo 644/755).

### Identita' esatte

| voce | valore |
|---|---|
| sorgente ricevuta | `sha256:5d6448ec8b98b4bac7ae86f03c01dac84978c873ea9a88839a7e767e830332ad` |
| build chiusa | `sha256:0f71b2331bbe31e80fb49370c6779ada49d82b6ca219bfa51e93f2b83b201eeb` |
| testa richiesta | `sha256:d302bb32544f2352ad479ae4e529cdc90809be6cb0f7e7e5aaaef22cf3fabe32` |
| descrittore | `b77128fe5333c86ec7d609c05649c8d7c4c2b1b588108db5044d4554d41404c4` |
| controllo amministrativo | `3669ed5fd0887b35670fb7d4152b0ba2e1a40d094d2868a604f21433a5fef739` |
| prove di build | `/var/lib/metnos-admin/rm0008-release3-evidence-20260909` |
| installata in | `/var/lib/metnos/executor-birth/releases-v1/00000000000000000003` |
| costruita dall'albero al commit | `5b146480` |

### La prova che questa versione risolve il blocco

| | Release 2 (abbandonata) | **Release 3** |
|---|---|---|
| `python_executable` nel descrittore | interprete gestito | **`/usr/bin/python3.12`** |
| interprete nelle unita' amministrative | `/var/lib/metnos/python-envs-v1/…` | **`/usr/bin/python3.12`** |

E' esattamente la clausola che la Release 2 non poteva soddisfare.

## 6. Il passo successivo, con i comandi

`internal/tools/rm0008_complete_release3.py`, due modalita'.

```
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_complete_release3.py audit
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_complete_release3.py complete
```

`audit` legge e confronta soltanto: unita' dichiarate uguali a prima, nessuna
modificata a mano, macchina ferma, e **rifiuta subito** se l'interprete
amministrativo non e' quello del sistema operativo. Si puo' rilanciare quante
volte si vuole.

`complete` esegue l'attraversamento vero sotto i lock del prodotto e
**ferma e riavvia i servizi**. Non lanciarlo con un turno in corso (§8.6).

Rispetto al tentativo della Release 2 c'e' **un pezzo in meno fuori dal
prodotto**: la riparazione d'emergenza del controllo amministrativo non serve
piu', perche' il controllo vivo e' ora quello firmato installato dalla
Release 2 (`35b3dc13…`), non piu' quello rattoppato a mano. Il vecchio
riconciliatore in `/tmp` **non va riusato**.

Dopo l'attraversamento, in quest'ordine: salute dei quattro servizi, catena di
proprieta', `metnos.target`, HTTP pubblico, **un turno reale** sul dominio
toccato (§8.5) — cioe' proprio l'accesso a Telepass — poi documentazione,
GII e pubblicazione incrementale con note in inglese.

## 6-bis. L'attraversamento rifiutato: causa, correzione, conseguenza

### Cosa ha detto la macchina

Con la macchina ferma, l'esame e' passato per intero:

```
EXACT_SIGNED_SUCCESSOR_VERIFIED sha256:0f71b233...
ADMINISTRATIVE_PYTHON /usr/bin/python3.12
SUCCESSOR_AUDIT_OK units 12 retirement sha256:c4092078... previous_head sha256:d302bb32...
RELEASE3_REFUSED OwnershipCoordinatorError birth_ownership_request_conflict
  FRAME executor_birth_ownership_coordinator.py 3677 _transition_edge_from_graph_v2
```

Le prime due righe sono la prova che la Release 3 risolve il blocco che aveva
fermato la Release 2. La terza e' il rifiuto.

### Causa: l'uscita in avanti conosciuta solo da meta' del sistema

L'abbandono della Release 2 (ADR 0226) dice che una traversata dimostrata
inattestabile **conserva la testa pubblicata e il proprio ultimo record**, e
che la release successiva si costruisce **sopra** di essa. Quella regola
stamattina e' stata insegnata a tre lettori:

- `_next_release_edge_v1` (costruttore),
- `_successor_claim_for_transition_v2` (rivendicazione),
- la regola del predecessore in `_resolve_ownership_coordinator_at_v2`.

Ne mancavano **tre**, tutti sul percorso dell'attraversamento, e tutti
esigono un predecessore `PREFLIGHT_VERIFIED`:

1. `executor_birth_ownership_coordinator.py::_transition_edge_from_graph_v2`
   — quello che ha rifiutato;
2. `executor_birth_ownership_coordinator.py::_prepared_record_v2` — avrebbe
   rifiutato subito dopo;
3. `birth_authority_provisioner.py::_require_completed_authority_predecessor_v2`
   — raggiunto dall'archiviazione del giornale del predecessore.

Risultato: una release sopra cui **tutti possono costruire** e che
**nessuno puo' attraversare**. Non e' un'uscita, e' un vicolo cieco.

### Correzione (nel repository, non ancora in una release)

- (1) e (2) ammettono un predecessore abbandonato. In (2) la verifica non si
  fida di un booleano: pretende il **documento di abbandono legato esattamente
  a quel record** (`_abandonment_binds_record_v2`), cosi' un abbandono di
  un'altra traversata non puo' fare da passi-passi.
- (3) non viene piu' raggiunto: un predecessore abbandonato **non e'** un
  predecessore completato, quindi il suo giornale non si archivia — e' proprio
  il record veritiero che l'uscita in avanti conserva. Il rifiuto resta intatto
  per un predecessore che non e' ne' completato ne' abbandonato. La domanda
  «questo predecessore e' abbandonato?» si fa al coordinatore sotto il lock
  (`_abandonment_for_predecessor_locked_v2`), non deducendola dallo stato del
  record: dedurla trasformerebbe un rifiuto in un salto silenzioso.

### Prova e stato

Committato in `9a3d35e0`, con lo scopo nuovo dichiarato nell'inventario di
confine e nella politica compilata, proiezione rigenerata e riferimenti
sorgenti riallineati (privato `sha256:0a27b038…`, pubblico `sha256:9180f62d…`).

`tests/portable/test_executor_birth_ownership_coordinator_v2.py::
test_the_crossing_admits_the_predecessor_the_builder_already_admitted`.
**Verificata in negativo**: senza la correzione fallisce con esattamente
l'errore visto stasera; con la correzione passa. Conserva il confine: senza
documento di abbandono il rifiuto e' quello di prima.

### Conseguenza operativa, da non sottovalutare

L'attraversamento gira sul codice **della release installata**
(`sys.path[:0] = [RELEASE, RELEASE/runtime]`), non su quello del repository.
La Release 3 gia' costruita **porta ancora la regola sbagliata**: correggere il
repository non la sblocca. Serve, nell'ordine:

1. **ritirare la rivendicazione pendente** della Release 3 (con una
   rivendicazione pendente il costruttore rifiuta qualunque sorgente diversa:
   vedi `_next_release_edge_v1`, ramo `pending`). Esiste un precedente:
   `/tmp/metnos-rm0008-withdraw-n2-20260909.py`, usato per il tentativo N2 —
   **leggerlo prima, non eseguirlo alla cieca**: e' pinnato su identita' vecchie;
2. riallineare i riferimenti sorgenti (il codice del coordinatore e del
   provisioner e' cambiato) con `internal/tools/rm0008_repin_source_roots.py`;
3. rigenerare e sigillare l'esportazione (§5), ricostruire con
   `internal/tools/rm0008_build_release3.py` **dopo averne aggiornato i quattro
   sigilli** (censimento, numero file, radice rivista, sequenza attesa);
4. aggiornare i sigilli di `internal/tools/rm0008_complete_release3.py` con le
   nuove identita' e attraversare.

Il costruttore verifica la **sequenza attesa**: se dopo il ritiro la catena
offre ancora la 3, resta `EXPECTED_SEQUENCE = 3`; se offre la 4, va aggiornata,
e va aggiornato anche il percorso della release nel completatore.

## 6-ter. Reperti della sessione, uno per riga

Ordinati per gravita'. Ognuno e' stato misurato, non dedotto.

1. **Il login si fermava per un'etichetta ARIA sbagliata del sito.** Il nome
   accessibile del vero collegamento era una chiave di traduzione mai risolta;
   gli unici nodi con scritto «Accedi» erano involucri senza destinazione,
   identici fra loro. Corretto: il testo visibile e' parte del nome di un
   controllo. **Generale**: vale per ogni sito con un'etichetta sbagliata.
2. **L'uscita in avanti era implementata a meta'.** Tre lettori su sei. La
   Release 3 era costruibile e non attraversabile. Corretto e provato (§6-bis).
   *Lezione*: quando una regola vive in piu' lettori, contarli **prima**, e la
   prova che li tiene d'accordo deve enumerare i lettori, non i decodificatori.
   La prova che avevo scritto stamattina teneva d'accordo i due *decodificatori*
   del documento di abbandono: non poteva accorgersi di questo.
3. **L'accesso di Telepass si apre in una scheda nuova su un host diverso**
   (`login.telepass.com`, `target="_blank"`). Il broker adotta la finestra ma
   chiede il consenso per il nuovo host: attendersi **un passaggio in piu'**.
4. **Un pannello dei cookie chiuso veniva contato come mai visto.** I conteggi
   venivano sovrascritti dal giro successivo, e la registrazione di cio' che si
   e' osservato non si scriveva proprio nel caso riuscito. Corretto: i conteggi
   sono il massimo osservato nel flusso.
5. **Una prova rotta invisibile.** La mia modifica ai contesti annidati aveva
   rotto `test_cookie_redaction_preserves_ids_and_json_structure`, che gira solo
   con `METNOS_SITES_SIM=1`. *Regola pratica*: far girare `tests/runtime/sites`
   almeno una volta con quella variabile (**454 verdi, 3 saltate**).
6. **L'elenco firmato dei file Python era indietro di 31 file**, 30 non miei,
   accumulati da sessioni precedenti. Rigenerato.
7. **Il costruttore rifiutava per motivi di messa in scena**, due volte: codice
   compilato lasciato dalle mie verifiche dentro le sorgenti, e permessi di
   gruppo del repository (il ricevitore accetta solo 644/755). Ora il
   costruttore vieta il bytecode e controlla i permessi **prima**, nominando il
   file.
8. **Il banner dei cookie di Telepass non si riproduce fuori dalla produzione.**
   Atteso 24 secondi, mai apparso nel mio browser. Non indagato oltre: la prova
   e' su copia fedele. Chi riprende non ci perda tempo credendo di sbagliare
   qualcosa.
9. **Un'osservazione utile sulla chiave amministrativa**: l'intestazione che
   funziona e' `Authorization: Bearer`, non `X-Admin-Key`.
10. **`sudo` senza password non c'e'** in questo ambiente: cinque prove del
    gruppo 2A non girano per questo, e non sono difetti di prodotto.
0. **URGENTE — da ieri alle 17:00 nessun servizio Metnos puo' PARTIRE**
   (misurato il 10/9). Il verificatore amministrativo vivo
   (`/usr/libexec/metnos/executor-birth-v1/preflight.py`, `35b3dc13…`) e' quello
   installato dalla Release 2 e **non conosce** `abandoned-crossings-v2`: il suo
   inventario ammesso del coordinatore e' `{record legacy, successor-claims-v1,
   transactions-v2, legacy-disposition-v2.json}`. L'abbandono ha creato quella
   directory alle 17:00:28, e da quel momento
   `_attest_service_startup_v1` rifiuta con `coordinator inventory`. **E'
   esattamente la funzione che `ExecStartPre`/`ExecStart` chiamano** (riga 11859
   del verificatore vivo): `check` e `launch` passano di li'.
   I cinque servizi girano perche' sono partiti alle **15:23:50**, prima
   dell'abbandono. **Un riavvio o un solo `restart` adesso lascia tutto giu'.**
   Il codice della Release 3 ammette quella directory: **l'attraversamento e' il
   rimedio**, ed e' anche l'unico. Fino ad allora: non riavviare nulla.
11. **Le sorgenti ricevute non fanno parte di un ritiro** (misurato il 10/9).
    `incoming-v1/sources-v1/` conserva **dieci** sorgenti ricevute dal 2 al 9
    settembre, compresa quella del tentativo N2 che il ritiro precedente non
    aveva toccato. Sono un deposito indirizzato per contenuto: una sorgente
    nuova ha un nome nuovo e non collide. Per questo il ritiro della Release 3
    muove **due** oggetti e non tre, anche se il nome della sorgente compare in
    tre posti.
12. **All'ultimo riavvio vero `metnos.target` NON e' partito** (8/9 18:50,
    letto nel giornale di sistema il 10/9). Causa misurata, non dedotta:
    `metnos-http.service` si e' rifiutato di partire con
    `birth_ownership_preflight_missing` — il bloccante di RM-0008 in persona.
    HTTP e' in `Requires=` del bersaglio, quindi e' caduto il bersaglio, con lui
    la prontezza, e i due timer (`PartOf=metnos.target`) si sono fermati.
    **E' esattamente cio' che la Release 3 risolve.** Il cablaggio invece e'
    giusto: tutte le unita' sono `enabled`, il bersaglio e' tirato su da
    `default.target` ed e' installato dal catalogo firmato — per progetto non
    e' previsto **nessun** passaggio manuale.
13. **Il giornale di nascita `34431385c4…` e' della traversata ABBANDONATA**
    (misurato il 10/9 sul rifiuto reale dello strumento). E' scritto nei record
    della traversata `922c17fd…` (release 2), che si ferma a `HEAD_REQUIRED`.
    L'uscita in avanti lo conserva apposta: archiviarlo sarebbe il salto
    silenzioso che la correzione dell'attraversamento rifiuta. La mia prima
    regola («nessun giornale») era troppo larga e ha fermato il ritiro: il
    fallimento chiuso ha fatto il suo mestiere, ma la regola giusta e' «uno
    solo, e quello». Corretta e provata.
14. **Il bersaglio e' giu' dalle 14:21 del 9/9** perche' il tentativo di
    attraversamento della Release 2 lo ha fermato e l'abbandono e' arrivato
    prima del passo che lo riaccende. L'attraversamento **lo riaccende da solo**
    (`_activate_signed_topology_v1`) e rifiuta di dichiarare successo se
    bersaglio e prontezza non risultano attivi: non c'e' un difetto da
    correggere, c'e' una traversata da finire.
16. **L'uscita in avanti aveva un OTTAVO lettore, e l'ha creato la correzione
    del 9/9.** Vietando di archiviare il giornale del predecessore abbandonato
    *come completato* — giusto — non si era detto cosa farne. Restava nell'unica
    casella attiva; la traversata successiva adottava quel giornale e rifiutava
    con `birth_provisioning_transaction_conflict`. **Regola imparata**: quando si
    toglie un permesso, dire subito che cosa succede al posto suo, altrimenti si
    e' spostato il blocco, non risolto. Corretto in
    `_archive_abandoned_authority_journal_v2`, provato in negativo.
17. **Il rituale di rilascio era esso stesso un difetto** (10/9). Sette passaggi
    manuali, sei impronte ricopiate a mano, tre cicli persi in un giorno.
    Sostituito da un comando solo (§6-quinquies). *Resta aperto* il criterio di
    Roberto: dev'essere una capacita' del prodotto, non uno strumento da
    sviluppatore (§9.4).
18. **Un rilascio non si prova finche' non si usa il prodotto** (10/9). I due
    difetti di §6-sexies non li ha trovati nessuna suite: sono usciti al primo
    turno reale dalla chat e al primo messaggio da Telegram, entrambi entro
    dieci minuti dall'attraversamento. Entrambi **preesistenti**, entrambi
    nascosti da un servizio che era giu'. Rimettere in piedi il bersaglio non
    ha creato i difetti: **ha smesso di nasconderli**.
19. **Una lettura non deve prendere un lucchetto di scrittura** (10/9). E' la
    forma generale del primo difetto: il caricamento del catalogo era gia'
    protetto da solo (impronta prima e dopo, rifiuto esplicito se lo store si
    muove), e il lucchetto esclusivo non aggiungeva correttezza — toglieva
    disponibilita' a chiunque leggesse nello stesso momento.
20. **Un servizio non puo' vietare cio' su cui si regge** (10/9). Forma
    generale del secondo: la sandbox degli executor **e'** un insieme di spazi
    dei nomi. `RestrictNamespaces` su un servizio che esegue turni non aggiunge
    un secondo confinamento, toglie l'unico che c'e'. Ora e' una prova che lo
    vieta a qualunque servizio del catalogo.
15. **La catena e' leggibile senza `sudo`.** Tutto `/var/lib/metnos/executor-birth`
    e' `root:root 755/644`, con **una sola** eccezione: il lucchetto
    `chain-v1/.required-head-v1.lock`, `600`. Un censimento di verifica si puo'
    quindi fare da utente normale — ed e' cosi' che sono stati congelati i
    sigilli del ritiro, senza chiedere a Roberto un giro di `sudo` in piu'.

## 6-quater. Cosa resta da fare, in ordine

**Aggiornato il 10/9 dopo l'attraversamento riuscito.** I quattro strumenti a
colpo singolo sono stati **ritirati** e sostituiti da uno solo:
`internal/tools/rm0008_release_cycle.py`. I passi 1-2 sono **fatti**; restano
i seguenti, in quest'ordine.

1. ~~`prepare` + `apply --cross` per la Release 3~~ — **fatto il 10/9**,
   esito in §6-sexies.
2. **Rilasciare le due correzioni vive** (§6-sexies): lucchetto del catalogo e
   sandbox di Telegram. Stesso ciclo, due comandi:

   ```
   # agente, senza sudo
   /opt/metnos/.venv/bin/python /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py prepare
   # Roberto, dal suo terminale
   sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py apply --cross
   ```

   Sara' anche la **prima misura vera** di quanto costa una modifica ordinaria
   ora che il rituale e' sparito: se costa piu' di due comandi, il difetto e'
   nel ciclo, non nella modifica.
3. **Turno reale** su Telepass (§8.5) — la frase esatta e' `accedi a telepass`.
   Da rifare **dopo** il punto 2, e da rifare **anche da Telegram**, che oggi
   non puo' eseguire nulla.
4. **Prova generale dell'attraversamento**: far girare l'attraversamento vero
   su una copia della catena, fermandosi prima della cucitura con systemd,
   com'e' gia' fatto per il ritiro. I tre rifiuti del 10/9 sarebbero costati
   secondi invece che cicli interi in produzione. **Promesso, non ancora
   scritto.**
5. **Riavvio vero**: l'unica prova onesta del «tutto disponibile senza
   interventi a mano» (reperto 12).
6. Documentazione IT/EN, roadmap, GII, pubblicazione incrementale in inglese.
7. Decidere sul sigillo di `tests/portable/conftest.py` (§7) e sull'accesso di
   Roberto alle directory d'installazione (§9).

## 6-quinquies. Il ciclo unico: perche' esiste e cosa non e' ancora

### Il rituale era il difetto

Ogni modifica al codice di nascita chiedeva **sette passaggi manuali con sei
impronte ricopiate a mano**: riallineare le radici, rigenerare l'elenco firmato,
rigenerare e sigillare l'esportazione, metterla in scena, riscrivere il
censimento nel costruttore, ritirare la rivendicazione, riscrivere le identita'
della build nello strumento di attraversamento, attraversare.

Il 10/9 sono andati persi **tre cicli completi**, e nessuno dei tre per un
difetto vero: due per lettori mancanti dell'uscita in avanti, uno per il
rituale stesso. Un'impronta che una persona ricopia non e' una revisione: e'
un'occasione per sbagliare. La revisione che conta e' quella dell'albero
sorgente, e si esprime una volta sola, eseguendo il ciclo.

### Cosa garantisce, e cosa no

Il passaggio di consegne fra `prepare` e `apply` **non e' un'autorita'**:
l'albero in scena viene rimisurato da root, il controllo della radice rivista
del prodotto gira comunque dentro la costruzione, e la distribuzione la firma
la chiave del prodotto. Il passaggio serve solo a rifiutare un albero cambiato
fra le due fasi.

L'attraversamento gira come **figlio dello stesso file**: la costruzione deve
importare il codice del candidato e l'attraversamento quello della release
appena installata, e i due non stanno in un solo interprete. Le identita'
arrivano al figlio come **argomenti prodotti dalla costruzione**, mai come
costanti che qualcuno mantiene.

Il ritiro conserva tutte le proprieta' della versione sigillata — due oggetti,
la rivendicazione per ultima, niente cancellato, storia conservata e
attestazione dei servizi invariate — ma **legge ogni identita' dalla catena al
momento**. La sua prova generale
(`internal/tools/rm0008_rehearse_withdrawal.py`) fa girare il codice vero su una
copia fedele della catena viva, senza root: due oggetti spostati, dieci
conservati identici, la stessa sorgente non e' un ritiro, un secondo giro non fa
niente, tre rifiuti attesi su tre.

### Cosa NON e' ancora, e va deciso

Questo resta uno strumento **per chi sviluppa**: un comando con `sudo` da un
albero di lavoro. Roberto ha posto il criterio giusto — *«robusta, semplice,
automatica e trasparente all'utente, altrimenti Metnos e' inutilizzabile»* — e
**questo non lo soddisfa ancora**. L'aggiornamento di Metnos deve diventare una
**capacita' del prodotto**: si chiede dall'interfaccia o dalla chat, il prodotto
si aggiorna da solo e riferisce l'esito, senza terminale, senza radice
d'installazione, senza impronte. E' il vero criterio di chiusura di RM-0008 ed
e' un lavoro di progettazione, non un ritocco: vedi §9.4.

## 6-sexies. L'attraversamento riuscito, e i due difetti che ha rivelato

### L'esito, verbatim

Il 10/9 il ciclo unico ha risposto:

```
CUTOVER_OK {"closed_build_id": "sha256:efeffa1e…",
            "cutover_id": "sha256:e5ca0c06…",
            "readiness_unit": "metnos-stack-ready.service",
            "request_id": "sha256:22adf6d9…",
            "state": "PREFLIGHT_VERIFIED",
            "target_unit": "metnos.target"}
```

Verificato **dopo**, non dedotto: `metnos.target` attivo (prima volta dalle
14:21 del 9/9), tutti i servizi del catalogo attivi, i due timer attivi, tre
head pubblicate, nessuna rivendicazione pendente, interprete amministrativo
`/usr/bin/python3.12`, HTTP `operational: true`, correzioni di login e cookie
vive nella release in esercizio.

Il ciclo ha richiesto **tre tentativi**, e nessuno dei tre e' fallito per la
regola della catena: archivio nominato per la head invece che per il tentativo,
poi un giornale orfano lasciato da un tentativo precedente. Entrambi difetti
**miei**, entrambi corretti dentro lo strumento, entrambi coperti dalla prova
generale del ritiro. La lezione e' quella gia' scritta in §6-bis e vale ancora:
*quando si toglie un permesso, dire subito che cosa succede al posto suo*.

### Difetto vivo 1 — il guardiano teneva in ostaggio il catalogo

**Sintomo.** Dalla chat, `open_sites` (il turno Telepass) falliva circa **un
turno su tre** con «una o piu' dipendenze non sono disponibili», mentre
`che ore sono` passava. Nessuna differenza fra i due: **solo tempismo**.

**Causa, misurata al secondo.** Nel giornale del servizio HTTP:

```
contract_store.ContractStoreError: catalog_lock_timeout:
  …/.contract-publications-v1.catalog-admission.lock
  ← runtime/loader.py:1436 load_catalog
  ← runtime/agent_runtime.py:3448 _admitted_code_dependency_projection
```

`metnos-stack-watchdog.timer` parte **ogni 2 minuti**; ogni giro dura 36-43 s e
il suo controllo del catalogo (`duration_ms` letto dal giornale: 3,2-3,3 s di
norma) prendeva il **lucchetto di ammissione in modo esclusivo**. Il turno
aspetta 5 s e rinuncia. Le due finestre coincidono esattamente: guardiano
09:44:54→09:45:37, con quel giro salito a **6.877 ms**; turno `edc10ae9`
09:45:24→09:45:29, `run_ms=5013`.

**Perche' sembrava nuovo.** Il guardiano era **giu' dal 9/9 alle 14:21**
insieme al bersaglio. L'attraversamento lo ha riacceso — correttamente — e il
difetto, che c'era gia', e' tornato visibile. **Non e' una regressione
dell'attraversamento.**

**Correzione (nel repository, non ancora in una release).** Il difetto vero non
e' il guardiano: e' che **una lettura prendeva un lucchetto di scrittura**, per
cui due lettori si escludevano a vicenda. Il confine del catalogo ha ora due
meta':

- `contract_store.catalog_admission_lock(exclusive=False)` — la meta' del
  lettore: esclude ancora ogni pubblicazione, non esclude piu' un altro
  lettore. In processo la disciplina e' la stessa (`_ProcessRWLock`), e uno
  scrittore in attesa blocca i nuovi lettori, cosi' una fila di letture non
  puo' affamare una pubblicazione. Un lettore che rientra chiedendo la
  scrittura sarebbe una promozione di lucchetto — si rifiuta, invece di
  bloccarsi.
- `loader.load_catalog` e l'esame a freddo del passaggio la usano. Tutto il
  resto (firma, pubblicazione, riconciliazione, ritiro) resta esclusivo.

E' sicuro perche' la lettura era **gia'** protetta da sola: il caricamento
calcola l'impronta dello store prima e dopo e accetta il catalogo solo se
coincidono, altrimenti riprova e infine rifiuta (`store_snapshot_unstable`).
Il lucchetto esclusivo non aggiungeva correttezza a un lettore: toglieva
disponibilita'.

Prove nuove in `tests/runtime/contracts/test_contract_store.py`: due processi
leggono insieme, e con lo stesso codice un lettore **scade** se il confine e'
tenuto da uno scrittore (verificata anche in negativo); la promozione da
lettore a scrittore e' rifiutata, la direzione opposta resta lecita.

### Difetto vivo 2 — Telegram vietava a se' stesso la sandbox

**Sintomo.** Da Telegram, anche `che ore sono` rispondeva:
`bwrap: No permissions to create new namespace`.

**Causa.** L'unita' `service-telegram-daemon` dichiarava
`RestrictNamespaces=yes`. Il demone Telegram esegue `run_turn` **in processo**,
come il server HTTP: ogni executor gira in una sandbox, e una sandbox **e'** un
insieme di spazi dei nomi nuovi. Vietarli a livello di unita' non aggiunge un
secondo strato di confinamento: **toglie l'unico che c'e'**, e il turno muore
prima dell'executor.

**Non e' una regressione dell'attraversamento**: la direttiva sta identica nel
catalogo della Release 2 e in quello della Release 3. `service-http` e
`service-durable-worker`, che ospitano la stessa identica catena, non l'hanno
mai avuta. Era una **copia** ereditata dalla vecchia unita' scritta a mano, mai
una politica.

**Correzione.** Direttiva rimossa dalla sorgente del catalogo e dalla vecchia
unita' nel repository, con il motivo scritto accanto. La regola e' tenuta da una
prova generale — `test_no_service_forbids_the_namespaces_its_own_sandbox_needs`
— che vieta `RestrictNamespaces` a **qualunque** servizio del catalogo: un
servizio che non deve eseguire executor si esprime con cio' che esegue, non
rompendo la sandbox.

**Il sigillo della topologia si e' mosso, ed e' il meccanismo che funziona.**
Cambiare il catalogo muove per costruzione
`_EXPECTED_SERVICE_SOURCE_IDENTITY_V1` in
`runtime/executor_birth_admin_preflight.py`: e' il riferimento rivisto
dell'**unica** topologia firmata che il verificatore ammette, e riscriverlo
**e'** l'atto di approvare la modifica. Nuovo valore
`sha256:9ea904e1…` (prima `sha256:ad3854f5…`), ricalcolato con una sonda in
sola lettura e verificato dalle prove che lo vogliono indipendente da casa del
servizio e interprete amministrativo (sei combinazioni, tutte verdi).
Da sapere per non spaventarsi: l'aiutante amministrativo vivo
(`/usr/libexec/metnos/executor-birth-v1/preflight.py`) **viene sostituito
dall'attraversamento** — quello attuale porta la data del 10/9 09:41 — quindi
dopo il passaggio sigillo e catalogo restano d'accordo.

### Cosa manca ancora, su questi due

Entrambe le correzioni sono nel worktree e **non nella release in esercizio**.
Partono col ciclo ormai a due comandi: `prepare` (agente) + `apply --cross`
(Roberto). Fino ad allora Telegram resta senza executor e il turno Telepass
riesce circa due volte su tre, fuori dalla finestra del guardiano.

## 7. Prove rosse, classificate con onesta'

Suite runtime completa: **91 rosse su 8652**, tutte preesistenti a questa
sessione (contracts 70, i18n 7, infra 6, tutor 4, executors 3, skills 1).
**Zero** nel dominio siti.

Suite portable a fine sessione: **2506 verdi, 6 rosse, 43 saltate**, di cui

- **5** chiedono `sudo` senza password per cambiare proprietario a file finti:
  limite d'ambiente, non difetto di prodotto;
- **1** era l'elenco firmato dei file Python, rimasto indietro di 31 file (30
  non miei): **rigenerato**, ora verde. Dichiarare uno scopo nuovo muove per
  costruzione quattro valori congelati (digest della proiezione, inventario
  reso, numero dei rilievi 161→162 e loro digest): aggiornarli **e'** l'atto di
  approvare lo scopo, e in `ef26477b` non c'e' nient'altro;
- **1** resta rossa **di proposito**: il sigillo di
  `tests/portable/conftest.py`. Quel file ha preso due righe con il lavoro
  ereditato (`329d51b3`) che aggiungono `tests/portable` al percorso di
  importazione. Rifissare il sigillo **approva una modifica all'avvio delle
  prove**: e' una decisione da mettere a verbale, non un numero da rinfrescare
  di passaggio. **In attesa di Roberto.**

Un chiarimento per chi legge le note della notte: era stata indicata come rossa
anche `test_executor_birth_distribution_release.py::
test_current_reviewed_source_assembles_and_passes_static_verification`.
**E' verde** — l'aveva gia' chiusa il riallineamento dei riferimenti sorgenti
(`45ac6e5f`), che e' arrivato dopo quella nota. Rieseguita il 10/9: **8 verdi**.

## 8. Commit di questa sessione (worktree `rm0008-reboot`, ramo `codex/rm0008-reboot`)

Base `8519edb1`. **Tutti senza le due righe finali di attribuzione** (riscritti
su richiesta esplicita):

```
329d51b3 cookie semantici + confine di avvio dei servizi   (lavoro ereditato)
a6cbe2b5 interprete amministrativo + uscita in avanti
5b83e945 abbandono della release 2 registrato
1ef63b87 .. 15ec2086  sei strumenti di diagnosi dei turni
d498cad6 il testo visibile fa parte del nome di un controllo   <- login
5b146480 riallineamento riferimenti + preparazione build 3
18832452 strumento di attraversamento della release 3
d2346b34 un pannello chiuso resta un pannello visto           <- conteggi
45ac6e5f riallineamento riferimenti dopo la correzione
2cc0c0b6 questa consegna
9a3d35e0 l'attraversamento legge l'uscita in avanti come il costruttore
6f22560d il rifiuto dell'attraversamento, la causa e i reperti
55ed809b ADR 0226: l'uscita in avanti ha sette lettori, non quattro
a4d4fe2d la sonda nomina cosa dovrebbe muovere un ritiro
ef26477b approvazione degli esiti congelati mossi dallo scopo nuovo
3df76f9f chiusura della consegna sullo stato misurato della suite
--- 10/9, la ricostruzione e l'attraversamento (§6-quinquies, §6-sexies) ---
9bd360ea strumento di ritiro della rivendicazione della Release 3
b2999c20 la crociera abbandonata conserva il proprio giornale di nascita
60b4d6c8 costruttore puntato sulla seconda esportazione rivista
0109d9b3 il ritiro registra cio' che dice l'aiutante, rifiuto compreso
ef14fb2a la ricostruzione preparata e cio' che ha misurato l'operatore
8a5f4a7c completatore armato sulla Release 3 ricostruita
d5416649 una crociera abbandonata ritira il giornale invece di bloccare
c3e8a193 un solo comando di ciclo, senza impronte ricopiate a mano
2eab08e0 elenco firmato dei file Python dopo il ritiro degli strumenti
f33cbe58 l'ottavo lettore, e il rituale che era esso stesso il difetto
6714383a la politica della catena lega il predecessore conservato
ac8f5841 l'archivio del ritiro prende il nome dal tentativo, non dalla head
959b275e un tentativo fallito lascia anche un giornale, e va ritirato
--- 10/9, non ancora committato (§6-sexies) ---
         il confine del catalogo ha una meta' per il lettore
         nessun servizio vieta gli spazi dei nomi che la sua sandbox usa
         questa consegna
```

Riferimenti sorgenti **del candidato** nel worktree (10/9, dopo `prepare`):
privato `sha256:0fe09130…` (755), pubblico `sha256:e5745238…` (743).
**Non sono quelli della release in esercizio**: la Release 3 porta la propria
copia firmata (`sha256:0a27b038…` privato, `sha256:9180f62d…` pubblico) e
resta valida com'e'. Questi appartengono alla versione successiva, quella con
le due correzioni di §6-sexies. Chi legge deve tenere distinti i due:
candidato e release installata non coincidono mai durante un ciclo aperto.

## 9. Decisioni aperte per Roberto

1. **Rilasciare le due correzioni vive** (§6-sexies) col ciclo a due comandi.
   Fino ad allora Telegram non esegue nulla e il turno Telepass riesce circa
   due volte su tre. L'attraversamento ferma e riavvia i servizi: va fatto
   quando non c'e' un turno in corso.
2. **Sigillo di `tests/portable/conftest.py`** — vedi §7.
3. **L'aggiornamento di Metnos deve diventare una capacita' del prodotto.**
   Criterio di Roberto, 10/9: *«robusta, semplice, automatica e trasparente
   all'utente, altrimenti Metnos e' inutilizzabile»*. Oggi c'e' un comando solo
   invece di sette passaggi, ma resta un comando con `sudo` da un albero di
   lavoro. Il fine e': lo si chiede dall'interfaccia o dalla chat, il prodotto
   si aggiorna e riferisce l'esito. E' progettazione, non un ritocco, e va messa
   in roadmap come criterio di chiusura di RM-0008.
4. `internal/tools/grant_roberto_access_to_install_root.sh` — scritto,
   approvato in linea di principio, **mai eseguito**. Rimedio provvisorio: la
   vera correzione e' separare la radice d'installazione dall'albero di
   sviluppo, cosa su cui Roberto e' d'accordo.

## 10. Vincoli operativi da non riscoprire

- I comandi `sudo` li lancia Roberto dal suo terminale: il prefisso `!` non
  offre un terminale e `sudo` non puo' chiedere la password.
- Il lettore dei turni e' installato e non chiede password:
  `sudo -n /usr/bin/python3.12 /usr/local/lib/metnos-diagnostics/inspect_sites_turn.py <turno>`.
- `session_broker.py` e i moduli di confine: un edit sgancia i processi vivi
  del sidecar (impronta di contratto). Con la Release 3 il punto e' risolto
  dall'attraversamento.
- Comunicazione con Roberto: parole semplici, esito prima di tutto, niente
  log grezzi; note Git e GitHub in inglese, conversazione in italiano.
- Mai riportare la password di Roberto in file, comandi o registri.
- In questo albero di lavoro **non c'e' `.venv`**: `scripts/export-public.sh` va
  lanciato con `METNOS_VENV=/opt/metnos/.venv`, altrimenti si ferma subito.
- La catena si legge senza `sudo` (reperto 15): un censimento di verifica non
  richiede un giro di privilegi.
- Quattro strumenti a colpo singolo (`rm0008_build_release3`,
  `rm0008_complete_release3`, `rm0008_withdraw_release3`,
  `rm0008_rehearse_release3_withdrawal`) sono stati **rimossi**: se li trovi
  citati in un documento piu' vecchio, il sostituto e' `rm0008_release_cycle`.
