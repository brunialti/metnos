# RM0008 — stato operativo e turni reali, 14 settembre 2026

Autore: Codex. Sviluppo individuale autorizzato da Roberto; controllo periodico
BACHECA disattivato. Questo rapporto **non certifica la chiusura di RM0008**.
Il checkpoint pomeridiano riportato più sotto è storico, non lo stato corrente.

## Aggiornamento serale: chiusura applicazioni e risposte veritiere

Correzione `fc83fa31`, successiva alla release 37. Release **38** attraversata
con `CUTOVER_OK`; salute HTTP `ok=true`, stack `ok=true, ready=true`.
La prima pubblicazione di `set_processes` è stata rifiutata con
`birth_authoring_target_unavailable`: la costruzione della richiesta cercava
un contratto già pubblicato anche per una prima ammissione. Corretto con
`0dc85e88`: la release **39** ha attraversato la verifica e ammesso e attivato
`set_processes` tramite Producer/Birth (`store_verified`, nessun bypass).
HTTP `ok=true`, stack `ok=true, ready=true`.

La prova reale della 39 ha trovato un ulteriore errore nella risposta finale.
Corretto con `77d00049`, distribuito nella release **40**: `CUTOVER_OK`,
`PREFLIGHT_VERIFIED`, pubblicazione componenti completata senza cambiamenti
aggiuntivi, codice di uscita 0. HTTP e stack pronti. La stessa richiesta
Word ora riporta lo stato già chiuso osservato dall'esecutore, senza dichiarare
una chiusura appena compiuta. Non viene dichiarata riuscita la chiusura di
un processo effettivamente aperto: serve ancora la prova con Word aperto e
conferma normale inviata da Roberto, non da Codex.

Evidenze riesaminate:

| Turno | Risultato reale |
|---|---|
| `df6396d3726e4ce6` | Ping a pc-roberto: 4 pacchetti a `192.168.1.137`, 4 risposte, nessuna perdita. |
| `7a4c989a008c4a25` | ExpressVPN: Roberto conferma di avere scelto «Fino al prossimo riavvio». Invio registrato `http_form_owner`, continuazione completata, `already_running=true`: riutilizzo di un'app già aperta, non prova di un nuovo processo. |
| `39aafb8eb4ab471b` | «chiudi expressvpn su pc-roberto» ha modificato `sites_stealth=off` e dichiarato falsamente la disattivazione della VPN. Nessuna chiusura. Il precedente valore della preferenza non è noto: nessun ripristino arbitrario. |
| `c04d1548c671462c` | «chiudi word su pc-roberto» ha proposto `taskkill /F /IM winword.exe` senza mantenere la destinazione. Non eseguito. Roberto precisa che Word non aveva documenti: non cambia l'errore di comportamento e risposta. |
| `f50d00eb0bcf48b8` (R39) | Ricerca e `set_processes` sul PC corretto. Word osservato già chiuso: `_undo.no_effect`, messaggio dell'esecutore «Risultano già chiusi…». Il template finale lo ha falsificato in «Ho chiuso Word.»: errore riprodotto, nessun processo terminato. |
| `aa1b75c9d56b45de` (R39) | ExpressVPN risolto e interrogato sul PC; chiesta conferma per chiusura normale/forzata con avviso sui dati. `final_kind=ask`, dialogo `a61bb0888a414b1b`. Nessuna scelta inviata da Codex, nessuna chiusura dichiarata. |
| `8727e84ef0e94f46` (R40) | Stessa richiesta Word, stesso PC. Risposta HTTP e record persistente concordano: «Risultano già chiusi su ROBERTO_PC_HP: Word.» `find_packages` e `set_processes` riusciti, nessuna proposta amministrativa, nessuna conferma inviata. 15,584 secondi. Lo stato visivo sul PC resta da confrontare con l'utente. |

