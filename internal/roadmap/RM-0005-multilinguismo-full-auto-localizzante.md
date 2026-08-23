# RM-0005 — Multilinguismo full e auto-localizzazione dell’istanza

| Campo | Valore |
|---|---|
| Stato | `in_progress`; progettazione consolidata, primo tratto verticale implementato |
| Creazione | 2026-08-21 |
| Ultima revisione | 2026-08-23 |
| Implementazione reale | Pipeline ancora parziale. Il vocabolario azioni è ora un tratto F2/F3/F6/F8 completo: superfici nel detection lexicon versionato, confini nel catalogo i18n, bootstrap congiunto, validazione strutturale, copertura nativa e prova su terza lingua sintetica |
| Decisione di prodotto | La lingua è una proprietà dell’istanza Metnos. Non esistono lingue diverse per utente, canale o turno |
| Nome pubblico | Multilinguismo per definizione |
| Conservazione | Roadmap persistente fino a implementazione dimostrata o cancellazione esplicita |
| Riservatezza | Documento interno. Non va copiato in `docs/`, incluso nel Tutor o pubblicato |

## 1. Sintesi

### Istruzione vincolante per gli agenti implementatori

Questo documento è indirizzato ad agenti di programmazione non-frontier. Gli
agenti **non devono rifare l’analisi**, dedurre lo stato del progetto o
reinterpretare il mandato. L’analisi dello stato e la scelta architetturale
sono già state svolte dal responsabile della roadmap. Un agente deve soltanto:

1. implementare la fase assegnata seguendo contratto, file e invarianti qui
   dichiarati;
2. non introdurre rami per dominio, lingua o provider;
3. non cambiare API, schema o semantica fuori dalla fase senza una decisione
   esplicita;
4. eseguire i test indicati e aggiungere i test minimi richiesti;
5. fermarsi e segnalare un conflitto concreto, senza “risolverlo” con
   un’interpretazione autonoma.

Le sezioni sullo stato corrente servono al coordinatore e non sono un compito
di audit per l’agente. La fase assegnata è completata soltanto quando il suo
criterio di uscita è dimostrato da test o da un report riproducibile.

RM-0005 definisce il **multilinguismo full** di Metnos: una persona installa
l’istanza, sceglie una lingua qualsiasi ammessa dal codice lingua e il sistema
parte immediatamente in inglese se quella lingua non è ancora disponibile. Una
pipeline automatica completa traduce e allinea progressivamente il patrimonio
linguistico dell’istanza. Quando la copertura e i controlli sono sufficienti,
l’istanza può essere riavviata nella nuova lingua.

La localizzazione non riguarda soltanto etichette e pulsanti. La lingua deve
attraversare tutto ciò che interpreta, pianifica, esegue, verifica e descrive
una richiesta:

1. comprensione dell’input e lessici di detection;
2. proposer, planner, synt e ogni prompt consumato da un LLM;
3. manifest degli executor, descrizioni e descrizioni degli argomenti;
4. messaggi deterministici, errori, conferme, notifiche e UI;
5. contenuti dei dispositivi remoti e dei canali;
6. sorgenti pubbliche usate dal Tutor e cataloghi compilati.

Il sistema non deve creare un ramo di codice per ogni lingua. Una lingua è un
dato di configurazione e una collezione di risorse allineate; il comportamento
rimane unico, tipizzato e verificabile. Questa è la parte innovativa: Metnos è
un sistema che **si auto-localizza**, usando la propria pipeline LLM per
tradurre il patrimonio operativo, mantenendo invariati identità, contratti,
chiavi, permessi e semantica.

## 2. Stato verificato al 23 agosto 2026

### 2.1 Già disponibile

- `install/disclaimer.py` accetta `other` e un codice ISO-639-1; registra la
  lingua desiderata e mantiene l’operatività in inglese.
- `runtime/i18n.py` fornisce catalogo SQLite, hash di provenienza, fallback
  `lingua dell’istanza → en → it` e marcatura delle traduzioni da completare.
- `runtime/i18n_translator.py` distingue testi user-facing e testi destinati a
  un altro LLM; traduce prompt lunghi, descrizioni dei manifest e messaggi.
- `deploy/run_prompts_translator.sh` ha tre livelli espliciti: prompt, manifest,
  database i18n. Il timer dei prompt è previsto alle 04:30.
