# SPEC — Integrazione Google Photos (upload, archivio, statistiche, UI)

> **Stato**: PROPOSTA (10/7/2026) — attende ratifica D2/D8 da Roberto, il resto è implementabile as-is.
> **Autore**: agente (analisi richiesta da Roberto). **Implementatore previsto**: LLM (Opus) — le istruzioni sono prescrittive e non ambigue; dove serve una scelta, è una Decisione numerata già presa o marcata «RATIFICA».
> **Obiettivi utente** (Roberto): (a) caricare foto/album su Google Photos; (b) scaricare foto; (c) numero foto e occupazione memoria; (d) ricerca per anno; (e) visualizzazione in UI.

---

## 0. Vincolo di policy che domina il design

Dal **31/3/2025** Google ha rimosso gli scope ampi della Photos Library API (`photoslibrary.readonly`, `photoslibrary.sharing`, `photoslibrary`): un'app può elencare/cercare/scaricare **solo i contenuti creati da lei stessa** (`*.appcreateddata`). L'accesso all'intera libreria esiste solo in due forme: **Picker API** (selezione interattiva dell'utente nella UI Google) e **Google Takeout** (export batch, schedulabile su Drive ogni 2 mesi).
Fonti: developers.google.com/photos/support/updates · developers.googleblog.com/en/google-photos-picker-api-launch-and-library-api-updates.

Conseguenze non negoziabili:
- ❌ conteggio/occupazione/ricerca sull'INTERA libreria via API — impossibile.
- ❌ delete di mediaItems via API (nemmeno app-created) — l'upload NON è reversibile.
- ✅ upload + creazione album + lettura/download del *creato-da-Metnos*.
- ✅ archivio completo SOLO via Takeout→Drive→import locale (Drive è già integrato).

## 1. Decisioni architetturali

- **D1 — Tre fasi indipendenti**: P1 upload/album (Library API), P2 archivio completo (Takeout→Drive→indice locale), P3 prelievi puntuali (Picker API, opzionale). P1 e P2 non condividono codice oltre l'auth; ognuna è shippabile da sola.
- **D2 — RATIFICA Roberto — niente oggetto `albums` nel vocab**: gli album si gestiscono come **argomento** (`album: str`) degli executor `*_images_google_photos`. Razionale: la governance §2.2 richiede escalation per nuovi token; l'alternativa (object `albums`) aggiunge un oggetto per un solo provider. Se Roberto preferisce `albums`, la spec cambia solo i nomi in §3.3.
- **D3 — Naming provider-suffisso** (pattern ADR 0141 `_github`, ADR 0136): executor `write_images_google_photos`, `find_images_google_photos`, `get_images_google_photos`. NON client-arg: Photos non è un backend alternativo di operazioni esistenti (regola client-arg vale per `files/events/contacts/dirs` multi-provider), è un dominio con semantica propria.
- **D4 — Auth riusa la skill `google-workspace`**: stesso client secret, stesso token file, scope aggiunti alla lista esistente (re-consent una tantum). NIENTE skill nuova.
- **D5 — L'archivio "verità" per statistiche/anno/UI è l'INDICE LOCALE** costruito sull'export Takeout (pipeline immagini esistente: `create_images_indices` → `find_images_indices`). `find_images_indices` espone GIÀ `metadata.total_count` e `metadata.total_size_gb` e `time_window="YYYY"` → gli obiettivi (c)(d)(e) sono soddisfatti dall'esistente una volta importato l'export.
- **D6 — `extract_files` va creato** (oggi NON esiste, è solo referenziato dal vocab §2.2/§2.3): è il pezzo mancante generale (non Photos-specifico) e serve al ramo Takeout. Executor core handcrafted, simmetrico a `compress_files`.
- **D7 — Quota account: DIFFERITA**. Il breakdown solo-Photos non è esposto dall'API; l'occupazione della libreria arriva ESATTA dall'export Takeout (D5). La quota totale account (`drive about` → `storageQuota`) si aggiunge eventualmente dopo, fuori da questa spec.
- **D8 — RATIFICA Roberto — P3 (Picker) sì/no**: costo medio (sessioni+polling+dialog); valore = prelievi puntuali senza attendere il Takeout. Implementare solo dopo P1+P2 validate.

