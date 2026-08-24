# RM-0002 — Linter multilingue dei manifest executor

**Stato:** `active`  
**Creazione:** 2026-07-23  
**Ultima revisione:** 2026-08-24 (riverifica completa contro codice e catalogo
correnti; nessun manifest modificato)  
**Implementazione:** non iniziata come motore multilingue. Dal 23 agosto 2026
RM-0005 ha però realizzato **due pezzi del disegno qui proposto** — la
pubblicazione transazionale dei contratti tradotti (§7.11) e un gate di
ammissione che invoca il linter prima di attivare una lingua — e con essi ha
reso **raggiungibile** il difetto centrale che questa roadmap descrive: quel
gate controlla la lingua scelta da `_description_text()`, non la lingua appena
prodotta. Vedi §1.1, §4.11 e §4.12  
**Conservazione:** roadmap persistente fino a implementazione dimostrata o
cancellazione esplicita di Roberto  
**Decisione di prodotto:** tutte le superfici linguistiche che possono essere
lette dal planner devono rispettare gli stessi invarianti strutturali, senza
usare l'italiano come approssimazione delle altre lingue e senza riscrivere
automaticamente la semantica dei manifest  
**Documenti di origine:** `CLAUDE.md` §2.5 e §7.9, ADR 0092, ADR 0219-0220,
RM-0005, `internal/design/TODO.md::EXE-DESC-001`, codice e catalogo verificati
il 2026-07-23 e rimisurati il 2026-08-24  
**Prove iniziali:** audit read-only di 115 manifest, simulazione del linter per
IT/EN, confronto degli atomi macchina e misura del percorso adattivo  
**Prove di riverifica (2026-08-24):** audit read-only di 122 manifest, linter
rieseguito per lingua, riconteggio delle lunghezze e degli atomi macchina,
audit di `manifest.lang_state.json`, lettura di `i18n_materializer.py`,
`i18n_pipeline.py` e `i18n_activation.py`; nessun file di prodotto modificato

## 1. Sintesi decisionale

Metnos deve avere **un solo linter multilingue**, deterministico e centrale.
Non servono un linter italiano e uno inglese, né regole duplicate per ogni
lingua. Il motore deve enumerare tutte le varianti testuali presenti nel
manifest, applicare una sola volta i controlli indipendenti dalla lingua,
applicare a ogni variante i controlli locali e confrontare fra lingue soltanto
gli invarianti macchina che devono restare identici.

La realizzazione raccomandata ha cinque proprietà:

1. controlla ogni lingua realmente presente, non la sola `it` e non una lingua
   scelta per ripiego;
2. separa contratto globale, forma locale e parità fra traduzioni;
3. opera appena prima di scrittura, traduzione, firma o promozione;
4. non modifica, accorcia o traduce alcun testo;
5. non entra nel percorso ordinario dei turni e non può quindi cambiarne
   latenza, piani o autorità.

Il linter non deve fingere di comprendere la traduzione. Può però provare in
modo affidabile che non siano cambiati nomi di executor, argomenti della
chiamata, segnaposto runtime, riferimenti fra tool e forma dichiarata
dell'output. Il significato residuo resta materia di revisione editoriale,
corpus di instradamento ed eventuale valutatore semantico separato.

La lunghezza inglese è minore soltanto **in aggregato**. Non è un'invariante
utilizzabile: nel catalogo reale l'inglese è più lungo in 24 descrizioni
principali su 115 e in 138 descrizioni argomento su 654 (rimisurato il 24 agosto
2026: 27 su 122 e 147 su 693). Un campo supera il limite soltanto in inglese e
un altro soltanto in italiano. Un linter solo italiano ha quindi falsi negativi
dimostrati, e un linter solo inglese li avrebbe a sua volta.

### 1.1 Aggiornamento del 24 agosto 2026 (riverifica)

L'analisi del 23 luglio è stata rieseguita per intero contro il codice e il
catalogo correnti. **Le conclusioni reggono tutte**; tre cose sono cambiate e
vanno lette prima del resto del documento.

**1. Il linter è ancora monolingue, ma adesso è un gate di attivazione.**
`manifest_lint._description_text()` è invariato: sceglie `it`, poi `en`, poi la
prima chiave disponibile. Nel frattempo RM-0005 ha aggiunto un chiamante nuovo,
`i18n_activation.validate_manifests()` (`runtime/i18n_activation.py:452-487`),
che per ogni contratto esegue `lint_file()` e `verify_executor()` e **blocca
l'attivazione di una lingua** se trova un errore. `lint_file()` non accetta un
parametro lingua. Conseguenza dimostrabile: quando l'istanza attiva una terza
lingua, il gate rilegge l'italiano e dichiara ammessa una superficie che non ha
mai guardato. Il difetto descritto a luglio come igiene di authoring è oggi un
difetto di correttezza sul percorso RM-0005. Dettaglio in §4.11.

**2. La parità degli atomi macchina non è più a zero divergenze.** A luglio le
quattro classi confrontate davano zero divergenze su 115 manifest. Oggi
`set_signatures` ha un `PATTERN` inglese che insegna al planner un argomento in
più rispetto all'italiano (`reason=`, §4.12). È esattamente la classe che §7.7
dichiara errore bloccante, ed è comparsa in un mese senza che nulla la
fermasse: nessuno dei controlli attivi confronta le chiamate fra lingue.

**3. Parte del disegno è già stata costruita, da RM-0005 e non da qui.**
`i18n_pipeline._promote_contracts()` implementa la pubblicazione transazionale
di §7.11 — candidato in memoria, confronto con l'originale, scrittura atomica,
rifirma, e ripristino di testo **e firma** in caso di errore. Ma la validazione
del candidato di un contratto è soltanto `_validate_common()`: parità dei token
Jinja, dei segnaposto `{...}` e del codice fra apici inversi, assenza di
sentinella, rapporto di lunghezza fra 0,35 e 3,5. Misurato sul catalogo:
**0 dei 170 capitoli `PATTERN:` dei manifest core — 85 manifest per due lingue —
contiene apici inversi**, quindi quella tutela non copre nessuna chiamata. I capitoli `SCOPO:/PATTERN:/NON:/OUT:`
non sono controllati affatto. Dettaglio e conseguenze in §4.11.

Il lavoro che resta a RM-0002 è quindi più piccolo e più mirato di quanto il
piano di luglio prevedesse: non serve più costruire la transazione di
pubblicazione, serve **dare una lingua** al motore che quella transazione e
quel gate già invocano.

## 2. Obiettivo e valore

Il manifest è contemporaneamente:

- contratto firmato dell'executor;
- superficie di scelta del planner locale;
- fonte di termini per il prefiltro;
- sorgente di alcuni comportamenti deterministici di estrazione argomenti;
- sorgente per traduzione, importazione e generazione;
- documento operativo per persone e modelli locali.

Un errore in una sola lingua può quindi produrre un executor formalmente
firmato e caricabile, ma difficile da scegliere o impossibile da invocare in
quella lingua. L'obiettivo di RM-0002 è impedire questa classe di divergenza
senza aumentare la fragilità del runtime e senza imporre una bonifica massiva
dei testi legacy.

Il beneficio atteso è osservabile:

- nessun argomento inesistente suggerito da una variante linguistica;
- nessun argomento risolto dal runtime esposto accidentalmente al modello;
- stessa forma di chiamata nelle traduzioni;
- stessi riferimenti di disambiguazione essenziali;
- avvisi di lunghezza completi e non duplicati;
- errori di traduzione fermati prima della scrittura e della firma;
- stessa politica per executor core, builtin, sintetizzati e importati;
- nessun costo nei turni normali.

## 3. Perimetro e fonti verificate

L'analisi ha seguito il percorso effettivo del codice al 23 luglio 2026:

- `runtime/manifest_lint.py`;
- `runtime/manifest_rules.py`;
- `runtime/loader.py`;
- `runtime/executor_standard.py`;
- `runtime/engine/proposer.py`;
- `runtime/prefilter.py`;
- `runtime/args_extractor.py`;
- `runtime/i18n_translator.py`;
- `runtime/sign.py`;
- `runtime/synt_multistage.py` e `runtime/synth_request.py`;
- `runtime/skill_codegen.py` e `runtime/generated_executor_contract.py`;
- manifest core, contratti builtin e import GitHub installati;
- prove correnti su descrizioni, traduzioni e rendering.

Le misure descrivono il working tree osservato, che contiene anche modifiche
non consolidate. Non provano che il processo HTTP già avviato abbia caricato
gli stessi byte. Gli esperimenti presenti su rendering adattivo e schema
model-facing non costituiscono implementazione di questa roadmap e devono
restare in un intervento separato.

Metnos usa il proprio server locale compatibile con il percorso `llama-server`.
Il disegno qui proposto è deterministico e indipendente dal fornitore LLM; non
introduce dipendenze da Ollama o da servizi esterni.

**Fonti aggiunte nella riverifica del 24 agosto 2026**, tutte lette in sola
lettura: `runtime/i18n_registry.py`, `runtime/i18n_materializer.py`,
`runtime/i18n_pipeline.py`, `runtime/i18n_activation.py`, `runtime/config.py`
(autorità di `INSTANCE_LANG`), `tests/runtime/infra/test_manifest_head_budget.py`,
`tests/runtime/skills/test_manifest_lang_state.py`,
`tests/runtime/skills/test_executor_manifests_gate.py`, RM-0005 e la parte
mutabile di `CLAUDE.md` per ADR 0219-0220. Il catalogo è stato rimisurato dal
filesystem, non da una fotografia precedente.

## 4. Stato reale verificato

### 4.1 Inventario effettivo

Rimisurato il 24 agosto 2026; fra parentesi il valore del 23 luglio quando è
cambiato.