- `runtime/jobs/detection_translate_pending.py` traduce il lessico di
  comprensione quando esistono righe pending, con esclusioni corrette per
  regex e concetti che richiedono revisione umana.
- Il vocabolario delle azioni non viene più reso da mappe IT/EN cablate: le
  superfici sono il concept versionato `vocab.action_surfaces`, i confini sono
  chiavi `VOCAB_ACTION_*_BOUNDARY`, il prefilter consuma la lingua attiva e il
  gate rifiuta mapping parziali. Il seed editoriale IT/EN resta soltanto la
  baseline distribuita; una terza lingua segue lo stesso percorso dati.
- I manifest hanno mappe linguistiche e `manifest.lang_state.json`; le firme
  vengono ricalcolate dopo una modifica.
- Tutor compila fonti pubbliche e manifest ammessi in un catalogo bilingue.

### 2.2 Gap verificati

- `desired_locale.json` viene scritto dall’installer, ma non esiste ancora un
  coordinatore unico che trasformi quella richiesta in directory, righe DB,
  lessici, prompt e target di tutti i traduttori.
- I traduttori lavorano soprattutto sulle lingue già presenti sotto
  `runtime/prompts/`; una lingua nuova non viene ancora materializzata in modo
  completo e idempotente.
- `runtime/i18n.py` e alcuni canali consentono ancora override per utente o
  turno (`language_context`). Questo contraddice il contratto di RM-0005 e va
  rimosso o confinato alle sole prove isolate.
- La traduzione dei prompt e dei manifest non dimostra da sola la copertura
  del proposer: servono inventario, test di caricamento e prova di equivalenza.
- La documentazione pubblica bilingue e il catalogo Tutor non sono ancora una
  destinazione automatica della lingua scelta all’installazione.

## 3. Contratto di prodotto futuro

### 3.1 Lingua per istanza

`instance_lang` è una sola impostazione dell’istanza, validata come tag BCP-47
normalizzato. È caricata al boot e propagata nel contesto di esecuzione. Tutti
gli utenti, canali, dispositivi, attività pianificate e turni della stessa
istanza usano quel valore.

- Non esiste `user_lang`, `channel_lang` o `turn_lang` operativo.
- Un cambio di lingua è una modifica amministrativa dell’istanza e richiede
  applicazione atomica della configurazione e riavvio controllato.
- Un’istanza che deve servire due lingue contemporaneamente usa due istanze
  Metnos distinte.
- L’inglese è il fallback operativo iniziale, non una lingua nascosta per
  singolo turno.

### 3.2 Installazione di una lingua non ancora disponibile

1. L’installer acquisisce e normalizza il codice lingua.
2. Se il codice è `it` o `en` e la copertura è certificata, lo usa direttamente.
3. Per ogni altro codice crea una richiesta di localizzazione firmata e
   idempotente, con stato `requested`.
4. Imposta `instance_lang=en`, `target_lang=<codice>` e stato operativo
   `bootstrap_english`.
5. Avvia normalmente Metnos in inglese; nessuna traduzione mancante può
   impedire il boot.
6. La pipeline notturna e i cicli di recupero lavorano fino a copertura
   sufficiente. Un errore lascia l’istanza in inglese e conserva l’evidenza.
7. Solo un comando amministrativo esplicito abilita la nuova lingua dopo il
   gate di copertura e il riavvio.

### 3.3 Catena di auto-localizzazione

La pipeline deve operare su un inventario deterministico, non su `find` e date
di modifica. Ogni risorsa ha: `resource_id`, `layer`, `source_lang`,
`target_lang`, hash della sorgente, hash della traduzione, versione, stato,
tentativi, ultimo errore e modello usato.

| Strato | Risorse | Controllo principale |
|---|---|---|
| Input | lessici, forme naturali, intenti e unità linguistiche | nessuna traduzione automatica di regex sensibili o parole di consenso |
| Comprensione | proposer, NLU e prompt di estrazione | stessa grammatica semantica e stessi identificatori |
| Pianificazione | planner, synt, vaglio, commenti del proposer | stessa forza prescrittiva, esempi e struttura |
| Contratti | manifest, argomenti, hint, output e messaggi dell’executor | schema, chiavi e firma invariati |
| Runtime | messaggi i18n, errori, conferme, notifiche | placeholder e classificazione invariati |
| Dispositivi | repertorio operativo del device shim | niente testo privato o non firmato |
| Conoscenza | documentazione pubblica e catalogo Tutor | fonti pubbliche, hash e compilazione coerente |