Ricevuta remota R40 `inv-18d54c7d1efc9202c0516787`, sul dispositivo
`7bd3da08649e43c2b7e0a6bdecc66ecd`: `already_closed=true`,
`_undo.outcome=no_effect`. Registro del turno: `mutations=0`, `failures=0`,
`false_success_detected=false`, nessuna classe d'errore. Il percorso è
`fastpath`: la correzione funziona anche con il piano già memorizzato, senza
cancellare o ritoccare la cache. Diagnostica in sola lettura `run-s0xs9kh8`.

La conferma sul turno serale non rettifica retroattivamente il consenso
contestato del turno pomeridiano `f35ee3d12afa4d57`.

Correzioni generali, senza nomi applicativi nel runtime:

- Verifica del verbo **e del dominio** dell'azione: modificare preferenze
  non soddisfa la richiesta di chiudere processi, anche da cache o recupero.
- Risoluzione di identità desktop registrate tramite `find_packages`;
  `set_processes` osserva sul dispositivo PID e tempo di creazione e chiede
  chiusura normale, forzata o annullamento. Nessun passaggio automatico alla
  forzata; successo solo dopo verifica che i processi non siano più attivi.
- Prima ammissione delle nuove sorgenti core attraverso lo stesso Producer
  e Birth delle modifiche; nessuna firma precedente inventata, nessuna
  pubblicazione diretta. Rilettura obbligatoria dal catalogo verificato.
- Messaggi IT/EN e documentazione riallineati; il catalogo pubblico descrive
  sorgenti, non pretende di attestare la disponibilità nell'istanza.
- Ricevuta terminale d'effetto autorevole rispetto al testo scritto prima
  dell'esecuzione. Lo stato `no_effect` prevale su contatori di elementi
  riusciti: zero modifiche reali, nessun falso apprendimento di efficacia.

Prove locali: gruppo snapshot/authoring/riconciliazione/rilascio/proprietà/
chiusura/routing: 405 superati e 1 saltato; i 9 casi che richiedevano socket
locali sono poi passati nell'ambiente autorizzato. Confine e catalogo:
83 superati; manifest e documentazione dei domini: 35 superati.
Questi conteggi non si sommano ai gruppi precedenti sovrapposti e non
sostituiscono la prova Windows reale. Nessun consenso inviato da Codex.

Correzione della risposta: 242 test superati e 7 sottocasi nel gruppo iniziale;
il test storico `test_recovery_challenger_realigned_to_intent` fallisce anche
eseguendo in memoria le versioni HEAD precedenti dei due moduli modificati
(nessuna modifica del codice per nascondere il risultato). Ulteriore prova
IT/EN della risposta completa, incluso `TurnLog.write`: gruppo mirato di
59 test e 9 sottocasi superati. Gruppi sovrapposti, non sommare.

Release 39: build
`sha256:802dc2205294f2e6aa6e369fd44185f9911182ca79fbfa9c7de4a3a877885280`,
cutover `sha256:a0a2ed8d1b390a55ad44e9cbe7c3cc404b1cd117dda57cb5a83df8aa850b96f2`.
Evidenza `/var/lib/metnos-admin/rm0008-cycle-evidence-802dc2205294f2e6`.
Generazione ammessa `set_processes`:
`sha256:495db5b22011c3450f78ae6e67575b3a1c1368417ee243ad4fe8bbd686642296`.

Release 40: build
`sha256:4cfa7f364d6cc9bea3d41c4f149613ca0621a15becff1b3e908dccae45952959`,
sorgente `sha256:d9e5dbb4ca001f420bf657cd52e2c87792ed1496aff284642b49dfa0f5add826`,
cutover `sha256:f9b577470339f137dd88102c6e7094fed19832dfbc5d8f045cb11236029368b7`,
richiesta `sha256:2a7e7109d72fdc1b645710ea69b21948b6c83680226a5e09555e8764a28fea0f`.
Evidenza `/var/lib/metnos-admin/rm0008-cycle-evidence-4cfa7f364d6cc9be`.
Controlli confine/catalogo: 82 superati prima della preparazione; la sola
verifica della nuova impronta sorgente è passata dopo `prepare` (1 test).
Non è stato rieseguito il test ExpressVPN di sola proposta: esecutore e
percorso di conferma invariati rispetto alla prova R39 sopra riportata.
Documentazione pubblica IT/EN aggiornata:
`https://43644119.mykleos.pages.dev`, catalogo Tutor locale non modificato
dal comando di pubblicazione statica.