| Classe | Percorso | Manifest trovati | Copertura CLI | Copertura pipeline RM-0005 |
|---|---|---:|---|---|
| core | `executors/*/manifest.toml` | 85 (82) | sì | sì |
| builtin firmati | `runtime/builtin_executor_contracts/*/manifest.toml` | 21 (17) | no | sì |
| import installati | `~/.local/share/metnos/executors/skills/*/*/manifest.toml` | 16 | no | **no** |
| totale osservato | tre classi | **122** (115) | **85/122** | **106/122** |

Tutti i 122 manifest hanno descrizione principale italiana e inglese: 244
superfici principali. Le descrizioni di argomento multilingue sono 693 (654),
tutte complete per `it` ed `en`: 1.386 superfici. Nessun manifest del catalogo
osservato dichiara una terza lingua, quindi il comportamento multilingue del
sistema non è ancora esercitato dal catalogo reale.

Il CLI di `manifest_lint.py --all` usa ancora soltanto `executors/*/manifest.toml`
(`runtime/manifest_lint.py:384-388`): non visita i contratti builtin e non segue
la struttura annidata degli import.

Novità rispetto a luglio: la pipeline RM-0005 possiede un proprio inventario,
`i18n_materializer.LocalizationPaths.manifest_roots`
(`runtime/i18n_materializer.py:56-59`), che visita con `rglob` sia i core sia i
builtin. È metà dell'helper condiviso chiesto in §7.1 — ma **non copre gli
import installati**, che restano invisibili sia al CLI sia alla traduzione. È
un dettaglio con conseguenze concrete: i 16 import contengono tutti e tre gli
errori strutturali noti del catalogo e tutti i 12 companion con placeholder.

La topologia `executors/skills/<skill>/<executor>` è dichiarata in
`config.PATH_SKILLS_BUILTIN` ma è oggi vuota; l'inventario comune deve
prevederla senza dipendere dalla sua popolazione attuale.

### 4.2 Comportamento del linter corrente

`_description_text()` seleziona:

1. `it`, se presente;
2. altrimenti `en`;
3. altrimenti il primo valore disponibile.

La stessa preferenza è ripetuta per ogni descrizione argomento. Di conseguenza
un manifest bilingue normale viene controllato soltanto in italiano. Le lingue
aggiuntive non vengono ispezionate.

La scelta non replica il runtime: `loader._resolve_lang_text()` seleziona la
lingua corrente e, se manca, ripiega prima sull'inglese e poi sulla prima lingua
in ordine alfabetico. Con un'istanza inglese il planner usa quindi proprio la
variante che il linter corrente non controlla. Il prefiltro tokenizza la
descrizione localizzata completa, mentre il proposer testuale usa la testa fino
a `OUT:`; entrambe sono superfici attive.

**Aggiornamento 24 agosto 2026.** La lingua corrente non arriva più da
`METNOS_LANG` ma da `config.INSTANCE_LANG`, autorità unica derivata da una
richiesta firmata (ADR 0219); `METNOS_LANG` resta soltanto l'avvio per
installazioni prive del documento firmato, e la lingua di bootstrap dichiarata è
`en` (`config.BOOTSTRAP_LANGUAGE`). Questo **rafforza** l'argomento di §11.2: la
lingua attiva di un'istanza non è più una variabile d'ambiente di sviluppo ma un
dato installato, e un'istanza può legittimamente avviarsi in una lingua la cui
superficie manifest non è mai stata controllata.

Nota minore osservata nello stesso punto: l'ultimo ripiego di
`loader._localized_builtin_contract()` è `current_lang = "it"`
(`runtime/loader.py:298`), raggiungibile solo se falliscono sia l'import di
`i18n` sia quello di `config`. Contraddice `BOOTSTRAP_LANGUAGE = "en"`. Non è un
difetto attivo ed è fuori dal perimetro di RM-0002; va corretto da chi tocca
quel ramo.

I controlli correnti sono:

- presenza e ordine di `SCOPO/PATTERN/NON/OUT`;
- posizione di `PATTERN` e `NON` rispetto al budget;
- lunghezza di testa, descrizione e argomenti;
- argomenti usati nel `PATTERN`;
- menzioni di argomenti `runtime_resolved`;
- forma dell'output;
- riferimenti morti nel capitolo `NON`;
- sovrapposizione delle affinity.

Il controllo affinity è indipendente dalla lingua, ma una conversione ingenua
del linter a un ciclo `for lang` lo eseguirebbe e lo conterebbe più volte.

### 4.3 Misure linguistiche

Rimisurato il 24 agosto 2026 sul catalogo di 122 manifest; fra parentesi la
misura del 23 luglio su 115 manifest.

| Superficie | Italiano | Inglese | Differenza EN rispetto a IT |
|---|---:|---:|---:|
| descrizioni complete | 30.464 caratteri (28.415) | 29.768 (27.723) | -696 (-692) |
| teste prima di `OUT:` | 23.482 (21.675) | 22.880 (21.086) | -602 (-589) |
| descrizioni argomento | 88.750 (80.164) | 80.359 (71.990) | -8.391 (-8.174) |

L'inglese è quindi più corto nel totale, ma non in ogni risorsa:

- 27 descrizioni principali su 122 sono più lunghe in inglese (erano 24 su 115);
- 147 descrizioni argomento su 693 sono più lunghe in inglese (erano 138 su 654);
- `send_messages.args.to_user.description` misura ancora 180 caratteri in
  italiano e 181 in inglese: soltanto l'inglese supera `ARG_DESC_MAX=180`, ed è
  l'unico argomento del catalogo in questa condizione.

Il superamento di `DESC_MAX=320` è oggi **numericamente simmetrico e
insiemisticamente asimmetrico**, che è una prova più forte di quella di luglio:

| Lingua | Descrizioni oltre 320 | Solo in quella lingua |
|---|---:|---|
| italiano | 9 | `find_dirs` (322 IT / 319 EN) |
| inglese | 9 | `write_files` (313 IT / 323 EN) |

Gli otto casi comuni sono `compute_signatures`, `create_events`,
`create_images_indices`, `delete_persons`, `find_credentials`, `find_urls`,
`get_location`, `get_signatures`. Contare per lingua darebbe «9 e 9» e
sembrerebbe indifferente quale lingua si controlli; l'insieme dimostra il
contrario, perché ciascuna lingua nasconde un caso che l'altra non vede.

Sulle descrizioni argomento la stessa asimmetria è molto più marcata: 115
superano `ARG_DESC_MAX` in italiano e 91 in inglese, di cui **25 soltanto in
italiano e 1 soltanto in inglese**.

Le teste prima di `OUT:` restano tutte entro `HEAD_MAX=240`: 244 su 244, in
entrambe le lingue. Il dato del 23 luglio (230 su 230) è confermato sul catalogo
cresciuto.

Il risultato confuta l'ipotesi operativa «se passa l'italiano, passa anche
l'inglese». La tendenza aggregata non è una proprietà per campo, e un mese di
crescita del catalogo non l'ha resa tale.

### 4.4 Risultati del linter simulato per lingua

Metodo, identico nelle due misure: si costruisce in memoria una copia
monolingue di ciascun manifest (descrizione principale e descrizioni argomento
sostituite dalla sola lingua in esame) e si invoca `lint_manifest()` con il
catalogo completo dei nomi e senza il confronto affinity, che è globale e
raddoppierebbe.

Sul catalogo completo, 122 manifest al 24 agosto 2026 (115 al 23 luglio):

| Lingua | Avvisi di lunghezza | Errori strutturali |
|---|---:|---:|
| italiano | 124 (108) | 3 (3) |
| inglese | 100 (85) | 4 (4) |

Gli avvisi si scompongono esattamente nelle misure di §4.3: 9 descrizioni oltre
`DESC_MAX` più 115 argomenti oltre `ARG_DESC_MAX` in italiano, 9 più 91 in
inglese. Nessun avviso riguarda la testa.

**I quattro errori sono gli stessi di un mese fa, nessuno è stato corretto.**

Tre sono presenti in entrambe le lingue e appartengono a import GitHub già
installati:

- `list_dirs_github` usa `path=` nel `PATTERN`, ma lo schema dichiara `paths`;
- `send_messages_github` usa `target_template=` e `body_template=`, ma lo
  schema dichiara `target` e `body`.

Questi difetti non vengono segnalati dal CLI corrente perché gli import annidati
non sono nel suo inventario, e non lo sono nemmeno dalla pipeline RM-0005 per la
stessa ragione (§4.1). Non sono stati corretti in questa analisi.

Il quarto compare **soltanto in inglese** ed è `get_location.actor`: il
controllo corrente interpreta «current actor» come esposizione dell'argomento
`actor`, anche se in quel punto `actor` è una parola naturale e la proprietà
viene nascosta al proposer. È il caso più istruttivo del documento: dimostra che
estendere meccanicamente le espressioni regolari italiane e inglesi aumenta i
falsi positivi, e che un errore può esistere in una lingua sola in entrambe le
direzioni — verso il falso negativo (i due campi di lunghezza) e verso il falso
positivo (questo).

Persistenza del quadro: fra il 23 luglio e il 24 agosto il catalogo è cresciuto
di 7 manifest e gli avvisi di lunghezza di 31, ma il conto degli errori non si è
mosso. Non è stabilità del catalogo: è assenza di un punto che li faccia
fallire. Il CLI non li vede, la firma non li invoca, il gate di attivazione li
vedrebbe ma soltanto nella lingua che sceglie da solo (§4.11).

### 4.5 Parità strutturale corrente fra IT ed EN