Il traduttore può usare un modello non-frontier secondo il workload
`translation.i18n` (qualità `fidelity`). Un modello frontier è soltanto una
possibile escalation amministrativa; non è una dipendenza del progetto.

## 4. Specifiche d’implementazione per LLM non-frontier

Queste istruzioni sono operative e devono essere eseguite in piccoli commit,
senza modifiche speculative. Ogni agente deve leggere i file indicati, eseguire
i test della fase e riportare i limiti residui.

### F0 — Inventario e gate di istanza

File: `runtime/config.py`, `runtime/i18n.py`, `install/disclaimer.py`.

- Definire `INSTANCE_LANG`, `REQUESTED_LANG` e `LOCALIZATION_STATE` in un solo
  modulo di configurazione.
- Normalizzare BCP-47; rifiutare input vuoto o ambiguo senza interrompere il
  boot.
- Persistire una richiesta firmata con codice, timestamp, versione del corpus
  e stato. Scrittura atomica (`tmp` + `os.replace`).
- Rendere `current_lang()` esclusivamente istance-scoped in produzione.
- Aggiungere test di riavvio, input invalido e doppia esecuzione installer.

### F1 — Rimozione degli override per utente/turno

File: `runtime/i18n.py`, `runtime/channels/daemon.py`,
`runtime/recurring_tasks.py`, `runtime/http_routes_agent.py`.

- Eliminare l’uso operativo di `language_context` per preferenze utente.
- Conservare un contesto di richiesta solo per propagare `instance_lang`, mai
  per sostituirlo.
- Rimuovere lookup di `users.get_pref(..., "lang", ...)` dai canali.
- Aggiornare i test che oggi pretendono lingue diverse nella stessa istanza.
- Vietare in lint ogni chiamata a `language_context` con valore proveniente da
  identità, canale o payload HTTP.

### F2 — Registry delle risorse linguistiche

Creare `runtime/i18n_registry.py` con API minima:

```python
register(resource_id, layer, source_lang, target_lang, source_hash)
claim(resource_id, target_lang) -> TranslationLease | None
complete(resource_id, target_lang, translation_hash, quality) -> None
fail(resource_id, target_lang, error_class) -> None
coverage(target_lang) -> CoverageReport
```

SQLite è sufficiente. Unicità su `(resource_id, target_lang, source_hash)`;
lease con scadenza e tentativi bounded; nessun LLM dentro il registry.

### F3 — Materializzazione della lingua richiesta

- Leggere `requested_locale` e creare, in modo idempotente, le directory
  `runtime/prompts/<lang>/`, gli stati dei manifest, le righe i18n e i lessici
  pending.
- Non copiare testo tradotto già esistente senza hash e provenienza.
- Registrare ogni risorsa nel registry prima di invocare il modello.
- Se una risorsa è strutturalmente non traducibile, marcarla `manual_review`;
  non dichiararla completata.

### F4 — Traduzione dei prompt e del proposer

- Enumerare tutti i `.j2` sotto `runtime/prompts/it/` e i prompt generati dal
  proposer/synt.
- Mascherare Jinja, JSON, identificatori, nomi executor e placeholder.
- Tradurre con il template LLM-targeted esistente; conservare numero di sezioni,
  regole, esempi e forza prescrittiva.
- Validare MiniJinja, placeholder, rapporto di lunghezza e assenza di sentinel.
- Scrivere candidato e hash; il loader usa solo una risorsa con stato ammesso.

### F5 — Traduzione dei manifest e dei contratti

- Enumerare `description`, descrizioni degli argomenti, hint e testi di output.
- Tradurre soltanto valori linguistici; non cambiare chiavi, enum, schema,
  capability, path, nomi executor o policy.
- Rifirmare il manifest dopo ogni modifica atomica.
- Eseguire `manifest_lint`, test di nascita e verifica della firma.

### F6 — Lessico di comprensione e proposer

- Convertire il lessico hardcoded in registri `(concept, lang)`.
- Tradurre forme naturali e mapping semplici; lasciare a revisione umana regex
  complesse, parole che decidono il consenso e marker di sicurezza.
