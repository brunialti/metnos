# Revisione della code base — Codex

Data: 2026-07-29  
Ambito: codice di produzione dell'intero repository, con approfondimento su Tutor  
Metodo: lettura e analisi statica; test, benchmark e risultati storici esclusi come fonte di prova

## Esito

L'incidente live di Tutor non dipendeva da una lacuna del corpus: la domanda informativa è uscita dal confine Tutor perché un'indisponibilità tecnica del classificatore è stata rappresentata con lo stesso valore `UNKNOWN` di una decisione semantica legittima. Il planner ha quindi ricevuto una richiesta che non avrebbe dovuto vedere e ha applicato correttamente la continuità del device prevista da ADR 0181. La correzione deve quindi riguardare il confine Tutor, non la collocazione sticky.

La revisione conferma inoltre due problemi trasversali di gravità critica o alta: manca una deadline end-to-end realmente propagata fino al provider e allo scheduler; un client LAN privo di credenziali può essere risolto come utente host. È emerso anche un ulteriore difetto alto non segnalato dalla revisione esterna: numerosi file contenenti turni, posizioni e stato per utente sono creati o conservati con permessi dipendenti dall'umask e risultano leggibili da altri utenti locali.

## Copertura

Sono stati inventariati 585 file di codice di produzione: 408 sotto `runtime/`, 89 executor, 40 script, 22 file dell'installer, 16 sorgenti Rust e gli altri componenti di servizio e deploy. La lettura manuale ha approfondito:

- l'intera pipeline Tutor F1–F4, catalogo firmato, probe, associazioni, proofreader, handoff e telemetria;
- autenticazione HTTP, risoluzione di attore e utente, sessioni, device e collocazione;
- scheduler, identity guard, provider LLM e relativi timeout;
- canale Telegram, orchestrazione, browser sidecar e client remoto;
- persistenza, credenziali, i18n, registro servizi, installazione e deploy;
- superfici amministrative e principali confini di rete e subprocess.

Sono state eseguite scansioni statiche per eccezioni generiche, subprocess e shell, SQL dinamico, endpoint HTTP, scritture su file e SQLite, stato globale, marker di debito e moduli di dimensioni eccezionali. Questa revisione non pretende che ogni riga dei 585 file abbia ricevuto lo stesso livello di lettura: i confini di sicurezza, persistenza e routing sono stati analizzati in profondità; il codice ripetitivo degli executor è stato verificato per pattern e campioni rappresentativi.

## Difetti confermati

### C1 — Critico — Tutor confonde indisponibilità tecnica e decisione semantica

`runtime/tutor/mode.py` riduce errori del provider, dello scheduler e del parsing a `UNKNOWN`. `runtime/tutor/service.py` interpreta quel valore come mancata applicabilità e restituisce `None`; il chiamante prosegue nel planner. `runtime/tutor_boundary.py`, in caso di eccezione, richiama inoltre lo stesso classificatore appena fallito.

La proprietà necessaria è: se Tutor ha preso in carico una richiesta informativa ma il suo gate tecnico non è disponibile, deve terminare con una risposta tipizzata e localizzata; non deve trasformare l'errore in autorizzazione implicita a eseguire un'operazione.

### C2 — Critico — Non esiste una deadline Tutor end-to-end

I timeout del trasporto LLM sono costanti interne fino a 600 secondi; l'ammissione dello scheduler può attendere indefinitamente; il confine HTTP usa un thread senza limite. Un timeout applicato soltanto a `Future.result()` o `asyncio.wait_for()` non interrompe il lavoro sottostante e non libera necessariamente le risorse.

La deadline deve essere un budget monotono unico, propagato a classificatore, ammissione, provider, probe e composizione. Ogni livello deve ricevere il tempo residuo, non iniziare un nuovo timeout completo.

### C3 — Critico — La fiducia LAN può impersonare l'host

`runtime/http_auth.py` assegna il ruolo `user` a intere reti private anche in assenza di credenziali e `device_id`. La successiva risoluzione dell'attore converge su `host` e, nell'installazione con un solo utente, sul relativo `user_id`.

La raggiungibilità di rete non è identità. La compatibilità LAN, se desiderata, deve essere un'opzione esplicita e non può mai creare o scegliere implicitamente un utente registrato.

### A1 — Alto — I timeout delle probe non arrestano i worker

La cancellazione di un `Future` già in esecuzione non interrompe il runner. Il pool condiviso può quindi saturarsi con operazioni che continuano dopo la risposta di timeout. `service_health` dichiara inoltre un budget inferiore alla somma dei timeout possibili delle sue sorgenti.

### A2 — Alto — Il catalogo Tutor compila o rilegge le sorgenti nel percorso di richiesta

Ogni loader passa da `compile_catalog()`, ricalcola l'impronta delle sorgenti e prende un lock esclusivo. Una singola risposta ripete il percorso più volte. La compilazione appartiene a installazione, deploy e processo di aggiornamento; il percorso online deve soltanto ammettere un artefatto già compilato e firmato, con cache invalidata dagli `stat` dell'artefatto e della firma.

### A3 — Alto — Il controfattuale F4 non misura il rango reale

`after_rank` è fissato a 1. Il gate verifica la struttura dell'associazione, non la proprietà dichiarata. Il calcolo deve usare la stessa funzione di aggiustamento del ranking di produzione e confrontare realmente il rango precedente e successivo.