Il 23 luglio, su 115 manifest, le divergenze erano **zero in tutte e quattro le
classi** confrontate (argomenti del `PATTERN`, segnaposto, riferimenti `NON:`,
forma dell'output). La riverifica del 24 agosto, su 122 manifest, **non conferma
più quel risultato**.

| Classe confrontata | Divergenze 23/7 | Divergenze 24/8 |
|---|---:|---:|
| nome della funzione chiamata nel `PATTERN` | 0 | 0 |
| insieme degli argomenti top-level della chiamata | 0 | **1** |
| segnaposto `${RUNTIME:...}` | 0 | 0 |
| segnaposto `{{...}}` | 0 | 0 |
| riferimenti a executor esistenti nel `NON:` | 0 | 0 |
| capitoli presenti e ordinati | non misurato | 0 |

La divergenza è `set_signatures`, descritta per esteso in §4.12: il `PATTERN`
inglese passa un argomento che quello italiano non passa. Entrambi gli argomenti
esistono nello schema, quindi nessun controllo attivo se ne accorge — il linter
verifica che gli argomenti del `PATTERN` appartengano allo schema, non che le
lingue insegnino la stessa chiamata.

Due precisazioni di metodo, perché il confronto non venga rifatto male:

- il confronto dei capitoli è stato aggiunto in questa riverifica ed è pulito:
  tutte le 244 descrizioni hanno `SCOPO:`, `PATTERN:`, `NON:` e `OUT:` presenti
  e nell'ordine giusto. I marker restano scritti in italiano anche nel testo
  inglese, quindi sono **invarianti macchina**, non prosa: una futura traduzione
  che li localizzasse romperebbe il proposer, e oggi niente lo impedisce (§4.11);
- il confronto della forma dell'output per presenza delle parole `entries` e
  `results` nel capitolo `OUT:` ha prodotto **due falsi positivi**, `list_dirs`
  e `set_messages`, dove la parola inglese «entries» compare come prosa
  («sorted entries») o dove una lingua elenca i campi e l'altra riassume («OUT:
  risultati e ricevuta» contro `OUT: {ok,ok_count,fail_count,results,failed}`).
  È la stessa ambiguità naturale/codice del caso `actor` di §4.4, su un'altra
  superficie, e conferma §7.7: la forma dell'output va confrontata soltanto
  quando è espressa come identificatore riconoscibile, mai per presenza di
  parola.

Resta vero che la traduzione corrente conserva **quasi** tutti gli atomi
macchina fondamentali. Ma «quasi» è una proprietà che decade da sola: è decaduta
in un mese, sul manifest di un executor firmato del nucleo, e nessun controllo
l'ha vista.

### 4.6 Budget adattivo e lunghezza reale

Tutte le 230 descrizioni principali IT/EN osservate hanno una testa entro
`HEAD_MAX=240`. Dopo normalizzazione e rimozione di `OUT:`, la testa più lunga
renderizzata è di 239 caratteri. **Confermato il 24 agosto 2026 sul catalogo
cresciuto: 244 teste su 244, in entrambe le lingue, entro il limite.**

Il renderer adattivo, ora consolidato in `manifest_rules.render_head()`, non
modifica nessuna delle teste del catalogo corrente: la redistribuzione del
budget non è richiesta da nessun manifest esistente.

Il percorso alternativo `agent_runtime.render_tools_for_provider()`
(`runtime/agent_runtime.py:1198`) **continua a non avere chiamanti di
produzione**: verificato il 24 agosto, gli unici riferimenti sono
`tests/runtime/engine/test_tool_schema_slim.py` e un commento in
`runtime/tool_schema_slim.py`. Le sue descrizioni storiche di provider e gli
esperimenti di compressione degli argomenti non rappresentano il percorso attivo
del planner e non devono guidare la progettazione del linter.

Osservazione aggiunta nella riverifica: la prova di budget esistente,
`tests/runtime/infra/test_manifest_head_budget.py`, carica gli executor
attraverso il loader. Misura quindi la sola lingua attiva dell'istanza, oggi
l'italiano. È una vista di diagnosi utile, non una copertura multilingue, ed è
il tipo di prova che §11.2 respinge come criterio di completezza.

Questo dato porta a due conclusioni:

1. il linter multilingue non dipende dal renderer adattivo;
2. il budget elastico non è una giustificazione per accettare nuove teste oltre
   il limite editoriale, perché la loro visibilità dipenderebbe dalla
   composizione del pool.

Le descrizioni argomento sono un caso diverso. Il proposer testuale attivo
espone nomi, obbligatorietà ed enum, non la prosa completa degli argomenti. La
prosa localizzata è però letta da `args_extractor.py` per alcuni flag booleani.
Accorciarla automaticamente può quindi cambiare la normalizzazione degli
argomenti anche quando non cambia il prompt del proposer.

### 4.7 Copertura dei confini di generazione e traduzione

Il percorso attuale non applica una politica testuale unica:

Aggiornato al 24 agosto 2026: la riga in grassetto è cambiata rispetto a luglio.

| Confine | Controllo corrente | Lacuna |
|---|---|---|
| Synt multistage | linter sulla descrizione sorgente flat (`synt_multistage.py:541`) | non controlla la futura traduzione |
| `synth_request` | involucro standard + firma | la variante mancante arriva dopo |
| import skill | involucro standard + validatore standard | nessun linter strutturale completo |
| proposte/promozione | validatori propri e firma | nessun punto unico di qualità testuale |
| traduttore manifest | `_validate_common`: token Jinja, `{...}`, apici inversi, sentinella, rapporto di lunghezza | non controlla capitoli, chiamate, argomenti, riferimenti o budget |
| **attivazione lingua** | **`lint_file` + `verify_executor` per ogni contratto (`i18n_activation.py:452-487`)** | **linta la lingua scelta da `_description_text()`, non quella attivata** |
| firma | standard executor | non invoca `manifest_lint` |
| loader | standard + firma | non deve diventare il primo punto di scoperta del difetto |
| CLI | linter core a un livello (85/122) | manca builtin, import e lingue non italiane |

La docstring del linter dichiara uso in «synt-admission + importer + CLI», ma
nel codice osservato l'importer non lo invoca. La documentazione descrive quindi
un'intenzione più ampia dell'enforcement reale; oggi i chiamanti effettivi sono
esattamente due, `synt_multistage.py` e `i18n_activation.py`, più il CLI.

Il confine di attivazione è nuovo e cambia la natura del problema: il linter non
è più soltanto dev-tooling, **è già un gate che può impedire l'attivazione di
una lingua**, e questo alza la posta su entrambi i lati della precisione.

Un falso positivo non produce più un avviso fastidioso in console: impedisce a
un'istanza di passare alla lingua che il proprietario ha chiesto. Oggi
`get_location.actor` non fa danno perché il gate legge l'italiano e quell'errore
esiste solo in inglese — cioè è innocuo per una ragione accidentale, non per
costruzione, e diventa un blocco reale per il primo manifest che non abbia una
descrizione italiana. Un falso negativo, simmetricamente, lascia attivare una
lingua che nessuno ha controllato. Vedi §4.11.

### 4.8 Stato delle traduzioni e companion

Rimisurato il 24 agosto 2026. Tutti i 122 manifest hanno il companion
`manifest.lang_state.json` e tutti hanno la firma `manifest.toml.sig`. Le voci
lingua tracciate sono 1.630, su 1.630 superfici testuali esistenti.

| Misura | 23 luglio | 24 agosto |
|---|---:|---:|
| voci lingua con `version_hash` diverso dal testo corrente | 173 | **193** |
| manifest interessati | 21 | **28** (6 core, 6 builtin, 16 import) |
| manifest con placeholder `PLACEHOLDER_NOT_SIGNED_IN_WORKTREE` | 21 | **12**, tutti import |

Le due righe si muovono in direzioni opposte, ed è corretto che lo facciano:

- i placeholder sono **diminuiti e circoscritti**. Core e builtin sono oggi
  puliti; restano 12 dei 16 import GitHub. È un miglioramento reale prodotto dal
  lavoro RM-0005, non da RM-0002, e conferma la diagnosi: gli import sono
  l'unica classe che nessuna pipeline visita;
- lo scostamento fra testo e `version_hash` è **peggiorato**, da 173 a 193 voci
  e da 21 a 28 manifest. Fra i core scostati compare `set_signatures`, cioè
  proprio il manifest la cui coppia IT/EN ha smesso di essere allineata (§4.12).
  Le due cose non sono indipendenti: qualcuno ha modificato una lingua, lo stato
  non è stato riallineato, e nessun confronto fra lingue lo ha fermato.

Questa misura non equivale automaticamente a corruzione: una differenza può
indicare una modifica in attesa di allineamento. Tuttavia rivela tre problemi
di protocollo, **tutti e tre ancora presenti nel codice del 24 agosto**:

1. il generatore degli import può lasciare
   `sha256:PLACEHOLDER_NOT_SIGNED_IN_WORKTREE` perché il signer crea il companion
   soltanto se manca e non sostituisce un placeholder esistente;
2. il traduttore non visita il percorso annidato degli import e quindi non
   risolve quei placeholder;
3. se più lingue risultano modificate nello stesso file,
   `i18n_translator._decide_edit_source` (righe 805-836) sceglie
   alfabeticamente `en`, perché il `mtime` del file non può distinguere quale
   campo sia stato editato. Una doppia modifica intenzionale può quindi essere
   reinterpretata come «inglese sorgente» e riscrivere l'italiano. Il codice
   dichiara la scelta nella propria docstring, quindi non è un difetto nascosto:
   è una convenzione deliberata di cui va cambiato il verdetto, non l'onestà.

Il linter multilingue non deve limitarsi a segnalare testi: deve inserirsi in una
transazione di pubblicazione che renda espliciti inventario, lingua sorgente,
stato e firma.