- Il proposer deve ricevere prompt e lessico nella stessa `instance_lang`.
- Testare equivalenza semantica su fixture IT/EN e sulla nuova lingua; vietare
  che la traduzione alteri un identificatore canonico.

**Stato 2026-08-23:** implementato per il sottosistema action vocabulary. Il
test `test_action_vocabulary_i18n.py` materializza una terza lingua sintetica,
verifica detection, rendering, fallback e copertura; il daemon accetta un
mapping tradotto soltanto se conserva esattamente tutte le chiavi canoniche e
forme non vuote. Restano da portare nello stesso registry unico gli altri
strati elencati in F0-F8: questo avanzamento non chiude RM-0005.

### F7 — Runtime, dispositivi e Tutor

- Allineare messaggi, notifiche e UI tramite il catalogo i18n.
- Generare il repertorio device dalla lingua ammessa e dalla distribuzione
  pubblica, mai da dati personali o cataloghi locali.
- Aggiungere la nuova lingua al corpus pubblico solo dopo gate di qualità;
  compilare Tutor con fonti pubbliche e manifest verificati.
- Il Tutor deve dichiarare chiaramente `bootstrap_english` finché la lingua non
  è abilitata.

### F8 — Gate di attivazione e manutenzione

`coverage(target_lang)` deve verificare almeno:

- 100% dei prompt richiesti e caricabili;
- 100% dei manifest ammessi con firma valida;
- 100% delle chiavi deterministiche obbligatorie;
- copertura del lessico non sensibile e report delle eccezioni;
- test di equivalenza del proposer e del planner;
- compilazione Tutor riuscita;
- zero placeholder mancanti, sentinel, chiavi sconosciute o testi riservati.

Solo dopo il superamento del gate un comando amministrativo può impostare la
lingua target come `instance_lang` e riavviare i servizi. Il job notturno resta
attivo per rilevare drift e tradurre nuove risorse.

## 5. Invarianti

- Una sola lingua operativa per istanza.
- Nessuna traduzione modifica semantica, schema, autorizzazione o firma.
- Una risorsa tradotta è sempre riconducibile a una sorgente tramite hash.
- Un errore LLM non blocca il boot e non promuove un candidato incompleto.
- Regex di sicurezza, consenso e identificatori canonici non vengono tradotti
  automaticamente senza regola esplicita.
- Nessun testo interno, personale, log o analisi entra nel corpus pubblico o
  nel Tutor.
- La pipeline è idempotente: rilanciarla non duplica righe, file, lease o
  traduzioni già allineate.

## 6. Criteri di accettazione

RM-0005 è `implemented` soltanto quando una fixture installa una lingua nuova,
avvia l’istanza in inglese, completa la pipeline e dimostra dopo riavvio:

1. la stessa lingua per chat web, Telegram, attività pianificate e device;
2. proposer, prompt, manifest, messaggi e lessico nella lingua target;
3. fallback inglese controllato per una singola risorsa mancante;
4. nessun override per utente o turno;
5. firma e schema dei manifest invariati;
6. Tutor compilato esclusivamente da fonti ammesse;
7. ripresa idempotente dopo interruzione del traduttore;
8. report di copertura riproducibile e zero dati riservati.

## 7. Rischi e misure

| Rischio | Misura |
|---|---|
| Traduzione semanticamente plausibile ma sbagliata | prompt LLM-targeted, hash, fixture equivalenti, gate umano solo sui casi sensibili |
| Accumulo infinito di pending | lease, cap per ciclo, retry bounded, stato `manual_review` |
| Falsa sicurezza da copertura nominale | test di caricamento reale e prova proposer/planner, non solo conteggio righe |
| Drift fra prompt, manifest e lessico | registry unico, version hash e verifica incrociata |
| Lingua per utente che ricompare | lint sulle fonti del contesto e test di istanza concorrente |
| Pubblicazione di materiale riservato | gate pubblico esistente e separazione netta `internal/`/`docs/` |

## 8. Riferimenti

- `decisions/0092-prompts-as-data-multilingue.md`
- `decisions/0152-i18n-pipeline-structural.md`
- `decisions/0173-i18n-no-approval-gate-en-fallback.md`
- `runtime/i18n.py`
- `runtime/i18n_translator.py`
- `runtime/jobs/i18n_translate_pending.py`
- `runtime/jobs/detection_translate_pending.py`
- `deploy/run_prompts_translator.sh`
- `install/disclaimer.py`