## 2. Prerequisiti manuali (Roberto, una tantum)

1. In Google Cloud Console, sul progetto del client secret esistente: **abilitare «Photos Library API»** (e, se P3, «Photos Picker API»).
2. Configurare **Google Takeout** (takeout.google.com): export di Google Photos, destinazione «Aggiungi a Drive», frequenza «ogni 2 mesi per 1 anno», formato `.tgz` (o `.zip`), dimensione massima 10 GB (più archivi va bene: la pipeline è vettoriale).
3. Dopo il deploy di P1: rifare il consent OAuth quando Metnos lo chiede (dialog `needs_inputs` esistente).

## 3. FASE P1 — Upload foto e album (Library API)

### 3.1 Scope OAuth
File da toccare (pattern esistente, vedi `skill_oauth_providers.json` + `google_api.py:45` + `setup.py:45`):
- `executors/skills/google-workspace/scripts/google_api.py::SCOPES` e `.../setup.py::SCOPES`: aggiungere
  `https://www.googleapis.com/auth/photoslibrary.appendonly` e
  `https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata`.
- `runtime/skill_oauth_providers.json`: aggiungere i due scope al preset `"all"` E creare preset `"all+photos"`? NO — DEVI aggiungere ai preset esistenti `"all"` (un solo preset aggiornato; niente proliferazione).
- Il token esistente diventa "partial": `setup.py::_missing_scopes_from_payload` già rileva gli scope mancanti → il flusso `needs_inputs` OAuth esistente guida il re-consent. Nessun codice nuovo per l'auth.

### 3.2 CLI: sub-comando `photos` in `google_api.py`
Aggiungere al CLI skill (stesso stile dei sub-comandi `drive|sheets|docs`) i comandi:

```
photos upload <path> [--album-id ID]      # 2 chiamate: uploads (bytes, X-Goog-Upload-*) → mediaItems:batchCreate
photos album-create <title>               # albums.create → {id, title, productUrl}
photos album-list                         # albums.list (solo app-created; paginate, pageSize=50)
photos search [--album-id ID] [--year YYYY] [--page-token T]
                                          # mediaItems:search con filters.dateFilter.ranges
                                          # (anno → {startDate:{year,month:1,day:1}, endDate:{year,month:12,day:31}})
photos download <media-item-id> --output <path>
                                          # mediaItems.get → baseUrl; GET baseUrl+"=d" (originale).
                                          # NB: baseUrl scade in 60 min → get+download nella stessa call.
```
Dettagli obbligatori:
- endpoint base `https://photoslibrary.googleapis.com/v1/`; upload bytes: POST `v1/uploads` con header `X-Goog-Upload-Content-Type`, `X-Goog-Upload-Protocol: raw` → token; poi `mediaItems:batchCreate` con `newMediaItems[{simpleMediaItem:{uploadToken, fileName}}]` e opz. `albumId`.
- batchCreate accetta max **50 item per chiamata** → chunking nel backend (non nel CLI).
- output SEMPRE JSON su stdout (pattern degli altri sub-comandi); errori → `{"error": ...}` con exit 1.

### 3.3 Backend runtime: `runtime/backends/images/google_photos.py`
Nuovo package `runtime/backends/images/` (`__init__.py` vuoto). Modulo con le stesse convenzioni di `backends/files/google_workspace.py` (riusa `skill_wrapper._needs_inputs_oauth_setup`, `_google_api_runner.run_with_retry`, `SKILL_NAME = "google-workspace"`, `_has_creds`, `_ensure_fresh_token` — importali/replica il prologo di google_workspace.py, NON duplicare la logica di refresh: estrai le 3 funzioni comuni in `runtime/backends/_google_auth_common.py` e fai usare quello a ENTRAMBI i moduli).

