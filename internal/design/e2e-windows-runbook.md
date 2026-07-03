# Runbook E2E Windows — W3.3 (executor remoti)

> Driver: un umano (o un agente Sonnet 5.5) col PC Windows target + una
> sessione PowerShell + il browser Metnos sullo stesso PC. Nessuno script:
> ogni passo è un comando copy-paste + un'osservazione da annotare.
> Riferimento: `internal/design/remote-executors.html` §14 (contratti),
> §15/§16 (fasi), §16.4 (questo runbook nel design doc originale).

## Prima di iniziare — leggere

1. `CLAUDE.md` (radice del repo) — norme di progetto.
2. `internal/design/remote-executors.html` §16.0 (vincoli trasversali) e
   §16.4 (il checklist che questo file esegue).
3. **Non scrivere codice Rust nuovo.** Il client (`0.2.3`) è già completo:
   Job Object (W3.1), lock single-instance, kill-al-timeout, fail-closed
   rimosso (sandbox attivo di default, fix 3/7 sera). Questo runbook
   ESEGUE e OSSERVA, non implementa. Se un passo fallisce per un bug reale
   del client, annotalo nell'Esito in fondo — non tentare un fix Rust
   senza fermarsi e chiedere (è un'altra macchina, altro toolchain, va
   validato di nuovo su Linux prima di distribuirlo).

## Convenzioni

- `$SERVER` = `http://192.168.1.33:8770` (console admin) — sostituisci se
  l'IP è diverso.
- `$AGENT` = `http://192.168.1.33:8765` (canale device, porta pairing).
- Serve la **admin key** del server per le chiamate `Invoke-RestMethod`
  dirette (non serve per il flusso join dal browser, che usa il cookie di
  sessione admin già autenticato). Chiedila a chi ha accesso a `.33` se non
  ce l'hai — non è nel repo.
- Ogni passo ha un **Atteso** e uno spazio ✅/❌ da compilare nell'Esito.

---

## 1 — Join e installazione dal browser (§5)

1. Sul PC Windows, apri `$SERVER/admin/devices` nel browser, autentica come
   admin se richiesto.
2. Compila il nome device (es. `laptop-windows-e2e`) e clicca **"Genera
   link di installazione"**. Poiché il browser NON gira sul server, il
   pulsante è già quello giusto (nessun `for_other_pc` da forzare).
3. Apri il link generato (stessa scheda o una nuova) — la pagina rileva
   Windows, avvia il download dell'installer dopo ~1,2s.
4. Sul file scaricato (`MetnosClientSetup.ps1`): tasto destro → **Esegui
   con PowerShell**. Se compare l'avviso SmartScreen, annota il testo
   esatto (passo 7) e procedi con "Ulteriori informazioni → Esegui
   comunque".
5. Segui l'avanzamento nella pagina join (o su `/admin/devices`): deve
   arrivare a **heartbeat**.

**Atteso**: la riga del device compare in `/admin/devices` con stato
"raggiungibile", `os_family=windows`. Annota il **device_id** (visibile
nella pagina, o via `GET $SERVER/admin/devices`) — serve per i passi dopo.

```powershell
# In alternativa al click, verifica lo stato via API (join_id dalla UI):
Invoke-RestMethod "$env:SERVER/agent/client/join/<join_id>/status"
```

---

## 2 — Execute read-only reale (Job Object attivo)

Usa **`compute_files_loc`**, non `find_files`: `find_files`/`read_files`/
`write_files`/`list_dirs` importano moduli `runtime/`-tier
(`backends.files.local`/`path_alias`) mai spediti al device dallo shim
(§8) — crasherebbero con `ModuleNotFoundError` su QUALSIASI OS remoto,
scoperto durante l'audit W3.2 (3/7). Solo `get_files` e `compute_files_loc`
sono self-contained e promossi `platforms=["linux","windows"]` oggi.

```powershell
$body = @{
    executor = "compute_files_loc"
    args = @{ paths = @("C:\Windows\System32\drivers\etc") }
    deadline_ms = 15000
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$SERVER/admin/devices/<device_id>/test-invoke" `
    -Headers @{ Authorization = "Bearer <ADMIN_KEY>" } `
    -ContentType "application/json" -Body $body
```

**Atteso**: HTTP 200, `state:"done"`, `result.ok:true`, `result.entries`
non vuoto (statistiche LOC sui file trovati), **`result.sandbox:
"job-object"`** — questa è la riga che conta di più: prova che il Job
Object è stato usato per davvero, non il fallback nudo.

---

## 3 — Timeout onesto + albero morto (Task Manager)

Nessun executor "dorme all'infinito" nel catalogo oggi (§16.0: non
inventare un executor test-only per un solo runbook). Forza il timeout con
una directory abbastanza grande + una deadline volutamente troppo corta:

