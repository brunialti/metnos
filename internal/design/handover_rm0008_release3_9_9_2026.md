# Consegna RM-0008 — Release 3 costruita, attraversamento bloccato e corretto (9-10/9/2026)

> Aggiornato **la notte del 10/9**: la ricostruzione e' **preparata e pronta**,
> non eseguita. Vedi **§6-quinquies** per cosa e' stato preparato e
> **§6-quater** per l'ordine dei passi che restano.
>
> La sezione **§6-bis** resta la piu' importante per capire *perche'*:
> l'attraversamento e' stato rifiutato dalla macchina, la causa e' stata trovata
> e corretta nel repository, ma **la Release 3 gia' costruita porta ancora la
> regola sbagliata** e va rifatta.

Documento per l'agente che subentra. Stato reale al momento della scrittura,
niente promesse: ogni affermazione qui sotto e' stata misurata, e dove non lo
e' stata c'e' scritto.

## 1. Dove siamo in una riga

La Release 3 e' **costruita, firmata e installata** accanto a quella in
esercizio, e il suo descrittore dichiara finalmente l'interprete giusto. Il
tentativo di attraversarla e' stato **rifiutato dalla catena**: l'uscita in
avanti introdotta stamattina era stata insegnata al costruttore ma non ai
lettori che attraversano. Causa trovata, corretta e coperta da prova nel
repository; la release costruita porta pero' ancora il codice vecchio, quindi
va ritirata la rivendicazione e ricostruita. **Tutto cio' che serve per farlo
e' pronto** (§6-quinquies): manca solo il via di Roberto e tre comandi.
Produzione **mai toccata**: zero riavvii, servizi tutti su.

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
15. **La catena e' leggibile senza `sudo`.** Tutto `/var/lib/metnos/executor-birth`
    e' `root:root 755/644`, con **una sola** eccezione: il lucchetto
    `chain-v1/.required-head-v1.lock`, `600`. Un censimento di verifica si puo'
    quindi fare da utente normale — ed e' cosi' che sono stati congelati i
    sigilli del ritiro, senza chiedere a Roberto un giro di `sudo` in piu'.

## 6-quater. Cosa resta da fare, in ordine

I passi 1-3 sono **preparati**: strumenti scritti, sigilli congelati,
esportazione rigenerata. Restano tre comandi con `sudo`, che lancia Roberto.

1. **Ritirare la rivendicazione pendente.**
   `internal/tools/rm0008_withdraw_release3.py`, tre modi:
   `census` (qualunque utente, sola lettura, nessun lucchetto — gia' eseguito:
   i sigilli dentro il file sono il suo risultato), `audit` e `withdraw` (da
   root, sotto i tre lucchetti del prodotto). Muove **due soli oggetti**, la
   rivendicazione **per ultima**: finche' c'e', la porta resta chiusa, quindi
   un'interruzione lascia un rifiuto e non una catena socchiusa. **Non ferma
   nessun servizio** e non cancella niente: i due oggetti vengono conservati
   sotto `/var/lib/metnos-admin/rm0008-withdrawn-release3-20260910`.
2. **Ricostruire.** L'esportazione e' gia' rigenerata, sigillata e messa in
   scena in `/tmp/metnos-release3-export-2` (modi 644/755, niente bytecode,
   niente collegamenti); i quattro sigilli del costruttore sono gia' aggiornati.
   Serve solo lanciare `internal/tools/rm0008_build_release3.py`.
3. **Attraversare.** La ricostruzione stampa le identita' nuove: vanno messe nei
   sigilli di `internal/tools/rm0008_complete_release3.py`, che oggi sono
   **volutamente vuoti** e rifiutano per nome (`pins not updated after the
   rebuild: …`). Poi `audit` e infine `complete`, che **ferma e riavvia i
   servizi**: non con un turno in corso (§8.6), e non senza il via di Roberto.
4. **Turno reale** su Telepass: e' la verifica richiesta da §8.5 ed e' cio' che
   Roberto aspetta.
5. Documentazione IT/EN, roadmap, GII, pubblicazione incrementale in inglese.
6. Decidere sul sigillo di `tests/portable/conftest.py` (§7) e sull'accesso di
   Roberto alle directory d'installazione (§9).

### I tre comandi, in fila

```
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_withdraw_release3.py audit
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_withdraw_release3.py withdraw
sudo /usr/bin/python3.12 /opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_build_release3.py
```

`audit` si puo' rilanciare quante volte si vuole: legge e confronta soltanto.

## 6-quinquies. Preparazione della notte del 10/9 (catena non toccata)

Nessun oggetto della catena e' stato spostato, nessun servizio fermato,
nessun `sudo` eseguito. Tutto quello che segue e' lavoro nel repository e in
`/tmp`.

