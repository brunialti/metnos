---
id: 0075
title: Prefilter — `_OBJECT_PRIMARY_TOOLS` esteso a tutti gli object canonici (anti synt-spurio)
date: 2026-05-04
status: accepted
area: runtime, prefilter
related:
  - 0045  # closed naming vocabulary
  - 0058  # intent extractor LLM-based
  - 0063  # universal helpers exception in prefilter
modifies:
  - 0063
---

## Context

`request_new_executor` (builtin verb-unique che lancia la cascata
reattiva del synt) viene chiamato dal PLANNER come step 1 ANCHE quando
l'executor giusto esiste gia' nel catalog. Esempi reali (turn log
4/5/2026 e precedenti):

- "Quante istanze di claudio sono running" → step1=request_new_executor,
  step2=get_processes ✓ esiste
- "Farmacia piu vicina" → step1=get_location, step2=request_new_executor,
  step3=find_places ✓ esiste
- "ridimensiona X a 200x200" → step1=request_new_executor, ...,
  stepN=change_images ✓ esiste
- "scrivi una mail" → step1=request_new_executor, step2=send_messages
  ✓ esiste

Conseguenze user-visible:
1. **Latenza**: `request_new_executor` invoca synt 5 stadi wise tier
   (Gemma 4 26B think=true) — 30-60 s di pura attesa per nulla.
2. **Synth ridondanti**: i nuovi executor cosi' generati portano lo
   stesso nome di handcrafted; il loader li rifiuta correttamente
   (§10.6.2) ma il file synt resta sul disco e si accumula. Esempio
   trovato: `~/.local/share/metnos/executors/get_processes/` (4/5/2026,
   spostato in `/tmp/metnos_synth_collision_get_processes_*`).

**Causa root** (analisi 4/5/2026): `_OBJECT_PRIMARY_TOOLS` in
`prefilter.py` conteneva solo `"places": ("find_places",)`. Per gli
altri object (processes, messages, files, dirs, urls, images, ...) il
prefilter non iniettava il tool canonico nel pool dei top-K se non
emergeva dal punteggio di affinity, lasciando il PLANNER a vedere solo
universal helpers + `request_new_executor` builtin → cascata di synt
spuri.

ADR 0063 introduceva il PRECURSOR universale (read/find/list/get
iniettato per ogni verbo NON producer); 0075 estende lo stesso principio
al verso opposto: il primary tool per object e' iniettato nel pool
quando l'object e' detectato, indipendentemente dal verbo.

## Decision

Esteso `_OBJECT_PRIMARY_TOOLS` a tutti gli object di prima classe:

```python
_OBJECT_PRIMARY_TOOLS = {
    "places":     ("find_places",),
    "processes":  ("get_processes",),
    "messages":   ("read_messages",),
    "files":      ("find_files", "read_files"),
    "dirs":       ("list_dirs", "find_dirs"),
    "urls":       ("get_urls",),
    "events":     ("read_messages",),       # eventi calendar via mail
    "contacts":   ("read_messages",),       # contatti via mail
    "images":     ("change_images", "find_files"),
    "packages":   ("get_packages",),
    "numbers":    (),                        # niente primary, lascia al ranker
    "texts":      ("read_files", "filter_texts_lines"),
    "signatures": ("get_signatures",),
}
```

Esteso `_OBJECT_HINTS["processes"]` con sinonimi user-facing:
`istanza, istanze, instance, instances, running, esecuzione, daemon,
demone, kill, uccidi, termina, stop`. Senza questi sinonimi
`detect_canonical_object` non identificava "processes" su query come
"Quante istanze di claudio sono running" (ne' "processo" ne' "process"
era nel testo).

Pulizia immediata: lo `~/.local/share/metnos/executors/get_processes/`
spurio e' stato spostato fuori dal catalog dir (in `/tmp/`, non
eliminato per investigation).

## Consequences

- Test post-fix su 8 query problematiche (verifica live 4/5/2026):
  `get_processes` PRIMO su "Quante istanze...", "mostra processi", "killa
  il processo"; `find_places` PRIMO su "Farmacia piu vicina";
  `send_messages` nel topK su "scrivi una mail".
- Bench su 300 query reali post-fix: Recall@5 token = 92.3% invariato
  rispetto al pre-fix (la maggioranza delle query del dataset era gia'
  correttamente servita; il fix tocca i casi specifici dei processes/
  contacts/etc che erano nel 7.7% mancante).
- Open: pulizia automatica dei synth-spuri rifiutati. Oggi il loader li
  marca `rejected` ma non rimuove i file. Da fare in iterazione futura
  un GC opzionale che elimini i synth con name in collisione persistente.
- Open: il prompt PLANNER (`agent_runtime.py:191-196`) e'
  PRESCRITTIVO ("DEVI invocare `request_new_executor` quando manca un
  tool"). Aggiungere un guard testuale "NON DEVI chiamarlo se
  un executor con nome `expected_name` e' gia' nel pool" e' un
  miglioramento ortogonale, da fare in iterazione successiva. La fix di
  questa ADR e' SUFFICIENTE perche' rimuove la causa upstream (assenza
  del tool nel pool); il guard pianter sarebbe una rete di sicurezza.

## References

- `runtime/prefilter.py` (`_OBJECT_PRIMARY_TOOLS`, `_OBJECT_HINTS`).
- `runtime/agent_runtime.py:191-196` (PLANNER prompt — guard pianter
  pendente).
- `runtime/synth_request.py:198-235` (`SYNTH_REQUEST_TOOL` description).
- `runtime/loader.py:288-295` (collision check ok, GC pendente).
- ADR 0063 (universal helpers exception in prefilter — complementare).