Release 38: build
`sha256:2411c210be65440f913e4a07f518d2475ed443f2b6c6f49692048ce1503524ae`,
sorgente `sha256:6b11b147bc10cee2c5550fb9f6a64ff885a92dca8b266ff6a5d89a4df442b6dc`,
cutover `sha256:b9ee063d08cc1a5655247a285a75e1ed443ae845876aa70d3d019290ba6b57c0`.
Evidenza amministrativa:
`/var/lib/metnos-admin/rm0008-cycle-evidence-2411c210be65440f`.
Documentazione statica pubblicata con successo su Cloudflare:
`https://1b117c7d.mykleos.pages.dev`; catalogo Tutor locale non modificato
dal comando di pubblicazione statica.

## Checkpoint pomeridiano precedente — esito storico

Base delle correzioni di questo checkpoint: `3ba5d3a6`.

- In esercizio resta la **release 32**, recuperata dopo il fallimento del
  solo tentativo di passaggio alla 33. Nessun retry della 33, cambio di head,
  cancellazione di ricevute o reset di claim.
- Correzioni locali: conservazione del destinatario nominale del ping,
  riconoscimento della quantità esplicita attraverso la grammatica centrale,
  distinzione fra identità non risolta e contesto ambiguo. Non distribuite.
- **176 test distinti superati** sui percorsi esaminati in questa fase;
  una controprova ripetuta dopo il completamento del catalogo i18n.
  Non sono prove di avvio di applicazioni reali né certificazione Birth.
- ExpressVPN: tentativo effettivo fallito, `package_not_registered` e
  `started=false`. **Consenso contestato dall'utente**: la scelta `session`
  nel registro non dimostra che Roberto l'abbia inviata.

## ExpressVPN: rettifica prioritaria sul consenso

Turno `f35ee3d12afa4d57`, richiesta «avvia expressvpn su pc-roberto».
La prima lettura del turno vedeva soltanto `find_packages` e la proposta
di `run_processes`, conclusa con `final_kind=ask`. Codex ha inizialmente
descritto quel punto come ancora in attesa. La lettura della continuazione
ha successivamente mostrato:

| Ora CEST | Evidenza persistente | Significato |
|---|---|---|
| 17:44:17–21 | `inv-18d53a6abd9c711e6f7ab231` | WinGet trova ExpressVPN; identità `ExpressVPN.ExpressVPN`. |
| 17:44:21–22 | `inv-18d53a6bcb60737b4430d994` | Proposta di scelta, `decision=needs_inputs`, `started=false`. |
| 17:44:31 | Dialogo `2194e34a54b345da`, `completed=true`, `decision=session` | Stato registrato; non prova dell'autore materiale della scelta. |
| 17:44:32–33 | `inv-18d53a6e5def595284cf879e` | Tentativo fallito: `package_not_registered`, `started=false`. |
| 17:56:10–16 | `inv-18d53b10e2454c2df443671c` | Lettura mirata sul PC: nessun processo con nome contenente `express`. |

Roberto dichiara: **«io non ho confermato»**. È quindi errata l'attribuzione
iniziale di Codex «la tua conferma». Non si deduce un consenso umano dal
solo flag `completed`. Non vengono effettuati altri avvii, retry o scelte
di persistenza per conto dell'utente. Non è stato cambiato lo stato della VPN.

Il registro del dialogo conserva scelta e timestamp, ma non una ricevuta
che distingua invio dal form, risposta in chat o pulsante di canale.
Il callback `gate_dispatch` di questo caso non conserva una propria
ricevuta terminale nel dialogo; l'esito è stato recuperato dalle invocazioni.
Il server HTTP è avviato con `access_log=None` in `runtime/agent_server.py`:
la consultazione del journal nella finestra interessata non ha identificato
la richiesta che ha completato il dialogo. **Origine della scelta irrisolta**;
non provati un clic dell'utente, un automatismo o un attore esterno.

