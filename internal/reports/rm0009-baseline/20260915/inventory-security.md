# RM-0009 — Inventario sicurezza D-G0.4/D-G0.5

Data: 15 settembre 2026. Tipo: inventory/decision candidate.
Autore: agente incaricato `rm0009_inventory_security`; revisore: coordinatore, più revisore sicurezza indipendente prima di G0.8.
Stato: **inventario consegnato con limiti; baseline non congelata; nessuna implementazione o attestazione FS autorizzata da questo documento**.

Vincolo aggiornato di Roberto, comunicato dal coordinatore durante la ricognizione: attendere che l'agente RM-0008 termini e committi. Sono consentite analisi e preparazione delle verifiche; nessun congelamento o modifica nelle aree RM-0008. Tutti i percorsi, le righe e i digest sotto dovranno essere ricontrollati sul futuro commit prima di assegnare implementazione.

## Esito

- 33 manifest legacy nel repository principale e 33 nella release selezionata; altri 66 nelle due copie di lavoro annidate. Le semantiche non sono tutte banali: owner/config, dialoghi, provider simulati, symlink, journal undo, immagini e fogli di calcolo.
- La release 51 ha già sostituito il richiamo host della sintesi con prove Birth; il repository principale conserva `synth_request._validate_birth_tests → test_runner.py`. Il matcher installato importa ancora `test_runner.check_expect`: rimuovere il file ora romperebbe il runner funzionale.
- Il solo hook in `loader.invoke_verb_unique` non coprirebbe i builtin ordinari. I durevoli executor passano già da `agent_runtime.invoke_executor`: aggiungere l'osservazione anche al ponte senza distinguere il tipo duplicherebbe il conteggio.
- G0.4: la precondizione “un solo principal locale fidato” non è dimostrata e gli account locali multipli sono confermati fuori dal sandbox. Raccomandato socket Unix diretto con autenticazione reciproca delle credenziali peer; nessuna chiave master trasmessa.
- I file di prodotto non sono stati modificati; nessun servizio, DB personale, test, runner legacy, commit o pubblicazione eseguiti. Il solo file scritto è questo rapporto.

## Fonti e baseline

Letti integralmente `CLAUDE.md`, `CLAUDE.mutabile.md`, `internal/AGENTS.md`; sezioni normative §§1–6, appendice A, D ed E.2 della roadmap RM-0009. La regola D.1 e l'incarico limitano questo lavoro all'inventario; non si usano le schede A.4 come incarichi operativi.

HEAD repository: `037840f455c005899dcaf089cc2e52dd614f07de`.
Albero sporco: manifest.lang_state/firme executor già modificati, diversi installer/script ritirati, CLAUDE.mutabile e le due roadmap modificate. Proprietà di queste modifiche da assegnare in G0.1; non sono nostre. Le firme 0600 e `install/data` non risultano leggibili nel sandbox: le differenze Git non dimostrano alterazioni semantiche e i mode/UID tradotti del sandbox non sono la proprietà reale dell'host.

Release candidata individuata leggendo **solo metadati pubblici** del required-head:
`/var/lib/metnos/executor-birth/releases-v1/00000000000000000051`.
`head_id=sha256:5fce7ebf4bf785b5613c93400b7a7176e630f091c6d3fb1b38bdc8b8a8b35d15`;
`closed_build_id=sha256:7048815b433d82361c216a1a081aa70712e378728b58572db0c9b7f883402544`.
È la selezione indicata dai metadati, **non una verifica crittografica né una prova del codice nel processo vivo**. Il coordinatore ha separatamente verificato HTTP e Telegram attivi come account `metnos`; la ricognizione non certifica FS-A/FS-B/RM-0008/F5.

### Due scansioni e limiti

| Albero | File di testo ammessi | SHA-256 input, passata 1 | SHA-256 input, passata 2 |
|---|---:|---|---|
| Repository con copie annidate | 7485 | `824958ff146605407d3419547f61a34cf8f134a04a563f4989830b7273531b6f` | `554f96c33f45eb99f405d714afba36dd85d2db2e3b15609ff97e75ff42c4038d` |
| Release 51 | 1190 | `0b554dd30612936503ebda1cb85f0eaf4ee8ec99e8b2f9f6b81ada234b5535ad` | `0b554dd30612936503ebda1cb85f0eaf4ee8ec99e8b2f9f6b81ada234b5535ad` |

Output estratti `matches`, `legacy` e `capabilities` identici nelle due passate; HEAD identico. La release è byte-stabile nel perimetro scansionato. Il digest generale repository è cambiato: deriva negli input, pur senza differenze nelle corrispondenze estratte. La prima scansione non ha conservato il vettore completo dei digest per file, quindi **il singolo file cambiato non è identificabile da queste due uscite**. Non dichiarare soddisfatta la baseline immutabile globale: G0.1/G0.6 devono congelarla e ripetere il confronto con elenco digest per file.

Metodo: cammino ordinato senza seguire symlink; SHA-256 per file e poi del JSON compatto ordinato `[[relative_path, sha256],...]`. Estensioni: `.py,.rs,.toml,.md,.j2,.sh,.gbnf,.html`; massimo 4 MB/file. Esclusi directory `.git,.venv,node_modules,__pycache__,.mypy_cache,.pytest_cache,target,dist,models,data,workspace`, la cartella personale di documenti e i rapporti in corso `internal/reports/rm0009-baseline`. Non si leggono DB, chiavi, token, file ambiente reali o log. Nessun errore nei file selezionati; ciò **non** significa accessibilità dei percorsi esclusi.

Limiti aggiuntivi espliciti: YAML, file `.candidate`, JSON di fixture e script con estensioni non ammesse non entrano nel digest. YAML e template noti sono stati cercati separatamente e sono elencati sotto, ma vanno aggiunti alla scansione congelata G0.6. Gli store e le skill utente installate fuori dalla release non sono stati aperti. Il censimento non può attestare completezza sui consumer importati privati. Le regex producono candidati, non un'analisi dinamica di tutti gli import.

## FS-A: runner e riferimenti

Repository principale:
`runtime/synth_request.py:60/72` lancia direttamente Python con `runtime/test_runner.py` e timeout 60s;
`runtime/test_runner.py:42,62,104,438` contiene shell di setup/teardown, executor e riferimenti;
`runtime/testing/runner.py:_run_birth` e `tests/tools/run_executor_manifests.py:24` conservano il collegamento legacy.
I test `test_birth_tests_all_green.py` e `test_executor_parallel_equivalence.py` non vanno eseguiti come prova “prima”.

Release 51:
`runtime/synth_request.py:63` usa `SynthTestData/validate_synth_tests`;
`runtime/executor_birth_functional.py:55` ammette soltanto `name,input,expect`;
`runtime/executor_birth_functional.py:188` chiama `run_birth_phase`, ma a riga 22 importa `check_expect` da `test_runner`.
`runtime/executor_birth_property_runner.py:403,409` chiama il runner Birth.
Questo non converte automaticamente i 33 manifest legacy già distribuiti.

