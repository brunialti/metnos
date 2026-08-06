# Vaglio avversariale della riformulazione (6/8/2026, sera)

> Roberto: «lancia agenti avversariali con lenti diverse sulla tua soluzione,
> poi integra le osservazioni e recepisci eventuali soluzioni».
>
> Qui si tiene traccia **mentre** succede: ogni lente ha la sua sezione,
> scritta appena l'agente rientra. Se la sessione si interrompe si perde al
> massimo l'agente in volo.

## La tesi sotto attacco

Le 34 guardie di `dispatch.py` (3819 righe, 53% del file) esistono per quattro
cause, non per 34 casi:

| causa | guardie | righe | spari/21gg | leva proposta |
|---|---|---|---|---|
| il piano non ha TIPI | 9 | 1059 | 162 | ogni tool dichiara produce/consuma → un validatore + un inseritore |
| il POOL offre il candidato sbagliato | 13 | 1079 | 249 | `[applicability]` nel manifest + un gate del pool |
| gli ARGS non hanno contratto forte | 9 | 627 | 10 | grammatica vincolata estesa dagli args |
| modelli di FLUSSO interi | 3 | 1020 | 101 | non è riparazione: è un piano → L1/skill |

Più la regola di ammissione: una guardia nuova deve dichiarare a quale causa
appartiene e perché il meccanismo di quella causa non la copre.

Prova a favore già in casa: `routing_pool._gate_image_modality` (2/7), 30
righe, ha eliminato una classe intera di misroute togliendo i tool-immagine dal
pool quando la query non nomina immagini.

## Stato del vaglio

| lente | esito | integrato? |
|---|---|---|
| 1. fattibilità nel codice reale | **3 leve su 4 abbattute**, la quarta ridotta a 2/9 | sì (sotto) |

---

## Lente 1 — fattibilità nel codice reale

**Verdetto: leva 1 REGGE PARZIALMENTE (2 guardie su 9), leve 2, 3 e 4 NON
REGGONO.** Ho verificato di persona le quattro affermazioni decisive: tutte
confermate.

### 1.1 La leva 3 (grammatica sugli args) era già stata fatta e bocciata

`internal/reports/grammar_args_ab_2026-07-06.md` — A/B del 6/7 sul proposer:
piani identici, `enum_invalid=0` in entrambi i rami, **stesso identico
guard_fire**. Probe avversario: il modello mappa da solo `RAR`→`zip`,
`KOI8`→`latin-1`. Conclusione già scritta lì: «la grammar-args previene un
errore che **non accade**». Il codice esiste
(`grammar_framework.build_framework_grammar_typed`), il flag esiste
(`METNOS_PROPOSER_GRAMMAR_ARGS`, `proposer.py:602`) ed è spento **di
proposito**.

E la premessa era pure falsa: la GBNF non vincola «solo i nomi» —
`tool_grammar._emit_tool_args` tipizza gli args per-tool da maggio.

**Lezione, la stessa che avevo predicato stamattina e non applicato a me
stesso**: ho proposto un esperimento che il repo aveva già fatto e archiviato.
Prima di proporre una leva, cercare se esiste già il suo referto.

### 1.2 La leva 2 (gate del pool) non gira dove girano le guardie

`routing_pool.py:434`: «Pool ridotto è **SOLO** per il prompt Proposer». Le
guardie invece girano anche sugli **hit L0/L1**, dove un pool non esiste
(`_apply_deterministic_structure_guards` è condivisa L0/L1/L3,
`dispatch.py:5804`). E il registro lo dice già a chiare lettere per
`enforce_provider_binding`: «il legame provider vale sul PIANO, non solo sul
pool … **anche quando il piano arriva da cache L0/L1**» (`dispatch.py:5596`).