Funzioni (firme esatte):
```python
def upload(args: dict) -> dict     # paths|from_step-entries, album (nome, opz.) → results
def find(args: dict) -> dict      # album (nome, opz.), year (int, opz.), max_results → entries app-created
def download(args: dict) -> dict  # ids|from_step, dst_dir → results
def list_albums(args: dict) -> dict  # → entries [{id,title,items_count,url}]  (usata da find con albums=true)
```
- `upload`: risolve `album` per NOME fra gli album app-created (`album-list`); se assente lo CREA (`album-create`). Chunk da 50. `results=[{path, media_item_id, album, ok}]`, `ok_count/fail_count` §2.8 (un file fallito non ferma gli altri, §2.1).
- `find`: `entries=[{id, filename, mime, created_at, width, height, album}]`; `year` → dateFilter come in §3.2. Cap `max_results` default 100 + campi §2.7 su troncamento.
- `download`: `results=[{id, local_path, bytes, ok}]`; `dst_dir` default `~/.local/share/metnos/Immagini/google-photos/` (workspace foto default — memoria `feedback_default_photo_workspace`).

### 3.4 Executor (3 nuovi, dir sotto `executors/`)
Convenzioni obbligatorie per TUTTI e tre: manifest §2.5 con `[description]` a capitoli IT+EN (`SCOPO/PATTERN/NON/OUT`), `manifest.lang_state.json`, args con description 1-frase+tipo+esempio+default, affinity 8-15 termini IT+EN, i18n per ogni stringa user-facing (§7.13, chiavi nuove sotto), firma `python3 runtime/sign.py sign executors/<name>` + commit manifest+sig insieme (§7.10). Dispatcher sottile → funzioni del backend §3.3 (stesso pattern di `find_files.py`, senza `_HANDLERS`: qui il provider è fisso).

1) **`write_images_google_photos`** — carica foto su Google Photos.
   - args: `paths: array[str]` (accetta 1 elemento, §2.4) | `from_step: int` (consuma `entries[*].path`); `album: str` opz. («crea se manca» nel manifest); `max_total: int` default 200 (§2.1 cap esplicito).
   - `revertible = false` (l'API non permette delete: dichiararlo nel manifest e nella description NON: «l'upload non è annullabile da Metnos»). NIENTE `reverse_pattern`.
   - `critical = true` (outbound verso servizio esterno).
   - OUT: `results` (§2.6, trasformativo).
2) **`find_images_google_photos`** — cerca fra le foto caricate da Metnos.
   - args: `year: int` opz.; `album: str` opz.; `albums: bool` default false (true → elenca gli album, D2); `max_results: int` default 100.
   - `NON:` nel manifest: «solo contenuti caricati da Metnos (limite API Google, non di Metnos); per l'intera libreria usare l'archivio Takeout (find_images_indices)».
   - OUT: `entries` (§2.6).
3) **`get_images_google_photos`** — scarica per id.
   - args: `ids: array[str]` | `from_step`; `dst_dir: str` default workspace foto.
   - OUT: `results` con `local_path` → il derive-attachments esistente (`agent_runtime._derive_file_attachments`) rende le foto scaricate visibili in chat/gallery senza lavoro aggiuntivo.
   - `reverse_pattern = "delete_created_paths"` (il download locale è annullabile).

### 3.5 Routing
- `runtime/prefilter.py::_OBJECT_HINTS["images"]`: NON toccare (già copre foto/photo).
- Lessico: nuovo concept `R("provider.google_photos", "phrases", substring, it=["google photos","google foto","su photos"], en=["google photos","to google photos"])` in `detection_lexicon_seed.py` — usato SOLO dall'affinity/manifest, NON serve bypass: il pool per (write|find|get, images) include i nuovi executor per nome/affinity; il marcatore provider nel testo li fa vincere sul fratello locale (affinity-match boost esistente).
- `vocab.py`: NESSUNA modifica (D2). Verificare che `naming_grammar.validate_name` accetti `google_photos` come qualifier provider (stesso pattern di `_google_workspace`; se il validatore ha una lista chiusa di provider-suffix, aggiungere `google_photos` in `tool_grammar._PROVIDER_SUFFIX_MARKERS`).