```powershell
$body = @{
    executor = "compute_files_loc"
    args = @{ paths = @("C:\Windows") }
    deadline_ms = 300
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$SERVER/admin/devices/<device_id>/test-invoke" `
    -Headers @{ Authorization = "Bearer <ADMIN_KEY>" } `
    -ContentType "application/json" -Body $body
```

Nella finestra fra l'invio e la risposta, apri **Task Manager** →
scheda Dettagli → cerca processi `python.exe`/`metnos-client.exe` figli.

**Atteso**: la risposta HTTP arriva comunque (200 o 202, mai un hang del
comando); il log del client (`Get-Content` sul file di log, se configurato,
o la finestra della Scheduled Task) mostra `job object terminato`; in Task
Manager il processo Python della scansione **scompare** entro pochi
secondi dal timeout — questo è il gemello Windows di
`sandbox_linux::tests::timeout_kills_executor_process`, verificato a mano
qui perché `cargo test` non gira in CI Windows.

---

## 4 — Doppio `run` manuale → lock

```powershell
& "$env:LOCALAPPDATA\Metnos\metnos-client.exe" run --server $SERVER
```

(la Scheduled Task `MetnosClient` ha già un'istanza attiva dal passo 1).

**Atteso**: uscita immediata con un errore che nomina il lock file
(`client.lock`) e dice che un'istanza è già attiva — MAI un secondo poller
silenzioso. `proclock.rs`, verificato su Windows per la prima volta qui
(il test Rust `proclock::tests` gira solo su Linux in CI).

---

## 5 — Mutante + reverse — ⛔ NON ESEGUIBILE con l'executor set attuale

**Nota onesta, non un salto**: `write_files`/`move_files`/`delete_files`
hanno la STESSA dipendenza non bundlata di `find_files` (§2 sopra) — sono
mutanti E hanno bisogno di `backends/files/local.py`, quindi oggi
NESSUN executor mutante può girare sul device. Questo passo del runbook
originale (§16.4 del design doc, scritto PRIMA dell'audit W3.2) presumeva
`write_files` disponibile — non lo è.

**Non inventare un workaround stasera.** Serve una decisione: (a) estendere
lo shim bundle (`agent_server.py::shim_bundle`) a includere
`backends/files/local.py` + `path_alias.py`, o (b) scrivere un executor
mutante minimale, self-contained, dedicato al device. Entrambe sono
modifiche vere, da progettare a sé — annotare qui come SKIP e aprire un
follow-up, non forzare una scorciatoia per chiudere la checklist.

---

## 6 — Revoca → poll rifiutato

```powershell
# Dalla UI: /admin/devices → riga del device → "Revoca".
# Oppure via API:
Invoke-RestMethod -Method Post -Uri "$SERVER/admin/devices/<device_id>/revoke" `
    -Headers @{ Authorization = "Bearer <ADMIN_KEY>" }
```

Osserva il log del client (la Scheduled Task resta attiva, ma il prossimo
poll deve fallire).

**Atteso**: il prossimo `poll` del client riceve `403 unknown_device`
(vedi `agent_server.py::_verified_device_body`); il client logga il rifiuto
e continua a ritentare con backoff (non crasha). Nessuna nuova invocazione
verrà mai consegnata a questo device_id.

---

## 7 — SmartScreen: onestà sull'attrito

Annota qui, testualmente, cosa ha mostrato Windows al passo 1.4 (titolo
esatto del popup, se "Ulteriori informazioni" era visibile subito o
nascosto, se l'editore risultava "sconosciuto"). Questo dato decide se
anticipare Authenticode (W6, oggi il pin sha256+Ed25519-pubkey-pinning è
l'unica difesa, §16 review 2 del 3/7) o se l'attrito è tollerabile per un
uso domestico fra dispositivi fidati.

---

## Esito (compilare a fine sessione)

| Passo | Data | Esito | Note |
|---|---|---|---|
| 1 — join+install | | | |
| 2 — execute read-only | | | |
| 3 — timeout+albero morto | | | |
| 4 — doppio run→lock | | | |
| 5 — mutante+reverse | | SKIP (gap architetturale, vedi nota) | |
| 6 — revoca→403 | | | |
| 7 — SmartScreen | | | (testo esatto del popup) |

**DEFINITION OF DONE W3** (§16.4): tutti i passi 1-4 e 6 spuntati ✅ sul PC
reale, 5 esplicitamente SKIP con motivo, 7 annotato. Aggiornare §0.bis del
design doc con l'esito quando questo runbook è completo.

## Riferimenti

ADR 0007 (topologia self-hosted, overlay Headscale) · 0011 (client/server
asimmetrico) · 0034 (routing/placement 3 livelli) · 0037 (binario
self-contained static-link) · 0046 (Rust + python-build-standalone, uv).
Codice: `runtime/agent_server.py`, `runtime/agent_mirror.py`,
`runtime/devices.py`, `runtime/invocations.py`, `runtime/http_routes_admin.py`
(`admin_device_test_invoke`), `client-rs/`.
