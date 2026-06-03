"""Universal parser prompt §7.3 — fill-in-the-blank JSON template.

LLM riempie una struttura JSON vuota invece di generare da zero.
Vocab IT/EN derivato da runtime/vocab.py::ACTION_MAPPING (universale §7.3,
non hardcoded query-specific).
"""

# Vocabolario verb/obj synonyms — UNIVERSAL §7.3:
# - Verb: ACTION_MAPPING (vocab.py, single source of truth)
# - Object: DERIVED from manifest.toml affinity (catalog-driven, no hardcoded mapping)
def _build_vocab_block():
    try:
        import sys
        sys.path.insert(0, '/opt/metnos')
        sys.path.insert(0, '/opt/metnos/e2e/simulator')
        from runtime.vocab import ACTION_MAPPING
        from vocab_synonyms_derived import load_cached
        # Verb synonyms
        verb_lines = []
        for verb, langs in sorted(ACTION_MAPPING.items()):
            it_syn = ", ".join(langs.get('it', [])[:6])
            en_syn = ", ".join(langs.get('en', [])[:3])
            verb_lines.append(f"  {verb}: IT[{it_syn}] EN[{en_syn}]")
        # Object synonyms from manifest affinity (catalog-derived)
        derived = load_cached()
        obj_synonyms = derived.get("objects", {})
        from collections import defaultdict
        obj_to_syn = defaultdict(set)
        for syn, canon in obj_synonyms.items():
            # Skip noisy multi-word entries
            if " " in syn and len(syn) > 25: continue
            obj_to_syn[canon].add(syn)
        obj_lines = []
        for canon, syns in sorted(obj_to_syn.items()):
            top = sorted(syns, key=len)[:12]
            obj_lines.append(f"  {canon}: [{', '.join(top)}]")
        return ("VERBI canonical → sinonimi IT/EN (da vocab):\n"
                 + "\n".join(verb_lines)
                 + "\n\nOGGETTI canonical → sinonimi (da manifest affinity):\n"
                 + "\n".join(obj_lines))
    except Exception as e:
        return ""


_VOCAB_BLOCK = _build_vocab_block()


SYSTEM_PROMPT = """Riempi i campi vuoti del JSON template seguente analizzando la query utente.

VERBO CANONICO → SINONIMI (IT/EN): scegli intent_verb dalla mappa.
""" + _VOCAB_BLOCK + """

Esempi disambiguazione: "rinomina"→move (sposta+nuovo-nome), "trascrivi"→read
(read di audio→testo), "calcola"→compute, "scarica"→get (atomico URL),
"estrai da pdf/html"→read (content), "estrai da archivio"→extract,
"estrai da testo"→filter (righe). "indicizza"→create (build_index).
""" + """

JSON TEMPLATE da riempire (NON cambiare la struttura, riempi solo i valori):
{
  "intent_verb": "<scegli da: read|write|move|delete|create|find|list|filter|sort|group|classify|get|set|send|describe|render|extract|compress|compute|compare|change|order|share>",
  "intent_object": "<scegli da: files|dirs|packages|messages|events|contacts|places|processes|urls|numbers|images|signatures|texts|proposals|persons|tasks|inputs|credentials|entries>",
  "inputs": [
    {"name": "<arg_name>", "semantic_type": "<type>", "value": "<concrete_value_from_query>"}
  ],
  "target": {
    "semantic_type": "<output_type>",
    "shape": "<list|scalar|dict>"
  },
  "constraints": [
    {"kind": "<filter|sort|aggregate|transform>", "key": "<constraint_key>", "value": "<value_or_null>"}
  ]
}

REGOLE DI RIEMPIMENTO (universali, §7.3):

A) intent_verb:
   - Inquiry/info ("che", "quali", "elenca", "dimmi", "mostra") → read|get|find|list
   - Quantitative ("quanti", "conta") → compute
   - Mutating action esplicito → write|move|delete|create|set|send|share

B) intent_object: entità dominio principale evocata dalla query
   (file→files, cartella→dirs, mail→messages, appuntamento→events,
    foto→images, persona/me/io→persons, posto/dove→places, url→urls,
    task/promemoria→tasks, processo→processes, ora/data/numero→numbers).

   CONSISTENZA con input semantic_type: l'object DEVE coincidere col
   tipo del valore concreto in input (priorità su parole-chiave query):
   - input url → object=urls (mai files)
   - input file_path → object=files
   - input dir_path → object=dirs
   - input image_path → object=images
   - input email_address → object=messages (outbound) o contacts
   - input person_name → object=persons

C) inputs: SOLO valori CONCRETI presenti nella query.
   semantic_type chiuso: file_path|dir_path|image_path|pdf_path|url|email_address|
   phone|person_name|slug|account_name|time_window|iso_timestamp|glob_pattern|
   count|scalar_metric|size_bytes|free_text|json_object|bool|name
   - "io/me/mio" → value="${RUNTIME:actor}" con type=person_name

D) target.semantic_type chiuso:
   scalar_metric|free_text|iso_timestamp|file_entry[]|image_entry[]|
   message_entry[]|event_entry[]|person_entry[]|url_entry[]|dir_entry[]|
   process_entry[]|task_entry[]
   - Quantitative query → scalar_metric scalar
   - Mutating action → scalar_metric scalar
   - List/enumeration → <object>_entry[] list
   - Read content → free_text scalar

E) constraints: vincoli che il path DEVE preservare:
   - Filtri tipizzati (per name/time/pattern/property) → kind=filter, key=<property>
   - Aggregazione → kind=aggregate, key=count|sum|avg|max|min
   - Ordinamento → kind=sort, key=<field>
   - Trasformazione → kind=transform, key=<op>

REGOLE STRUTTURALI:
- inputs: array vuoto [] se nessun valore concreto.
- constraints: array vuoto [] se nessun vincolo (es. "che ora e" non ha constraints).
- ${RUNTIME:actor} NON aggiungere come constraint — solo come input value.

OUTPUT: SOLO il JSON riempito, niente prosa, niente think blocks."""
