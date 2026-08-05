# Verifica igiene ambiente Metnos — report (22/7/2026)

> Scan deterministico (find/du/git) su `/opt/metnos`, `~/.local/share/metnos`, `~/.local/state/metnos`.
> Fotografia iniziale precedente alla pulizia. Il 23/7/2026 il lotto di
> documenti interni inequivocabilmente superati del §4 è stato cancellato su
> richiesta esplicita; dati runtime, backup e artefatti non sono coinvolti da
> quel lotto. Tre livelli: 🟢 **sicuro** (rigenerabile/temporaneo) ·
> 🟡 **cautela** (verificare prima) · 🔴 **NON toccare** (dati reali/vivi/personali). Totale spazio
> recuperabile senza rischio: **~17 GB**.

---

## Sommario

| Categoria | Spazio | Rischio |
|---|---|---|
| Artefatti build/test rigenerabili | **16.3 GB** | 🟢 nullo |
| Log gonfio + cache immagini | **~0.4 GB** | 🟢 nullo |
| Backup DB di bonifiche concluse | ~40 MB | 🟡 basso |
| Storico turni/log da rotazione | ~1 GB | 🟡 basso |
| Doc interni superati (handover chiusi) | ~0.3 MB | 🟡 (storia) |
| Git: 531 file non committati | — | 🟡 igiene |

---

## 1. 🟢 Sicuro da cancellare (rigenerabile / temporaneo) — ~16.7 GB

| Percorso | Dim | Cos'è / come si rigenera |
|---|---|---|
| `to_delete/` | **5.0 GB** | Marcato per cancellazione (nome + gitignored). Contiene `.venv-image-poc` (torch/CUDA/nvidia). `rm -rf to_delete`. |
| `client-rs/target/` | **6.1 GB** | Build Rust. `cargo build` la ricrea. Il sorgente è 248 KB. |
| `tests/e2e/tmp/` | **5.1 GB** | 17 run-dir di test e2e (data/turns effimeri, incl. 8 `*.jsonl.bak`). Rigenerate a ogni run. |
| `~/.local/share/metnos/logs/vlm_server.log` | **355 MB** | Un SINGOLO log gonfio (VLM server). Troncare/ruotare — nessun altro log supera 228 KB. |
| `.venv/` | 42 MB | Virtualenv locale, ricreabile da requirements. |
| `~/.local/share/metnos/thumbcache/` | 62 MB | Cache miniature foto, rigenerata on-demand. |
| `tests/simulator/parse_cache*` + `typing_cache` | ~8 MB | Cache di parsing/typing del simulatore (gitignored, rigenerabili). `parse_cache.pre_D` = residuo di un vecchio esperimento. |
| `workspace/.mnestoma/mnest.sqlite.bak.20260430` | — | Backup mnestoma di aprile (workspace gitignored). |

**Nota**: `client-rs/target` + `tests/e2e/tmp` + `to_delete` da soli sono ~16.3 GB su un repo il cui codice
utile (`runtime` 40M + `executors` 4.5M + `client-rs/src` 248K + `docs` 8.8M) sta in <60 MB.

## 2. 🟡 Backup DB di bonifiche concluse (verificare, poi cancellare) — ~40 MB

Backup manuali `.bak-<data>-<motivo>` di bonifiche/migrazioni ormai chiuse (potatura telos, layer-overlap,
famiglie morte — 1-2/7; ADR 0180 igiene proposte). Il DB **vivo** accanto è quello senza suffisso.

| File | Data | Motivo (concluso) |
|---|---|---|
| `share/metnos/i18n.sqlite.bak-20260519-135041` | 19/5 | pre-migrazione i18n v2 |
| `share/metnos/autopath.sqlite.bak-20260701-223324` (7.3M) | 1/7 | bonifica autopath |
| `share/metnos/telos_proposals*.bak-20260702-potatura` (×3) | 2/7 | potatura telos |
| `state/metnos/proposals_state.db.bak-2026070{1,2}-*` | 1-2/7 | bonifica layer-overlap |
| `state/metnos/change_intents.sqlite.bak-20260702-*` (×2) | 2/7 | bonifica famiglie morte |
| `state/metnos/pairings.db.bak` | — | backup senza data |

**🔴 Eccezione cautela**: `share/metnos/persons.sqlite.bak.1778408818` è il backup del **registro
biometrico reale** (usato per ripristino il 6/7). È vecchio ma tocca dati sensibili → tenerlo finché
`persons.sqlite` vivo è confermato integro, poi eliminare.

## 3. 🟡 Dati runtime da rotazione (vivi ma cumulativi) — ~1 GB

Non sono cruft: sono storico. Ma crescono senza pruning.

- **`turns/` 651 MB, 89 file** dal 2026-04-26 al 2026-07-22. Alimentano i cruscotti admin, ma 3 mesi di
  storico a 651 MB meritano una policy di rotazione (es. archiviare/comprimere >60 gg). ADR 0186 / §10.5.
- **`logs/` 355 MB** = quasi tutto `vlm_server.log` (§1). Manca una rotazione dei log VLM.
- Minori: `_history/` 7.3M (blob undo turni), `cost/` 2.2M, `undo.jsonl` 3.1M — crescita lenta, ok.

## 4. 🟡 Documenti interni superati (scopo esaurito) — `internal/`