### Lo strumento di ritiro (nuovo)

`internal/tools/rm0008_withdraw_release3.py`. Nasce dal precedente del
tentativo N2 (`/tmp/metnos-rm0008-withdraw-n2-20260909.py`), che era sigillato
su identita' vecchie: l'architettura e' la stessa, le identita' e la topologia
no. Differenze reali:

- **due oggetti invece di quattro**: l'attraversamento della 3 si e' fermato al
  primo lettore, quindi non esiste nessuna transazione. Verificato leggendo
  `transactions-v2`, che contiene ancora **solo** le due traversate note
  (release 1 e release 2 abbandonata);
- il giornale di nascita non si **sposta** mai. Ne esiste **uno**, ed e' quello
  della traversata abbandonata: l'uscita in avanti lo conserva apposta. Lo
  strumento legge dalla traversata abbandonata **quale** sia, invece di
  sigillarlo, e pretende che sia l'unico. Un secondo giornale apparterrebbe alla
  3 e verrebbe **rifiutato per nome**, non indovinato (reperto 13);
- la catena e' sigillata **membro per membro** e non come un albero unico,
  perche' il suo lucchetto e' leggibile solo da root e un lucchetto non e'
  storia. L'inventario della directory e' controllato a parte, cosi' nulla puo'
  comparire di fianco;
- la guardia semantica non e' sigillata su un numero indovinato: **misura cosa
  attestano i servizi prima del ritiro e pretende che sia identico dopo**.
  E' la proprieta' che ci interessa davvero — «ritirare la Release 3 non cambia
  cosa parte» — e non richiede di sapere in anticipo quale release e' selezionata.

Verificato: il censimento e' **riproducibile** (rieseguito, identico ai sigilli
congelati nel file).

### Cosa e' successo quando Roberto ha lanciato i comandi (10/9, mattina)

1. `withdraw` si e' **fermato**, correttamente, sul giornale di nascita
   (reperto 13). Nessun oggetto spostato.
2. La ricostruzione ha superato censimento e radice rivista, ha **ricevuto la
   sorgente nuova** (`93c296e9…`) e si e' fermata su
   `birth_ownership_deployment_invalid: successor edge`. E' il ramo `pending`
   di `_next_release_edge_v1`: c'e' una rivendicazione e la sua sorgente non e'
   questa. **Conferma che il ritiro e' il rimedio giusto**, letta nel codice e
   non supposta.
3. Le sorgenti ricevute sono ora **undici**: quella in piu' e' innocua
   (reperto 11), il deposito e' indirizzato per contenuto.
