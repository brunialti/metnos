# RM0008 — stato operativo e turni reali, 14 settembre 2026

Autore: Codex. Sviluppo individuale autorizzato da Roberto; controllo periodico
BACHECA disattivato. Questo rapporto **non certifica la chiusura di RM0008**.
Base delle ultime correzioni: `3ba5d3a6`.

## Esito essenziale

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