### A4 — Alto — Permessi dei dati locali non garantiti centralmente

L'installer dichiara radici private, ma applica `0700` soltanto ad alcune sottodirectory. Diversi writer usano `open()` o `write_text()` e quindi ereditano l'umask. Sul sistema installato sono risultati, fra gli altri, `locations.jsonl`, turni, database di stato e richieste di posizione con modi `0644`/`0664`, e directory con `0755`/`0775`.

Serve un'invariante centrale: radici e directory sensibili `0700`, file sensibili `0600`, sia alla creazione sia come riparazione idempotente all'avvio. La cifratura delle credenziali, che è già implementata correttamente, non sostituisce questa protezione per gli altri dati personali.

## Difetti e debiti medi

- La verifica del catalogo accetta qualunque chiave del trust store condiviso. Vincolare l'artefatto alla chiave autore nominata migliora la separazione dei domini, pur non proteggendo da un aggressore che controlli lo stesso account del servizio.
- La capsule live dei servizi omette la descrizione localizzata. La documentazione F2 contiene già lo scopo; aggiungere la descrizione evita divergenze e rende autosufficiente l'evidenza live.
- Telegram può persistere un handoff Tutor prima del gate di autonomia. Non consente l'esecuzione, ma produce stato e una proposta di consenso per un principal che non può procedere.
- Le capsule delle probe espongono nomi di classi Python; il composer deve ricevere motivi appartenenti a un vocabolario chiuso, mentre i dettagli tecnici restano nei log.
- Alcune lacune del catalogo sono registrate come indisponibilità del composer, rendendo il ledger poco diagnostico.
- La cache delle probe non include `source_version`.
- Il proofreader deterministico impone formule e stemming italiano/inglese e, per lingue nuove, ricade impropriamente sull'inglese. Le condizioni di copertura devono provenire dalla sorgente localizzata, non da un elenco di aperture di frase codificato nel programma.
- Le stopword Tutor dipendono dal lessico Sites: accoppiamento improprio fra due domini.
- La promozione positiva delle associazioni è semantica, mentre la valutazione negativa elimina solo l'hash testuale esatto; formulazioni equivalenti possono sopravvivere.
- Il digest del compilatore Tutor è fissato all'import; un processo lungo può non rilevare una modifica del compilatore.
- Il registro servizi può superare il budget dichiarato perché somma probe systemd e HTTP.
- La connessione SQLite globale di `runtime/i18n.py`, aperta con `check_same_thread=False` senza una serializzazione esplicita, è un rischio di concorrenza da verificare con una prova mirata.
- Alcune pagine amministrative e lessici di selezione device conservano stringhe italiane/inglesi nel codice. Non tutte le superfici sono ancora conformi all'invariante i18n richiesta per la UI.
- `TurnLog._CAP_FIELD_FALLBACK` conserva una mappa executor→campo che dovrebbe appartenere ai manifest: è un debito hardcoded contrario alla configurazione semantica.

## Timeout ingannevoli fuori da Tutor

`runtime/jobs/promoter_example.py` restituisce da un blocco `ThreadPoolExecutor` dopo `future.result(timeout=5)`, ma l'uscita dal context manager esegue `shutdown(wait=True)`: il limite di cinque secondi non è reale. `runtime/skill_description_llm.py` abbandona l'attesa di un thread daemon, ma la chiamata provider continua. Entrambi richiedono timeout propagati al trasporto, non wrapper di thread.

## Controlli positivi

- Lo store credenziali usa Fernet, derivazione HKDF, scritture atomiche e modalità `0600`.
- Il proxy foto pubblico usa capability firmata, pinning DNS, controllo SSRF sugli IP, rivalidazione dei redirect e limiti di dimensione e tempo.
- Il sidecar Playwright non ha un'autenticazione applicativa forte, ma le unità di produzione lo vincolano al loopback; la proprietà deve restare verificata nel deploy.
- L'aggiornamento del client Rust verifica firma del server, SHA-256, monotonia della versione e rollback.
- Non sono emersi `eval`, `exec`, pickle o YAML unsafe nel codice di produzione. L'unico `shell=True` individuato è in un'utilità di smoke/development.
- Gli identificatori SQL dinamici esaminati provengono da schemi interni e non da input utente.
- Il filtro per owner dei device remoti è presente nel runtime corrente; alcuni testi progettuali storici risultano più vecchi del codice.

## Rilievi non accettati come difetti

La collocazione sticky di ADR 0181 non va disattivata quando il turno non nomina un device: la continuità è la sua funzione esplicita. Nell'incidente il problema è che Tutor ha ceduto impropriamente il turno al planner. Correggere il routing rimuove il sintomo senza rompere una semantica deliberata.

La risposta completa nella telemetria Tutor non è di per sé una violazione della minimizzazione dichiarata: il record è anche cronologia conversazionale e la query grezza non viene duplicata. Va chiarito il commento e, soprattutto, protetto il file con l'invariante dei permessi. Una diversa politica di retention sarebbe una scelta di prodotto, non una patch implicita.

## Limiti della revisione

L'analisi statica non prova la qualità di ogni risposta LLM né distingue, senza telemetria del turno, se una specifica attesa sia avvenuta nel semaforo o nella socket. I rischi di concorrenza e le proprietà di timeout richiedono test deterministici dopo le correzioni. La sicurezza del processo non protegge da un aggressore che controlli l'account Unix del servizio o il codice installato.