Riferimenti main (codice, test e documenti, con righe della baseline; quelli storici vanno classificati, non cancellati indiscriminatamente):

- `EXECUTOR_STANDARD.md`: 411.
- `decisions/0029-sqlite-test-framework-cluster-scope.md`: 15, 36, 56.
- `decisions/0061-test-runner-vector-matchers.md`: 15, 37.
- `decisions/0079-planner-anti-collision-guard.md`: 132.
- `decisions/anti-regression-index.md`: 366.
- `executors/set_credentials/manifest.toml`: 130.
- `internal/design/audit_metnos_multidominio_21_7.md`: 270, 389.
- `internal/reports/rm0008-adversarial-evidence-20260825.md`: 53, 54.
- `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`: 797, 1051.
- `internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md`: 155, 782, 789, 1616, 1617, 1619, 1623, 1624, 1632, 1681, 1791, 1829, 2028, 2108, 2111, 2233.
- `runtime/executor_birth_property_runner.py`: 23, 185.
- `runtime/executor_birth_runner.py`: 4, 427, 665.
- `runtime/synth_request.py`: 61, 72, 76, 78, 91.
- `runtime/test_runner.py`: 5, 438.
- `runtime/testing/populate_cases.py`: 649, 651, 652, 657, 659, 663, 664, 670, 671, 676, 677, 682, 683, 688, 689, 694, 695, 700, 701, 706, 707, 2723, 2724.
- `runtime/testing/runner.py`: 16, 134.
- `runtime/testing/seed_modules.py`: 47, 90, 97, 130, 131, 132, 133, 134.
- `tests/README.md`: 23.
- `tests/portable/test_executor_birth_runner_linux_real.py`: 48, 87.
- `tests/portable/test_executor_birth_runner_windows.py`: 61.
- `tests/portable/test_executor_birth_runner_windows_appcontainer.py`: 63.
- `tests/runtime/executors/test_birth_tests_all_green.py`: 6, 30.
- `tests/runtime/executors/test_executor_birth_property_runner.py`: 181, 205, 235.
- `tests/runtime/executors/test_executor_parallel_equivalence.py`: 5, 11, 15, 23, 26, 35, 41, 55, 56, 62, 64, 68.
- `tests/runtime/infra/test_executor_birth_runner.py`: 108, 130, 228, 256, 271, 277, 291, 299, 337, 346, 356.
- `tests/runtime/remote/test_invocation_scope.py`: 14.
- `tests/runtime/skills/test_builtin_executor_contracts.py`: 229.
- `tests/runtime/skills/test_executor_manifests_gate.py`: 11.
- `tests/tools/run_executor_manifests.py`: 2, 24.

Produttori/grammatica fuori dalla sola ricerca “test_runner”:
`runtime/executor_birth_identity.py:275–277`;
`runtime/synth_request.py:254–257`;
`runtime/skill_codegen.py:593–632`;
`runtime/templates/manifest.toml.j2`;
`runtime/prompts/it/synt_tests.j2`, `runtime/prompts/en/synt_tests.j2`;
`runtime/prompts/it/synt_tests.yaml`, `runtime/prompts/en/synt_tests.yaml`;
`runtime/prompts/it/_pending/synt_tests.j2.candidate`, `runtime/prompts/en/_pending/synt_tests.j2.candidate`.
`runtime/testing/registry.py` e `runtime/testing/populate_cases.py` hanno anche setup_code/teardown_code per i test del registro: separare test di sviluppo fidati e prove di candidato; non migrarli alla cieca.

### Espansione candidata D-FS-A.3

Tutte le righe sotto hanno owner proposto “esecutore sicurezza assegnato dal coordinatore”, livello alto sistemi/sicurezza, revisore diverso dall'autore; stato **unassigned/not_authorized_here**. Dipendenza minima FS-A.2 e G0.6; equivalenza delle asserzioni nel runner Birth prima/dopo; fixture dichiarative effimere e nessuna shell libera sull'host. Gli ordinali identificano esattamente i casi `[[tests]]`.

{"repo":{"manifests":33,"cases":113,"setup":91,"teardown":90,"env":35},"release":{"manifests":33,"cases":113,"setup":91,"teardown":90,"env":35}}

