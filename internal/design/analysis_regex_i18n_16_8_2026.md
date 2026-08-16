# Le regex e i termini cablati non passano dall'i18n — analisi

**Data**: 16 agosto 2026 · **Stato**: analisi con misura, nessuna modifica
**Richiesta**: analisi approfondita dell'uso delle regex; devono passare dal
processo i18n per qualunque lingua corrente di Metnos. Verificare anche
l'esistenza di liste di termini in una o più lingue cablate nel testo.

> **AGGIORNAMENTO 16/8, sera — decisione presa.** La regex si GENERA da un
> elenco di parole, prima dell'uso o al boot, e si prende da un dizionario di
> regex compilate — che in gran parte esiste gia' (`_regex_cache`). Il
> vincolo aggiunto da Roberto: l'elenco dei termini e' di **lunghezza
> imprecisata**, perche' in una lingua piu' termini italiani possono cadere
> in uno solo; il template deve trattarlo come tale, e la scrittura
> dell'elenco va curata molto. Il refactor e' **completo e one-off, ma non
> ora**. Le istruzioni implementative stanno in
> `spec_regex_generate_dal_lessico.md`, scritte per essere eseguite da un
> modello di classe inferiore.
>
> Tutti i numeri vengono da una scansione AST di `runtime/`, `executors/`,
> `scripts/` e `install/` eseguita il 16/8 (script in
> `scratchpad/sweep2.py`), non da impressioni.

---

## 1. Il risultato in una tabella

| Che cosa | Quante | Passano dall'i18n? |
|---|---|---|
| Regex letterali che contengono parole | 303 | — |
| ⤷ di cui **tecniche** (path, tag, placeholder, estensioni) | 187 | non serve |
| ⤷ di cui **scritte in una lingua** (53 solo IT, 45 IT+EN, 18 solo EN) | **116** | **NO** |
| Concetti nel `detection_lexicon` | 93 | sì, ma… |
| ⤷ di cui `kind="regex"` | **25** | **il daemon di traduzione li SALTA** |
| ⤷ di cui `phrases` / `mapping` (traducibili) | 68 | sì |
| Copertura lingue di TUTTI i 93 concetti | it+en | **nessuna terza lingua** |
| Liste di termini in lingua cablate nel codice | 99 | **NO** |
| ⤷ di cui nel percorso della RICHIESTA utente | **24 liste, 286 termini** | **NO** |

Detto in una riga: **il meccanismo i18n esiste, funziona, ed è aggirato in
tre modi diversi.**

Le tre componenti hanno tre trattamenti distinti, e ognuna ha la sua sezione
con problema e soluzione:

| Componente | Problema | Soluzione | Dove |
|---|---|---|---|
| Regex di lingua nel codice (116) | scritte a mano, invisibili al processo i18n | template con slot a cardinalità libera, regex GENERATA | §5, spec §2-§5 |
| Il dizionario stesso (93 concetti) | `register()` sa contare fino a due; l'unione nasconde i buchi; 25 concetti si accodano in eterno | firma a dizionario di lingue, unione osservabile, due esiti distinti | **§8**, spec parte B |
| Liste di parole cablate (24, 286 termini) | cinque forme diverse, una sola ricetta non basta | nel lessico ciò che cambia con la lingua, e solo quello | **§9**, spec parte C |

---

## 2. I tre modi in cui l'i18n viene aggirato

### 2.1 Regex di lingua scritte direttamente nel codice — 116

Sono le più gravi perché stanno nel percorso caldo del planner e nessuno le
vede come testo traducibile. Campioni reali:

```python
# runtime/engine/dispatch.py:3319
r"(?i)\b(cerca(mi)?|trova(mi)?|search|find|apri|open|leggi|read|scarica|
   download|mostra(mi)?|show)\b"

# runtime/engine/dispatch.py:3321
r"(?i)\b(il|lo|la|i|gli|le|un|uno|una|the|a|an|di|del|della|dei|degli)\b"

# runtime/engine/dispatch.py:5536
r"(?:\bnuov[aoe]\s+cartell\w*\b|\bnew\s+folder\b|\bnon\s+sovrascriv\w*\b|…)"

# runtime/prefilter_rules.py:87
r"\b(?:oggi|ieri|domani|today|yesterday|tomorrow|ora|now|ultim[ae]|…)"

# runtime/agent_runtime.py:163
r"^\s*\(\s*(?:posso provare|posso cercare|posso aiutarti|posso suggerirti|…)"
```