4. Al secondo giro il ritiro si e' fermato su `PreflightError coordinator
   inventory`: non un suo difetto, ma **il reperto 0**. La guardia semantica
   pretendeva un'attestazione riuscita; ora registra **cio' che il verificatore
   dice**, rifiuto compreso, e pretende che sia identico prima e dopo. Rifiutare
   il ritiro per quel motivo avrebbe chiuso l'unica via d'uscita: il ritiro e'
   il primo passo della riparazione. Il rifiuto viene stampato, non ingoiato.

### Il ritiro e la ricostruzione sono AVVENUTI (10/9)

```
RELEASE3_CLAIM_WITHDRAWN; HEADS_UNCHANGED; NO_SERVICE_STOP; RECEIPTS_RETAINED
SIGNED_SUCCESSOR_BUILT 3 sha256:28fb5158…
BUILD_ONLY_OK; NO HEAD CHANGE OR SERVICE STOP
```

Identita' della Release 3 **ricostruita**, tutte rimisurate sui byte installati:

| voce | valore |
|---|---|
| sorgente ricevuta | `sha256:93c296e9…` |
| build chiusa | `sha256:28fb5158…` |
| descrittore | `20d1fbd1…` |
| verificatore della release | `94789146…` |
| prove di build | `eca5c8c5…` / `218240b3…` |
| testa richiesta | `sha256:d302bb32…` (invariata) |
| `python_executable` | **`/usr/bin/python3.12`** |

Il verificatore di questa release **conosce** `abandoned-crossings-v2`: e' il
rimedio al reperto 0. I sigilli di `rm0008_complete_release3.py` sono armati con
questi valori. Resta solo `audit` e poi `complete`.

### La prova su copia fedele (il pezzo che conta)

`internal/tools/rm0008_rehearse_release3_withdrawal.py <cartella>`. Legge la
catena viva, **non ci scrive**, non chiede root e non ferma niente: copia gli
oggetti che il ritiro tocca in una cartella di lavoro e ci fa girare **il
codice vero** dello strumento di ritiro. Rieseguibile quando si vuole.

Cosa ha dimostrato, eseguito il 10/9:

1. **il ritiro** sposta esattamente due oggetti e lascia i **dodici** oggetti
   conservati **identici byte per byte**; rilanciarlo non muove piu' nulla;
2. **un ritiro interrotto a meta'** riprende, e a meta' strada la
   rivendicazione e' **ancora al suo posto**: la porta resta chiusa, il
   costruttore continua a rifiutare;
3. cinque rifiuti attesi avvengono davvero: una traversata aperta per la 3, una
   rivendicazione diversa da quella rifiutata, una testa pubblicata nuova, la
   storia che si muove **durante** il ritiro, un posto d'archivio gia' occupato.

Sono rilassate solo due cose sulla copia, ed e' scritto nello strumento: chi
possiede i file (una copia appartiene a chi l'ha fatta) e il controllo che
rifiuta le cartelle scrivibili da tutti, limitato alla radice di lavoro perche'
`/tmp` lo e' per costruzione. Tutto il resto e' controllato come in esercizio.

### L'esportazione rigenerata

| voce | valore |
|---|---|
| percorso | `/tmp/metnos-release3-export-2` |
| file | 1747 (invariato) |
| censimento | `10b8ef54f5c404ca0f7a40eb2d939a1e05509eaa24a3826638520ac7d4977109` |
| radice rivista scritta dentro | `sha256:9180f62d…` |
| collegamenti, modi anomali, collegamenti duri, bytecode | 0 |

La vecchia esportazione (`/tmp/metnos-release3-export`) e' stata **lasciata
dov'era**: sono i byte esatti della Release 3 gia' installata, e servono per
confronto se qualcosa non torna.

**Le prove di nascita girano dentro l'esportazione stessa.** Copiata in una
cartella di lavoro (per non lasciare bytecode nella messa in scena) e lanciate
le tre suite che riguardano proprieta', provisioning e attraversamento:
**241 verdi, 15 saltate**, compresa la prova nuova
`test_the_crossing_admits_the_predecessor_the_builder_already_admitted`.
I byte che stiamo per installare **sanno attraversare**. Il censimento della
messa in scena e' stato ricontrollato dopo: invariato.

Verificato che l'esportazione nuova porta davvero le correzioni:
`_abandonment_binds_record_v2` e `predecessor_abandonment` nel coordinatore,
`_abandonment_for_predecessor_locked_v2` anche nel provisioner e nella politica
compilata, il testo visibile nel nome dei controlli del sidecar, e i conteggi
dei cookie al massimo osservato.

### I sigilli del costruttore

| sigillo | prima | adesso |
|---|---|---|
| sorgente | `/tmp/metnos-release3-export` | `/tmp/metnos-release3-export-2` |
| censimento | `5238b5a7…` | `10b8ef54…` |
| numero file | 1747 | 1747 |
| radice rivista | `sha256:4b8a659c…` | `sha256:9180f62d…` |
| sequenza attesa | 3 | 3 |
| prove di build | `…-20260909` | `…-20260910` |

La sequenza attesa **resta 3**: la testa pubblicata e' ancora quella della
Release 2 (`d302bb32…`, sequenza 2) e l'abbandono la conserva, quindi dopo il
ritiro la catena torna a offrire esattamente la 3.

### Il completatore disarmato

`internal/tools/rm0008_complete_release3.py` aveva i sigilli del **primo**
tentativo. Quei byte non esistono piu' dopo il ritiro: lasciarli avrebbe fatto
fallire lo strumento su un confronto qualsiasi, molto dopo, con un messaggio
che non dice la verita'. Ora i sigilli sono vuoti e c'e' un controllo che
**rifiuta per nome**, prima di tutto il resto e senza bisogno di root.

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
--- 10/9, preparazione della ricostruzione (§6-quinquies) ---
         strumento di ritiro della rivendicazione della Release 3
         costruttore riallineato sull'esportazione nuova
         completatore disarmato fino alla ricostruzione
```

Riferimenti sorgenti correnti nel repository:
privato `sha256:0a27b038…` (755), pubblico `sha256:9180f62d…` (743).
**Sono avanti rispetto alla Release 3**, che porta la propria copia firmata:
appartengono alla versione successiva.

## 9. Decisioni aperte per Roberto

1. **Ritirare, ricostruire, attraversare** — tutto preparato (§6-quinquies),
   serve solo il suo via. Il ritiro e la ricostruzione **non fermano niente**;
   solo l'ultimo passo, l'attraversamento, ferma e riavvia i servizi, e va
   fatto quando non c'e' un turno in corso.
2. **Sigillo di `tests/portable/conftest.py`** — vedi §7.
3. `internal/tools/grant_roberto_access_to_install_root.sh` — scritto,
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
- La catena si legge senza `sudo` (reperto 12): un censimento di verifica non
  richiede un giro di privilegi.