| ID candidato | Manifest di proprietà proposto | Casi e campi legacy | Semantica da conservare |
|---|---|---|---|
| D-FS-A.3.001 | `executors/compress_files/manifest.toml` | 1:setup/teardown; 5:setup/teardown; 6:setup/teardown; 7:setup/teardown; 8:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.002 | `executors/compute_files_loc/manifest.toml` | 1:env/setup/teardown; 2:setup/teardown; 3:setup/teardown; 4:setup/teardown; 5:setup/teardown; 6:setup/teardown | albero sorgenti, contenuti binari e budget lavoratori |
| D-FS-A.3.003 | `executors/consult_frontier/manifest.toml` | 4:setup | preparazione nulla (`true`) |
| D-FS-A.3.004 | `executors/create_dirs/manifest.toml` | 1:setup/teardown; 2:setup/teardown; 4:setup/teardown; 5:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.005 | `executors/create_files_spreadsheet/manifest.toml` | 1:setup/teardown; 2:setup/teardown; 3:setup/teardown; 5:setup/teardown; 6:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.006 | `executors/create_images_indices/manifest.toml` | 3:env/setup/teardown | albero indice e budget lavoratori |
| D-FS-A.3.007 | `executors/delete_dirs/manifest.toml` | 1:setup/teardown; 3:setup/teardown; 4:setup/teardown; 5:setup/teardown; 7:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.008 | `executors/delete_files/manifest.toml` | 1:setup/teardown; 3:setup/teardown; 6:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.009 | `executors/find_contacts/manifest.toml` | 1:env; 2:env; 3:env; 4:env | risposta contatti sintetica tramite METNOS_SUBPROCESS_FAKE |
| D-FS-A.3.010 | `executors/find_dirs/manifest.toml` | 1:env/setup/teardown; 2:setup/teardown; 3:setup/teardown; 4:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.011 | `executors/find_files/manifest.toml` | 1:env/setup/teardown; 2:setup/teardown; 3:setup/teardown; 4:setup/teardown; 5:setup/teardown; 6:setup/teardown; 7:setup/teardown; 10:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.012 | `executors/find_files_hash/manifest.toml` | 1:env/setup/teardown; 2:setup/teardown; 3:setup/teardown; 4:setup/teardown | file uguali/diversi e cache disattivata |
| D-FS-A.3.013 | `executors/find_images_google_photos/manifest.toml` | 1:env; 2:env | home skill vuota / indisponibilità provider |
| D-FS-A.3.014 | `executors/get_approval/manifest.toml` | 4:env/setup/teardown | owner fittizio e archivio approvazioni isolato |
| D-FS-A.3.015 | `executors/get_files/manifest.toml` | 1:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.016 | `executors/get_images_google_photos/manifest.toml` | 2:env | home skill vuota / indisponibilità provider |
| D-FS-A.3.017 | `executors/get_inputs/manifest.toml` | 1:env; 2:env; 3:env; 4:env; 5:env/setup/teardown; 6:env/setup/teardown | owner fittizio e archivio dialoghi isolato |
| D-FS-A.3.018 | `executors/get_location/manifest.toml` | 1:env/setup/teardown; 3:env/setup/teardown; 4:env/setup/teardown | radici utente temporanee, configurazione posizione e disabilitazione fonti rete |
| D-FS-A.3.019 | `executors/get_places/manifest.toml` | 7:env | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.020 | `executors/get_proposals/manifest.toml` | 1:env/setup/teardown | archivi data/state isolati |
| D-FS-A.3.021 | `executors/list_dirs/manifest.toml` | 1:env/setup/teardown; 2:setup/teardown; 4:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.022 | `executors/move_files/manifest.toml` | 1:setup/teardown; 3:setup/teardown; 4:setup/teardown; 5:setup/teardown; 6:setup/teardown; 8:setup/teardown; 11:setup/teardown; 12:setup/teardown; 13:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.023 | `executors/read_contacts/manifest.toml` | 1:env; 2:env; 3:env; 4:env | risposta contatti sintetica tramite METNOS_SUBPROCESS_FAKE |
| D-FS-A.3.024 | `executors/read_files/manifest.toml` | 1:setup/teardown; 3:setup/teardown; 4:setup/teardown; 6:setup/teardown; 7:setup/teardown; 8:setup/teardown; 9:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.025 | `executors/read_files_csv/manifest.toml` | 1:setup/teardown; 2:setup/teardown; 3:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.026 | `executors/read_files_doc/manifest.toml` | 4:env | home skill vuota / indisponibilità provider |
| D-FS-A.3.027 | `executors/read_files_ocr/manifest.toml` | 4:setup/teardown; 5:setup/teardown | file immagine/PDF sintetici e casi invalidi |
| D-FS-A.3.028 | `executors/read_files_xlsx/manifest.toml` | 1:setup/teardown; 2:setup/teardown | workbook sintetici con Python |
| D-FS-A.3.029 | `executors/read_messages/manifest.toml` | 1:env; 2:env; 3:env | home/config vuoti per assenza credenziali |
| D-FS-A.3.030 | `executors/undo_last_turn/manifest.toml` | 1:setup/teardown; 2:setup/teardown; 3:setup/teardown | journal sintetico e file per verifica inverso |
| D-FS-A.3.031 | `executors/write_files/manifest.toml` | 1:setup/teardown; 3:setup/teardown; 4:setup/teardown; 5:setup/teardown; 6:setup/teardown; 7:setup/teardown; 8:setup/teardown; 9:setup/teardown | alberi/file sintetici, assenza/presenza, collisioni e pulizia confinata |
| D-FS-A.3.032 | `executors/write_files_doc/manifest.toml` | 5:env | home skill vuota / indisponibilità provider |
| D-FS-A.3.033 | `executors/write_images_google_photos/manifest.toml` | 3:env | home skill vuota / indisponibilità provider |

Ogni riga possiede un solo manifest, quindi i 33 gruppi sono disgiunti. Nuova fixture candidata per ogni ID: stesso nome executor sotto `tests/fixtures/birth_legacy/<executor>.json` **da espandere materialmente nel work manifest**; nessun percorso generico è un lease. Per massima concretezza, elenco delle nuove fixture candidate:

- D-FS-A.3.001: `tests/fixtures/birth_legacy/compress_files.json`.
- D-FS-A.3.002: `tests/fixtures/birth_legacy/compute_files_loc.json`.
- D-FS-A.3.003: `tests/fixtures/birth_legacy/consult_frontier.json`.
- D-FS-A.3.004: `tests/fixtures/birth_legacy/create_dirs.json`.
- D-FS-A.3.005: `tests/fixtures/birth_legacy/create_files_spreadsheet.json`.
- D-FS-A.3.006: `tests/fixtures/birth_legacy/create_images_indices.json`.
- D-FS-A.3.007: `tests/fixtures/birth_legacy/delete_dirs.json`.
- D-FS-A.3.008: `tests/fixtures/birth_legacy/delete_files.json`.
- D-FS-A.3.009: `tests/fixtures/birth_legacy/find_contacts.json`.
- D-FS-A.3.010: `tests/fixtures/birth_legacy/find_dirs.json`.
- D-FS-A.3.011: `tests/fixtures/birth_legacy/find_files.json`.
- D-FS-A.3.012: `tests/fixtures/birth_legacy/find_files_hash.json`.
- D-FS-A.3.013: `tests/fixtures/birth_legacy/find_images_google_photos.json`.
- D-FS-A.3.014: `tests/fixtures/birth_legacy/get_approval.json`.
- D-FS-A.3.015: `tests/fixtures/birth_legacy/get_files.json`.
- D-FS-A.3.016: `tests/fixtures/birth_legacy/get_images_google_photos.json`.
- D-FS-A.3.017: `tests/fixtures/birth_legacy/get_inputs.json`.
- D-FS-A.3.018: `tests/fixtures/birth_legacy/get_location.json`.
- D-FS-A.3.019: `tests/fixtures/birth_legacy/get_places.json`.
- D-FS-A.3.020: `tests/fixtures/birth_legacy/get_proposals.json`.
- D-FS-A.3.021: `tests/fixtures/birth_legacy/list_dirs.json`.
- D-FS-A.3.022: `tests/fixtures/birth_legacy/move_files.json`.
- D-FS-A.3.023: `tests/fixtures/birth_legacy/read_contacts.json`.
- D-FS-A.3.024: `tests/fixtures/birth_legacy/read_files.json`.
- D-FS-A.3.025: `tests/fixtures/birth_legacy/read_files_csv.json`.
- D-FS-A.3.026: `tests/fixtures/birth_legacy/read_files_doc.json`.
- D-FS-A.3.027: `tests/fixtures/birth_legacy/read_files_ocr.json`.
- D-FS-A.3.028: `tests/fixtures/birth_legacy/read_files_xlsx.json`.
- D-FS-A.3.029: `tests/fixtures/birth_legacy/read_messages.json`.
- D-FS-A.3.030: `tests/fixtures/birth_legacy/undo_last_turn.json`.
- D-FS-A.3.031: `tests/fixtures/birth_legacy/write_files.json`.
- D-FS-A.3.032: `tests/fixtures/birth_legacy/write_files_doc.json`.
- D-FS-A.3.033: `tests/fixtures/birth_legacy/write_images_google_photos.json`.