Distribuzione: `engine/dispatch.py` 20 · `agent_runtime.py` 15 ·
`prefilter_rules.py` 13 · `time_window_parser.py` 12 · `compound_decomposer.py`
7 · `describe_entries.py` 4 · altri 45 file con 1-4 ciascuno.

Per una terza lingua queste regex **non matchano nulla** e falliscono in
silenzio: nessun errore, solo un comportamento che smette di funzionare.

### 2.2 Concetti `kind="regex"` nel lessico — 25 su 93

Questi *sono* registrati nel `detection_lexicon`, quindi sembrano a posto. Non
lo sono: il daemon di traduzione li rifiuta **per scelta dichiarata**

```python
# runtime/jobs/detection_translate_pending.py
#  - regex: NON auto-generato (un regex sbagliato e' peggio del gap). Resta
```

e li conta in `skipped_regex` lasciandoli pendenti per sempre. La motivazione
è corretta (una regex sbagliata è peggio di un buco), ma la conseguenza non è
stata tratta: **il 27% dei concetti è un muro a due lingue** che nessun
processo automatico può superare.

I 25: `confirm.yes/no`, `count.cap_pattern`, `help.*` (5),
`output.count_request`, `output.visualize_request`, `query.multistep`,
`sites.collection_search_request`, `tasks.recurrence_phrase`,
`tasks.schedule_phrase`, `text.auxiliary_verb`, `text.request_verb`,
`tutor_gate.*` (9).

Nota: `confirm.yes` / `confirm.no` sono il sì/no dell'utente ai dialoghi di
conferma. In una lingua non coperta, **l'utente non può confermare niente.**

### 2.3 Liste di termini cablate nel codice — 99, di cui 24 nel percorso della richiesta

Le più grosse, con il conteggio dei termini:

| File | Termini | Che cosa contengono |
|---|---|---|
| `runtime/prefilter.py` | 81 | prossimità (33), EXIF/foto (26), tempo (16) |
| `runtime/fast_path.py` | 79 | «che ora è» (13), «che giorno» (13), «annulla» (19), «dove sono» (11), «chi sei» (23) |
| `runtime/target_device.py` | 29 | «su questo pc», «sul mio portatile», «del server» |
| `runtime/engine/dispatch.py` | 17 | «tutti i file», «escludi/senza includere» |
| `runtime/compare_entries.py` | 13 | «simile», «similarità», «distanza semantica» |
| `runtime/skill_codegen.py` | 12 | «sito», «apri», «avvia sessione» |
| `runtime/describe_images.py` | 11 | «descrivi foto», «cosa c'è nella foto» |
| `runtime/store_entries.py` | 11 | «archivio», «raccolta», «registro dati» |
| `runtime/backend_resolver.py` | 7 | «calendario locale», «in locale» |
| altri 5 file | 26 | varie |

Queste violano direttamente la regola già registrata come feedback di Roberto
(«MAI liste nomi/sinonimi hardcoded — usa `detection_lexicon` + resolver»).

---

## 3. Un caso che vale da solo: la prossimità, tre volte

`prefilter.py:741` contiene **33 termini di prossimità** cablati
(`"vicino a me", "vicina", "piu vicino", "in zona", "near me", "nearest"…`)
per iniettare `get_location` nel pool quando la query è location-relative.

Oggi stesso, per un difetto diverso, ho registrato nel lessico il concetto
`geo.self_proximity` con **le stesse forme**, per far incatenare `get_location`
dalla guardia `ensure_proximity_center`.

E l'affinity di `find_places` nel manifest contiene **una terza copia**
(`"vicino", "vicina", "vicine", "intorno", "in zona", "nelle vicinanze",
"near", "nearby"…`).

Tre elenchi, tre posti, un solo significato. In una terza lingua se ne
tradurrebbe uno solo. È il sintomo esatto del problema: senza un'autorità
unica, ogni difetto risolto aggiunge un elenco invece di estenderne uno.

---

## 4. Perché è successo: la regola c'è, il meccanismo no