**Handover/handoff a scopo esaurito** (11 file in `internal/design/`, ~700K totale): sono per definizione
effimeri — servono finché il lavoro è in corso. Questi hanno il topic **chiuso** (marcato nel doc stesso):

| Doc | Stato dichiarato |
|---|---|
| `handover_booking_e2e_15_7.md` | «— RISOLTO» |
| `HANDOFF_SITES_BOOKING_2026-07-13.md` | «— RISOLTO (13/7 sera)» |
| `HANDOFF_SITES_EXTRACTION_2026-07-13.md` | topic sites chiuso (ADR 0191) |
| `handoff_zero_regressions_2026-07-17.md` | «incidenti chiusi e riprodotti» |
| `handoff_opus_sites_intelligent_robot.md` | sites implementato (ADR 0191) |
| `handoff_opus_sessione_6_7_sera.md` | session log del 6/7 |
| `handoff_sites_f2_codex.md`, `handoff_checkpoint_2026-07-{18,21}_*`, `handoff_remote_authority_2026-07-16.md` | checkpoint/handoff datati, topic assorbiti |

**Report/spec datati** in `internal/reports/` (1.6M) e `internal/design/`:
- `internal/reports/area{1,2,3,4}_*_2026-07-05.md`, `audit_runtime_2026-05-28.html`, `bughunt_2026-05-31.jsonl`,
  `downloads_inventory_pre_delete_*.txt` (residuo di una pulizia precedente).
- `internal/design/CP5_PROGRESS.md`, `README_specs_2026-07-06.md`, `spec_fase7_w4_appcontainer.md` (fase 7 CHIUSA).

**🔴 NON dichiarare superati (ancora CITATI come riferimento attivo)** — verificato per cross-ref:
`spec_args_provenance_architecture.md` (**6 citazioni**, incl. `CLAUDE.mutabile.md`), `spec_argtransform_registry.md`
(2), `spec_fase7_disconnect_robustezza.md` (1), `analysis_intelligent_sites_robot_2026-07-14.md` (1). Restano.

**Esito 23/7/2026**: gli handoff chiusi, i resoconti di sessione, i report di
area assorbiti dalle ADR e le analisi utente assorbite da RM-0001 sono stati
cancellati su indicazione esplicita. I riferimenti correnti sono stati spostati
alle fonti normative o alle prove sostitutive. La storia dei documenti già
tracciati resta disponibile in Git; il contenuto utile dei preparatori non
tracciati è stato consolidato nella relativa roadmap. Audit recenti, specifiche
aperte e report di conformità sono stati conservati.

## 5. 🔴 NON toccare (dati reali / vivi / personali)

- **Modelli vivi**: `tool_classifier/v1/model.safetensors` + `intent_classifier` (2.4 GB) — usati dal
  prefilter/intent; `index/` 630M (indici foto SQLite); `playwright-browsers/` 646M (sites); `models/` 2.0G (LLM/VLM).
- **Store reali**: `persons.sqlite`, credenziali, `devices`, `autopath.sqlite`, `scratchpad.db`, `mnestoma`
  (i **vivi**, non i `.bak`). `mirror/` 171M, `persons_examples/` 8.7M (esempi biometrici).
- **Personali** (mai committare né cancellare): `ANALISI MEDICHE E CERTIFICATI/`, `UTENZE E SPESE/`.

## 6. 🟡 Igiene git — segnalazione

Branch `session/detection-lexicon-i18n`: **531 file modificati (M) + 181 non tracciati (??) + 9 cancellati (D)**
non committati. È un accumulo notevole di lavoro non salvato su un branch di sessione — rischio di perdita e
review difficile. Molti degli 181 untracked sono probabilmente candidati/lang_state/report generati da cron o
da altre sessioni (che per convenzione vanno committati modularmente). Consiglio: triage e commit modulari, o
stash/branch dedicati, prima che l'insieme diventi ingestibile.

## 7. docs pubblici (`docs/`)

- `docs/drafts/install_on_demand_sudoers_setup.md` (8K) — 1 bozza; verificare se promuovere o rimuovere.
- `docs/_redirects` (103 righe) — verificare le regole verso doc rimosse (i redirect legacy Myclaw/Mykleos
  puntano a file esistenti; nessun file legacy residuo trovato). Basso valore.
- 🔴 `docs/{it,en}/Metnos_QuickTour.pdf` — **vivi** (rigenerati oggi), NON toccare.

---

## Azioni consigliate (in ordine di ritorno)

1. `rm -rf to_delete client-rs/target tests/e2e/tmp .venv` + troncare `vlm_server.log` → **~16.7 GB** liberati,
   zero rischio (tutto rigenerabile). `client-rs/target` e `.venv` si ricreano al primo build/setup.
2. Rimuovere i backup DB `.bak-<data>` di §2 (tenere `persons.sqlite.bak` finché il vivo è confermato).
3. Policy di rotazione per `turns/` (>60gg) e per i log VLM (logrotate/troncamento periodico).
4. Archiviare gli handover chiusi di §4 in `_archive/` (non cancellare — storia); lasciare i 4 spec citati.
5. Triage dei 531 file git non committati.

**Nulla di distruttivo è stato eseguito.** Ogni riga qui è una proposta verificata; i punti 🔴 e
`persons.sqlite.bak` richiedono conferma esplicita prima di qualsiasi azione.