### 4.9 Comportamento `--strict`

Il CLI corrente, in modalità `--strict`, conta ogni avviso come errore e termina
con codice non zero, ma stampa ancora la riga con etichetta `[warn]`. Il riepilogo
può quindi dire «1 error» mentre la singola riga dice «warn».

Non è un problema semantico del manifest, ma rende meno verificabile il nuovo
percorso di adozione e va corretto insieme alla struttura dei finding.

**Invariato al 24 agosto 2026** (`runtime/manifest_lint.py:378-403`): `--strict`
sposta tutti i finding nel contatore degli errori, mentre la riga stampata usa
`Finding.__str__`, che continua a leggere `severity` e quindi a scrivere
`[warn ]`.

### 4.10 Costo misurato

Una simulazione completa su 99 manifest residenti nel repository, due lingue e
controlli correnti ha richiesto circa 19,5 ms per ciclo sul sistema di sviluppo.
Il costo è trascurabile nei confini di authoring e traduzione. Non vi è motivo
di pagarlo a ogni turno o a ogni invocazione executor.

**Rimisurato il 24 agosto 2026:** 2,6 ms per una passata monolingua sui 122
manifest, con i manifest già letti e il catalogo dei nomi precalcolato, media su
tre cicli. Le due cifre **non sono confrontabili** — quella di luglio includeva
lettura e parsing dei file, questa no — e vanno tenute distinte invece che
presentate come un miglioramento. Quello che entrambe dimostrano è la stessa
cosa, ed è l'unica che serve alla decisione: il controllo costa millisecondi, e
aggiungere una lingua ne aggiunge una frazione — i controlli locali si ripetono
per lingua, quelli globali no (§7.3). Nessuna scelta di disegno di questa
roadmap deve essere motivata dal costo.

### 4.11 Sovrapposizione con RM-0005 (nuova, 24 agosto 2026)

RM-0005 è stata chiusa il 23 agosto 2026 e ha costruito la localizzazione
versionata dell'istanza. Tocca gli stessi manifest di questa roadmap, quindi il
confine va scritto con precisione: **cosa esiste già, cosa manca, e cosa è
diventato più urgente per il fatto che il resto esiste.**

#### Cosa RM-0005 ha già costruito e RM-0002 non deve rifare

**La pubblicazione transazionale di §7.11.**
`i18n_pipeline._promote_contracts()` (righe 615-666) fa esattamente la sequenza
che §7.11 chiedeva: legge il manifest e la firma correnti, costruisce il testo
candidato in memoria sostituendo la sola lingua di destinazione, lo riparsifica,
confronta, scrive in modo atomico, rifirma, aggiorna `lang_state`. In caso di
qualunque eccezione ripristina **sia il testo sia la firma**, con una nota nel
codice che spiega la ragione: la lingua in esercizio carica quello stesso file.
Questo pezzo è fatto, ed è fatto bene.

**Un invariante forte che §7 non aveva previsto.** Prima di scrivere, il
promotore verifica `_strip_target_prose(original) == _strip_target_prose(parsed)`:
tolta la prosa della lingua di destinazione, il manifest deve essere identico.
Rende impossibile che una traduzione tocchi schema, capability, firma, `execution`
o le **altre lingue**. È più forte del confronto per atomi proposto in §7.7 su
tutto ciò che non è prosa, e va conservato.

**Metà dell'inventario comune di §7.1.**
`i18n_materializer.LocalizationPaths.manifest_roots` visita core e builtin con
`rglob`. Manca la classe degli import installati.

**Un gate di ammissione che invoca il linter.**
`i18n_activation.validate_manifests()` esegue `lint_file` e `verify_executor` su
ogni contratto registrato prima di attivare la lingua, e registra l'esito come
controllo `manifest_admission`.

#### Cosa manca, misurato

Per un contratto la validazione del candidato è soltanto
`_validate_common(source, translated)` (righe 195-208). Controlla: parità dei
token Jinja `{{...}}`/`{%...%}`, parità dei segnaposto `{nome}`, parità dei
blocchi fra apici inversi, assenza di sentinella non risolta, rapporto di
lunghezza fra 0,35 e 3,5. Confrontando questo elenco con le superfici reali:

| Atomo del manifest | Protetto da `_validate_common`? | Misura |
|---|---|---|
| capitoli `SCOPO:/PATTERN:/NON:/OUT:` | **no** | 244 descrizioni li usano; tradurli rompe proposer e linter |
| nome della funzione nel `PATTERN` | **no** | 0 dei 170 capitoli `PATTERN:` del catalogo core contiene apici inversi |
| argomenti top-level della chiamata | **no** | stessa ragione |
| `${RUNTIME:chiave}` | **parzialmente** | `_FORMAT_RE` cattura `{RUNTIME:...}` ma confronta solo il gruppo `RUNTIME`: scambiare `actor` con `now` passa |
| riferimenti a executor nel `NON:` | **no** | non confrontati |
| budget `HEAD_MAX`/`DESC_MAX`/`ARG_DESC_MAX` | **no** | solo un rapporto 0,35-3,5, che a 320 caratteri lascia passare fino a 1.120 |
| altre lingue, schema, firma, capability | **sì** | `_strip_target_prose`, invariante forte |

La misura «0 su 170» è il punto centrale e va ripetuta perché è controintuitiva:
la tutela del codice fra apici inversi esiste ed è corretta, ma nel formato dei
manifest Metnos le chiamate del `PATTERN` sono scritte in chiaro
(`find_files(base_path="/", patterns=["*.jpg"])`), non fra apici. Quindi quella
tutela, sui manifest, protegge zero chiamate.

#### Perché adesso è più urgente, non meno

Finché la seconda lingua era l'inglese scritto a mano dallo stesso autore
dell'italiano, l'assenza di controllo era compensata dalla revisione umana. Con
RM-0005 la seconda lingua può essere **prodotta da un modello e promossa da un
lavoro notturno**, e l'unico controllo strutturale che quel testo attraversa è
quello sopra. Poi il gate di attivazione rilegge — e per il difetto di
`_description_text()` rilegge l'italiano.

La catena completa, oggi:

```text
modello traduce la descrizione in lingua X
  -> _validate_common: Jinja, {segnaposto}, apici inversi, rapporto lunghezza
  -> _strip_target_prose: schema/firma/altre lingue invariati
  -> scrittura atomica + rifirma
  -> ... gate di attivazione: lint_file(manifest)
                              ^^^^^^^^^ legge `it`, non X
  -> lingua X attivata
```

Nessuno di questi passaggi ha mai letto la lingua X con una regola che conosca
il formato dei manifest Metnos. **Questa è la lacuna che RM-0002 esiste per
chiudere**, ed è la ragione per cui il piano di §12 va riletto alla luce di §12.0.

### 4.12 Difetti nuovi trovati nella riverifica (24 agosto 2026)

Non sono stati corretti: RM-0002 è read-only per costruzione (§16). Sono
elencati qui perché sono la prova che la classe di difetto descritta dalla
roadmap non è teorica.

**D1 — `set_signatures` insegna due chiamate diverse.** Il `PATTERN` italiano è
`set_signatures(kind="blacklist", signature="bin:cmd:kind")`; quello inglese è
`set_signatures(kind="blacklist", signature="bin:cmd:kind", reason="reason")`.
Entrambi sono validi rispetto allo schema, che dichiara `kind`, `signature`,
`reason` e `severity` con `required = ["kind", "signature"]`. Nessun controllo
attivo confronta le due chiamate, quindi il planner di un'istanza inglese vede
un esempio con un argomento in più rispetto a quello di un'istanza italiana.
Anche i capitoli `OUT:` divergono: `OUT: esito e ricevuta` contro
`OUT: {signature,kind,removed?,message,_undo}`. È la classe che §7.7 dichiara
errore bloccante per i manifest nuovi o toccati. `set_signatures` compare anche
fra i sei core con `version_hash` scostato (§4.8): la modifica è avvenuta e lo
stato non è stato riallineato.

**D2 — le descrizioni argomento hanno una deriva editoriale asimmetrica.** 115
argomenti superano `ARG_DESC_MAX` in italiano contro 91 in inglese, di cui 25
soltanto in italiano. Non è un errore strutturale e **non autorizza alcun
accorciamento automatico** (§16), ma è il tipo di divergenza che rende falsa
l'idea che una lingua approssimi l'altra: qui la lingua più prolissa è
l'italiano, mentre sul totale dei caratteri l'italiano è più lungo solo del 10%.

**D3 — la simmetria dei conteggi nasconde l'asimmetria degli insiemi.** Nove
descrizioni oltre `DESC_MAX` per lingua, ma `find_dirs` sfora solo in italiano e
`write_files` solo in inglese (§4.3). Un conteggio per lingua sembrerebbe dire
che controllare una lingua vale l'altra. È esattamente il ragionamento che
questa roadmap smonta, e adesso c'è un caso per direzione.

**D4 — i tre errori degli import sono immobili da un mese.** Nessuno li ha visti
perché nessun percorso li visita: né il CLI (§4.1), né la pipeline RM-0005
(§4.11), né la firma (§4.7). Sono l'argomento più semplice a favore
dell'inventario condiviso di §7.1.

## 5. Problema architetturale

Il problema non è «tradurre anche il linter». I messaggi del linter sono
diagnostica interna e possono restare italiani. Il problema è definire su quali
superfici opera ciascuna regola.

Un manifest contiene tre famiglie diverse di dati:

1. **dati globali**, uguali per tutte le lingue: nome, schema, capability,
   output dichiarato, affinity mista, collocazione e firma;