Firma/lang_state non sono inclusi nei gruppi di conversione: la pubblicazione autorevole appartiene al coordinatore FS-A.4. La proprietà esclusiva deve includere il futuro test preciso nel work manifest prima dell'incarico; l'inventario non inventa test già esistenti.

I 33 percorsi relativi esistono anche nella release 51; non modificarla in posto. Differenza dei campi rispetto al main: solo `executors/create_images_indices/manifest.toml` (semantica da riconciliare sulla baseline scelta).

Copie di lavoro annidate: 66 percorsi distinti, inventariati, non autorizzati come target di conversione simultanea. G0.6 decide quali siano fonti da integrare e quali copie storiche fuori rilascio; il loro owner è il coordinatore RM-0008, da confermare. Non moltiplicare i gruppi di prodotto per la stessa capacità presente in un altro checkout:

- `.claude/worktrees/rm0008-ci-public/executors/compress_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/compute_files_loc/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/consult_frontier/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/create_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/create_files_spreadsheet/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/create_images_indices/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/delete_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/delete_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/find_contacts/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/find_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/find_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/find_files_hash/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/find_images_google_photos/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_approval/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_images_google_photos/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_inputs/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_location/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_places/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/get_proposals/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/list_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/move_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_contacts/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_files_csv/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_files_doc/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_files_ocr/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_files_xlsx/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/read_messages/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/undo_last_turn/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/write_files/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/write_files_doc/manifest.toml`.
- `.claude/worktrees/rm0008-ci-public/executors/write_images_google_photos/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/compress_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/compute_files_loc/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/consult_frontier/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/create_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/create_files_spreadsheet/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/create_images_indices/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/delete_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/delete_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/find_contacts/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/find_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/find_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/find_files_hash/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/find_images_google_photos/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_approval/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_images_google_photos/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_inputs/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_location/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_places/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/get_proposals/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/list_dirs/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/move_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_contacts/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_files_csv/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_files_doc/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_files_ocr/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_files_xlsx/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/read_messages/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/undo_last_turn/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/write_files/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/write_files_doc/manifest.toml`.
- `.claude/worktrees/rm0008-reboot/executors/write_images_google_photos/manifest.toml`.

### Fixture candidate e falsi positivi

La ricerca `setup/teardown/env` trova anche normali ambienti di subprocess nei test (non automaticamente campi di manifest). Elenco preciso da triage, non insieme autorizzato di modifiche:

- `tests/benchmarks/tools/benchmark_compute_files_loc.py`: 242, 246.
- `tests/benchmarks/tools/benchmark_read_urls_html.py`: 373.
- `tests/e2e/certification/run_full_http.py`: 164, 183, 191, 206, 280, 290.
- `tests/e2e/driver/server.py`: 373, 421.
- `tests/e2e/scenarios/test_skill_import.py`: 45, 74, 80, 130, 134, 172, 179.
- `tests/internal/test_release_gate.py`: 180.
- `tests/portable/test_executor_birth_keystore_portable.py`: 141.
- `tests/runtime/durable_workloads/test_schema.py`: 63.
- `tests/runtime/engine/test_fastpath_lifecycle.py`: 254, 342, 386, 410, 438, 488, 528, 561, 585, 623, 647, 672, 723, 751, 781, 1207.
- `tests/runtime/engine/test_recovery_keeps_required_action.py`: 105.
- `tests/runtime/entries/test_get_inputs_subprocess.py`: 40, 56, 104, 141.
- `tests/runtime/executors/test_bounded_subprocess.py`: 25, 224.
- `tests/runtime/executors/test_get_images_indices.py`: 129.
- `tests/runtime/i18n/test_instance_language_config.py`: 19, 31.
- `tests/runtime/infra/test_llm_concurrency.py`: 44.
- `tests/runtime/infra/test_stack_units.py`: 217.
- `tests/runtime/infra/test_suite_environment_preflight.py`: 40, 67, 73, 79, 109.
- `tests/runtime/infra/test_yaml_section_render.py`: 118.
- `tests/runtime/remote/test_agent_server_remote.py`: 369.
- `tests/runtime/remote/test_device_shim_closure.py`: 81.
- `tests/runtime/remote/test_helper_wire_contract.py`: 389.
- `tests/runtime/safety/test_cli_credentials.py`: 31, 39.
- `tests/runtime/sites/test_sidecar_searxng.py`: 247, 255.
- `tests/runtime/skills/test_install_llm_service.py`: 144.
- `tests/tools/release_gate.py`: 353, 405, 523, 619, 638.
- `tests/tools/run_executor_manifests.py`: 60.

Prove esistenti pertinenti: `tests/runtime/infra/test_executor_birth_runner.py`,
`tests/runtime/infra/test_executor_birth_runner_windows_v1.py`,
`tests/portable/test_executor_birth_runner_linux_real.py`,
`tests/portable/test_executor_birth_runner_windows.py`,
`tests/portable/test_executor_birth_runner_windows_appcontainer.py`,
`tests/runtime/executors/test_executor_birth_property_runner.py`.
Nessuna eseguita. La loro presenza non certifica isolamento né equivalenza.

## FS-B: superfici e consumer

Il broker deve togliere i segreti dal processo executor, non soltanto oscurare il risultato.
Nel main `runtime/sandbox.py:549–564` monta vault e admin.key per la capability “metadata_only”; `mail_extras:842–892` monta admin.key e credenziali SMTP selezionate; `skill_extras` espone home provider RW e rete.
`agent_runtime.py:3679` costruisce l'ambiente a partire da `os.environ.copy()`: la verifica negativa deve coprire anche variabili ereditarie, non solo mount.
`skill_wrapper.py:204–212` inietta il token GitHub nel subprocess; `oauth_flow.py:159–171` può salvare copie OAuth in chiaro.
`credentials.py` e `protected_undo.py` derivano segreti dall'admin.key.
`vaglio.py:51–62` è un filtro di pattern, non il confine autorevole delle radici.

### Gruppi disgiunti candidati D-FS-B.3

I gruppi sottostanti sono inventario proposto, **non assegnazioni**. Dipendenze FS-B.2, F0.2, F1.2 e work manifest G0.6; livello alto sicurezza; revisore indipendente. File shared sono assegnati a un solo gruppo, gli altri li leggono. Nessun gruppo modifica root di release o dati personali.