Il `detection_lexicon` è progettato bene: DB locale-driven, `register()`
idempotente, coda di traduzione, `enqueue_language()` per aggiungere una
lingua in un colpo solo. Ma ha **due buchi strutturali** che spingono chi
scrive codice a girargli intorno:

1. **`register()` accetta solo `it=` ed `en=`.** La firma è
   `register(concept, kind, *, it, en, match_mode)`: due parametri fissi. Non
   c'è modo di seedare un concetto in tre lingue anche volendo. Le altre
   lingue esistono solo come righe generate dal daemon.
2. **Per una regex il daemon non genera niente.** Chi ha bisogno di
   morfologia (`vicin\w+`, `cartell\w*`) sceglie `kind="regex"` e con quella
   scelta esce dal processo i18n senza accorgersene: il concetto è nel DB, ma
   in due lingue per sempre.

Quando il meccanismo corretto costa più di quello sbagliato, il codice usa
quello sbagliato. Le 116 regex e le 99 liste sono la misura di quel costo.

---

## 5. Che cosa vuol dire «sottoporre le regex al processo i18n»

Non significa tradurre una regex. Significa **non scrivere più regex di
lingua**: una regex è un artefatto *derivato*, e ciò che si traduce è
l'elenco di parole da cui deriva.

Tre forme possibili, in ordine di preferenza:

**(a) Elenco di frasi, regex generata.** Il concetto si registra come
`phrases` (traducibile); il matcher costruisce la regex a partire dalle
forme, con la flessione ottenuta da un troncamento dichiarato per lingua.
Copre il 90% dei casi reali: quasi tutte le 116 regex sono alternanze di
parole con qualche `\w*`.

**(b) Regex con segnaposto.** Il pattern resta nel lessico ma con i termini
sostituiti da riferimenti al vocabolario tradotto: `\b{VERBI_RICERCA}\s+
{OGGETTI_FILE}\b`. La struttura è invariante fra lingue, il contenuto no.
Serve per i pochi pattern con struttura sintattica vera (ordine, negazione).

**(c) Regex scritta a mano per lingua.** Quello che si fa oggi, ma
**dichiarato**: il concetto resta `regex` e la nuova lingua entra in una coda
di lavoro umano invece di sparire in `skipped_regex`. Va bene solo per i casi
in cui (a) e (b) non bastano, e deve essere l'eccezione visibile, non la
regola invisibile.

Il vero lavoro non è convertire 116 regex: è **rendere (a) più comodo di
scriverne una a mano**. Finché non lo è, la deriva riprende.

---

## 6. Che cosa NON è un problema

Per onestà del conteggio, queste 187 regex restano dove sono e non entrano
nel lavoro:

- placeholder e sintassi interna: `${FILLER:…}`, `${RUNTIME:…}`, `^now$`,
  `^last-(\d+)d$`;
- percorsi e nomi di file: `/etc/(passwd|shadow)`, `\.(jpg|png|heic)\b`,
  `.aws/credentials`;
- marcatori di formato: tag HTML, URL, hash, ISO date;
- pattern di lint sul NOSTRO testo (`prompts_lint`, `manifest_lint`): non
  leggono la richiesta dell'utente, controllano quello che scriviamo noi.
  Restano italiani perché il corpus che controllano è italiano.

Analogamente, le 55 liste in `runtime/testing/populate_cases.py` (casi di
prova) e le 14 in `runtime/ui_surfaces.py` (etichette di interfaccia, già
per-lingua per costruzione) non sono debito.

---

## 7. Ordine di lavoro proposto

Per rischio decrescente, cioè: che cosa si rompe per primo se qualcuno
aggiunge una lingua domani.

1. **`confirm.yes` / `confirm.no`.** Senza questi l'utente non può rispondere
   a una carta di conferma. Sono `regex` oggi; diventano `phrases`.
2. **Il percorso della richiesta**: `prefilter.py` (81 termini), `fast_path.py`
   (79), `target_device.py` (29), `dispatch.py` (17). Sono 206 termini in
   quattro file e coprono la maggior parte del traffico.
3. **Rendere (a) comodo**: matcher che compila una regex da un elenco di
   frasi con flessione dichiarata; `register()` che accetta un dizionario di
   lingue invece di due parametri fissi.
4. **Le restanti 20 regex `kind="regex"`**, convertite a (a) o (b) dove
   possibile, dichiarate come (c) dove no.