### 3.6 Chiavi i18n nuove (live + `install/data/i18n_seed.sqlite` + `gen_i18n.py`)
```
ERR_GPHOTOS_UPLOAD    it="Caricamento su Google Photos fallito per {name}: {reason}."  en=…
MSG_GPHOTOS_UPLOADED  it="{n} foto caricate su Google Photos{album, es. « nell'album X»}."  en=…
MSG_GPHOTOS_IRREVERSIBLE it="Nota: l'API Google non permette di eliminare foto caricate — l'upload non è annullabile da Metnos."  en=…
```
(la nota IRREVERSIBLE va appesa UNA volta al primo upload del turno, nel result `message`).

### 3.7 Test P1 (criteri di accettazione)
- Unit (`runtime/tests/test_google_photos_backend.py`): chunking 50, risoluzione album per nome, error-shape §2.8; CLI mockato (monkeypatch `run_with_retry`).
- Manifest-test `[[tests]]` nei manifest: lista vuota ok (§2.1), args invalidi.
- E2E reale (gate umano, come `e2e_google_backend.py`): `e2e/e2e_google_photos.py` — carica 2 foto di test in album `metnos-e2e`, `find` le ritrova (year=anno corrente), `get` le riscarica, confronto sha256. ≥1 turno reale `/agent/turn`: «carica le foto di /tmp/x su google photos nell'album Test» (§8.5).

## 4. FASE P2 — Archivio completo via Takeout (statistiche, anno, UI)

### 4.1 Nuovo executor core: `extract_files` (generale, non solo Takeout)
- Dir `executors/extract_files/`, handcrafted, simmetrico a `compress_files`.
- args: `paths: array[str]` | `from_step`; `dst_dir: str` (required); `format: enum[auto,zip,tar,gztar,gz]` default `auto` (da estensione: `.zip/.tar/.tar.gz/.tgz/.gz`); `max_total: int` default 20 (archivi per chiamata).
- Implementazione: stdlib `zipfile`/`tarfile`/`gzip`. **OBBLIGO zip-slip guard**: ogni member path risolto DEVE stare sotto `dst_dir` (`Path(dst_dir, member).resolve().is_relative_to(Path(dst_dir).resolve())`), altrimenti skip + conteggio in `errors` (§2.8). `tarfile.extractall(filter="data")` (Python ≥3.12).
- OUT: `results=[{path, dst_dir, extracted_count, skipped_unsafe, ok}]` + `ok_count/fail_count`.
- `reverse_pattern = "delete_created_dirs"`; `revertible = true`; placement: solo server (nessun `[placement] device_ok`).
- Manifest `NON:`: «per RECORD da testo usare extract_entries; per COMPRIMERE usare compress_files» (§2.2).

### 4.2 Import Takeout = comando NL schedulato (ADR 0186 — MAI job bespoke)
Task ricorrente via builtin `create_tasks` (query NL, actor Roberto, canale telegram):
```
label: "import-takeout-photos"
when:  "daily@06:30"        # il Takeout arriva ~ogni 2 mesi; il daily è idempotente e a vuoto costa ~1 turno
query: "trova su google drive i file il cui nome inizia con takeout- più recenti di 2 giorni,
        scaricali in /home/roberto/.local/share/metnos/Immagini/google-photos-takeout/archivi,
        estraili in /home/roberto/.local/share/metnos/Immagini/google-photos-takeout/foto
        e aggiorna l'indice immagini di quella cartella"
```
Pipeline attesa dal planner: `find_files(client=google_workspace, query="takeout-", time_window=last-2d)` → `download` (gw `download()`, esistente) → `extract_files(from_step, dst_dir=…)` → `create_images_indices(base_path=…/foto, recursive=true)`.
**Convergenza §8.5**: il turno canonico sopra va eseguito reale e iterato finché il piano combacia (0 errori). Se il compound a 4 step si rivela fragile, il fallback APPROVATO è spezzare in 2 task (`daily@06:30` scarica+estrae; `daily@06:50` indicizza) — NON scrivere un executor-orchestratore.
Nota Takeout: gli archivi contengono `Takeout/Google Foto/<Album o Anno>/...` + sidecar `*.json` con `photoTakenTime`. L'indice legge EXIF (`taken_at_iso`) che nelle foto Google è presente; i sidecar si IGNORANO in P2 (l'EXIF basta per l'anno; i sidecar sono un'estensione futura per le foto senza EXIF).

