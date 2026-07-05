# Report AREA 1 · CP1·M0 — idempotenza guard + contratto d'ordine (mandato Fable, 5/7/2026)

**Riferimento**: `internal/fable_mandate_2026-07-05.md` (Area 1, CP1) · ADR 0177 T4+T3 · Branch `session/detection-lexicon-i18n` (non pushato) · Prod live.

## Esito: CP1 CONSOLIDATO ✓ — e il T4 ha già ripagato il mandato

### T4 — Idempotenza guard su cache-hit (S3: da ⚠ a ✓ misurato)
- **Sweep su corpus REALE** (L0 fastpaths + L1 autopaths + piani flagship, 33 piani): **33/33 idempotenti**, full-chain e per-guard.
- **Test durevole** `runtime/tests/test_guard_pipeline_contract.py`: corpus incorporato rappresentativo (L0-hit pulito, piano avvelenato, flagship KAKEBO pulito+sporco, mail→foglio, mono-azione, delete-mail) — double-apply full-chain + per-guard (diagnostica del violatore).
- **Il T4 ha SCOVATO 2 non-idempotenze reali** (il rischio silenzioso S3 ERA reale):
  1. `recursive=True` spurio al 2° giro — falso-positivo prefisso-4 «delle»~«della» in `args_extractor._bool_flag_triggered` → parole-funzione IT aggiunte al noise-set.
  2. la clausola delete-mail (riscritta §5 in `move_messages(Trash)`) veniva **ri-appesa** da enforce al giro dopo, e `derive_tool_name` la risolveva a **`delete_messages_github`** (fallback suffissi che pescava una variante PROVIDER senza marker) → coverage delete-via-move in `_dropped_required_verbs` + esclusione varianti provider dal fallback.

### T3 — Contratto d'ordine della pipeline
`dispatch.GUARD_PIPELINE` dichiarativa (14 guard: nome, gate v3, fn) con i vincoli di posizione documentati accanto alla tupla; `_apply_deterministic_structure_guards` la itera (comportamento identico). Il test di sequenza blocca nomi+ordine: chi tocca la pipeline aggiorna il contratto consapevolmente.

## Caccia live intrecciata (bug-burst dei turni di Roberto, stessa area)
Durante CP1 Roberto ha testato «elenca i file della cartella C:\Windows\…\etc sul PC-ROBERTO e metti i path in uno spreadsheet» → catena di 5 turni-bug (8b675402 → 2cd8862a → e130c549 → 68f28b01 → a9ec3b06), chiusa a **errore=0 live** (turn `b5a53690`: `list_dirs` SUL device, xlsx 7 righe tutte C:\…). Difetti misurati e fixati (dettaglio nei commit):
1. **Fusione path** (`/opt/metnos/C:\Windows\…`): assoluto Windows su host POSIX fuso col CWD da `Path.resolve()` → forma estranea all'host mai risolta (§2.4).
2. **Extractor cieco ai path Windows/UNC** → `_PATH_RE` esteso (spazi nei segmenti intermedi, finale no).
3. **Default appreso AVVELENATO** (`base_path=/opt/metnos/executors/read_files`, 46 usi auto-rinforzanti) → filtro install-root su cattura E iniezione (`args_resolver`) + purga DB.
4. **Guard fantasma** `_overwrite_phantom_install_args` (ripara anche i piani cachati on-hit).
5. **`_degenerate_find_to_list`** (§2.2): intento LIST + find senza selettore = enumerazione contenitore → list_dirs (device-eligible).
6. **Align demoliva il piano corretto cachato** (asimmetria files↔dirs) + **fratello scelto a ordine-HASH del set** (find_files vs get_files fra due restart, violazione §11) → equivalenza `_fs_equivalent` (4 punti) + ordine PRODUCER fisso in `derive_tool_name`.
7. **Sink con path estratto come output** (file-mostro senza estensione) → fill non riempie path sui writer + suffisso `.xlsx` §2.4 nel backend.
8. **Simbionte firma**: il bundle di `list_dirs` co-loca `path_alias.py` via symlink → l'edit di path_alias INVALIDAVA la firma e il loader scartava list_dirs in silenzio (la trappola «tool sparito») → re-sign, meccanismo annotato nell'indice.

## Scoperte per i CP successivi (⚠ da tenere)
- ⚠ **Alternative-cache del proposer Metis** («retry serving cached alternative without LLM call»): un TERZO layer di cache che serve framework fra turni vicini — da includere nello scope cache-discipline (CP5/M4, e nel catalog-sig di negative-path-cache).
- ⚠ `routing_pool` OVERSIZED 33 candidati (target 12) ricorrente su intent (list, files) — prefilter poco discriminante su questa classe; materiale per CP3 (consolidamento guard/pool).
- ⚠ Il flaky pre-esistente `test_prefilter_get_files_routing` (ordering) resta; in isolamento passa.

## Prove del cancello §A
- Test: **T3+T4 (16) + 12 regressioni fs-listing** nuovi; suite aree toccate **875 passed** (unico rosso = flaky pre-esistente, verde in isolamento); gate §2.8 4/4.
- Sweep S3: 33/33. Turni reali: `b5a53690` (errore=0 live sul device) + i 5 turni diagnostici.
- Commit modulari: `e278570` (path+re-sign), `16a4ef1` (args), `22da9a5` (engine+T3), `2f038b9` (.xlsx), `a8f7ef0` (test), `d8291e4` (ADR+indice). Prod riavviata e live.
- ADR 0177: S3 ✓ misurato/bloccato, S7 chiuso (legacy rimosso 4/7), S2 confermato (inventario 14 guard), M0 marcato FATTO. Indice anti-regressione +17 righe.

## Prossimo checkpoint (dal mandato, dopo ok Roberto)
**CP2 · M2 (T5) — Finalizer unico**: una sola fonte del messaggio finale (zero-result→template→synth→describe→policy), de-duplicato, i18n-garantito. Sana S5 (5 percorsi divergenti, misurati ✓ nell'ADR).
