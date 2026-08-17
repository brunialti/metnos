# V26.5.6.4 — post mortem causale del live K1/34

Analisi **offline**, 9 agosto 2026. Zero rete, zero chiamate modello, zero
rerun, zero rescore post-hoc, zero modifiche a gold, artifact congelati, gate e
output originali in `/tmp`.

## 0. Conclusione in una frase

Il trasporto è perfetto e la semantica è a zero: la forma compatta ha **tolto
`clause_id` e promosso lo span sorgente a chiave primaria dell'identità di
clausola**, così la decodifica pretende due invarianti globali — span iniettivo
su proiezioni ∪ clausole non supportate, e uguaglianza *esatta* di span fra una
dipendenza e la sua proiezione — che **lo schema JSON non sa esprimere**, che
quindi la decodifica vincolata non può imporre, che vivono solo nella prosa del
prompt e che **abortiscono prima di qualunque validazione semantica**.

## 1. Verifica degli artifact

| Artifact | SHA-256 atteso | Esito |
|---|---|---|
| `/tmp/metnos_v26564_live_k1_34.json` | `9c15cc29…fc04` | **corrisponde** |
| `/tmp/metnos_v26564_live_k1_34_evaluation.json` | `c2602051…4522` | **corrisponde** |

- 20/20 dipendenze fissate in `ARTIFACT_PINS` intatte (byte e dimensione).
- 14/14 hash incrociati coerenti (batch → freeze/runner/pre-gate/gate;
  evaluation → batch; gate → verifier/review/preflight).
- Tabelle hash del README candidato: 10/10 corrispondono ai file reali.
- Conformità al gate: 34 casi autorizzati / 34 tentati, limite 34 POST / 34
  attempts, retry ammessi 0 / retry 0, `total_transport_attempts` 35 = 1 GET di
  preflight + 34 POST. `authorizes_runtime_cutover` **false**,
  `cutover_authorized` **false**, `model_calls` 0 e `network_calls` 0 nel
  valutatore. Suite di mutazione del gate: 27/27 respinte, 0 fallimenti.

Il gate esterno richiedeva `output_must_be_absent`; ora l'output esiste, quindi
**quel gate non può riautorizzare nulla**: un eventuale nuovo live richiede un
gate nuovo.

## 2. Contatori, validità, errori, latenza

| Metrica | Valore |
|---|---|
| casi configurati / tentati / valutati | 34 / 34 / 34 |
| richieste accettate dal server | 34 |
| risposte HTTP | 34, **tutte 200** |
| frame decodificati | 34 |
| **casi validi** | **0** |
| casi invalidi | 34 |
| retry | 0 |
| errori di trasporto / catene di eccezione | 0 / 0 |
| `phase` per tutti i record | `complete` |
| latenza totale min / p50 / p95 / max | 5.173 / 6.109 / 9.447 / 9.494 ms |
| latenza trasporto media | 6.420 ms |
| latenza facade media | 94 ms |

La latenza è quasi interamente del modello: la facade locale pesa ~1,4% del
totale. Non c'è alcun problema di trasporto, di protocollo o di infrastruttura.

`status` = `MODEL_BATCH_COMPLETE`, `accuracy_claimed` = false,
`posthoc_credit` = false, `query_material_included` = false. Il batch è onesto:
dichiara di non rivendicare accuratezza.

### Valutazione congelata

Tutte le porte a zero: `all_gates_exact` 0, `coverage_exact` 0,
`dependency_exact` 0, `direct_binding_exact` 0, `safety_exact` 0,
`evidence_valid` 0, `positive_usable` 0/9, `negative_usable_leakage` 0/25.

**Attenzione all'unico numero che sembra buono**: `negative_usable_leakage = 0`
è un passaggio **vacuo**. Tutti i `predicted_*` dei 34 record sono `null`: non è
uscito nulla di utilizzabile, quindi non poteva esserci fuga. Non è prova di
rifiuto corretto sui 25 negativi.

## 3. Catena causale

### 3.1 La condotta è sequenziale e si ferma al primo errore

Il worker esegue **schema → adapter → validator** e ritorna al primo fallimento.

- **34/34 hanno superato lo schema JSON.** Lo schema non è il vincolo attivo.
- **33 sono morti nell'adapter** (32 + 1).
- **1 solo caso ha raggiunto il validator.**

Conseguenza diretta e importante: `proof_family` + `reference_type` **non
significa "un solo caso ha problemi di prova/tipo"**, significa "un solo caso è
arrivato abbastanza avanti da farsi controllare". La qualità semantica di 33
casi su 34 è **non misurata**, non "buona".

### 3.2 I 32 casi — `two semantic clauses claim the same source span`

`metnos_v265_compact_adapter.py::_expand_graph`, righe 71-78:

```python
projections = [atom for atom in atoms if atom["atom_kind"] == "projection"]
anchors = projections + unsupported
spans = [_span(item) for item in anchors]          # (start, end)
if len(spans) != len(set(spans)):
    raise UnsafeCompactGraph("two semantic clauses claim the same source span")
```

