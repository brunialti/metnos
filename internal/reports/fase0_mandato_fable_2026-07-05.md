# Report FASE 0 — Mandato Fable 5/7/2026 (orientamento + chiusura punti aperti)

**Riferimento**: `internal/fable_mandate_2026-07-05.md` · **Branch**: `session/detection-lexicon-i18n` (non pushato) · **Prod**: live sul working-tree, riavviata dopo ogni deploy runtime.

## Esito: 4/4 punti CHIUSI ✓

### 1. Residuo working-tree ✓
6 commit modulari, attribuzione letta dal diff (non chiesta):
- `dfcc6a0` ondata doc pubblica remote-executors (intro {it,en} + remote_executors/sandbox {it,en}): riga «Dispositivi», capitolo esecuzione-su-PC, de-gergalizzazione §7.8 (placement→collocazione, device→dispositivo).
- `91ad7fe` gemella interna (internal/design/{remote-executors,e2e-runbook} + README client a stato corrente).
- `c6d4b98` fix mio: README client dichiarava 0.2.7 — STANTIO (Cargo.toml + mirror latest = **0.2.9**).
- `dececa2` lang_state i18n (bookkeeping del traduttore per i manifest di `fa0e988`, incl. nuova entry `args.name`).
- `fe6f706` 3 report sessione 3-4/7. · `d326ca1` il mandato stesso.
- Deploy Cloudflare: 84 file già live (l'ondata era stata deployata dal working-tree il 4/7 §9.2) — ora anche committata.

### 2. `find` Drive nome-esatto ✓ (decisione Roberto + fix)
**Scoperta nuova (✓ misurata)**: `f5ded21` era TYPE-BLIND — una CARTELLA col nome esatto («Richieste effettuate») oscurava i 2 fogli omonimi-fuzzy → il filtro MIME del reader la scartava → **not_found al posto della scelta** (turn `9fc0111a`). Opzioni presentate; Roberto ha scelto il fix consigliato: **`find_files` esclude le cartelle** (confine oggetti §2.2: files≠dirs; per le cartelle c'è `find_dirs`) + tiene la preferenza nome-esatto.
Commit `d2879a9`. Verificato: Richieste effettuate→3 file→scelta a 2 ✓; KAKEBO SPESE 2026→1 doc esatto ✓; KAKEBO→vettoriale ✓. 9 test verdi.

### 4. Turno 697 live sul device ✓
PC-ROBERTO online (heartbeat 21s) → turno reale `2b470352`: `list_dirs` **eseguito sul device** (`_ran_on_device=PC-ROBERTO`) → xlsx **7 righe popolate** (header `path` + 6 path), niente write spurio, messaggio onesto. Il bug originale di `697d1d08` è chiuso end-to-end live.

### 3. Issue B — form/colloquio disambiguazione ✓ (3 difetti misurati, tutti fixati)
Riproduzione HTTP reale a 2 turni (`/agent/turn`, conv persistente). Cause radice:
- **B2 — fmt**: su HTTP la scelta MONO-step degradava a `dialogue` (testo numerato); il form cliccabile scattava solo con preview o ≥2 step. Telegram aveva già la regola giusta. Fix: predicato condiviso `channels.inline_ui.all_choice_like` (kind cliccabili, senza cap-24 Telegram) nei DUE decider (`orchestration.invoke_get_inputs_internal` + `get_inputs._decide_fmt`, executor re-firmato §7.10).
- **template — dict-choices**: `dialog_form.html` rendeva le choices `{value,label}` (ADR 0127) come repr Python → il submit NON validava mai («Scegli una fra:…»). Difetto PRE-esistente, visibile solo ora che le scelte raggiungono il form. Fix dict-aware; ≤6 alternative → RADIO, oltre → select. Anche multi_choice.
- **B1 — resume cieco**: il colloquio digitato («1») consumava il dialog e RIESEGUIVA l'executor, ma `_shape_result_for_chat` buttava le `entries` → «✓ Operazione completata» senza il foglio. Fix col discriminatore **§2.6**: `entries`=reader → i dati SONO la risposta → `_fmt_reader_entries` (tabella markdown per righe con header-detection; lista compatta per dict; cap 20 + `MSG_TOP_OF` §2.7). I mutanti (`results`) restano al «✓». Giova a resume-executor, resume-credenziali, cap-expand.

**Validazione live (turni reali via prod :8770)**: flusso FORM → marker `INLINE_FORM` → radio con ID puliti → submit → **tabella del foglio (Top 20 su 115)** in `data-completion-text` → bolla chat; flusso DIGITATO → «1» → stessa tabella. Commit `59f3519` + `ead8257` (13 test regressione `test_dialog_choice_form_http.py`) + `55141c3` (digest manifest).

## Prove del cancello §A
- Test: 13 nuovi + **127 verdi** cluster dialog/get_inputs/orchestration + gate §2.8 (`test_compound_spreadsheet_execution.py` 4/4) + drive (5/5).
- Bench compound `compound_extract_create_bench.py` (v3, 2 run): **8/8 query STABILI** (piano corretto in tutti i run) — nessuna regressione.
- Turni reali: `2b470352` (device), flusso A/B Issue-B (HTTP prod), `9fc0111a`→scelta post-fix.
- Doc: nessuna pagina descriveva le soglie fmt → niente claim stantio; ondata doc committata+deployata (punto 1).
- Commit: 12 totali Fase 0, modulari, niente Co-Authored-By, branch non pushato.

## ⚠ Residui onesti (non bloccanti per il cancello)
- ⚠ La chat web usa un **select nativo o radio dentro iframe**: su Telegram le scelte erano già bottoni; il rendering web ora è cliccabile ma la UX (stile bolle, focus) non è stata rifinita oltre il funzionale.
- ⚠ `_fmt_reader_entries` mostra top-20: per fogli molto larghi (>8-10 colonne) la tabella markdown può sbordare in bolle strette — accettato (§2.7 nota il cap; niente troncamento silenzioso).
- ⚠ Il 2° foglio omonimo «Richieste effettuate - Pagina elenco…» resta su Drive (dato reale dell'utente, non artefatto di test): la scelta a 2 è quindi il comportamento CORRETTO, non un difetto.

## Prossimo passo (dal mandato)
**AREA 1 — Engine, CP1·M0 (safety)**: test idempotenza guard su cache-hit + contratto d'ordine pipeline guard. Attendo ok di Roberto al cancello Fase 0.
