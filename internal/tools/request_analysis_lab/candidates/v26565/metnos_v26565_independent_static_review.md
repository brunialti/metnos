# Review indipendente offline V26.5.6.5

Data: 9 agosto 2026. Review **indipendente**: conclusioni dell'autore non
ereditate, ogni numero riprodotto in locale.

Verdetto: **STATIC BLOCK**.

Zero rete, zero chiamate modello, zero trasporto, zero gate creato o consumato,
zero live. Gold, overlay, fixture, V26.5.6.4 e i nove artifact congelati
V26.5.6.5 non sono stati modificati: verificati byte per byte prima e dopo la
review.

## 0. Il verdetto in tre righe

L'idea architetturale regge e l'ho provata: **portare l'identità di clausola
nella struttura chiude davvero i quattro codici che avevano ucciso il live
V26.5.6.4**, e 4.000 frame schema-validi generati a caso non ne accendono
nessuno. Ma le tre proprietà che il candidato dichiara *oltre* a quella —
espansore totale, insieme fatale coincidente con lo schema, impronta
query-free — sono **tutte e tre falsificabili con input di una riga**, e una di
esse riporta in vita esattamente il guasto che il post mortem prometteva di
eliminare: un caso che muore prima di essere misurato.

I quattro blocker sono chiudibili offline senza toccare prompt, schema,
registro, validator o gold. Nessuno richiede un live.

## 1. Riproduzione