Controprove isolate nuove: `turno:f35ee3d1`, `avvio temporaneo?` e
`I did not confirm` non consumano una scelta pendente, non modificano i
valori raccolti e non chiamano il callback HTTP. Il parser condiviso con
Telegram è deterministico, non un classificatore LLM. Questi test escludono
quel preciso percorso sugli input provati, non certificano l'intera UI.

## Turni: risultato reale, senza equiparare `ok` all'esecuzione

| Turno | Richiesta / prova | Esito verificato |
|---|---|---|
| `cc3cabf224e141e0` | Ping IP, numero omesso | Eseguito `ping -c 4 192.168.1.137`; 4 risposte, nessuna perdita. |
| `9e424a5e02f6476f` | 2 ping allo stesso IP | Nessun comando eseguito; quantità rompeva l'esposizione del comando al pianificatore. Correzione locale. |
| `24ec7b5607f746df` | Ping a pc-roberto | Richiesto nuovamente il destinatario; il nome tecnico veniva tolto dalla richiesta del pianificatore. Correzione locale. |
| `343553163b904a4e` | Ping a pc-roberto | Proposto erroneamente `8.8.8.8`, non eseguito. Non approvare la proposta. |
| `d6a5a734bf134100` | Avvia Word sul PC | Ricerca eseguita, identità di lancio non risolta; il consumatore è stato fermato prima dell'invocazione. |
| `bd1f1f3567ee4dfb` | «posizionw del server metnos» | Spiegazione generica dell'architettura; nessuna posizione o identità concreta osservata. Richiesta non soddisfatta. |
| `f35ee3d12afa4d57` | Avvia ExpressVPN | Ricerca positiva ma avvio successivo fallito; vedere rettifica sul consenso. |
| `79a200e7af264d21` | «turno:f35ee3d1» | Invocato `get_now`, risposta sull'ora. Interpretazione errata, non analisi del turno citato. |

La ricerca remota mirata `inv-18d53a4fa04b27f52ec5afef` ha inoltre trovato
«Microsoft 365 - it-it», versione `16.0.20326.20144`, ma nessuna identità
di avvio per Word. I candidati Copilot e Local AI Manager non sono Word e
non vanno scelti al suo posto. **L'avvio di Word non è risolto**.

Difetto generale da correggere nella risoluzione Windows:
`find_packages` e la fase di query del helper usano WinGet, mentre
`helper-rs/src/win_activation.rs` risolve l'avvio mediante registrazioni
HKLM con `WinGetPackageIdentifier`. Una ricerca positiva non garantisce
una destinazione avviabile secondo quel secondo contratto. Il controllo
preventivo deve interrogare lo stesso risolutore dell'avvio, senza percorsi
cablati, selezione arbitraria di eseguibili o reinstallazioni automatiche.
Il percorso desktop deve anche distinguere applicazioni visibili e processi
del helper LocalSystem: non basta dimostrare che esista un processo.

## Correzioni locali e prove riusate

1. `runtime/target_device.py`: mantiene nella richiesta i nomi tecnici nudi
   che possono essere oggetti dell'operazione; continua a rimuovere il solo
   complemento esplicito di esecuzione. Nessun indirizzo o host speciale.
2. `runtime/prefilter.py` e `runtime/safety/canonicalize.py`: una quantità
   numerica tra invocazione localizzata e comando non lo esclude dal pool.
   I comandi pertinenti derivano dalla grammatica numerica centrale; non
   vengono generati argomenti o ampliate autorizzazioni. Negazioni intatte.
3. `runtime/engine/executor.py`: la proiezione incompleta emette
   `source_identity_unresolved`, distinta da `ambiguous_source_context`.
   Il blocco del consumatore resta invariato. Messaggio nuovo nel seed
   pubblico e nel catalogo shim, IT/EN. Confronto logico del seed: solo due
   righe aggiunte; nessuna riga esistente cambiata; integrità SQLite `ok`.