| ID candidato | Famiglia e percorsi concreti di proprietà proposti | Effetto richiesto / prova pertinente |
|---|---|---|
| D-FS-B.3.001 | CRUD: `executors/find_credentials/find_credentials.py`, `executors/find_credentials/manifest.toml`, `executors/set_credentials/set_credentials.py`, `executors/set_credentials/manifest.toml`, `executors/delete_credentials/delete_credentials.py`, `executors/delete_credentials/manifest.toml`, `runtime/loader.py`, `runtime/credentials.py`, `scripts/generate_builtin_executor_contracts.py` | Conversione builtin e metadati soli; `tests/runtime/safety/test_credentials.py`, `tests/runtime/skills/test_builtin_executor_contracts.py`. |
| D-FS-B.3.002 | Mail: `runtime/mail_client.py`, `executors/read_messages/read_messages.py`, `executors/move_messages/move_messages.py`, `executors/send_messages/send_messages.py` | Operazioni IMAP/SMTP chiuse nel core; `tests/runtime/backends/test_mail_client_credentials.py`. |
| D-FS-B.3.003 | Siti/URL: `runtime/playwright_sidecar/credential_injection.py`, `runtime/playwright_sidecar/session_broker.py`, `executors/login_urls/login_urls.py`, `runtime/credential_mandates.py` | Riutilizzare broker già esistente; mandato binding/origine/operazione, nessuna credenziale al figlio; `tests/runtime/sites/test_sites_security.py`, `tests/runtime/sites/test_login_urls.py`. |
| D-FS-B.3.004 | Google Workspace/Photos/Vision: `runtime/backends/_google_auth_common.py`, `runtime/backends/_google_api_runner.py`, `runtime/backends/events/google_workspace.py`, `runtime/backends/contacts/google_workspace.py`, `runtime/backends/files/google_workspace.py`, `runtime/backends/messages/gmail_google_workspace.py`, `runtime/backends/images/google_photos.py`, `runtime/backends/images/google_vision.py`, `runtime/oauth_flow.py`, `executors/skills/google-workspace/scripts/google_api.py`, `executors/skills/google-workspace/scripts/gws_bridge.py`, `executors/skills/google-workspace/scripts/setup.py` | OAuth/refresh restano nel core; i CLI non ricevono token/root provider; `tests/runtime/backends/test_google_token_refresh.py`, `tests/runtime/backends/test_google_photos_backend.py`. |
| D-FS-B.3.005 | GitHub e wrapper comune: `runtime/skill_credentials.py`, `runtime/skill_wrapper.py` | Togliere token/env e fallback gh dal processo executor, mantenere operazioni tipizzate; `tests/runtime/skills/test_skill_credentials.py`. Script importati fuori release ancora da censire senza aprire i token. |
| D-FS-B.3.006 | Geografia: `runtime/google_places_client.py` | Richiesta Places nel core, nessuna API key al figlio; `tests/runtime/backends/test_geo_provider_outcomes.py`. |
| D-FS-B.3.007 | Frontier: `runtime/llm_provider.py` | Chiamata autenticata dal core, uso/costo/mandato separati; test nuovo candidato `tests/runtime/safety/test_frontier_broker.py`. |
| D-FS-B.3.008 | Telegram: `runtime/channels/telegram.py`, `runtime/channels/daemon.py`, `runtime/backends/messages/telegram_bot.py` | Il token bot rimane nel canale; nuovo candidato `tests/runtime/safety/test_telegram_broker.py`. |
| D-FS-B.3.009 | CIFS/admin: `runtime/cifs_helper.py`, `runtime/system/admin.py` | Mount chiuso e credenziali temporanee non accessibili al figlio generico; `tests/runtime/backends/test_cifs_helper.py`, `tests/runtime/http/test_admin_mount_cifs.py`. |
| D-FS-B.3.010 | Confini core di raccolta/ripresa: `runtime/agent_runtime.py`, `runtime/orchestration.py`, `runtime/args_resolver.py` | Raccolta segreti e resume dal principal, mai poteri da payload; `tests/runtime/safety/test_credentials_extraction.py`, `tests/runtime/engine/test_orchestration.py`, `tests/runtime/safety/test_credential_forms_injection.py`. |
| D-FS-B.3.011 | Undo protetto: `runtime/protected_undo.py` | Segreti per inverso restano nel core e ricevuta vincolata al proprietario; `tests/runtime/infra/test_protected_undo.py`. |
| D-FS-B.3.012 | Migrazione credenziali: `runtime/credentials_migrate.py` | Distinguere tool di migrazione amministrativa da consumer executor e impedirne il caricamento come capacità; nuovo candidato `tests/runtime/safety/test_credentials_migration_boundary.py`. |

Google è un solo gruppo perché i backend condividono autorità e refresh: spezzarlo per prodotto duplicando `_google_auth_common.py` non sarebbe disgiunto. Il gruppo GitHub possiede `skill_wrapper.py` condiviso: va integrato prima dei consumer che usano il nuovo contratto del wrapper. I gruppi sono disgiunti per file ma non tutti parallelizzabili per interfaccia.

Consumer core di firma/autenticazione, da mantenere nella radice protetta e **non trasformare in broker di firma generica**:
`runtime/http_auth.py`, `runtime/config.py`, `runtime/sign.py`, `runtime/devices.py`, `runtime/pairing.py`, `runtime/invocations.py`, `runtime/i18n_pipeline.py`, `runtime/tutor/catalog.py`, `runtime/synth_orphan_cleanup.py`, `runtime/executor_birth_bootstrap.py`, `runtime/executor_birth_operational.py`, `runtime/executor_birth_keystore.py`, `runtime/executor_birth_receipts.py`, `runtime/executor_birth_retention.py`, `runtime/executor_birth_reattestation.py`, `runtime/executor_birth_ownership_chain.py`, `runtime/executor_birth_ownership_cutover.py`, `runtime/contract_store.py`;
`scripts/client_signing.py`;
`client-rs/src/identity.rs`, `client-rs/src/config.rs`, `client-rs/src/runner.rs`.
Installer: `install/phases/phase3_code.py`, `phase4_secrets.py`, `phase6_firstboot.py`, `runtime/stack_migration.py`, `runtime/stack_reconcile.py` richiedono ownership distinta e lettura INSTALL_NOTES prima di qualsiasi futuro edit.

Consumer diretti candidati runtime/executor, ricerca riproducibile con righe:

