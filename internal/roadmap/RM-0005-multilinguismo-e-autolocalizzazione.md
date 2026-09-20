# RM-0005 — Multilinguismo completo e auto-localizzazione dell’istanza

| Campo | Valore |
|---|---|
| Identificatore | `RM-0005` |
| Stato | `closed` il 2026-08-23 |
| Creazione | `2026-08-21` |
| Ultima revisione | `2026-08-23` |
| Conservazione | conservata come traccia verificabile; esclusa da `docs/` e dal Tutor |
| Implementazione reale | F0-F8 implementate e certificate il 2026-08-23 |
| Origine e prove | decisione portante: una sola lingua firmata per istanza; ADR 0219 e ADR 0220 |

## 1. Sintesi

### Stato del mandato

Il mandato è concluso. Questo documento conserva il contratto implementato, le
invarianti e le prove di chiusura; non contiene fasi ancora assegnabili. Una
manutenzione successiva deve:

1. preservare il contratto e le invarianti qui dichiarati;
2. non introdurre rami per dominio, lingua o provider;
3. non cambiare API, schema o semantica senza una decisione
   esplicita;
4. rieseguire i gate pertinenti e aggiungere prove per ogni estensione;
5. riaprire o creare una roadmap soltanto se compare nuovo lavoro di prodotto.

Le fasi seguenti restano come traccia verificabile dell’implementazione, non
come elenco di attività pendenti.

**Debito successivo, senza riapertura.** La revisione avversariale del 24 agosto
2026 ha distinto l'atomicità dei singoli file e dell'attivazione dell'istanza da
una garanzia più forte sul contratto composto da manifest, firma e stato. Ha
inoltre rilevato che il firmatario generale può ricalcolare il digest del
codice durante una promozione linguistica e che il loader può rileggere byte
diversi da quelli verificati. Questi limiti non annullano il contratto di
localizzazione qui certificato: sono assegnati a RM-0007 e alla proposta ADR
0223, che devono essere completate prima dei nuovi blocchi di RM-0002. Il
successivo legame fra codice verificato e byte effettivamente eseguiti è
censito separatamente come `EXEC-BIND-001` e non riapre RM-0005.

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

### 2.1 Implementazione conclusa

- `install/disclaimer.py` accetta un tag BCP-47 strutturalmente valido; conserva
  la scelta accettata e la fase 3 materializza una richiesta firmata,
  idempotente e atomica mantenendo l’operatività in inglese quando necessario.
- `runtime/config.py` è l'autorità unica di `INSTANCE_LANG`, `REQUESTED_LANG` e
  `LOCALIZATION_STATE`; verifica la firma al riavvio e non blocca l'avvio per
  input o documenti invalidi.
- `runtime/i18n.py` fornisce catalogo SQLite, hash di provenienza e fallback
  `lingua dell’istanza → lingua bootstrap`.
- `runtime/i18n_translator.py` distingue testi user-facing e testi destinati a
  un altro LLM; traduce prompt lunghi, descrizioni dei manifest e messaggi.
- `runtime/i18n_registry.py`, `i18n_materializer.py`, `i18n_pipeline.py` e
  `i18n_activation.py` implementano inventario, lease, traduzione, review,
  promozione e attivazione atomica per ogni tag BCP-47 valido.
- `deploy/run_prompts_translator.sh` avanza un batch limitato dal registro
  firmato senza elenchi di lingue e senza attivazione implicita.
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
- UI e servizi espongono risorse editoriali enumerate; il device è rigenerato
  soltanto dal seed pubblico ammesso; Tutor viene ricompilato dopo verifica dei
  manifest e dichiara lo stato `bootstrap_english` fino all’attivazione.
- Il gate post-promozione rilegge prompt, manifest, messaggi, lessico,
  documenti pubblici, device e Tutor; la fixture di accettazione dimostra
  idempotenza, fallback controllato e una terza lingua sintetica.

### 2.2 Limiti intenzionali

- Regex e forme di consenso restano in `manual_review`: non sono falsamente
  conteggiate come tradotte.
- L’attivazione e il riavvio sono amministrativi ed espliciti; il job notturno
  prepara e verifica, ma non cambia autonomamente la lingua dell’istanza.

## 3. Contratto di prodotto implementato

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
2. Se il corpus del codice è già certificato, lo usa direttamente.
3. Altrimenti crea una richiesta di localizzazione firmata e idempotente.
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

## 4. Fasi implementate