Registro dei test, senza ripetizioni indiscriminate:

- Target device, guard/catalogo admin, esposizione al proposer: 6 casi
  selezionati più 124 complementari, incluso il lessico di rilevazione.
  I primi sei sono stati esclusi dall'esecuzione complementare: totale 130.
- Proiezione e scelta delle identità: 40 casi superati.
- Coerenza seed/shim e rendering degli errori: 4 casi superati, di cui
  3 nuovi rispetto ai 170 e una controprova dopo il completamento i18n.
- Consenso contestato: 3 nuovi casi HTTP isolati descritti sopra.
- `git diff --check` superato. I test precedenti di passo1/passo2 non sono
  stati rilanciati e non sono sommati a questi 176.

## Release 33: fallimento non aggirato

Build `sha256:9c281a584889eb053839ead97ee937fecd5db6fc437ee9f6c86a50e42e432cd0`,
sorgente `sha256:31529357d2150d4718e2d82f9fe01ca52be1084e19180a8793af39d92e5acc2c`.
Un solo apply, rc78, prima di `CUTOVER_OK`. Ultimo record autenticato:
`PREPARED`, sequenza 0, senza head pubblicato; richiesta
`sha256:276203456ca3de579632b2435e314ec6dcfa1b6217adb699dbf723480bd0815c`.

Riattestazione `core:run_processes/manifest.toml` rifiutata con
`property_runner_unavailable`, richiesta produttore
`sha256:d62a2a4e3cf27c4b1efe624c9736d4dbbf59c9ac920eee3cfe4e6858ca593424`.
Il registro produttore è stato letto, non modificato; l'osservazione della
riga non è una verifica indipendente di un envelope terminale firmato.

Diagnosi statica: manca una V2 precedente per questo corrente, quindi la
continuità seleziona correttamente i controlli ordinari. Il cutover usa un
figlio root ordinario che adotta temporaneamente l'identità metnos, mentre
il runner nativo richiede un sottogruppo cgroup delegato dal servizio.
Il percorso di deploy post-cutover ha già una delega; quello del cutover no.
È una spiegazione coerente con codice e ambiente, **non il dettaglio nativo
originale**, che il rifiuto generico non ha conservato.

I servizi fermati dal cutover sono stati recuperati con l'attivazione del
catalogo firmato della release 32 selezionata, sotto lock e verifiche prima
e dopo: `run-gibb98st`, rc0. Salute successiva operational/ready; 122
componenti, PC raggiungibile anche nelle letture successive.

Il recupero di un tentativo `PREPARED` non è coperto dall'abbandono ordinario
`HEAD_REQUIRED`. Il precedente recupero N2 documenta un archivio conservativo
di quattro oggetti; il suo script ha identità congelate per N2 e **non è
stato adattato o eseguito sulla 33**. Prima di una nuova transizione servono
una soluzione verificata per l'ambiente nativo e il percorso esplicito di
recupero. Nessun fallback V1 nel percorso V2, reset del rifiuto terminale,
riscrittura della storia o nuova release automatica per aggirarlo.

## Chiusura ancora non raggiunta

Priorità immediata: chiarire il consenso contestato e rendere osservabile
la continuazione; poi avvio Windows e rilascio corretto. I ping locali
corretti devono essere verificati sullo stack effettivamente distribuito.
L'interazione Telegram reale non è stata collaudata in questa fase.
Restano inoltre F5/certificatore indipendente e prove reali richieste,
F6/retention e banco nativo Windows: salute API, fixture e riattestazioni
non sostituiscono quelle condizioni.

Evidenze principali: lettori amministrativi `run-a5jdvrqj`, `run-h6lq31a3`,
`run-_jzr9tif`, `run-r_sd_4ep`, `run-dglrf5lt`, `run-fuyrelhc`,
`run-s4j36omh`. Nessun token, URL di consenso firmato o segreto nel rapporto.