- `executors/delete_credentials/delete_credentials.py`: 29, 34, 40.
- `executors/find_credentials/find_credentials.py`: 30, 35, 39, 44.
- `executors/login_urls/login_urls.py`: 315.
- `executors/set_credentials/set_credentials.py`: 19, 40, 47, 58, 62.
- `runtime/agent_runtime.py`: 807, 3392.
- `runtime/args_resolver.py`: 67.
- `runtime/cifs_helper.py`: 30.
- `runtime/config.py`: 485.
- `runtime/credential_mandates.py`: 10, 259.
- `runtime/credentials.py`: 3, 45, 46, 70, 72, 75, 97, 101, 108, 109, 149, 152.
- `runtime/credentials_migrate.py`: 43.
- `runtime/devices.py`: 410.
- `runtime/executor_birth_bootstrap.py`: 705, 760.
- `runtime/executor_birth_operational.py`: 326, 335, 348, 380, 407, 427, 430, 444.
- `runtime/google_places_client.py`: 36.
- `runtime/http_auth.py`: 5, 39, 179, 180.
- `runtime/i18n_pipeline.py`: 98.
- `runtime/invocations.py`: 342, 451.
- `runtime/llm_provider.py`: 632.
- `runtime/mail_client.py`: 58, 195.
- `runtime/oauth_flow.py`: 161, 186.
- `runtime/orchestration.py`: 796, 840.
- `runtime/pairing.py`: 135.
- `runtime/protected_undo.py`: 28, 41.
- `runtime/sandbox.py`: 554, 556, 562, 869, 876, 879.
- `runtime/sign.py`: 158, 168, 409, 538, 631, 692.
- `runtime/skill_credentials.py`: 69.
- `runtime/skill_wrapper.py`: 590.
- `runtime/stack_migration.py`: 207.
- `runtime/stack_reconcile.py`: 353.
- `runtime/synth_orphan_cleanup.py`: 240.
- `runtime/channels/daemon.py`: 1107.
- `runtime/channels/telegram.py`: 105.
- `runtime/playwright_sidecar/credential_injection.py`: 41.
- `runtime/system/admin.py`: 1449.
- `runtime/tutor/catalog.py`: 290.

Famiglie sensibili: mail (`mail:read/write/send`), credenziali (`metnos:credentials_metadata_only`), provider (`provider:access`), amministrazione (`system:admin`), invio (`channel:out`), persone/messaggi tramite oggetto e capability metnos. Non basta cercare la parola “sensitive” o “credentials”: `fs:read` con root di controllo raggiungibile è sensibile anche se input innocuo. `code:exec`, rete e frontend browser richiedono matrice dell'autorità effettiva.

## F6: call graph verificato

| Percorso | Catena reale main | Punto da fissare in G0.6 |
|---|---|---|
| Locale subprocess | `invoke_tool_by_name:6109 → invoke_executor:3817 → invoke_scheduled:3842 → _invoke_executor_impl_optional_context:3810 → _invoke_executor_impl:3456`; iniezione runtime:3618, risoluzione path:3623, scope:3628, undo:3641, wrap_command e subprocess | Dopo argomenti/scope risolti e prima di `_undo_pending`. |
| Remoto | stesso impl → scelta placement → canonicalizzazione payload → undo:3583 → `remote_exec.invoke_remote:61/101 → invocations.enqueue_invocation:681`; `agent_server.poll:291/319 → next_invocation:937`; client `Runner.run:187 → poll:311 → handle:368 → execute:488 → sandbox.run_sandboxed:604`; `result:340 → complete_invocation:996` | Hook remoto prima dell'undo per “zero effetti”, oltre che prima enqueue. Hook in remote_exec da solo arriva troppo tardi per l'undo. v2 e authorize_start non esistono nel main verificato. |
| Builtin ordinario | `invoke_tool_by_name:6040 → _invoke_builtin_handler:5948 → invoke_scheduled:6001 → _call_handler → handler` | Non passa da loader. D-F6.2c con proprietà solo loader è incompleta: deve includere questo punto o un API comune realmente condivisa. |
| Verbo unico | `invoke_tool_by_name:6067/6075 → invoke_scheduled → loader.invoke_verb_unique:520 → callable`; ingresso diretto anche HTTP, Telegram, orchestration e admin→sudoer | Hook comune nel loader, contesto autenticato per i chiamanti diretti; decidere se admin→sudoer sia una nuova invocazione o una delega interna, senza deduplica basata sul solo nome. |
| Durevole executor | `DurableExecutionBridge` ramo `RunnerKind.EXECUTOR:714 → _executor_invoker → _invoke_executor:190/200 → agent_runtime.invoke_executor(execution_context=context)` → percorso locale/remoto precedente | Un solo conteggio per tentativo, con owner/workload/attempt conservati; mai anche un secondo hook scheduler indistinto. |
| Durevole workload/internal | `RunnerKind.WORKLOAD:794` oppure runner interno → `invoke_scheduled:837 → admitted_call` | Tipizzare non applicabile solo per runner provatamente senza autorità executor; “workload” non equivale automaticamente a privo di effetti. |
| Altri scheduler | `runtime/tutor/compose.py:111`, `mode.py:97`, `obligations.py:125` | Sono chiamate a scheduler del modello; non contare ogni invocazione scheduler come executor F6. |

Chiamanti diretti verbo unico:
`runtime/http_routes_agent.py:865`,
`runtime/channels/daemon.py:864`,
`runtime/orchestration.py:818`,
`runtime/system/admin.py:1678`.
Altri ingressi executor da verificare nella matrice di copertura:
`runtime/http_routes_admin.py:2042`, `runtime/speculation.py:104`,
`runtime/tutor/probes.py:729`, percorsi di ripresa in `agent_runtime.py`.