5. **Deduplicare i tre elenchi di prossimità** (§3) su un'unica autorità.
6. **Una guardia** che impedisca la ricomparsa: un test che fallisce se una
   nuova regex letterale nel percorso della richiesta contiene parole di
   lingua. Senza, questo documento andrà riscritto fra sei mesi con numeri
   più grandi.
7. **I tre difetti del dizionario** (§8): firma di `register()`, unione
   osservabile, coda che converge. Il punto 1 va fatto PRIMA delle
   migrazioni, o si convertono 93 concetti due volte.
8. **Le 24 liste cablate** (§9), per forma: prima A e B (meccaniche), poi
   C (fast_path, che cambia fonte ma non semantica), poi D ed E.

---

## 8. Il dizionario multilingua stesso — problema e soluzione

Le regex sono il sintomo più visibile, ma il dizionario che dovrebbe
risolverle ha tre difetti propri. Vanno trattati, altrimenti il refactor
delle regex sposta il debito senza estinguerlo.

### 8.1 `register()` sa contare fino a due

```python
def register(concept: str, kind: str, *, it, en, match_mode="substring")
```

Due parametri fissi. **Non esiste modo di seedare un concetto in tre lingue,
nemmeno volendo.** `SEED_LANGS = ("it", "en")` è coerente con la firma, e
tutto il resto del modulo (fallback, unione, copertura) è invece già
locale-driven. È l'unico punto dove le due lingue sono cablate nella
struttura, non nei dati.

**Soluzione**: la firma diventa `register(concept, kind, *, forms, match_mode)`
dove `forms` è `dict[str, payload]` — `{"it": [...], "en": [...], "de": [...]}`.
`SEED_LANGS` smette di essere una costante e diventa **derivata**: le lingue
seedate sono le chiavi che il seed ha effettivamente scritto. Le 93
registrazioni esistenti si convertono meccanicamente
(`it=X, en=Y` → `forms={"it": X, "en": Y}`), e questo è l'unico motivo per cui
il cambio è a basso rischio: è una trasformazione sintattica verificabile.

### 8.2 L'unione fra lingue nasconde i buchi

`_union_langs()` unisce sempre `{lingua corrente} ∪ {it, en}`. Per un'istanza
in tedesco, un concetto senza forme tedesche continua a matchare le forme
italiane e inglesi.

Questo è **voluto** e va tenuto: copre i comandi-prestito («undo», «ok») che
un tedesco scrive davvero. Ma ha una conseguenza non tratta: **il degrado non
si vede**. Il sistema sembra funzionare, risponde a un sottoinsieme di frasi
che nessun tedesco scriverebbe, e l'utente conclude che Metnos «a volte non
capisce». Un fallimento intermittente è più difficile da diagnosticare di uno
totale.

**Soluzione**: non togliere l'unione — renderla **osservabile**. Ogni match
che è avvenuto SOLO grazie a una lingua di prestito è un dato: se
`geo.self_proximity` in un'istanza tedesca matcha sempre e solo via `it`,
quel concetto è di fatto non tradotto anche se la riga esiste. Un contatore
per `(concetto, lingua_che_ha_matchato)` trasforma un'impressione in una
misura, ed è lo stesso schema già usato per gli spari delle guardie
(`engine/guard_stats.py`): contatore persistente, riepilogo notturno,
verdetto umano.

### 8.3 La copertura si accoda in eterno per 25 concetti

`_startup_coverage_check()` è turnkey: se la lingua d'istanza non copre tutti
i concetti, avvisa e chiama `enqueue_language()`. Ma il daemon **salta i
`kind="regex"`**. Quindi in un'istanza tedesca:

1. il boot accoda 93 concetti;
2. il daemon ne traduce 68 e ne salta 25;
3. il boot successivo riavvisa, riaccoda gli stessi 25;
4. per sempre.

Un meccanismo che non converge e lo dice ogni volta con lo stesso testo è
indistinguibile, per chi legge i log, da un meccanismo rotto.

**Soluzione**: due esiti distinti, come per il Tutor.
- **Pendente** = tradurremo, il daemon ci arriva.
- **Non traducibile automaticamente** = richiede una persona, e va in una
  coda di lavoro umano visibile in `/admin`, non nel log di boot.