Le specifiche seguenti costituiscono il record dei confini realizzati. Ogni
estensione deve continuare a rispettarne i criteri e i test indicati.

### F0 — Inventario e gate di istanza · `implemented`

File: `runtime/config.py`, `runtime/i18n.py`, `install/disclaimer.py`.

- Definire `INSTANCE_LANG`, `REQUESTED_LANG` e `LOCALIZATION_STATE` in un solo
  modulo di configurazione.
- Normalizzare BCP-47; rifiutare input vuoto o ambiguo senza interrompere il
  boot.
- Persistire una richiesta firmata con codice, timestamp, versione del corpus
  e stato. Scrittura atomica (`tmp` + `os.replace`).
- Rendere `current_lang()` esclusivamente istance-scoped in produzione.
- Aggiungere test di riavvio, input invalido e doppia esecuzione installer.

**Stato 2026-08-23:** implementato. La scelta accettata viene firmata in fase 3
con la chiave autore dell'installazione e scritta atomicamente; il documento
porta codice operativo, obiettivo, data, versione del corpus e stato. Il boot
risolve una sola autorità, rifiuta alterazioni senza fermarsi e il contesto di
richiesta può soltanto propagare `instance_lang`. Gate:
`test_instance_language_config.py` e suite i18n completa.

### F1 — Rimozione degli override per utente/turno · `implemented`

File: `runtime/i18n.py`, `runtime/channels/daemon.py`,
`runtime/recurring_tasks.py`, `runtime/http_routes_agent.py`.

- Eliminare l’uso operativo di `language_context` per preferenze utente.
- Conservare un contesto di richiesta solo per propagare `instance_lang`, mai
  per sostituirlo.
- Rimuovere lookup di `users.get_pref(..., "lang", ...)` dai canali.
- Aggiornare i test che oggi pretendono lingue diverse nella stessa istanza.
- Vietare in lint ogni chiamata a `language_context` con valore proveniente da
  identità, canale o payload HTTP.

### F2 — Registry delle risorse linguistiche · `implemented`

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

### F3 — Materializzazione della lingua richiesta · `implemented`

- Leggere `requested_locale` e creare, in modo idempotente, le directory
  `runtime/prompts/<lang>/`, gli stati dei manifest, le righe i18n e i lessici
  pending.
- Non copiare testo tradotto già esistente senza hash e provenienza.
- Registrare ogni risorsa nel registry prima di invocare il modello.
- Se una risorsa è strutturalmente non traducibile, marcarla `manual_review`;
  non dichiararla completata.

### F4 — Traduzione dei prompt e del proposer · `implemented`

- Enumerare tutti i `.j2` sotto la lingua bootstrap e i prompt generati dal
  proposer/synt.
- Mascherare Jinja, JSON, identificatori, nomi executor e placeholder.
- Tradurre con il template LLM-targeted esistente; conservare numero di sezioni,
  regole, esempi e forza prescrittiva.
- Validare MiniJinja, placeholder, rapporto di lunghezza e assenza di sentinel.
- Scrivere candidato e hash; il loader usa solo una risorsa con stato ammesso.

### F5 — Traduzione dei manifest e dei contratti · `implemented`

- Enumerare `description`, descrizioni degli argomenti, hint e testi di output.
- Tradurre soltanto valori linguistici; non cambiare chiavi, enum, schema,
  capability, path, nomi executor o policy.
- Rifirmare il manifest dopo ogni modifica atomica.
- Eseguire `manifest_lint`, test di nascita e verifica della firma.

### F6 — Lessico di comprensione e proposer · `implemented`

- Convertire il lessico hardcoded in registri `(concept, lang)`.
- Tradurre forme naturali e mapping semplici; lasciare a revisione umana regex
  complesse, parole che decidono il consenso e marker di sicurezza.
- Il proposer deve ricevere prompt e lessico nella stessa `instance_lang`.
- Testare equivalenza semantica su fixture IT/EN e sulla nuova lingua; vietare
  che la traduzione alteri un identificatore canonico.

**Stato 2026-08-23:** implementato per l’intero lessico censito. Il
test `test_action_vocabulary_i18n.py` materializza una terza lingua sintetica,
verifica detection, rendering, fallback e copertura; il daemon accetta un
mapping tradotto soltanto se conserva esattamente tutte le chiavi canoniche e
forme non vuote. Regex e consenso sono eccezioni tipizzate a revisione manuale.
Estensione ai lessici dei resolver deterministici e vincolo di inventario:
§6.2.