2. **testi locali**, diversi per lingua: descrizione principale e descrizioni
   degli argomenti;
3. **atomi macchina replicati nei testi**, che devono restare uguali:
   chiamate, argomenti, segnaposto, riferimenti canonici e campi di output.

Il linter corrente confonde queste famiglie: sceglie un testo canonico, poi
applica insieme controlli globali e locali. Un semplice ciclo sulle lingue
duplicherrebbe i controlli globali e non proverebbe la parità fra traduzioni.

## 6. Invarianti della soluzione

La realizzazione non deve violare i seguenti vincoli:

- nessuna modifica automatica ai manifest;
- nessuna traduzione automatica avviata dal linter;
- nessun allentamento di schema, autorità o firma;
- nessun controllo LLM nel percorso deterministico;
- nessun blocco retroattivo del caricamento per un nuovo avviso editoriale;
- nessuna assunzione che `it` sia la lingua sorgente o la più lunga;
- nessuna assunzione che `en` sia sempre disponibile negli artefatti candidati;
- nessuna duplicazione delle regole nei tre generatori;
- nessuna scansione diversa fra linter, traduttore, firma e inventario;
- nessuna bonifica massiva dei 108 avvisi di lunghezza italiani;
- nessuna dipendenza dal renderer adattivo sperimentale;
- nessun uso di una memoria semantica o di un LLM per decidere errori bloccanti.

## 7. Architettura proposta

### 7.1 Un solo inventario dei manifest

Introdurre un helper puro, piccolo e senza dipendenze dal loader, per enumerare
le fonti riconosciute:

- executor core a un livello;
- contratti builtin a un livello;
- executor utente diretti;
- executor utente sotto `skills/<skill>/<executor>`;
- eventuale `_imports/<skill>/<executor>` ancora ammesso dal loader durante la
  migrazione.

L'helper deve restituire almeno percorso, classe di origine e nome atteso. Il
loader può mantenere i propri controlli di abilitazione e collisione; il linter
e il traduttore devono però condividere la stessa topologia del filesystem.

Non è corretto importare `_iter_executor_dirs` dal loader: porterebbe nel
dev-tooling firma, configurazione e altri effetti collaterali. La direzione KISS
è estrarre soltanto l'enumerazione neutra in un modulo comune.

### 7.2 Modello delle superfici testuali

Il linter deve trasformare il manifest in record immutabili concettualmente
equivalenti a:

```text
TextSurface(
    resource="description" | "args.<name>.description",
    language="it" | "en" | <altra lingua>,
    text=<stringa esatta>,
    transient=<bool>
)
```

Regole:

- per un manifest su disco, si controllano tutte le chiavi lingua con valore
  stringa non vuoto;
- la presenza obbligatoria di `it` ed `en` resta responsabilità dello standard
  executor e non viene diagnosticata due volte;
- eventuali lingue aggiuntive sono controllate automaticamente;
- una descrizione flat è ammessa soltanto nell'oggetto transitorio dello stage
  Synt, con lingua sorgente passata esplicitamente dal chiamante;
- il linter non usa la catena di ripiego del loader durante l'authoring: deve
  controllare la risorsa esatta, non nascondere una lingua mancante dietro `en`.

### 7.3 Tre passaggi distinti

| Passaggio | Frequenza | Esempi |
|---|---|---|
| globale | una volta per manifest | affinity, catalogo, schema/output, coerenza generale |
| locale | una volta per superficie lingua | capitoli, budget, pattern, runtime arg, output, riferimenti |
| trasversale | una volta per gruppo di traduzioni | parità di chiamate, argomenti, segnaposto e riferimenti |

Questa separazione impedisce il raddoppio degli avvisi affinity e rende esplicito
quando un problema appartiene soltanto a `en` o a `args.foo.description[fr]`.

### 7.4 Struttura dei finding

`Finding` deve portare campi strutturati, non affidarsi al parsing del messaggio:

```text
check       identificatore stabile della regola
severity    error | warn
resource    description | args.<name>.description | manifest
language    codice lingua oppure null per controlli globali
message     spiegazione leggibile
evidence    valori misurati o atomi divergenti, in forma limitata
```

Per la lunghezza si emette un solo finding per risorsa, con tutte le lingue che
superano il limite e le misure delle altre. Esempio concettuale:

```text
length args.to_user.description: max=181>180; it=180, en=181
```

In questo modo il totale dei problemi non raddoppia artificialmente, ma resta
possibile filtrare per lingua.

### 7.5 Controlli globali

Devono essere eseguiti una sola volta:

- sovrapposizione affinity;
- esistenza del nome nel contesto previsto;
- validità della forma globale di output usata come riferimento;
- consistenza fra proprietà e `required` già delegata allo standard;
- inventario e collisioni, quando forniti dal chiamante.

Il linter non deve duplicare i controlli di autorità, firma, lifecycle o
capability di `executor_standard.py`.

### 7.6 Controlli locali per lingua

Per ogni descrizione principale:

- capitoli invarianti `SCOPO:`, `PATTERN:`, `NON:`, `OUT:` presenti e ordinati;
- chiamata dell'executor presente nel `PATTERN`;
- argomenti top-level della chiamata appartenenti allo schema o agli universali;
- nessun argomento runtime-owned passato nella chiamata;
- posizione di `PATTERN` e `NON` rispetto ai budget;
- lunghezza della testa e della descrizione completa;
- riferimenti nel `NON:` risolti nel catalogo completo;
- forma `entries`/`results` o output purpose-specific coerente;
- assenza di segnaposto corrotti o parziali.

Per ogni descrizione argomento:

- lunghezza editoriale;
- conservazione di tipo, esempio, default e vincoli macchina quando espressi in
  forma strutturata riconoscibile;
- nessun tentativo di tradurre nomi di argomento o token riservati;
- nessuna riscrittura o troncamento automatico.

### 7.7 Controlli trasversali fra lingue

Il confronto deve essere stretto sugli atomi macchina e prudente sulla prosa.

**Errori deterministici bloccanti per nuovi/toccati:**

- nome della funzione chiamata diverso o assente;
- insieme degli argomenti top-level del `PATTERN` diverso;
- segnaposto `${RUNTIME:...}` o `{{...}}` mancanti, aggiunti o modificati;
- token di piping come `from_step` alterati;
- riferimento a un executor canonico sostituito da un nome inesistente;
- campo macchina dell'output rimosso dalla traduzione quando è espresso come
  identificatore riconoscibile.

**Avvisi, da promuovere solo dopo corpus ed evidenza:**

- insieme dei riferimenti validi nel `NON:` diverso fra due lingue;
- esempi numerici, unità o wildcard differenti;
- differenza fra `entries` e `results` non già catturata dallo schema;
- rapporto di lunghezza estremo;
- una variante molto più verbosa delle altre.

Non devono essere confrontati con uguaglianza lessicale:

- sinonimi;
- ordine naturale delle frasi;
- articoli e morfologia;
- numero di parole;
- similarità embedding;
- valutazioni libere del significato.

### 7.8 `runtime_resolved`: eliminare il falso positivo linguistico

La regola corrente cerca il nome dell'argomento come parola nella prosa. Questo
è affidabile per identificatori come `spreadsheet_id`, ma non per parole inglesi
comuni come `actor`, `client`, `account` o `provider`.

La nuova regola deve distinguere tre casi:

1. `actor=` nel `PATTERN`: **errore certo**;
2. identificatore in forma codice, backtick, assegnazione o elenco argomenti:
   **errore**, salvo contesto esplicito di omissione;
3. parola naturale omonima, per esempio «current actor»: **avviso ambiguo** o
   astensione, non errore bloccante.

I marker di omissione devono essere organizzati per lingua (`it`, `en`, future
lingue supportate). Per una lingua senza lessico, il linter applica i controlli
strutturali certi e si astiene dal giudizio semantico sul contesto. Non deve
usare il lessico inglese come ripiego universale.

Questa regola evita di cambiare la buona prosa di `get_location` soltanto per
soddisfare un'espressione regolare troppo larga.

### 7.9 Politica delle lunghezze

I limiti devono essere verificati per ogni lingua, ma restano separati dal
budget dinamico del pool.

- `HEAD_MAX`: limite editoriale della testa completa per singola lingua;
- `DESC_MAX`: limite della descrizione completa per singola lingua;
- `ARG_DESC_MAX`: obiettivo editoriale per ogni descrizione argomento;
- budget del renderer: vincolo operativo della superficie mostrata al modello;
- misure token: metrica di prova, non regola deterministica del linter.

Usare il massimo delle lunghezze linguistiche per decidere se la risorsa passa
è equivalente a richiedere che passino tutte le lingue. Il messaggio deve però
mostrare le singole misure, non soltanto il massimo.

Il conteggio in caratteri è coerente con il renderer corrente, che taglia per
caratteri e confine di parola. Per lingue con segmentazione molto diversa
dall'italiano e dall'inglese non basta a stimare il costo token. Una futura
lingua di questo tipo richiede un benchmark tokenizer separato, senza rendere il
linter dipendente dal modello installato.

I 108 avvisi italiani e 85 inglesi del catalogo completo non autorizzano
accorciamenti di massa. Le descrizioni argomento possono influenzare
`args_extractor`; ogni intervento resta per famiglia, con equivalenza verificata.

### 7.10 Modellare la superficie realmente visibile

`manifest_lint._visible_to_llm()` oggi usa un taglio grezzo a 260 caratteri.
Il renderer taglia a confine di parola e, nel working tree, può distribuire un
residuo fino a un limite superiore. Le due implementazioni possono quindi
divergere per un executor futuro oltre budget.