Tutte le corrispondenze F6 runtime (alcune sono docstring/definizioni; la catena sopra distingue l'esecuzione):

- `runtime/agent_runtime.py`: 3456, 3604, 3814, 3817, 3842, 3898, 5948, 6001, 6040, 6067, 6075, 6109, 6254, 6264, 6304, 6315, 6739, 6770, 6781, 7847, 7909.
- `runtime/agent_server.py`: 319, 361.
- `runtime/executor_scheduler.py`: 933.
- `runtime/http_routes_admin.py`: 2042.
- `runtime/http_routes_agent.py`: 865, 901, 3944.
- `runtime/invocations.py`: 681, 937, 996.
- `runtime/loader.py`: 520.
- `runtime/orchestration.py`: 36, 502, 818, 894.
- `runtime/remote_exec.py`: 61, 101.
- `runtime/speculation.py`: 104.
- `runtime/channels/daemon.py`: 864, 898.
- `runtime/durable_workloads/execution.py`: 200, 837.
- `runtime/system/admin.py`: 1280, 1678.
- `runtime/testing/populate_cases.py`: 821, 2490, 2544.
- `runtime/tutor/compose.py`: 111.
- `runtime/tutor/mode.py`: 97.
- `runtime/tutor/obligations.py`: 125.
- `runtime/tutor/probes.py`: 729.

Test pertinenti esistenti, non eseguiti:
`tests/runtime/remote/test_invocations.py`,
`tests/runtime/remote/test_agent_server_remote.py`,
`tests/runtime/remote/test_invocation_scope.py`,
`tests/runtime/durable_workloads/test_execution_bridge.py`,
`tests/runtime/durable_workloads/test_direct_invocation.py`,
`tests/runtime/executors/test_executor_scheduler.py`,
`tests/runtime/executors/test_executor_scheduler_durable.py`,
`tests/runtime/skills/test_builtin_executor_contracts.py`,
`tests/runtime/engine/test_orchestration.py`.
Il ponte durevole possiede già prove di rifiuto lifecycle/contratto per tentativo, ma non dimostrano il nuovo F6.
Nuovo test candidato di matrice: `tests/runtime/safety/test_invocation_authority_paths.py`; coprire diniego/errore reader shadow, esito invariato, zero grant, conteggio singolo e i percorsi diretti. Nuova suite v2 solo dopo specifica wire/CAS.

## G0.4: modello helper amministrativo

Fatti verificati sull'host con letture mirate fuori sandbox:
- UID interattivi 1000, 1001 e 1002 con shell, oltre a root; account servizio UID995/GID985 con nologin. Non c'è prova che tutti i principal siano fidati.
- TCP 8770 ascolta su `0.0.0.0`, non esclusivamente loopback. Non è stata inviata alcuna richiesta.
- `/usr/local/sbin/metnos-agent-admin` è root:root 0755; unità HTTP root:root 0644. `/run/metnos` assente al momento dell'osservazione.
- I processi reali non sono visibili nel namespace ordinario Codex; il coordinatore ha verificato separatamente User=metnos, HTTP/Telegram active/running. Non dedurre servizio morto dal `ps` sandbox.
- `metnos-agent-admin` esegue script revisionati con SHA e ambiente ridotto come root; la sua docstring dichiara “full root delegation, not a sandbox”. **Non è** il client HTTP sicuro S0.1 e non va riutilizzato come broker generico.
- `internal/tools/metnos_admin_request.py` non è presente nella baseline main.

Attacco concreto: mentre il servizio è fermo un altro processo locale può occupare la porta alta 8770. Un client che invia admin.key al primo listener, anche con URL loopback, consegna il segreto prima di sapere chi risponde. Il semplice controllo preliminare del PID lascia una race. Nessuna prova elimina questo scenario qui.

**Decisione tecnica candidata per G0.6: socket Unix diretto, senza token e senza trasmissione di admin.key.**
Socket creato/supervisionato dal servizio dentro directory amministrativa non sostituibile da principal non fidati; client verifica tipo socket, owner, mode, catena directory senza symlink, identità inode prima/dopo connessione e `SO_PEERCRED` del server collegato. Server verifica `SO_PEERCRED` del client e mappa solo UID amministrativi espliciti al principal autorizzato. Verificare i due lati sul socket connesso, non soltanto il pathname.
Il servizio metnos è parte della base fidata: gli executor non devono poter impersonare il peer o accedere al socket. UID servizio identico da solo non distingue un executor dal core; servono esclusione socket dalla sandbox, namespace/permessi e attestazione del confine, oppure listener supervisionato con separazione di identità. Root compromesso fuori dal modello.

Riuso route: dedicare un listener Unix alla stessa applicazione/dispatcher e introdurre un contesto locale autenticato dal trasporto; l'endpoint non accetta header/payload che dichiarano UID/ruolo. Le route ordinarie mantengono ACL e autenticazione esistenti. Client solo metodo ammesso + path relativo canonico, nessun host/schema/URL assoluto, redirect, proxy, CONNECT o forwarding; body e risposta limitati, output sanificato. Nessun nuovo token è giustificato: la richiesta diretta evita segreti su TCP e può riusare il dispatcher; un token aggiungerebbe superficie senza evidenza di necessità.

Da congelare prima di S0.1: percorso socket derivato dalla configurazione (proposta `/run/metnos-admin/http.sock`, non hardcoded nel prodotto), UID/GID ammessi dal deployment, proprietà directory, creazione/riavvio e rimozione sicura inode obsoleto, mapping principal, identificazione del trasporto e percorso di binding aiohttp. La proposta socket non è ancora implementazione reviewable.

Prove da prescrivere a S0.1: server impostore e peer UID errato; socket sostituito tra stat/connect; directory o mode errati; symlink; normale processo executor incapace di raggiungere il socket; servizio fermo/riavviato senza inoltro TCP; URL assoluto/redirect/proxy rifiutati; chiave master assente da argv/env/stdout/stderr/log e dal socket. Nuovo test candidato `tests/internal/test_metnos_admin_request.py`; `runtime/admin_local_auth.py` e `tests/runtime/http/test_admin_local_auth.py` per il profilo socket; startup da definire sulla baseline release reale.

## Decisioni tecniche mancanti G0.6 / impedimenti

1. Congelare un solo albero di implementazione: main differisce dalla release e dal worktree RM-0008. Non reintrodurre il vecchio richiamo host per integrare FS-A.
2. Estendere/ripetere la scansione su YAML/candidate/fixture JSON safe e consumer skill installati, con digest per file e proprietà delle modifiche esistenti. Le 66 copie annidate richiedono classificazione di ownership, non edit duplicati.
3. Congelare contratto delle fixture ermetiche: file binari/generati, symlink di prova, database sintetici, owner, provider fake, lavoratori e ambiente negato. `HOME` nei manifest è un valore legacy da sostituire con contesto confinato, non un permesso a repurporre HOME del processo agente.
4. Scorporare il matcher puro ancora importato dalla release prima del ritiro test_runner; distinguere registry di test fidati e nascita candidato.
5. Correggere work manifest F6 per builtin ordinari, chiamanti diretti, remoto prima dell'undo, durable executor/internal/workload e scheduler Tutor. L'esistenza di una funzione comune non prova che tutti vi passino.
6. Congelare API broker e gestione owner senza duplicare file condivisi; escludere segreti anche dagli ambienti ereditati, mirror OAuth, CLI provider e richieste di firma.
7. Congelare socket S0.1 e identità core/executor; `SO_PEERCRED` non rende distinguibili processi ostili dello stesso UID senza ulteriore isolamento.
8. Fissare filesystem/denyset inode per hardlink e risoluzione no-follow multipiattaforma; nessuna degradazione Windows/JobObject deve allargare accesso.
9. Fissare ricevute FS e compatibilità con release RM-0008 effettiva. Nessuna certificazione è stata verificata qui; D2 resta bloccata fino ai prerequisiti normativi.
10. G0.7–G0.10 e schede complete con test/fixture esatti restano al coordinatore. Questo rapporto fornisce candidati e rilievi, non autorizza codice o esercizio.

## Matrice di prove ermetiche preparatoria

Questa matrice non esegue codice candidato e non crea test nelle aree RM-0008. I file sono candidati concreti per il work manifest successivo al commit RM-0008. Prima della consegna dei confini reali, le dipendenze sono oggetti inerti e i contatori degli effetti appartengono al test. Le prove del vero isolamento restano future e passano soltanto dal runner Birth già isolato.

| Contratto | Input ostile o guasto | Oracolo deterministico | File candidato |
|---|---|---|---|
| Classificazione fixture | setup shell libero, env sconosciuta, percorso assoluto fuori radice | errore tipizzato prima di chiamare qualunque adattatore; zero chiamate processo | `tests/runtime/safety/test_birth_legacy_contract.py` |
| Equivalenza legacy | tutti i 113 casi catalogati, inclusi binari/undo/provider simulati | stessi input e asserzioni sostanziali, confronto fixture canoniche; esecuzione candidata soltanto nella futura suite Birth | `tests/runtime/safety/test_birth_legacy_equivalence.py` |
| Ritiro runner | import del matcher puro e tutti i riferimenti runtime | nessun import/esecuzione del runner ritirato; il matcher continua a validare gli stessi vettori | `tests/runtime/safety/test_birth_runner_references.py` |
| Ambiente processo | variabile segreta fittizia aggiunta al processo padre | builder puro produce solo chiavi ammesse; valore sentinella assente da env/argv/log | `tests/runtime/safety/test_executor_environment_boundary.py` |
| Broker | handle cross-owner/cross-target, payload alterato, scadenza, replay | nessun consumer privilegiato chiamato; token fittizi su archivio temporaneo | `tests/runtime/safety/test_credential_broker_authority.py` |
| Protected roots | alias, symlink, hardlink, magiclink e sostituzione inode | vettori condivisi read/write/execute e diniego prima di costruire bind/ACL | `tests/runtime/safety/test_protected_roots_contract.py` |
| Helper locale | listener/socket impostore, stesso pathname con inode diverso, peer inatteso | rifiuto sul socket collegato; nessun byte di chiave master trasmesso | `tests/internal/test_metnos_admin_request.py` |
| Autenticazione socket | header UID/ruolo falsificato, UID executor, peer assente | principal esclusivamente da trasporto attendibile; nessuna route amministrativa chiamata | `tests/runtime/http/test_admin_local_auth.py` |
| F6 in ombra | deny o eccezione lettore su ogni ingresso reale | esito/effetti ordinari identici, un contatore, zero grant e zero AuthorityViolationV1 | `tests/runtime/safety/test_invocation_authority_paths.py` |
| F6 enforcement locale | deny dopo argomenti definitivi e prima undo | zero journal undo, zero subprocess, zero handler builtin | `tests/runtime/safety/test_invocation_authority_enforcement.py` |
| Remoto v2 | revoca pre-CAS, poll perso, payload mutato, crash post-CAS | nessun secondo grant; execution_unknown senza receipt, redelivery identica conserva identità | `tests/runtime/remote/test_remote_authority_v2.py` |
| Durevole | retry, owner cancellato, rientro via scheduler comune | conteggio una volta per attempt; zero effetti al diniego e distinzione executor/internal/workload | `tests/runtime/durable_workloads/test_invocation_authority.py` |

Prima di trasformare questa matrice in incarichi: rileggere il commit finale RM-0008, ripetere scansioni con manifest per-file, confrontare differenze semantiche main/release, decidere i file nuovi effettivi e verificare conflitti con i test già introdotti dall'altro agente. Nessuna riga costituisce prova superata.

## Riproduzione della scansione

Programma inline di sola lettura usato nelle due passate (non importa moduli Metnos e non invoca test). Il suo perimetro e i limiti precedenti sono parte integrante del risultato:

```python
import os, re, json, hashlib, pathlib, subprocess, tomllib
ROOT=pathlib.Path('/opt/metnos')
RELEASE=pathlib.Path('/var/lib/metnos/executor-birth/releases-v1/00000000000000000051')
deny={'target','dist','models','data','workspace','ANALISI MEDICHE E CERTIFICATI','.git','.venv','node_modules','__pycache__','.mypy_cache','.pytest_cache'}
allowed={'.py','.rs','.toml','.md','.j2','.sh','.gbnf','.html'}
patterns={
 'runner':r'\btest_runner\b|\brun_birth_phase\b',
 'credentials':r'\b(?:import credentials|from credentials import)|\b(?:ADMIN_KEY_PATH|CRED_DIR)\b|admin\.key|author_priv|load_private\(',
 'provider':r'resolve_github_token|google_token\.json|GITHUB_TOKEN|GH_TOKEN|refresh_token',
 'f6':r'\b(?:invoke_executor|_invoke_executor_impl|_invoke_builtin_handler|invoke_verb_unique|invoke_scheduled|invoke_remote|enqueue_invocation|next_invocation|complete_invocation)\s*\(',
 'legacy_fixture':r'["\'](?:setup|teardown|env)["\']\s*:|(?:^|\n)\s*(?:setup|teardown|env)\s*=',
}
def scan(root):
 files=[]; errors=[]; matches={k:[] for k in patterns}; legacy=[]; caps={}
 for base, dirs, names in os.walk(root,followlinks=False,onerror=lambda e: errors.append(str(e))):
  dirs[:]=sorted(d for d in dirs if d not in deny and not (pathlib.Path(base)/d).is_symlink() and not (pathlib.Path(base)/d).as_posix().startswith('/opt/metnos/internal/reports/rm0009-baseline'))
  for name in sorted(names):
   p=pathlib.Path(base)/name; rel=p.relative_to(root).as_posix()
   if p.suffix not in allowed or p.is_symlink(): continue
   if p.stat().st_size>4000000: errors.append('oversize:'+rel); continue
   try: b=p.read_bytes(); s=b.decode('utf8')
   except (OSError,UnicodeError) as e: errors.append(rel+':'+type(e).__name__); continue
   files.append([rel,hashlib.sha256(b).hexdigest()])
   for kind,pat in patterns.items():
    if kind=='legacy_fixture' and not (rel.startswith('tests/') or 'fixture' in rel or rel.endswith('manifest.toml')): continue
    lines=[i for i,line in enumerate(s.splitlines(),1) if re.search(pat,line)]
    if lines: matches[kind].append([rel,lines])
   if p.name=='manifest.toml':
    try: m=tomllib.loads(s)
    except ValueError: errors.append('toml_invalid:'+rel); continue
    fields={}
    for i,t in enumerate(m.get('tests',[]),1):
     if isinstance(t,dict):
      found={k: (sorted(t[k]) if isinstance(t[k],dict) else hashlib.sha256(str(t[k]).encode()).hexdigest()) for k in ('setup','teardown','env') if k in t}
      if found: fields[str(i)]=found
    if fields: legacy.append({'path':rel,'fields':fields})
    names=[c.get('name','') for c in m.get('capabilities',[]) if isinstance(c,dict)]
    if names and any(re.search(r'credentials|mail|messages|people|persons|admin|provider',n) for n in names): caps[rel]=names
 files.sort(); errors.sort()
 out={'file_count':len(files),'input_digest':hashlib.sha256(json.dumps(files,separators=(',',':')).encode()).hexdigest(),'errors':errors,'matches':matches,'legacy':legacy,'capabilities':caps}
 return out
result={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'repo':scan(ROOT),'release':scan(RELEASE)}
print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':')))

```
