# Revisione indipendente della code base — Claude Code Opus 5

Data: 2026-07-29  
Ambito: sola lettura del codice di produzione, esclusi test e benchmark  
Esecutore: Claude Code, modello `claude-opus-5`, effort `max`, modalità `plan`, strumenti di sola lettura  
Sessione Claude: `2c686dd2-2f2f-4cbb-bc95-0ddcd4367118`

## Esito sintetico

La revisione ha ricostruito l'incidente del turno live sui servizi. La conoscenza necessaria era presente nel corpus Tutor; il classificatore della modalità Tutor è rimasto bloccato o non disponibile, ha restituito il valore semantico `UNKNOWN` e quel valore è stato interpretato come «Tutor non applicabile». La richiesta è quindi caduta nel planner operativo, che l'ha classificata come `get_processes` e l'ha inviata al device sticky PC-ROBERTO.

La revisione considera critici tre difetti: il fail-open del confine Tutor, l'assenza di una scadenza complessiva e l'attribuzione automatica al profilo host dei client LAN senza credenziali.

## Difetti confermati dalla revisione esterna

### D1 — Critico — Il gate della modalità Tutor fallisce in apertura verso il planner

File: `runtime/tutor/mode.py`, `runtime/tutor/service.py`, `runtime/tutor_boundary.py`.

Gli errori del provider, dello scheduler e di parsing sono ridotti allo stesso valore `UNKNOWN` usato per un esito semantico legittimo. `TutorService.answer_request()` interpreta ogni modalità diversa da `EXPLAIN` o `MIXED` come `None`; il chiamante considera `None` un rifiuto di competenza e prosegue nel planner. Il recupero al confine viene eseguito soltanto sulle eccezioni, non su `None`, e prova inoltre a richiamare lo stesso classificatore appena fallito.

Impatto: una domanda informativa può essere reinterpretata come operazione. Nell'incidente osservato l'operazione era in sola lettura, ma il meccanismo non impone questa limitazione.

### D2 — Critico — Tutor non ha una scadenza end-to-end

File: `runtime/llm_provider.py`, `runtime/llm_helpers.py`, `runtime/executor_scheduler.py`, `runtime/http_routes_agent.py`.

I provider ammettono timeout letterali fino a 600 secondi. Le acquisizioni dei semafori globali e per risorsa dello scheduler non hanno timeout. Il confine HTTP esegue Tutor con `asyncio.to_thread()` senza una scadenza. Con un solo slot LLM, le richieste concorrenti possono accumulare attese seriali e occupare il pool di thread HTTP.

### D3 — Critico — Un client LAN senza credenziali viene risolto come utente host

File: `runtime/http_auth.py`, `runtime/http_routes_agent.py`, `runtime/devices.py`.

Un peer appartenente alle reti LAN codificate riceve il ruolo `user` anche senza credenziale e senza `device_id`. La risoluzione successiva converte l'attore privo di device in `host` e, nell'installazione con un solo host, assegna il suo `user_id`.

Impatto: un client della rete locale può ereditare device, scope delle credenziali, task e collocazione sticky dell'host. La fiducia LAN non è configurabile separatamente tramite l'impostazione dei proxy fidati.

### D4 — Alto — I timeout dichiarati delle probe F3 non arrestano il lavoro

File: `runtime/tutor/probes.py`, `runtime/services_registry.py`.

`Future.result(timeout=...)` smette di aspettare ma `Future.cancel()` non interrompe un worker già partito. Quattro probe bloccate possono saturare permanentemente il pool condiviso. Inoltre `service_health` dichiara un budget di 5 secondi, mentre una singola raccolta può attraversare timeout systemd e HTTP per circa 8 secondi.

### D5 — Alto — I loader Tutor ricompilano o verificano il catalogo troppo spesso

File: `runtime/tutor/catalog.py`.

Ogni loader chiama `compile_catalog()`. Il percorso ricalcola l'impronta leggendo i byte delle sorgenti e prende un lock esclusivo anche quando il catalogo è già corrente. Una richiesta Tutor può ripetere il percorso più volte e le richieste concorrenti vengono serializzate; durante la compilazione fredda possono restare bloccate dietro il lock.

### D6 — Alto — Il gate controfattuale F4 non può fallire

File: `runtime/tutor/counterfactual.py`.

Il controllo assegna sempre `after_rank = 1` e poi verifica `after_rank > baseline_rank`, ma `baseline_rank` è sempre almeno 1. Il controllo dimostra la validità strutturale dell'associazione, non che l'algoritmo di produzione non peggiori il rango della fonte confermata.

### D7 — Medio/alto — La firma del catalogo non è vincolata a una chiave dedicata

File: `runtime/tutor/catalog.py`, `runtime/sign.py`.

Il catalogo viene firmato con una chiave generata localmente e verificato contro qualunque chiave pubblica nella directory fidata, condivisa anche con i manifest degli executor. La proprietà effettiva è soprattutto rilevazione della corruzione, non autorizzazione forte del contenuto.

### D8 — Medio — La capsule live dei servizi perde la descrizione

File: `runtime/tutor/probes.py`, `runtime/services_registry.py`, `runtime/ui_surfaces.py`, `runtime/tutor/sources.py`.

Lo snapshot dei servizi contiene descrizione italiana e inglese, ma `_service_payload()` conserva soltanto chiave, etichetta, stato, installazione, salute e scope. La fonte documentale F2 contiene invece scopo e funzione. La revisione segnala anche il rischio di divergenza tra l'audience della documentazione e quella della probe.