`clause_id` è stato rimosso dalla forma compatta, e viene **ricostruito
ordinando gli span**: `clause_by_span = {span: index ...}`. Lo span è quindi la
chiave primaria surrogata dell'identità di clausola. Condizione necessaria e
sufficiente del codice: **≥2 ancore con la stessa coppia (start, end)**.

Provato meccanicamente (`metnos_v26564_postmortem_encoding_probe.py`, sezione B):
due frame distinti — due proiezioni sullo stesso span, e una proiezione più una
clausola non supportata sullo stesso span — **soddisfano lo schema congelato e
vengono respinti dall'adapter con esattamente questo codice**. Con
`response_format: json_schema, strict: true`, la decodifica vincolata **non ha
alcun modo** di impedirli.

Provato anche che non si tratta di un limite di rappresentazione del gold: la
fixture congelata attende **al massimo una proiezione per grafo** (31 casi
`supported` con 1 proiezione, 3 `typed_ambiguity`). Il frame in forma gold passa
schema e adapter. **L'encoding sa rappresentare la risposta giusta per tutti e
34 i casi.** Il modello ha prodotto ancore semantiche in eccesso.

Provato per esclusione: il ramo `status = "unsupported"` solleva un codice
*diverso* (`unsupported clauses share one span`), mai osservato. Quindi nei 33
fallimenti d'adapter **lo status non è mai stato `unsupported`**.

Caso limite provato: l'ordinale 7 (`neredeyim`, turco) ha `segment_count = 1`.
Con un solo segmento l'unico span possibile è `(1,1)`: **due ancore qualsiasi
collidono per necessità aritmetica**. Per quel caso è dimostrato che il modello
ha emesso ≥2 ancore dove il gold ne vuole 1.

### 3.3 Il caso singolo — `dependency has no projection with the same clause span`

Righe 81-84 dello stesso file: per ogni atomo, `_span(atom)` deve appartenere
all'insieme degli span delle proiezioni — **uguaglianza esatta di tupla, non
inclusione**. L'unico caso è l'ordinale 24, `trova ristoranti vicino a me`:
l'unica query dei 34 che richiede davvero una dipendenza
(`spatial.located_at` che produce una posizione, consumata da `spatial.near`).

Provato meccanicamente: una dipendenza ancorata a uno span **più stretto** della
propria proiezione (4-5 dentro 1-5) soddisfa lo schema e viene respinta con
questo codice; allargando lo span della dipendenza fino a coincidere con quello
della proiezione, l'adapter accetta.