Il linter deve usare gli stessi helper puri di `manifest_rules.py` per calcolare
la superficie visibile. Tuttavia i controlli di sicurezza su un argomento
runtime-owned devono ispezionare tutta la testa potenzialmente visibile fino al
limite superiore, non soltanto il budget medio.

Il renderer di pool dipende dall'insieme dei tool. Il linter per-manifest non
deve simulare una composizione favorevole per assolvere una testa lunga. La
regola editoriale resta per singolo manifest; i test di pool dimostrano in
aggiunta boundedness ed equivalenza sul catalogo reale.

### 7.11 Transazione di traduzione appena prima della scrittura

Il punto corretto per fermare una traduzione difettosa è dopo aver ottenuto il
testo candidato e **prima** di modificare file, stato o firma:

```text
leggi manifest + lang_state
  -> determina in modo non ambiguo la sorgente
  -> traduci in memoria
  -> costruisci il manifest candidato in memoria
  -> linter locale + confronto trasversale
  -> se errore: conserva integralmente manifest/stato/firma correnti
  -> se valido: scrivi manifest e stato come una sola pubblicazione
  -> firma
  -> verifica firma e hash finali
```

Il traduttore non deve applicare sostituzioni al file e scoprire il difetto
dopo. Un fallimento del linter è un risultato tipizzato e ritentabile; non deve
essere mascherato come `noop` o traduzione riuscita.

### 7.12 Protocollo `lang_state`

Il companion deve distinguere:

- fotografia del testo pubblicato;
- lingua sorgente dell'ultima traduzione;
- hash della sorgente;
- modifica locale ancora da propagare;
- conflitto con più lingue modificate.

Politica raccomandata:

- zero lingue cambiate: nessuna azione;
- una lingua cambiata: quella è la sorgente;
- più lingue cambiate e invarianti macchina uguali: non scegliere
  alfabeticamente; accettare entrambe come modifiche intenzionali soltanto se
  l'operazione di authoring lo dichiara, altrimenti stato `multi_source_conflict`;
- risorsa nuova già completa in IT/EN: adottare entrambe, non ritradurne una;
- placeholder: errore di pubblicazione per un artefatto attivo;
- lingua mancante: tradurre dalla sorgente dichiarata, non dalla prima chiave
  alfabetica.

Il signer non può semplicemente aggiornare tutti i `version_hash`: cancellerebbe
l'informazione necessaria a capire quale lingua è stata modificata. Serve un
unico helper di pubblicazione che riceva la sorgente o classifichi esplicitamente
il conflitto prima della firma.

### 7.13 Profili di severità

Una stessa regola deve poter operare con profili chiari:

| Profilo | Uso | Errori | Avvisi |
|---|---|---|---|
| `audit` | catalogo esistente | riportati | riportati, non bloccanti |
| `candidate` | Synt prima della completezza bilingue | bloccano solo difetti certi della lingua sorgente | riportati |
| `translation` | candidato tradotto in memoria | difetti locali e parità macchina bloccano la scrittura | riportati |
| `active_on_touch` | firma/promozione di manifest standard | errori certi bloccano | politica strict esplicita |

Non deve esistere un `--strict` che cambia soltanto il conteggio. La severità
effettiva va resa visibile nella singola riga e nel risultato strutturato.

### 7.14 Punti di integrazione

Aggiornato il 24 agosto 2026 con lo stato reale di ciascun punto.

| Componente | Integrazione proposta | Stato al 24/8 |
|---|---|---|
| `manifest_lint.py` | motore puro multilingue e risultato strutturato | da fare, invariato |
| inventario comune | scoperta core, builtin, utente diretto e import annidati | **metà**: `i18n_materializer` copre core+builtin, non gli import |
| `synt_multistage.py` | profilo `candidate` sulla lingua sorgente | chiamante già presente, profilo da introdurre |
| `generated_executor_contract.py` | richiamo comune dopo rendering del candidato completo | da fare |
| `skill_codegen.py` | profilo bilingue prima della prima firma/installazione | da fare |
| `i18n_translator.py` / `i18n_pipeline.py` | profilo `translation` sul candidato in memoria | **transazione fatta** da RM-0005, **validazione del contratto assente** (§4.11) |
| `i18n_activation.py` | profilo bilingue prima di attivare la lingua | **chiamante presente, linta la lingua sbagliata** |
| `sign.py` | profilo `active_on_touch`, errori certi soltanto | da fare, non invoca il linter |
| `loader.py` | nessun nuovo blocco editoriale; solo audit opzionale e standard esistente | invariato, corretto così |
| CLI/test | inventario completo e filtri per origine/lingua | da fare; il CLI copre 85/122 |

Il loader non deve diventare il primo punto in cui una nuova regola editoriale
rende indisponibile un executor già firmato. La qualità si applica prima della
pubblicazione; il caricamento continua a verificare contratto, firma e standard.

## 8. Impatto macro

### 8.1 Affidabilità del prodotto

L'impatto positivo maggiore è sul confine lingua→vocabolario chiuso. Il planner
riceverà la stessa grammatica di chiamata in ogni lingua, riducendo richieste di
input spurie, argomenti inventati e instradamenti divergenti.

La modifica non migliora da sola la qualità semantica delle descrizioni. Evita
però che una traduzione formalmente fluida corrompa gli atomi che rendono
eseguibile il contratto.

### 8.2 Sicurezza e autorità

Il linter non concede capacità e non cambia il sandbox. Rafforza indirettamente
la sicurezza impedendo che una traduzione suggerisca al modello argomenti
runtime-owned, provider o bersagli non previsti.

Non deve però irrigidire il sistema sulla base di parole naturali ambigue. Un
falso positivo su `actor` non è una violazione di autorità: il vero controllo
resta lo schema model-facing che nasconde l'argomento e il runtime che lo
inietta. La severità deve riflettere la certezza dell'evidenza.

### 8.3 Manutenibilità

Un inventario e un motore centrali riducono divergenza fra tre generatori,
traduttore, signer e CLI. Una modifica a una regola testuale viene recepita nei
punti di pubblicazione senza copiare template o espressioni regolari.

La centralizzazione non deve trasformarsi in un modulo monolitico che ingloba
firma, standard e traduzione. Le responsabilità restano separate e vengono
composte in un piccolo orchestratore di validazione.

### 8.4 Esperienza di sviluppo

L'autore vede il percorso esatto, la lingua e la misura. Non deve eseguire due
comandi né interpretare totali raddoppiati. I legacy restano utilizzabili; i
nuovi errori certi vengono fermati sul componente toccato.

Il caso corrente degli import dimostra il valore pratico: tre errori di pattern
diventano visibili senza scandire manualmente directory diverse.

### 8.5 Prestazioni

Il linter resta fuori dai turni. Il costo attuale stimato di circa 20 ms per un
controllo bilingue esteso è irrilevante rispetto a generazione e traduzione LLM.
L'inventario deve essere calcolato una volta per comando e riusato per tutti i
manifest, soprattutto per riferimenti `NON:` e affinity.

### 8.6 Evoluzione a nuove lingue

La struttura proposta accetta automaticamente nuove chiavi lingua. I controlli
puramente sintattici funzionano senza codice dedicato. Soltanto i giudizi che
usano parole naturali, come i marker di omissione, richiedono un lessico
esplicito e devono astenersi quando manca.

Questo permette elasticità senza dichiarare falsamente che ogni euristica IT/EN
sia universale.

## 9. Impatto micro sul codice

### 9.1 `runtime/manifest_lint.py`

Modifiche previste:

- sostituire `_description_text()` con enumerazione delle superfici;
- dividere `lint_manifest()` in passaggio globale, locale e trasversale;
- arricchire `Finding` con `resource` e `language`;
- usare helper del renderer invece di slicing duplicato;
- rendere il catalogo un input già materializzato;
- correggere la presentazione `--strict`;
- mantenere API transitoria per la descrizione flat Synt con lingua esplicita.

Rischio principale: alterare il numero o la severità dei finding usati da Synt.
Mitigazione: testare il profilo sorgente attuale e introdurre il bilingue prima
in sola osservazione.

### 9.2 Inventario comune

Un modulo piccolo deve contenere solo percorsi e visita delle strutture
riconosciute. Non deve importare `loader.py`, verificare firme o consultare
credenziali. I chiamanti decidono quali origini includere.

Rischio principale: scansionare artefatti ritirati o disabilitati come attivi.
Mitigazione: ogni record porta origine e stato; il CLI può mostrare tutto,
mentre traduttore e ammissione applicano filtri espliciti.

### 9.3 `runtime/i18n_translator.py`

Modifiche previste:

- usare l'inventario comune;
- costruire il TOML candidato in memoria;
- applicare linter locale e trasversale prima della scrittura;
- non scegliere alfabeticamente una sorgente multipla;
- non lasciare manifest, state e firma parzialmente allineati;
- riportare `lint_rejected`, `multi_source_conflict` e `state_placeholder`.

Rischio principale: fermare traduzioni che oggi verrebbero applicate. È un
arresto sicuro e visibile, preferibile a firmare una superficie corrotta.

**Aggiornamento 24 agosto 2026.** Il percorso di traduzione dei manifest non è
più solo `i18n_translator.py`: la pipeline RM-0005 (`i18n_pipeline.py`) ha già
il candidato in memoria, non scrive su errore e ripristina testo e firma. Delle
sei modifiche elencate qui sopra, la seconda, la terza limitatamente alla
transazione, e la quinta sono **già realizzate lì**. Restano da fare: la regola
di validazione del contratto (§4.11), la scelta non alfabetica della sorgente
multipla — `_decide_edit_source` è invariato — e la copertura degli import.
L'intervento va portato dove il candidato esiste già, non duplicato.

### 9.4 Generatori e promozione

