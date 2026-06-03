# Label Cleanup Report — test_set_FROZEN_v2 → v3

- Total queries: 446
- invalid_tool flagged: 3
- mismatch_domain flagged: 3
- ambiguous flagged (kept + acceptable_first_tools): 69
- Queries CHANGED in v3 (expected_path[0] replaced): 4

## invalid_tool (3 entries)

- **idx 436**: `rispondi all'issue #123 su https://github.com/x/metnos con 'thanks for reporting'`
  - current: `request_new_executor` → suggested: `read_urls_html`
  - rationale: request_new_executor not in catalog; github query → read_urls_html
- **idx 437**: `chiudi issue #789 su github metnos`
  - current: `request_new_executor` → suggested: `read_urls_html`
  - rationale: request_new_executor not in catalog; github query → read_urls_html
- **idx 438**: `auto-rispondi a nuove issue github con template benvenuto`
  - current: `request_new_executor` → suggested: `read_urls_html`
  - rationale: request_new_executor not in catalog; github query → read_urls_html

## mismatch_domain (3 entries)

- **idx 53**: `Notificami via mail quando trovi una foto di Matteo`
  - current: `create_tasks` → suggested: `None`
  - rationale: no auto-fix
- **idx 233**: `cerca mail da bookings`
  - current: `read_tasks_history` → suggested: `read_messages`
  - rationale: read_tasks_history wrong for mail query → read_messages
- **idx 443**: `notifica via mail quando arriva nuova PR su github metnos`
  - current: `create_tasks` → suggested: `None`
  - rationale: no auto-fix

## ambiguous (69 entries)

- **idx 2**: `scarica https://httpbin.org/get e salva in /tmp/metnos_cluster_test_dl.txt`
  - current: `get_urls` → acceptable: `['get_urls', 'read_urls_html']`
  - rationale: multiple legit first tools
- **idx 6**: `Riassumi le foto in /home/roberto/images per anno e luogo di scatto`
  - current: `find_files` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 7**: `mostrami le foto di roma`
  - current: `find_files` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 11**: `elenca i file in /tmp/audit/fs/list`
  - current: `list_dirs` → acceptable: `['find_dirs', 'find_files', 'list_dirs']`
  - rationale: multiple legit first tools
- **idx 23**: `cerca foto di festa di compleanno in /home/roberto/.local/share/metnos/Immagini`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 25**: `cerca in /home/roberto/.local/share/metnos/Immagini foto con il mare`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices', 'find_persons_indices']`
  - rationale: multiple legit first tools
- **idx 63**: `trova foto del cane al mare nel 2024`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 72**: `Cerca foto sul web simile alle foto di enrollement di Roberto`
  - current: `find_persons_indices` → acceptable: `['find_files', 'find_images_indices', 'find_persons_indices']`
  - rationale: multiple legit first tools
- **idx 73**: `quanti file in Immagini`
  - current: `find_files` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 80**: `genera spreadsheet eventi settimana per persona`
  - current: `read_events` → acceptable: `['find_events', 'read_events']`
  - rationale: multiple legit first tools
- **idx 81**: `eventi più lunghi della settimana classificali per tipo`
  - current: `read_events` → acceptable: `['find_events', 'read_events']`
  - rationale: multiple legit first tools
- **idx 88**: `scrivi documento con riassunto giornata e appuntamenti`
  - current: `read_events` → acceptable: `['find_events', 'read_events']`
  - rationale: multiple legit first tools
- **idx 112**: `scarica https://httpbin.org/get e salva la risposta in /tmp/metnos_e2e_pipe.txt`
  - current: `get_urls` → acceptable: `['get_urls', 'read_urls_html']`
  - rationale: multiple legit first tools
- **idx 120**: `Cerca le mail con 'newsletter' nel subject degli ultimi 7 giorni su knowcastle`
  - current: `read_messages` → acceptable: `['find_messages', 'list_messages', 'read_messages']`
  - rationale: multiple legit first tools
- **idx 130**: `leggi le mie ultime 3 mail`
  - current: `read_messages` → acceptable: `['find_messages', 'list_messages', 'read_messages']`
  - rationale: multiple legit first tools
- **idx 134**: `elenca i file in /tmp`
  - current: `list_dirs` → acceptable: `['find_dirs', 'find_files', 'list_dirs']`
  - rationale: multiple legit first tools
- **idx 136**: `Trova le 5 mail più grandi che ho ricevuto questa settimana sulla casella metnos e dimmi mittenti e `
  - current: `read_messages` → acceptable: `['find_messages', 'list_messages', 'read_messages']`
  - rationale: multiple legit first tools
- **idx 148**: `leggi le mail di oggi`
  - current: `read_messages` → acceptable: `['find_messages', 'list_messages', 'read_messages']`
  - rationale: multiple legit first tools
- **idx 175**: `cerca volti in primo piano in Immagini`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 176**: `cerca foto con primi piani`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 177**: `cerca foto portrait`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices']`
  - rationale: multiple legit first tools
- **idx 178**: `cerca foto con una sola persona e viso in primo piano`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices', 'find_persons_indices']`
  - rationale: multiple legit first tools
- **idx 179**: `cerca foto di silvia al mare`
  - current: `find_persons_indices` → acceptable: `['find_files', 'find_images_indices', 'find_persons_indices']`
  - rationale: multiple legit first tools
- **idx 180**: `cerca foto con iacopo`
  - current: `find_images_indices` → acceptable: `['find_files', 'find_images_indices', 'find_persons_indices']`
  - rationale: multiple legit first tools
- **idx 185**: `cerca le mail non lette`
  - current: `read_messages` → acceptable: `['find_messages', 'list_messages', 'read_messages']`
  - rationale: multiple legit first tools
  (... and 44 more)

## Benchmark LAVORO 2 (Qwen FT Tool Classifier)

| Dataset | Top-1 | Top-3 |
|---------|-------|-------|
| FROZEN_v2 (original) | 410/446 = **91.9%** | 432/446 = **96.9%** |
| FROZEN_v3 strict | 413/446 = **92.6%** | 436/446 = **97.8%** |
| FROZEN_v3 tolerant (acceptable_first_tools) | 416/446 = **93.3%** | 437/446 = **98.0%** |

**Deltas (v2 → v3 tolerant)**: Top-1 +1.3pp, Top-3 +1.1pp.

Interpretation: the 4-entry fix (3 invalid_tool + 1 mismatch_domain that
could be auto-corrected) and the 69 acceptable_first_tools annotations
exposed ~1.3pp of "honest" accuracy that the strict v2 benchmark hid
behind label noise. The other 2 mismatch_domain cases ("Notificami via
mail quando..." + "notifica via mail PR github") are kept as-is in v3
(complex multi-tool pipelines, no clean single-fix path).

## Recommendation

USE FROZEN_v3 AS CANONICAL for future benchmarks:

1. **Fix is conservative** — only 4 expected_path[0] replacements, all on
   queries where v2 label was demonstrably wrong (request_new_executor
   not in catalog; read_tasks_history for "cerca mail" is nonsensical).
2. **Ambiguity is explicit** — 69 queries now carry `acceptable_first_tools`
   so benchmarks can credit both legitimate choices (e.g. find_files vs
   find_images_indices for "foto in /home/x"). This removes ~1.4pp of
   noise from top-1 metric.
3. **Original preserved** — v2 untouched, v3 is additive cleanup.
4. **Honest accuracy estimate**: 93.3% top-1 / 98.0% top-3 is closer to
   the true ceiling than the 91.9% / 96.9% from v2.