### F7 — Runtime, dispositivi e Tutor · `implemented`

- Allineare messaggi, notifiche e UI tramite il catalogo i18n.
- Generare il repertorio device dalla lingua ammessa e dalla distribuzione
  pubblica, mai da dati personali o cataloghi locali.
- Aggiungere la nuova lingua al corpus pubblico solo dopo gate di qualità;
  compilare Tutor con fonti pubbliche e manifest verificati.
- Il Tutor deve dichiarare chiaramente `bootstrap_english` finché la lingua non
  è abilitata.

### F8 — Gate di attivazione e manutenzione · `implemented`

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

RM-0005 supera il gate tecnico quando una fixture installa una lingua nuova,
avvia l’istanza in inglese, completa la pipeline e dimostra dopo riavvio:

1. la stessa lingua per chat web, Telegram, attività pianificate e device;
2. proposer, prompt, manifest, messaggi e lessico nella lingua target;
3. fallback inglese controllato per una singola risorsa mancante;
4. nessun override per utente o turno;
5. firma e schema dei manifest invariati;
6. Tutor compilato esclusivamente da fonti ammesse;
7. ripresa idempotente dopo interruzione del traduttore;
8. report di copertura riproducibile e zero dati riservati.

**Esito:** criteri dimostrati da
`tests/runtime/i18n/test_i18n_activation.py::test_full_acceptance_is_idempotent_and_runtime_surfaces_share_locale`,
dalla suite `tests/runtime/i18n/`, dal lint F1 e dalla verifica di tutte le
firme dei manifest installabili.

### 6.1 Closeout

RM-0005 è `closed` dal 23 agosto 2026 perché non restano fasi o gate aperti:

- F0-F8 risultano implementate e referenziate nelle ADR 0219-0220;
- la suite pertinente ha concluso con 579 test superati e 1.118 subtest;
- l’export sanificato è pubblicato sul ramo pubblico `main` nel commit
  `c03f29e587e8f017e7456767b33eaf0fd0cfac8a`;
- la documentazione bilingue è stata distribuita su Cloudflare Pages e
  verificata sul dominio canonico `https://metnos.com`;
- il repository sorgente non presenta modifiche residue dopo la pubblicazione.

### 6.2 Manutenzione 2026-08-30 — inventario deterministico del lessico

Verifica richiesta su RM-0005 chiusa; nessuna fase riaperta. Il codice
committato risultava verde (398 test i18n, i quattro gate nominati in F0/F6/F8
e la verifica dei contratti builtin). La verifica ha però trovato, nel lavoro
in corso che porta i lessici dei resolver deterministici nel registro
(`runtime/detection_lexicon_seed_resolvers.py`), una violazione di §3.3
«inventario deterministico».

**Difetto.** Gli undici concetti `resolver.*` / `fast_path.*` entravano nel
registro soltanto quando un modulo consumatore veniva importato, non tramite
`ensure_seeded()`. Conseguenza misurata su registro pulito: `enqueue_language`
accodava 91 concetti su 102 e **zero** degli undici lessici dei resolver, dei
quali nove sono automaticamente traducibili. Il gate di copertura poteva quindi
leggere una lingua come completa mentre `fast_path` e i resolver restavano
nella lingua di bootstrap — esattamente il rischio «falsa sicurezza da
copertura nominale» del §7. La proprietà «un concetto esiste se e solo se
`ensure_seeded()` lo registra» non era nuova al lavoro in corso, ma le sue due
prove rosse l'hanno resa visibile: `manual_review_concepts()` dichiarava dieci
concetti a revisione umana mentre il registro appena creato ne conosceva otto.

**Correzione.** `detection_lexicon_seed.register_all()` registra ora anche i
lessici modulari, così `ensure_seeded()` torna autorità unica dell'inventario.
Si richiama `register_all()` e non `ensure_registered()`: il secondo accoda, e
l'accodamento rientra in `ensure_seeded()`.

**Prove.** Inventario 102/102 registrati e 102/102 accodati per una lingua
nuova, undici resolver compresi; `tests/runtime/i18n/` 413 test superati e
1.136 subtest, senza modificare alcuna prova.

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
- `runtime/i18n_registry.py`
- `runtime/i18n_materializer.py`
- `runtime/i18n_pipeline.py`
- `runtime/i18n_activation.py`
- `decisions/0220-versioned-localization-admission.md`
- `deploy/run_prompts_translator.sh`
- `install/disclaimer.py`