Di più: 6 delle 13 guardie non scelgono un candidato — scrivono `args.client`,
riscrivono `delete_entries`→`move_messages` (e `delete_messages` non esiste in
catalogo: non c'è niente da togliere dal pool), inseriscono `compute_entries`,
o decidono su una proprietà (find senza selettore) che a tempo-pool **non
esiste ancora**, perché gli args li scrive l'LLM dopo.

### 1.3 `_gate_image_modality` è un caso speciale, non un modello

Il suo segnale è `prefilter._OBJECT_HINTS["images"]`, che il commento marca come
**eccezione motivata** al §7.3 («jpg/png/heic … vocabolario chiuso e stabile da
30 anni»). Gli altri oggetti non hanno nulla di simile: «luogo» sta sia in
`location` sia in `places`, «documento» in `files`, «log» in `texts`. E la
proprietà che sfrutta — le immagini la query le NOMINA sempre — sta nel testo,
non nel manifest: `[applicability]` la sposterebbe dove non c'è.

### 1.4 Precedente empirico che uccide le leve «dichiara nel manifest»

`[planning].companions` è una superficie dichiarativa aggiunta al pool, con
caricamento nel loader e punto fisso nel ranking. **Dichiarazioni nei manifest:
zero.** (Verificato: `grep -rl "\[planning\]" executors/*/manifest.toml` = 0.)
Una leva che dipende dall'authoring dei manifest in questo repo ha già fallito
una volta, in silenzio.

### 1.5 Modi nuovi di fallire, che oggi non esistono

- **Capacità persa in silenzio**: il gate immagini è fail-open solo perché fa
  `return kept or pool`. Con N filtri componibili l'intersezione può togliere
  il tool giusto lasciando il pool **non vuoto**: nessun fail-open scatta, la
  guardia non c'è più, e l'errore diventa un misroute muto — peggio di un piano
  riparato.
- **Invalidazione di massa**: `[applicability]` o i tipi nei manifest = re-sign
  = `tools_sig`/`pool_sig` nuovi = **L0/L1 azzerate in un colpo** (ADR 0182).
- **Vincoli d'ordine spostati**: la GUARD_PIPELINE l'ordine almeno lo dichiara;
  il pool no.

### 1.6 La leva 1 vale, ma su un perimetro quattro volte più piccolo

Delle 9 guardie, **2** sono davvero dataflow (`propagate_sink_schema_to_extract`,
`enforce_complete_sink_cardinality`). Le altre discriminano su informazioni che
un manifest non contiene: il **plurale nella query**, il lessico
`fs.files_in_folder`, l'**ordine dichiarato dall'intent**, lo **schema di uno
store registrato a runtime** (che cambia coi dati dell'utente), lo **stato di
protocollo** di una sessione siti.

### 1.7 Difetto di metodo trovato nella MIA misura

I numeri «spari/21gg» vengono dal journal reale (non da un replay, come
l'agente ha supposto), ma contano **righe di log**, e **10 guardie su 34 non
loggano nulla quando sparano**: nelle somme per famiglia valevano zero. Il
contatore esatto (`guard_stats`) ha un giorno di vita e 38 piani. La classifica
per causa regge come ordine di grandezza, non come misura fine.

## Che cosa sopravvive, e cosa ci metto al posto

Dalla lente 1, riformulato:

1. **`step_chunk` (mappa step→clausola) da variabile locale a superficie
   condivisa.** Vive dentro `_fill_clause_args`, la guardia **più calda del
   traffico misurato**; e da quella nozione dipendono per costruzione
   `decontaminate_reader_qualifier`, `scope_sink_provider_to_clause`,
   `align_provider_client`, `normalize_result_folder_exclusion`. È l'unica leva
   che tocca ciò che spara davvero.
2. **Il ritiro si compra con la quarantena, non con la migrazione**: il
   meccanismo (dormienti nel riepilogo notturno, protocollo in 4 passi, oracolo
   a 804 casi) è già in piedi da oggi; gli mancano 21 giorni di traffico vero.
3. **Regola di ammissione girata** — e questa è meglio della mia. Una guardia
   nuova non dichiara «a quale causa appartengo» ma **quale informazione uso**:
   (a) lessico della query · (b) ordine dichiarato dall'intent · (c) schema
   runtime di uno store · (d) stato di sessione · (e) schema statico dei
   manifest. **Solo (e) è candidabile a una superficie dichiarativa**; le altre
   quattro sono codice nel motore per costruzione. È verificabile a colpo
   d'occhio e chiude il dibattito «34 casi contro 4 cause».
4. **Prima di qualunque validatore di dataflow**: tipizzare
   `[output].schema_inline`, oggi una stringa opaca letta per substring. Da
   solo assorbe le 2 guardie sink, senza toccare né pool né grammatica.