### D9 — Medio — La collocazione sticky si applica anche a un turno senza device esplicito

File: `runtime/target_device.py`, `runtime/agent_runtime.py`.

La revisione propone di applicare il target sticky soltanto quando la richiesta corrente cita un dispositivo. Questo rilievo descrive correttamente il meccanismo osservato, ma la sua classificazione come difetto di prodotto deve essere confrontata con ADR 0181, che definisce intenzionalmente la continuità sticky.

### D10 — Medio — Telegram crea l'handoff Tutor prima del gate di autonomia

File: `runtime/channels/daemon.py`, `runtime/orchestration.py`.

Tutor e la persistenza dell'handoff F3 precedono il controllo `LEVEL_BLOCKS_RUN`. L'esecuzione ricontrolla l'autonomia, quindi non è un'escalation di privilegi; resta una scrittura di stato e una proposta di consenso esposta a un principal in sola lettura.

### D11 — Medio — Il recupero Tutor richiama il componente appena fallito

File: `runtime/tutor_boundary.py`.

Il percorso di recupero, raggiunto su eccezione, richiama `classify_mode()` e può raddoppiare l'attesa prima di restituire ancora `None`.

### D12 — Basso/medio — Le capsule delle probe espongono nomi interni di eccezioni

File: `runtime/tutor/probes.py`.

Il nome della classe Python dell'eccezione viene inserito nei metadati passati al composer LLM. È preferibile un vocabolario chiuso di motivi, mantenendo il dettaglio tecnico soltanto nei log.

### D13 — Basso — Le lacune di completezza del catalogo sono attribuite al composer

File: `runtime/tutor/service.py`, `runtime/tutor/gaps.py`.

Alcuni `ValueError` prodotti durante la costruzione del contesto vengono catturati dal gestore generico e registrati come `composer_unavailable`, confondendo un problema di contenuto con uno infrastrutturale nel ledger F4.

### D14 — Basso — La telemetria si dichiara minimizzata ma conserva la risposta completa

File: `runtime/tutor/telemetry.py`.

La query grezza non viene salvata, mentre `final_message` contiene l'intera risposta, che può includere testo documentale o inventari live. La revisione chiede di allineare descrizione e politica effettiva.

## Rischi di design segnalati

- R1: il proofreader deterministico usa stemming e formule di arresto specifiche per italiano e inglese; una nuova lingua eredita impropriamente l'inglese.
- R2: le stopword di Tutor dipendono dal lessico del sottosistema Sites.
- R3: la promozione positiva usa somiglianza semantica, mentre una valutazione negativa rimuove soltanto l'hash esatto della formulazione.
- R4: i passaggi di selezione mutano la lista dei risultati senza un'invariante esplicita che protegga il primo elemento.
- R5: la chiave cache delle probe omette `source_version`.
- R6: il digest del compilatore del catalogo viene fissato al momento dell'import.
- R7: il contatore utenti dell'identity guard viene incrementato prima dell'acquisizione del lock; un fallimento di acquisizione lo lascerebbe incoerente.
- R8: il budget dichiarato di `service_health` non può contenere il caso peggiore del runner.

## Ipotesi lasciate aperte dalla revisione

- H1: dal solo codice non è possibile distinguere se i 600 secondi siano stati consumati dal semaforo LLM o dalla socket del provider; `queue_ms` e `run_ms` del turno lo distinguono.
- H2: la produzione esatta di `object=processes` richiede il log dei candidati del turno; il bypass deterministico non spiega da solo l'esito.
- H3: il codice rende molto probabile che PC-ROBERTO provenisse dallo store sticky, ma la conferma richiede lo stato del turno precedente.

## Patch minime proposte dalla revisione esterna

1. Separare modalità semantica e disponibilità tecnica; su indisponibilità Tutor deve rispondere con un errore tipizzato e non cedere il turno al planner.
2. Introdurre una deadline unica per Tutor, comprendendo ammissione scheduler, provider e confine HTTP.
3. Rendere la fiducia LAN esplicita e disattivata per impostazione predefinita; un principal senza credenziali non deve mai diventare un utente registrato.
4. Separare ammissione in lettura e compilazione del catalogo, evitando hash e lock esclusivi ripetuti.
5. Ricalcolare davvero il rango controfattuale con la stessa funzione usata in produzione.
6. Disattivare la collocazione sticky in assenza di un riferimento corrente al device.
7. Conservare la descrizione nella capsule dei servizi e unificare la fonte dell'audience.
8. Vincolare il catalogo a una chiave nominata.

## Evidenza metodologica

La revisione esterna dichiara di aver letto integralmente i principali moduli Tutor (`mode`, `detect`, `models`, `handoff`, `compose`, `telemetry`, `conversation`, `counterfactual`, `associations`, `gaps`, `probes`, `semantic`, `service`, `catalog`, `render`), oltre a `tutor_boundary.py`, `executor_scheduler.py`, `target_device.py` e `http_auth.py`; ha ispezionato le parti pertinenti di server HTTP, runtime agente, orchestrazione, canale Telegram, provider LLM, registro servizi, fonti Tutor, superfici UI, intent extraction, prefilter, firma e persistenza dialoghi. Non ha letto test o benchmark, non ha eseguito test e non ha modificato file.