I tre percorsi restano distinti per input e lifecycle, ma invocano la stessa
funzione di validazione del candidato. I template restano elastici sulla prosa e
vincolanti sugli atomi core.

Rischio principale: un generatore monolingue non può superare subito il profilo
active. Mitigazione: profilo `candidate` alla sorgente, traduzione in memoria,
poi profilo bilingue prima della promozione.

### 9.5 `runtime/sign.py`

La firma deve rifiutare soltanto errori certi per un manifest standard toccato.
Gli avvisi legacy restano visibili ma non impediscono manutenzione non
correlata. Il signer non deve decidere da solo la lingua sorgente.

Rischio principale: rendere impossibile rifirmare un manifest a causa di una
nuova euristica incerta. Mitigazione: soltanto regole strutturali ad alta
precisione nel profilo bloccante; le nuove euristiche iniziano come avvisi.

### 9.6 Test

Le prove esistenti sono prevalentemente sul parser di argomenti del `PATTERN` e
sui budget. Manca una matrice sistematica lingua×origine×lifecycle. La roadmap
prevede di aggiungerla senza moltiplicare copie dello stesso caso.

## 10. Rischi e contromisure

| Rischio | Probabilità | Impatto | Contromisura |
|---|---|---|---|
| falsi positivi su parole naturali come `actor` | alta senza redesign | alto: manifest inutilmente riscritto o bloccato | distinguere codice, pattern e omonimia naturale |
| raddoppio di affinity e avvisi globali | alta con ciclo ingenuo | medio | passaggio globale unico |
| linter e renderer descrivono superfici diverse | media | alto | helper di rendering condivisi e test di equivalenza |
| traduzione valida linguisticamente ma pattern corrotto | concreta | alto | controllo candidato prima della scrittura |
| aggiornamento parziale manifest/state/firma | concreta | alto | pubblicazione transazionale e verifica finale |
| sorgente scelta alfabeticamente fra due edit | concreta | alto | stato di conflitto, sorgente esplicita |
| import annidati invisibili | già presente | alto | inventario comune |
| placeholder `lang_state` persistenti | già presente | medio/alto | vietarli negli artefatti attivi |
| nuovi controlli bloccano legacy non toccati | media | alto | audit non bloccante e profilo on-touch |
| warning di lunghezza induce bonifica massiva | media | alto sulla semantica | nessun autofix; interventi per famiglia con corpus |
| confronto trasversale pretende traduzioni letterali | media | medio | confrontare solo atomi macchina |
| lingua futura senza marker lessicali | alta nel tempo | medio | astensione sulle euristiche non supportate |
| linter nel loader aumenta fragilità di avvio | media se collocato male | alto | applicarlo ai confini di pubblicazione, non ai turni |
| aggiornamento del linter cambia l'ammissione globale | media | alto | regole versionate nei report e introduzione warn-first |
| test solo sintetici non vedono regressioni di routing | alta | alto | corpus IT/EN e turni reali controllati |

## 11. Alternative considerate

### 11.1 Conservare il linter solo italiano

Respinta. Ha già due falsi negativi di lunghezza e non copre un errore inglese.
Il loader può usare l'inglese come ripiego, quindi la variante non è meramente
documentale.

### 11.2 Lintare soltanto la lingua attiva dell'istanza

Respinta come controllo di authoring. Un cambio della lingua d'istanza o
un'installazione diversa renderebbe attiva una superficie mai validata. Può
essere utile come vista di diagnosi, non come criterio di completezza.

Il 24 agosto 2026 questa alternativa è **più** debole di quanto fosse a luglio,
non meno: con ADR 0219 la lingua non è più una variabile d'ambiente ma un dato
installato e firmato, e con RM-0005 un'istanza può attivare una lingua prodotta
da un modello. Lintare «la lingua attiva» significherebbe, per costruzione,
controllare sempre l'unica lingua che nessuno ha ancora messo in esercizio —
oppure, come accade oggi nel gate di attivazione, controllarne una terza per
distrazione (§4.11).

### 11.3 Applicare ogni controllo a ogni lingua

Respinta. Duplicherrebbe affinity e altri finding globali, gonfierebbe i totali
e non confronterebbe le traduzioni.

### 11.4 Usare un LLM per giudicare l'equivalenza

Non ammessa come blocco principale. È costosa, non riproducibile e può dare
falsa sicurezza. Un valutatore LLM separato può produrre avvisi editoriali su
un campione, ma non sostituisce gli invarianti deterministici.

### 11.5 Correggere automaticamente i manifest

Respinta. Accorciamento e riscrittura possono alterare routing,
normalizzazione degli argomenti e confini `NON:`. Il linter diagnostica; una
correzione resta esplicita, firmata e testata.

### 11.6 Eseguire il linter soltanto nel loader

Respinta. Scoprire un difetto al riavvio è troppo tardi e rende un aggiornamento
del linter capace di oscurare executor già firmati. Il controllo corretto è
appena prima della pubblicazione.

### 11.7 Aumentare i limiti perché l'inglese è in media più corto

Respinta. La media non descrive i singoli campi e i limiti influenzano l'intero
pool. Qualunque revisione dei budget richiede benchmark separato, non deriva
dal supporto multilingue.

## 12. Piano di realizzazione

### 12.0 Revisione del piano al 24 agosto 2026

Il piano di luglio resta valido nella sostanza, ma due delle sue fasi sono state
in parte realizzate da RM-0005 e una priorità è cambiata. Questa sezione dice
cosa fare **oggi**; le fasi originali restano sotto, invariate, come specifica di
dettaglio.

**Priorità 1 — dare una lingua a `lint_file`.** È il lavoro più piccolo del
documento e il più urgente, perché è l'unico che oggi produce un difetto di
correttezza e non solo di igiene: `i18n_activation.validate_manifests()` esiste,
blocca l'attivazione, e legge la lingua sbagliata. Serve un parametro lingua
esplicito su `lint_manifest`/`lint_file` e il suo passaggio dal gate di
attivazione. Non richiede il motore multilingue completo e non cambia il
comportamento di nessun chiamante esistente se il valore predefinito conserva
l'attuale selezione.

**Priorità 2 — validare il candidato di traduzione con le regole dei manifest.**
`_validate_common` protegge Jinja, segnaposto e apici inversi; non protegge
nessuna delle superfici che rendono eseguibile un manifest (§4.11). Il punto di
innesto esiste già ed è `_translate_item`, ramo `contract`: il candidato è in
memoria, la transazione attorno è corretta, manca solo la regola. I controlli
minimi, tutti già specificati in §7.6 e §7.7: capitoli presenti e ordinati; nome
della funzione chiamata invariato; insieme degli argomenti top-level invariato;
`${RUNTIME:chiave}` invariato **chiave compresa**; budget per lingua.

**Priorità 3 — completare l'inventario con gli import installati.** Una sola
classe manca (§4.1) e contiene tutti e tre gli errori noti e tutti e dodici i
companion con placeholder. Il modo giusto è l'helper neutro di §7.1, condiviso
fra CLI, materializzatore e linter; il modo sbagliato è aggiungere una radice
alla tupla del materializzatore e lasciare il CLI dov'è.

**Priorità 4 — il motore multilingue completo** (`TextSurface`, finding con
`resource` e `language`, tre passaggi, profili di severità) resta il disegno di
destinazione ed è invariato. Diventa conveniente quando esiste una terza lingua
reale, che oggi non esiste: il catalogo è ancora IT+EN puro.

**Cosa NON fare più**, perché già fatto e fatto bene da RM-0005: la transazione
di pubblicazione, il ripristino di testo e firma in caso di errore, l'invariante
«tolta la prosa della lingua bersaglio, il manifest è identico». Si riusano.

### F0 — Congelamento e corpus

- nessun edit ai manifest;
- isolare le modifiche sperimentali del renderer dalla modifica al linter;
- fotografare i 115 manifest, le 230 descrizioni principali e i 654 argomenti;
- registrare finding per origine e lingua;
- costruire casi sintetici per errori soltanto EN e soltanto IT;
- fissare i tre errori importati come casi attesi dell'audit, non correggerli
  dentro questa fase.

**Uscita:** baseline riproducibile e nessuna modifica di prodotto.

### F1 — Motore multilingue in sola osservazione

- introdurre `TextSurface` e finding strutturati;
- separare controlli globali/locali/trasversali;
- controllare tutte le lingue presenti;
- aggiungere inventario core+builtin+import;
- mantenere invariati i codici di uscita dei percorsi produttivi;
- correggere soltanto la presentazione incoerente di `--strict`.

**Uscita:** rapporto completo, nessun nuovo blocco e nessun manifest modificato.

### F2 — Blocco delle traduzioni difettose

- costruire il candidato in memoria;
- applicare controlli locali e trasversali;
- lasciare intatti file e firma su errore;
- introdurre stati tipizzati del companion;
- eliminare la scelta alfabetica nei conflitti multipli;
- coprire gli import annidati.

**Uscita:** una traduzione che cambia un argomento o segnaposto viene rifiutata
prima di qualunque scrittura.

### F3 — Adozione comune nei generatori

- collegare i tre percorsi al validatore comune;
- profilo sorgente per candidati monolingui;
- profilo bilingue prima della promozione;
- nessun template duplicato per lingua;
- nessuna possibilità di firmare placeholder attivi.

**Uscita:** ogni nuovo executor applica automaticamente la politica centrale.

### F4 — Firma on-touch e pulizia mirata

- bloccare in firma soltanto gli errori strutturali certi;
- mantenere gli avvisi legacy non bloccanti;
- correggere separatamente i tre pattern importati con test propri;
- risolvere i companion inconsistenti senza scegliere arbitrariamente una
  lingua sorgente;