È il rovescio della stessa causa. Un solo campo porta **due semantiche
incompatibili**: identità di clausola (uguale dentro la clausola, distinta fra
clausole) e ancoraggio probatorio (il più stretto intervallo difendibile per
quell'atomo). Nessun prompt può far soddisfare entrambe in modo affidabile, e lo
schema non può controllarne nessuna.

### 3.4 Il caso al validator — `proof_family` + `reference_type`

Ordinale 32, `nel processo di installazione dove mi trovo`. Nel validator
congelato:

- `reference_type` (riga 405): un binding `bound` usa un `ref` il cui
  `value_type` di registro non è in `accepted_reference_types` dello slot.
- `proof_family` (riga 248): una prova usa un `kind` fuori dalla famiglia
  ammessa per quel fatto.

Il gold per questo caso è `actor_workflow_position_open`, cioè
`workflow.position`. Entrambi i codici sono coerenti con una scelta di relazione
o di riferimento sbagliata (per esempio `spatial.*` al posto di
`workflow.position`), **ma quale slot, quale `ref` e quale prova non è
determinabile**: vedi §4.

### 3.5 Precedente: è la seconda volta, e la cura ha peggiorato il male

Questo non è un incidente isolato. Nel live V26.4.1 (handover §2) **20 casi su
34 avevano come unico errore `clause_ids`**, perché «prompt e schema non
dichiaravano la consecutività richiesta dal validator»: stessa identica classe
di guasto, cioè un invariante preteso dal decodificatore e non esprimibile né
dichiarato nello schema.

La risposta architetturale (handover §3) è stata **togliere il campo**:

> V26.5 elimina dall'output LLM: `clause_id`, derivato dall'ordine degli span.

È qui che il guasto cambia grado. Con `clause_id` esplicito l'errore era
*locale e per caso* — un'etichetta numerica sbagliata su una parte dei casi.
Derivandolo dallo span, l'identità di clausola diventa una proprietà **globale e
inferita**, e un solo span ripetuto invalida l'intero grafo. Il guasto passa da
20/34 parziale a **34/34 totale**.

La lezione generalizzabile non è «rimettere `clause_id`»: è che **derivare un
identificatore non elimina il vincolo, lo sposta dove lo schema non arriva**. Se
il vincolo deve restare fatale, deve diventare struttura (§5, S1); se non può
diventare struttura, non deve essere fatale (§5, S2).

## 4. Ciò che NON è diagnosticabile, e perché

`_run_case` persiste `expanded_frame` **solo** quando lo stato è
`evaluated_valid`. Con `valid_cases = 0`, **nessun frame è stato conservato**.
V26.5.6.4 ha inoltre rimosso deliberatamente `raw_frame_sha256` e i byte/digest
di `model_content` (README del candidato, righe 15-18) perché il valutatore
offline non può ricostruirli.

Resta quindi **non ricostruibile senza un nuovo run**:

- quali due ancore si siano sovrapposte, e in quale clausola;
- se la collisione fosse proiezione+proiezione oppure proiezione+clausola non
  supportata (entrambe provate possibili, §3.2);
- se lo status fosse `supported` o `typed_ambiguity` (escluso solo
  `unsupported`);
- quali relazioni, riferimenti e prove siano stati scelti;
- per il caso 32, quale slot e quale prova abbiano prodotto i due codici.

**Ipotesi non provata**, coerente con la distribuzione ma da non spacciare per
risultato: il modello copre l'ambiguità tipizzata emettendo *due proiezioni
concorrenti sulla stessa clausola* invece di usare `typed_ambiguity` con
alternative. Depone a favore il fatto che i due soli casi sfuggiti alla
collisione siano i due con struttura esplicita e disambiguante — l'ordinale 24
(dipendenza reale) e l'ordinale 32 (`nel processo di installazione…`). Non è
verificabile con gli artifact attuali.

Nota metodologica: l'accostamento fra casi opachi e query è stato **verificato
crittograficamente** (34/34 su `segment_count` e `segment_layout_sha256`
ricalcolati dalle query di `metnos_v2656_runtime_controls34.json`), quindi
l'identificazione dei casi citati sopra è provata, non presunta.

## 5. Successore minimo generalizzabile

Nessuna mappatura di superficie, nessuna lista linguistica, nessun cambio di
gold. Il gold attuale (1 proiezione / 3 `typed_ambiguity`) è rappresentato
esattamente dal successore — provato nella sezione C della sonda.

**S1 — l'identità di clausola torna nella struttura del documento.** Gli atomi
smettono di essere un vettore piatto la cui clausola si ricostruisce da uno span
inferito, e diventano clausole che **possiedono** esattamente una `projection` e
le proprie `dependencies`. L'identità di clausola è la posizione nel vettore:
nessuna etichetta numerica viene emessa, la compattezza è preservata, e lo span
retrocede a **pura prova**.

- il codice dei 32 casi diventa **impossibile da rappresentare**: una seconda
  proiezione nella stessa clausola è schema-invalida (provato);
- il codice del caso singolo sparisce per costruzione: la dipendenza appartiene
  alla sua clausola per contenimento, senza alcuna uguaglianza di span;
- gli archi fra clausole continuano a funzionare con `source_ordinal` su un
  appiattimento deterministico (per ogni clausola: dipendenze in ordine, poi
  proiezione).

**S2 — regola invariante, la parte davvero generalizzabile.** Ogni invariante
*fatale* del decodificatore deve essere **o esprimibile nello schema di
risposta, o retrocessa a non fatale**. Un invariante che non è né l'uno né
l'altro è un run a zero latente: è esattamente ciò che è successo qui.

**S3 — diagnosi non censurante.** La condotta non deve fermarsi al primo stadio.
Eseguire gli stadi in modo best-effort e riportare i codici **di tutti**, così
un run misura più di un fallimento per caso.

**S4 — impronta strutturale sul fallimento.** Persistere, sui casi invalidi, un
riassunto limitato e privo di materiale di query: status, numero di atomi,
proiezioni, dipendenze e clausole non supportate, e la forma del multiinsieme
degli span. Costa nulla ed è esattamente ciò che oggi manca per diagnosticare i
32 casi senza rieseguire.

## 6. Test offline prima di un eventuale run

Ordine obbligato; nessuna inferenza finché tutti non sono verdi e la review
indipendente non è chiusa.

1. **T1 rappresentabilità del gold** — ogni attesa canonica congelata deve
   essere rappresentabile e accettata dalla condotta. *(sezione A della sonda,
   verde)*
2. **T2 vuoto del divario schema/decodificatore** — per ogni invariante fatale
   del decodificatore, dimostrare che **nessun** frame schema-valido lo viola.
   Oggi l'insieme è non vuoto ed è provato con tre frame. *(sezione C, verde sul
   successore)*
3. **T3 non censura** — la valutazione riporta l'esito per stadio su tutti i
   casi, non solo il primo fallimento.
4. **T4 non regressione** — l'autotest 85/85 del candidato resta verde.

Sonda: `metnos_v26564_postmortem_encoding_probe.py`, **tutte le asserzioni
verdi**, zero rete e zero modello.

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26564/metnos_v26564_postmortem_encoding_probe.py
```

## 7. Artifact di questa analisi

| File | SHA-256 |
|---|---|
| `metnos_v26564_live_k1_34.json` (archiviato) | `9c15cc295e9465d987c2fd6bcb86ed5d66ed7b28a00a160065b3f7e9de90fc04` |
| `metnos_v26564_live_k1_34_evaluation.json` (archiviato) | `c2602051b1b62daf1b93ec9a0693158c7a341cfe175131c09c43845d8b184522` |

Entrambi byte-identici agli originali in `/tmp`, che restano intatti. I sei
artifact congelati d'autore, i gate e i verifier non sono stati toccati.