| Controllo | Esito |
|---|---:|
| self-test `/usr/bin/python3 -I -B` | **42/42 PASS** |
| `metnos_v26565_author_selftest_result.json` rigenerato | **byte-identico** (`51b53bc3…4bcb12`) |
| artifact del freeze d'autore | **9/9** hash e dimensione |
| dipendenze congelate riusate | **5/5** hash |
| tabella hash del README candidato | **10/10** |
| freeze | `b7b02367bba88c244dd772b0722f4925292e4e318eca80b5df0b0556f3638f90` |
| `__pycache__` / `.pyc` nel bundle | **0** (il residuo dichiarato nel README non c'è più) |
| rete / modello / live / gate | **0 / 0 / 0 / 0** |

Comando di replay del self-test:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26565/metnos_v26565_offline_selftest.py
```

Comando della sonda di questa review (nuovo file, non nel freeze):

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26565/\
metnos_v26565_independent_static_review_probe.py \
  --json .../metnos_v26565_independent_static_review_probe_result.json
```

26 sonde: 14 rilievi, 9 conferme, 3 misure di contesto. Seme fisso `20260809`,
esito deterministico.

## 2. Ciò che è confermato

**C1 — la correzione S1 funziona, e non solo sui casi scelti.** Ho generato
frame casuali sull'intero spazio del contratto e ne ho tenuti **4.000
schema-validi** (su 4.501 generati). `primary_cardinality`,
`orphan_dependency`, `clause_span_consistency` e `projection_missing`:
**0 accensioni**. La ragione è strutturale e la sottoscrivo: `clause_id` è la
posizione nell'array, quindi è unico per costruzione; ogni clausola proiettata
emette esattamente un atomo di proiezione; le dipendenze appartengono alla
clausola per contenimento; tutti gli atomi di una clausola ereditano lo stesso
span dalla testa. `contains`+`minContains` garantisce almeno una proiezione.

**C2 — l'arco fra clausole diverse funziona.** Clausola 2 che consuma
l'`unknown` della proiezione della clausola 1: schema-valido, espanso, valido.

**C4 — Unicode.** Una sola impronta su 9 scritture **× NFC/NFD/NFKC/NFKD**
(l'autore provava le sole 9 scritture), uniformemente valide.

**C5 — contaminazione.** Zero query verbatim e zero 3-gram e 4-gram condivisi,
**sia nel prompt sia nello schema** (l'autore auditava il solo prompt). L'unico
2-gram condiviso è `in the`. Nessun identificatore di gold, fixture od overlay
raggiunge il contratto.

**C6 — nessun aggancio di superficie.** Ogni `enum`/`const` dello schema è o
una chiave strutturale fissa o una denotazione del registro congelato:
**0 letterali inspiegati**. Il prompt è ASCII, senza esempi fra virgolette.

**C6c — `DERIVED_REFERENCE_BRIDGE`.** Confermato: nel bundle compare **solo** in
`metnos_v26565_offline_selftest.py` (definizione + un uso) e nel README, oltre
alla sonda di questa review che lo ispeziona. Non è
nel prompt, nello schema, nell'espansore, nella condotta; `actor.current_position`
non compare in nessun artifact di richiesta. È un ponte fra due registri tecnici
congelati, una voce, nessun token di lingua sorgente, mai a runtime.

**Statico — nessun collegamento al runtime.** Il bundle importa solo `hashlib`,
`json`, `copy`, `pathlib`, `argparse`, `importlib`, `sys`, `regex`,
`jsonschema`. Nessun `runtime.*`, nessun `subprocess`, nessun socket, nessun
`urllib`. L'unico `compile/exec` è il caricamento del validator congelato,
pin-nato su SHA prima dell'esecuzione.

## 3. Blocker

### B1 — l'espansore **non è totale**, e la condotta non lo protegge

Il README dichiara «l'espansore è **totale**: non solleva mai, su nessun
input», e il gruppo `no_fatal_invariant_outside_the_schema` asserisce «the
expander is total over every probe frame». Falso. Cinque input di una riga fanno
sollevare `expand_frame` **e** `structural_fingerprint`:

| input schema-invalido | eccezione |
|---|---|
| `{"status":"supported","clauses":5}` | `TypeError: 'int' object is not iterable` |
| clausola con `"dependencies": 5` | `TypeError: 'int' object is not iterable` (impronta: `has no len()`) |
| `{"status":"typed_ambiguity","alternatives":5}` | `TypeError` |
| `{"status":"typed_ambiguity","alternatives":[5,6]}` | `AttributeError: 'int' object has no attribute 'get'` |
| `{"status":"unsupported","clauses":5}` | `TypeError` |

La causa è un idioma ripetuto: `clause.get(KEY_DEPENDENCIES, []) or []`,
`source.get(KEY_CLAUSES) or []`, `source.get(KEY_ALTERNATIVES) or []`. `or []`
neutralizza `None` e i falsi, **non** uno scalare vero. `_binding_kinds` è
invece protetto con `isinstance(..., list)`: la difesa esiste, ma solo su un
contenitore su quattro.

Il difetto conta perché `metnos_v26565_offline_pipeline.py` protegge lo **stadio
3** con `try/except` e lascia lo **stadio 2 nudo**. Quindi l'eccezione esce da
`evaluate` e il caso non viene registrato su **nessuno** dei tre stadi: è
letteralmente il guasto V26.5.6.4 — un caso che muore prima di farsi misurare —
riproposto in un ramo che esiste *apposta* per gestire frame che lo schema ha
già respinto. Se la decodifica vincolata fosse autorevole quel ramo non
servirebbe; il candidato lo esegue perché sa che non lo è.

Correzione: `isinstance(..., list)` sui quattro contenitori più un `try/except`
attorno allo stadio 2 simmetrico a quello dello stadio 3, con codice
`expander_exception:<Tipo>`.

### B2 — l'impronta strutturale è query-free solo sui frame schema-validi

S4 esiste per i casi **invalidi**: «ogni record porta un'impronta strutturale
query-free, anche sui fallimenti». Su un frame schema-invalido l'impronta copia
**verbatim** ogni stringa controllata dal modello: `status`, `relations`,
`clause_roles`, `speech_acts`, `binding_kinds`, `proof_kinds`. Provato con un
marcatore al posto del materiale di richiesta: sei campi contaminati, e
l'impronta è ciò che verrebbe persistito in un batch dichiarato
`query_material_included=false`.

Il test di riservatezza dell'autore non lo vede perché costruisce **ogni** frame
sondato con `build_frame_from_template`, cioè schema-valido per costruzione:
`iter_errors` non compare nel gruppo. Il vocabolario chiuso è già calcolato
nel test (`allowed`); manca solo di applicarlo **dentro** `structural_fingerprint`
invece che nell'asserzione.

Correzione: filtrare ogni stringa dell'impronta contro il vocabolario tecnico
chiuso, sostituendo il fuori-vocabolario con un conteggio
(`out_of_vocabulary_strings: n`).

### B3 — `typed_ambiguity` con clausole fuori registro: identità di clausola incoerente fra alternative

Lo schema permette clausole `out_of_registry` **dentro** l'array di ogni
alternativa. La forma normale congelata ha però un solo `unsupported_clauses`
globale. L'espansore risolve prendendo quelle della **prima** alternativa e
scartando quelle delle successive; il validator poi somma quella lista globale
al footprint di **ogni** alternativa, i cui `clause_id` sono per-alternativa.

Tre conseguenze, tutte su frame **schema-validi**:

| frame | codici |
|---|---|
| alternative che differiscono sulle clausole fuori registro | espansione `alternatives_disagree_on_unsupported_clauses`; validator `alternative_coverage`, `clause_ids` |
| stessa clausola fuori registro a indice diverso nelle due alternative | idem |
| 4 fuori-registro + 4 proiettate contro 8 proiettate (bound = 8) | validator `alternative_coverage`, `clause_ids`, **`clause_limit`** |

`clause_ids` è **la famiglia di codici che uccise V26.4.1** (20 casi su 34 il
cui unico errore era `clause_ids`, handover §2). Torna raggiungibile qui, su un
frame che lo schema accetta, per un artefatto di proiezione dell'espansore — non
per un errore semantico del modello. In più le clausole fuori registro delle
alternative 2..N spariscono **silenziosamente** dalla forma normale: perdita
semantica senza codice dedicato.

Nessuno dei 42 test copre questa forma: la fixture non produce mai
`typed_ambiguity` con clausole fuori registro.

Correzione: o l'espansore rifiuta di proiettare quando le alternative
divergono, con un codice esplicito e senza scarto silenzioso, o l'identità di
clausola viene resa globale al frame invece che per-alternativa.

### B4 — `max_atoms_per_analysis` non è imposto, e lo stadio 3 censura sé stesso

Lo schema di risposta limita le `dependencies` **per clausola** a
`max_atoms - 1` e le clausole a `max_clauses`, quindi ammette fino a
**8 × 16 = 128 atomi**; il limite congelato è **16**, e la forma normale del
validator lo impone con `maxItems`. Un frame schema-valido di 8 clausole con 2
dipendenze ciascuna produce **24 atomi**: lo schema di risposta lo accetta senza
errori, e il validator lo respinge con un codice `schema`.

Il punto grave non è il limite: è che `validate_frame` **ritorna subito** dopo
lo schema interno (`if errors: return {...}`). Lo stadio 3 dichiara
`stage_ran=true` e `stages_measured=3`, ma i codici semantici misurati sono
**zero**. È di nuovo il guasto di V26.5.6.4 — «uno solo è arrivato a farsi
controllare» — spostato di uno stadio e mascherato da un contatore che dice 3.

Sui 34 Phase-1 la forma non si presenta; il candidato però è il successore
destinato ai 109 + 70 adversarial multi-azione e multi-dominio, dove 17 atomi
sono raggiungibili. E, indipendentemente dalla frequenza, **falsifica la tesi
centrale**: esiste un frame schema-valido che un invariante fuori dallo schema
rende invalido, censurando la diagnosi.

Correzione: derivare dal registro un tetto agli atomi anche nello schema di
risposta (per esempio `dependencies.maxItems` in funzione di `max_clauses`), e
far riportare al pipeline i codici semantici anche quando lo schema interno del
validator fallisce.

## 4. Rilievi non bloccanti

**N1 — `source_ordinal`: spazio di arrivo derivato e non limitato.** Lo schema
lo vincola solo a `minimum: 1`; il tetto vero è il numero di atomi dello scope,
che il modello deve contare a mente. Nel campione casuale
`edge_source_ordinal_not_backward` (6.277) e
`edge_source_ordinal_out_of_range` (3.637) sono i due codici più frequenti in
assoluto. È l'unico identificatore ancora **emesso contro un ordine derivato**:
la lezione del post mortem («derivare un identificatore non elimina il vincolo,
lo sposta dove lo schema non arriva») vale ancora qui, attenuata dal fatto che
ora il guasto è misurato e non aborta. Aggiungere almeno
`maximum: max_atoms_per_analysis`.

**N2 — ambiguità del prompt su `source_ordinal` in `typed_ambiguity`.** Il
prompt dice che l'ordinale conta «from one across the whole analysis», mentre
l'espansore riparte da 1 **dentro ogni alternativa** (verificato:
`[[1,2],[1,2]]`). Un modello che legge alla lettera produce un frame
schema-valido che fallisce espansione e validazione. Il testo va reso esplicito:
l'appiattimento è per alternativa.

**N3 — l'insieme «fatale» dichiarato è più stretto del reale.** Sui 4.000 frame
schema-validi, **3.322 sono invalidi** e 678 validi; i codici che decidono sono
tutti fuori schema. I due ordinamenti prosaici `clause_source_order` (1.478) e
`projection_source_order` (1.057) sono l'esempio più chiaro: «Emit clauses in
source order» vive solo nella prosa. Sono regole del validator congelato, non
introdotte qui, e — questa è la differenza vera rispetto a V26.5.6.4 — sono
**riportate, non fatali**: `invalid_from_expansion_only = 0`, cioè nessun frame
è stato invalidato dalla sola espansione senza che il validator lo confermasse.
Va però corretta la frase del README: l'insieme fatale coincide con lo schema
**per i quattro codici del post mortem**, non in generale.

**N4 — la validità strutturale non è un oracolo semantico.** Sweep indipendente
di **1.934 mutazioni scalari singole** sui 34 frame gold: 1.680 respinte, 753
bloccate dallo schema, **254 restano valide** e di queste **53 cambiano la
semantica gold** (per esempio `speech_act` `open_question`→`imperative`, o
`ref` `actor.current`→`actor.quoted`). Il gruppo `mutation_suite` dell'autore
asserisce solo `valid is False` su 12 mutazioni scelte a mano, quindi non può
vederle. L'oracolo semantico esiste già nel file
(`normalise_expanded` vs `normalise_gold`): va usato anche nella mutation suite.
Zero eccezioni della condotta su 1.934 mutanti: nessuna delle 34 forme gold
tocca il difetto B1.

**N5 — contabilità del self-test.** Due controlli di `materialisation` girano
dentro `if SCHEMA_PATH.exists():` / `if PROMPT_PATH.exists():`: se i due
artifact mancassero il totale scenderebbe a 40/40 e resterebbe «all green». Il
totale dichiarato va reso incondizionato.

**N6 — disgiunto morto.** In `no_emitted_identity`,
`"Do not \nemit numeric labels" in prompt.replace("\n", "\n")`: la `replace` è
un no-op e il letterale non corrisponde mai (nel prompt la frase è su una riga
sola). Il controllo passa solo per il secondo disgiunto. Rimuovere il primo.

## 5. Cosa non ho verificato, e perché

- **Accuratezza semantica**: nessun live, nessun modello. 42/42 e 4.000 frame
  casuali non dicono nulla su quanto bene il modello analizzi una richiesta.
- **I 109**: il registro è Phase-1 con 10 relazioni. Confermo il limite già
  dichiarato dall'autore.
- **Il validator e il registro congelati** sono stati letti per verificare le
  tesi del candidato, non ri-revisionati: sono riusati invariati e le loro
  regole prosaiche (§N3) preesistono a V26.5.6.5.
- Nessun gate progettato, proposto o creato.

## 6. Condizione per il PASS

Chiudere B1, B2, B3 e B4 **offline**, senza toccare prompt, schema, registro,
validator, fixture, overlay o gold, e aggiungere ai test: contenitori scalari
per i quattro campi, impronta su frame schema-invalido con marcatore,
`typed_ambiguity` con clausole fuori registro in ogni combinazione di indice, e
un frame oltre `max_atoms_per_analysis`. Riparare N5 e N6 nello stesso passaggio
e usare l'oracolo semantico nella mutation suite (N4).

Fino ad allora **nessun trasporto è autorizzabile** e nessun gate va progettato.