Dopo il refactor a template (§5.a) i concetti in questa seconda categoria
scendono da 25 a pochissimi, ma la distinzione serve comunque: è ciò che
impedisce a un buco permanente di travestirsi da coda.

---

## 9. Le liste di parole cablate — problema e soluzione

Le 24 liste nel percorso della richiesta (286 termini) **non hanno tutte la
stessa forma**, e questo è il motivo per cui non basta dire «spostatele nel
lessico». Cinque forme, cinque destinazioni diverse.

| # | Forma nel codice | Esempio | Destinazione |
|---|---|---|---|
| A | Elenco piatto, match per sottostringa/parola | `prefilter.py` EXIF (26 termini) | `kind="phrases"` |
| B | Elenco i cui gruppi hanno **comportamento diverso** | `target_device.py`: adjunct si strippa, nominal no | `kind="mapping"` |
| C | Elenco che diventa un **indice a lookup esatto** | `fast_path.py`: `_PATTERN_INDEX[norm]` | `phrases` + indice costruito da `forms()` |
| D | Frammento di regex **interpolato** in un pattern più grande | `target_device.py::_PREP_NOMINAL` dentro una regex | slot di un `template` (§5.b) |
| E | Elenco **accoppiato a struttura** (nomi di tool, precursori) | `prefilter.py::_QUERY_DEPENDENT_PRECURSORS` | le parole al lessico, la struttura resta nel codice |

La forma C merita una nota, perché è quella che si sbaglia: `fast_path` non
fa un match, fa un `dict[norm]`. Migrarlo a `match()` ne cambierebbe la
semantica — da uguaglianza esatta a contenimento — e il modulo è
deliberatamente esatto («niente regex, niente fuzzy») per non rubare query al
planner. La migrazione corretta lascia il lookup esatto e sostituisce solo la
**fonte** dei termini: `forms(concept)` invece della tupla letterale, con
l'indice ricostruito quando il lessico cambia.

La forma E è quella che si sbaglia nell'altro senso: `_QUERY_DEPENDENT_PRECURSORS`
associa `find_places → get_location` quando la query è location-relativa. Il
nome dei due tool **non è lingua** e non va nel lessico; i 33 marcatori sì.
Spostare tutto significherebbe mettere nel dizionario multilingua dei nomi di
executor, che non si traducono.

**Soluzione generale**: un unico principio, applicato per forma —
*nel lessico va ciò che cambia con la lingua, e SOLO quello.* Il resto
(struttura, nomi di tool, comportamento di strip, tipo di match) resta dove
sta e legge le parole dal lessico.

**Un corollario che vale la pena scrivere**: due delle 24 liste sono la stessa
lista (§3, la prossimità in tre posti). Applicando il principio, `prefilter`,
la guardia `ensure_proximity_center` e l'affinity di `find_places` leggerebbero
tutti e tre da `geo.self_proximity`. L'affinity è il caso limite — vive in un
manifest firmato e la si cambia solo con una misura (regola di Roberto) — e
per ora resta duplicata: va segnata come debito residuo, non risolta di
soppiatto.

---

## 10. Decisioni aperte, per Roberto

1. **Fin dove arrivare.** Le 116 regex e le 24 liste sono ~4-5 giorni di
   lavoro con test. I punti 1-2 di §7 (i più rischiosi) sono ~1 giorno e
   coprono la parte che si rompe davvero.
2. **`register()` a dizionario di lingue** (§4.1): è un cambio alla firma di
   un modulo centrale usato da 93 concetti. Da fare adesso o quando arriva la
   terza lingua? La memoria del debito i18n dice «solo su aggiunta lingua»;
   questa analisi mostra che rimandare costa deriva continua.
3. **La guardia anti-ricomparsa** (§7.6): la si vuole? È l'unica cosa che
   rende il lavoro permanente, ma renderà più scomodo scrivere codice che
   legge testo utente — che è esattamente il punto.

---

## Riferimenti

Scansione: `scratchpad/sweep2.py` (AST su `runtime/`, `executors/`,
`scripts/`, `install/`). Meccanismo: `runtime/detection_lexicon.py`,
`runtime/detection_lexicon_seed.py`,
`runtime/jobs/detection_translate_pending.py`. Debito già registrato:
memoria `project_i18n_lexicon_debt.md`, e la nota in testa a
`detection_lexicon_seed.py` sul dict `{it,en}` a due locali fissi.