### 4.3 Statistiche, anno, UI — TUTTO esistente, zero codice
- «quante foto ho nell'archivio google photos» → `find_images_indices(base_path=…/foto)` → `metadata.total_count`, `total_size_gb` (già nel manifest OUT).
- «foto del 2019» → `find_images_indices(time_window="2019")` (già supportato: `_parse_time_window` accetta `YYYY`).
- UI: le entries di `find_images_indices` producono attachments → thumbnail + gallery (`/agent/gallery/<turn_id>`) già funzionanti.
- Occupazione: `total_size_gb` = occupazione reale della libreria alla data dell'ultimo export (dichiararlo nella risposta è compito del describe: nessun lavoro).

### 4.4 Test P2
- Unit `runtime/tests/test_extract_files.py`: zip/tgz felici, zip-slip bloccato (member `../../evil`), lista vuota, formato ignoto → errore onesto.
- E2E: archivio zip di 3 jpg di test → turno reale con la query canonica §4.2 (senza il ramo Drive: file locale) → indice creato → `find_images_indices` con `time_window` dell'anno delle foto trova 3.
- Accettazione finale (con Takeout reale di Roberto): conteggio = numero foto dell'export; «foto del <anno>» in chat mostra la gallery.

## 5. FASE P3 — Picker API (OPZIONALE, dopo P1+P2 — D8)

Design minimo (implementare solo su ratifica): scope `photospicker.mediaitems.readonly`; CLI `photos picker-create` → `{pickerUri, sessionId}`; executor `get_images_google_photos(picker=true)` → risposta `needs_inputs` con il link pickerUri (dialog esistente); task di polling `sessions.get` finché `mediaItemsSet=true` (riusa il meccanismo dialog/gate-resume, NON un poller nuovo); poi download come §3.4(3). Da specificare in dettaglio a valle di P1.

## 6. Non-goals e rischi dichiarati

- **Non-goals**: sync bidirezionale; delete su Photos (API non lo consente); breakdown quota per-prodotto (API non lo espone); lettura full-library via Library API (policy).
- **Rischi**: (1) baseUrl 60 min → mai persistere baseUrl, download immediato; (2) rate limit Library API (10k req/day default) → i cap `max_total/max_results` sono il freno; (3) Takeout `.tgz` multi-volume grandi → `extract_files` è vettoriale e il task è idempotente (re-run sicuro: l'indice `create_images_indices` senza `force` salta gli invariati); (4) upload non annullabile → nota utente obbligatoria (§3.6) + `critical=true`.

## 7. Ordine di implementazione (per l'LLM implementatore)

1. P2.§4.1 `extract_files` (autonomo, testabile subito, colma un buco del vocab).
2. P1 §3.1→3.7 in sequenza (scope → CLI → backend → executor → routing → i18n → test).
3. P2 §4.2 task + convergenza sul turno canonico.
4. Validazione con Roberto (upload reale + primo Takeout) → poi decidere D8 (P3).

Ogni passo: suite (`METNOS_ENGINE=v3 pytest runtime/tests/ -q`) + ≥1 turno reale `/agent/turn` sul dominio toccato (§8.5) + re-sign degli executor toccati (§7.10) + restart servizio (§8.6: mai durante un turno attivo).