- rieseguire due cicli completi consecutivi.

**Uscita:** nessun errore certo nel catalogo attivo e nessuna regressione nei
flussi di riferimento.

### F5 — Estensione oltre IT/EN

- aggiungere una lingua soltanto con corpus e traduttore verificati;
- definire marker lessicali o astensione esplicita;
- misurare token e segmentazione;
- mantenere invarianti gli atomi macchina.

**Uscita:** la nuova lingua non richiede una copia del linter e passa le stesse
prove strutturali.

## 13. Piano di test

### 13.1 Prove unitarie

- errore di `PATTERN` presente soltanto in inglese;
- errore presente soltanto in italiano;
- argomento annidato in dict non scambiato per argomento top-level;
- `runtime_resolved` passato come keyword: errore;
- `runtime_resolved` in backtick senza omissione: errore;
- `runtime_resolved` come parola naturale omonima: non errore bloccante;
- marker di omissione IT ed EN;
- lingua sconosciuta: controllo strutturale e astensione lessicale;
- una sola emissione affinity per manifest;
- una sola emissione di lunghezza per risorsa con misure per lingua;
- parità di `${RUNTIME:...}`, `{{...}}`, executor e argomenti;
- `--strict` con etichetta e totale coerenti.

### 13.2 Prove di inventario

- core;
- builtin;
- executor utente diretto;
- `skills/<skill>/<executor>`;
- `_imports/<skill>/<executor>` se ancora supportato;
- directory ritirata esclusa dal profilo attivo ma visibile nell'audit;
- skill disabilitata classificata senza confonderla con core.

### 13.3 Prove del traduttore

- candidato valido scritto e firmato;
- argomento del `PATTERN` tradotto: nessuna scrittura;
- segnaposto perso: nessuna scrittura;
- errore dopo chiamata LLM: manifest/state/firma byte-identici;
- una lingua editata: sorgente corretta;
- due lingue editate: conflitto, nessuna scelta alfabetica;
- risorsa nuova IT+EN: entrambe adottate;
- placeholder state: pubblicazione rifiutata o inizializzazione esplicita;
- import annidato visitato una sola volta;
- retry idempotente.

### 13.4 Prove dei generatori

- Synt monolingue passa `candidate` ma non `active`;
- traduzione completa passa il profilo bilingue;
- import con pattern/schema divergenti viene fermato;
- proposta promossa usa lo stesso validatore;
- variazione ricca della prosa resta ammessa quando gli atomi core sono validi;
- template non può sovrascrivere la politica di esecuzione o lo standard.

### 13.5 Prove sul catalogo reale

- 122/122 manifest scoperti (115/115 alla data dell'analisi originale);
- 244/244 descrizioni principali controllate (erano 230/230);
- 693/693 descrizioni argomento censite (erano 654/654);
- le divergenze note negli atomi macchina IT/EN sono esattamente quelle
  registrate in §4.5 e §4.12: una sola, `set_signatures`. Il criterio non è più
  «zero divergenze» ma «nessuna divergenza non registrata», perché la baseline
  di zero è decaduta il 24 agosto;
- conteggi di baseline spiegabili per origine e lingua;
- nessuna modifica ai file durante `audit`;
- nessuna testa corrente alterata dal rendering adattivo;
- tempo del controllo completo registrato.

### 13.6 Prove di non regressione del runtime

Con linter disattivo e attivo soltanto ai confini di authoring devono essere
identici:

- catalogo caricato;
- firme dei piani;
- pool del proposer;
- ordine degli executor;
- argomenti estratti;
- richieste di consenso;
- effetti e output finali;
- tempi dei turni entro il rumore di misura.

### 13.7 Prove live

Dopo l'eventuale implementazione, non durante questa analisi:

- una query italiana che usa un executor con argomento runtime-owned;
- equivalente inglese;
- una query multidominio IT con almeno file, messaggi e calendario;
- equivalente EN o un sottoinsieme semanticamente controllato;
- un executor importato GitHub dopo correzione del suo pattern;
- riavvio e verifica catalogo soltanto a turno concluso.

## 14. Criteri di arresto e rollback

La promozione si arresta se accade uno dei seguenti eventi:

- un manifest non toccato diventa non caricabile;
- il numero di executor attivi cambia per il solo aggiornamento del linter;
- una traduzione rifiutata modifica comunque manifest, state o firma;
- una parola naturale genera un errore bloccante non strutturale;
- affinity o altri finding globali sono duplicati per lingua;
- il linter e il renderer producono superfici diverse nei casi entro budget;
- il traduttore sceglie una sorgente senza evidenza;
- un import attivo conserva placeholder di stato dopo pubblicazione;
- un avviso di lunghezza porta a un accorciamento automatico;
- un corpus IT/EN cambia piano senza che sia stato modificato il manifest
  relativo.

Il rollback consiste nel disattivare i nuovi punti di blocco mantenendo il
motore in modalità `audit`. Non richiede ripristino dei manifest perché la
prima fase non li modifica e la fase di traduzione conserva il precedente
artefatto fino alla pubblicazione completa.

## 15. Criteri di completamento

RM-0002 può passare a `implemented` soltanto quando:

- esiste un solo inventario condiviso per tutte le topologie ammesse;
- il linter controlla ogni lingua presente e non privilegia `it`;
- i controlli globali sono eseguiti una volta;
- i finding indicano risorsa e lingua;
- la parità degli atomi macchina è verificata deterministicamente;
- `get_location.actor` non è un falso errore bloccante;
- il gate di attivazione di una lingua controlla **quella** lingua, e la prova lo
  dimostra con una fixture di terza lingua difettosa che viene fermata;
- una traduzione che altera capitoli, chiamata, argomenti o chiave di un
  `${RUNTIME:...}` viene rifiutata prima della scrittura;
- gli errori dei tre pattern importati sono rilevati prima della pubblicazione;
- il traduttore valida in memoria e non scrive su errore;
- `lang_state` non contiene placeholder negli artefatti attivi;
- un conflitto con più lingue modificate non sceglie alfabeticamente;
- i tre generatori usano lo stesso punto comune;
- firma e loader conservano disponibilità e semantica attuali;
- nessun manifest viene accorciato automaticamente;
- il catalogo attivo passa due cicli completi consecutivi;
- corpus IT/EN e prove live non mostrano regressioni di routing o argomenti;
- tempi e conteggi finali sono registrati in un rapporto di implementazione;
- l'indice anti-regressione e l'ADR pertinente vengono aggiornati soltanto dopo
  che il comportamento è realmente attivo.

## 16. Non-obiettivi

RM-0002 non autorizza:

- modifica dei manifest osservati (115 il 23 luglio, 122 il 24 agosto),
  `set_signatures` compreso: D1 di §4.12 è una constatazione, non un mandato di
  correzione dentro questa roadmap;
- correzione immediata dei tre import GitHub;
- riscrittura delle descrizioni legacy;
- modifica dei limiti 240/320/180;
- promozione del renderer adattivo sperimentale;
- esposizione delle descrizioni argomento nel proposer attivo;
- traduzione delle affinity, che restano lista mista IT+EN;
- introduzione di una dipendenza LLM nel linter;
- supporto dichiarato a nuove lingue senza corpus;
- modifica del loader per rifiutare nuovi avvisi editoriali al boot;
- deploy di documentazione pubblica.

## 17. Decisioni raccomandate

Le seguenti scelte sono sufficientemente supportate dall'analisi:

1. realizzare il linter come motore unico multilingue;
2. controllare tutte le lingue presenti, non soltanto `it`/`en` hardcoded;
3. mantenere `it` ed `en` obbligatorie nello standard active corrente;
4. eseguire una sola volta i controlli globali;
5. confrontare deterministicamente soltanto atomi macchina;
6. trattare la menzione naturale di un argomento runtime comune come ambigua,
   non come errore certo;
7. raggruppare gli avvisi di lunghezza per risorsa;
8. applicare i blocchi appena prima di pubblicazione, traduzione e firma;
9. non aggiungere il linter editoriale al percorso ordinario dei turni;
10. estrarre un inventario neutro condiviso;
11. sostituire la scelta alfabetica della sorgente multipla con un conflitto
    esplicito;
12. non mescolare questa implementazione con gli esperimenti del renderer.

## 18. Registro di avanzamento

| Data | Stato | Evento | Prove |
|---|---|---|---|
| 2026-07-23 | `active` | analisi macro/micro e creazione della roadmap | codice e 115 manifest ispezionati; nessun manifest modificato |
| 2026-08-24 | `active` | riverifica completa contro codice e catalogo correnti | 122 manifest, linter rieseguito per lingua (IT 3 errori/124 avvisi, EN 4/100), lunghezze e atomi macchina ricontati, `lang_state` riaudito, pipeline RM-0005 letta; nessun file di prodotto modificato |

Esito della riverifica del 24 agosto 2026, in breve:

- **le conclusioni del 23 luglio reggono tutte**; nessuna decisione di §17 è
  stata smentita dai dati nuovi;
- **due pezzi del disegno sono stati costruiti da RM-0005**, non da qui: la
  pubblicazione transazionale (§7.11) e metà dell'inventario comune (§7.1);
- **il difetto centrale è passato da igiene a correttezza**: il gate di
  attivazione di una lingua invoca il linter, e il linter legge un'altra lingua
  (§4.11);
- **la parità degli atomi macchina non è più a zero**: `set_signatures` insegna
  due chiamate diverse nelle due lingue (§4.12, D1);
- **i tre errori degli import non si sono mossi in un mese**, perché nessun
  percorso li visita (§4.12, D4);
- il piano operativo è stato riscritto in §12.0 in quattro priorità, la prima
  delle quali è piccola e chiude la lacuna di correttezza.
