//! runner.rs — loop di esecuzione del client (§6/§14.2 design doc).
//!
//!   flush spool → poll → verify server_sig → pull executor (cache-miss) →
//!   run_sandboxed → persisti result nello spool → consegna → heartbeat
//!
//! Invarianti: il client non genera comandi (§ invariante 2); ogni invocazione
//! e' verificata (server_sig) prima dell'esecuzione; `invocation_id` gia'
//! eseguito non si ri-esegue (dedup §6.4); result firmato dal device.
//!
//! Consegna affidabile del result (§12): il result e' PRIMA persistito nello
//! spool (`spool/results/<id>.json`) e SOLO DOPO consegnato. Se il server e'
//! giu' durante il POST, il result resta nello spool e viene ri-consegnato al
//! giro successivo — senza MAI ri-eseguire l'executor (niente doppio
//! side-effect su un mutante). Consegna avvenuta = file rimosso.

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::config::Paths;
use crate::identity::{self, Identity};
use crate::state::State;
use crate::wire::{HeartbeatRequest, Invocation, InvocationResult, PollRequest, PollResponse};
use crate::{executors, pyenv};
// Dispatch per-piattaforma (§16.2 W3.1): stessa firma su entrambi i moduli
// (sandbox_windows ri-esporta Limits/SandboxOutput da sandbox_linux; il
// check sandbox_disabled() e' condiviso e richiamato DENTRO ciascun modulo
// — runner.rs non ha piu' bisogno di un import qualificato separato, 3/7).
#[cfg(unix)]
use crate::sandbox_linux as sandbox;
#[cfg(windows)]
use crate::sandbox_windows as sandbox;

/// Header con la firma Ed25519 (b64url) del device sui bytes ESATTI del body.
const SIG_HEADER: &str = "X-Metnos-Device-Sig";

const POLL_BLOCK_MS: u64 = 25_000;
const HEARTBEAT_EVERY: Duration = Duration::from_secs(30);
const BACKOFF_MAX: Duration = Duration::from_secs(30);

pub struct Runner {
    server: String,
    device_id: String,
    server_pubkey: String,
    id: Identity,
    paths: Paths,
    http: reqwest::Client,
    /// invocation_id già eseguiti in questo processo: non ri-eseguire (§6.4).
    executed: HashSet<String>,
    capabilities: Vec<String>,
    /// Cache per-processo: lo shim (executor_helpers+messages) e l'interprete
    /// python si risolvono UNA volta, non ad ogni execute.
    shim_dir: Option<PathBuf>,
    python: Option<PathBuf>,
}

impl Runner {
    pub fn new(server: String, st: &State, id: Identity, paths: Paths) -> Result<Self> {
        let device_id = st
            .device_id
            .clone()
            .context("device non appaiato: esegui prima `register`")?;
        let server_pubkey = st.server_public_key.clone().context(
            "server_public_key assente in state: ri-esegui `register` \
             (il server deve fornirla per verificare le invocazioni)",
        )?;
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(POLL_BLOCK_MS / 1000 + 15))
            .build()?;
        // I result non ancora consegnati (crash precedente) contano come
        // "già eseguiti": non ri-eseguire, solo ri-consegnare.
        let executed = pending_result_ids(&paths);
        Ok(Self {
            server,
            device_id,
            server_pubkey,
            id,
            paths,
            http,
            executed,
            capabilities: vec!["fs".into(), "net".into(), "pkg".into()],
            shim_dir: None,
            python: None,
        })
    }

    pub async fn run(mut self) -> Result<()> {
        tracing::info!(server = %self.server, device = %&self.device_id[..12.min(self.device_id.len())], "runner avviato");
        // GC spool (§12): un result mai consegnato oltre la retention e'
        // stale (il server ha gia' chiuso quel turno con timeout onesto).
        // Scarto ONESTO: warn per-file, mai silenzioso.
        let pruned = prune_stale_spool(&self.paths);
        if pruned > 0 {
            tracing::warn!(pruned, "spool: result stale scartati (oltre retention)");
        }
        // Heartbeat su task tokio SEPARATO (§B5): il loop principale si blocca
        // per decine di secondi durante il primo download+estrazione del runtime
        // python (pyenv::resolve) e durante l'esecuzione di un executor lungo.
        // Con l'heartbeat inline il device appariva «offline» per ~1min al primo
        // giro. Un task dedicato batte ogni HEARTBEAT_EVERY a prescindere da cosa
        // fa il loop di poll/execute. Runtime multi-thread (tokio full) → i due
        // task girano davvero in parallelo anche se l'estrazione occupa un worker.
        spawn_heartbeat(self.http.clone(), self.server.clone(),
                        self.device_id.clone(), self.id.clone());

        let mut backoff = Duration::from_secs(1);
        let mut cursor: Option<String> = None;

        loop {
            // Ri-consegna i result rimasti nello spool (server tornato su).
            self.flush_pending().await;

            match self.poll(cursor.as_deref()).await {
                Ok(Some(inv)) => {
                    backoff = Duration::from_secs(1);
                    let inv_id = inv.invocation_id.clone();
                    cursor = Some(inv_id.clone());
                    if self.executed.contains(&inv_id) {
                        // Già eseguita: la consegna del result la fa flush_pending.
                        tracing::info!(invocation = %inv_id, "già eseguita: skip (dedup §6.4)");
                        continue;
                    }
                    if let Err(e) = self.handle(inv).await {
                        tracing::error!(invocation = %inv_id, "esecuzione fallita: {e:#}");
                    }
                }
                Ok(None) => {
                    backoff = Duration::from_secs(1);
                }
                Err(e) => {
                    tracing::warn!("poll fallito (server giu'?): {e:#}; ritento fra {:?}", backoff);
                    tokio::time::sleep(backoff).await;
                    backoff = (backoff * 2).min(BACKOFF_MAX);
                }
            }
        }
    }

    /// POST firmato: serializza il value UNA volta, firma quei bytes esatti,
    /// li invia come body con la firma nell'header. Il server verifica i bytes
    /// ricevuti (nessun round-trip canonico → float-safe, §6.3 refinement).
    fn signed_post(&self, path: &str, value: &Value) -> Result<reqwest::RequestBuilder> {
        let body = serde_json::to_vec(value)?;
        Ok(self.signed_body_post(path, body))
    }

    fn signed_body_post(&self, path: &str, body: Vec<u8>) -> reqwest::RequestBuilder {
        let sig = self.id.sign_b64(&body);
        let url = format!("{}{}", self.server.trim_end_matches('/'), path);
        self.http
            .post(&url)
            .header(SIG_HEADER, sig)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
    }

    async fn poll(&self, cursor: Option<&str>) -> Result<Option<Invocation>> {
        let body = PollRequest {
            device_id: &self.device_id,
            cursor,
            capabilities: &self.capabilities,
            block_ms: POLL_BLOCK_MS,
        };
        let value = serde_json::to_value(&body)?;
        let resp = self
            .signed_post("/agent/poll", &value)?
            .send()
            .await
            .context("POST /agent/poll")?;
        if !resp.status().is_success() {
            bail!("poll HTTP {}", resp.status());
        }
        let parsed: PollResponse = resp.json().await.context("parse poll response")?;
        // Self-update W4 (5/7/2026): su mismatch scarica il descrittore
        // FIRMATO, verifica con la pubkey pinnata, swap atomico e respawn.
        // Idempotente per sha: nessun loop se il binario e' gia' quello
        // pubblicato (version string diversa a parita' di build).
        if let Some(v) = &parsed.server_client_version {
            if v.as_str() != env!("CARGO_PKG_VERSION") {
                match crate::selfupdate::maybe_update(&self.server, &self.server_pubkey).await {
                    Ok(true) => crate::selfupdate::respawn_and_exit(),
                    Ok(false) => {}
                    Err(e) => tracing::warn!("self-update fallito (riprovo al prossimo poll): {:#}", e),
                }
            }
        }
        Ok(parsed.invocation)
    }

    async fn handle(&mut self, inv: Invocation) -> Result<()> {
        // 1. verifica server_sig con la pubkey pinnata (§6.2). Firma non valida
        //    = rifiuto + log, nessuna esecuzione (§12).
        let signed = inv.signed_bytes()?;
        if identity::verify_b64(&self.server_pubkey, &inv.server_sig, &signed).is_err() {
            tracing::error!(invocation = %inv.invocation_id, "server_sig NON verificata: RIFIUTO (attacco/replay)");
            return Ok(());
        }

        let start = Instant::now();
        let result = match self.execute(&inv).await {
            Ok(r) => r,
            Err(e) => InvocationResult {
                invocation_id: inv.invocation_id.clone(),
                device_id: self.device_id.clone(),
                ok: false,
                entries: json!([]),
                n_processed: 0,
                elapsed_ms: start.elapsed().as_millis() as i64,
                sandbox: "none".into(),
                error: Some(format!("{e:#}")),
                error_class: Some("device_error".into()),
                payload: json!({}),
            },
        };

        // 2. Persisti il result PRIMA di segnare eseguito e PRIMA della consegna
        //    (§12): se il server e' giu', il file resta e verra' ri-consegnato,
        //    MAI ri-eseguito (niente doppio side-effect su un mutante).
        let body = serde_json::to_vec(&result.body_value())?;
        write_pending_result(&self.paths, &inv.invocation_id, &body)?;
        self.executed.insert(inv.invocation_id.clone());

        // 3. Prova la consegna (idempotente lato server); l'esito è gestito da
        //    flush_pending — un fallimento lascia il result nello spool.
        self.flush_pending().await;
        Ok(())
    }

    async fn execute(&mut self, inv: &Invocation) -> Result<InvocationResult> {
        // Il gate fail-closed pre-W3.1 (rifiuta salvo METNOS_SANDBOX=off) e'
        // stato RIMOSSO 3/7: era corretto SOLO nella finestra in cui
        // sandbox_windows.rs non esisteva ancora (nessun sandbox reale su
        // Windows = meglio rifiutare che eseguire nudo). Con sandbox_windows
        // (Job Object, primitiva OS sempre disponibile) il dispatch sotto
        // chiama SEMPRE un sandbox reale per costruzione, simmetrico a unix
        // — nessun pre-check ne' env var richiesti per il caso normale.
        // Tenerlo avrebbe invertito la semantica di METNOS_SANDBOX=off
        // (da "salta il contenimento" a "unico modo di eseguire qualcosa").

        let exec = executors::ensure_executor(
            &self.server,
            &self.server_pubkey,
            &inv.executor,
            &inv.manifest_sha256,
            &inv.code_sha256,
            &self.paths.executors_dir,
        )
        .await?;
        pyenv::assert_stdlib_only(&exec.dir)?;

        // Shim + interprete: risolti UNA volta per processo (cache). Lo shim
        // NON e' content-addressed come gli executor: se un modulo runtime
        // viene aggiunto al bundle server DOPO l'avvio del client, il client
        // gia' in esecuzione non lo vedrebbe mai (memoizzato una volta). Sotto
        // c'e' l'auto-guarigione: su import fallito lo shim viene rigenerato.
        if self.shim_dir.is_none() {
            self.shim_dir = Some(
                executors::ensure_shim(&self.server, &self.server_pubkey, &self.paths.cache_dir)
                    .await?,
            );
        }
        if self.python.is_none() {
            let env = pyenv::resolve(&self.server, &self.paths.cache_dir).await?;
            tracing::info!(python = %env.python.display(), source = %env.source, "interprete risolto (cache)");
            self.python = Some(env.python);
        }
        let python = self.python.clone().unwrap();
        tracing::info!(executor = %inv.executor, "esecuzione");

        let args_json = serde_json::to_string(&inv.args)?;
        let extra_env: Vec<(String, String)> =
            inv.env_injections.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let limits = sandbox::Limits {
            wall: Duration::from_millis(inv.deadline_ms.max(1000)),
        };

        // Esecuzione con auto-guarigione dello shim (costo zero sul percorso
        // felice): se l'executor esce con output non-JSON PERCHE' un import e'
        // fallito (ModuleNotFoundError/ImportError), lo shim in cache e'
        // stantio — ri-scarica lo shim UNA volta e riprova. Ogni altro output
        // non-JSON resta un errore, invariato.
        let mut refreshed = false;
        loop {
            let shim = self.shim_dir.clone().unwrap();
            let start = Instant::now();
            let out = sandbox::run_sandboxed(
                &exec, &python, &shim, &args_json, &extra_env, &limits,
            )
            .await?;
            let elapsed_ms = start.elapsed().as_millis() as i64;

            if out.timed_out {
                return Ok(InvocationResult {
                    invocation_id: inv.invocation_id.clone(),
                    device_id: self.device_id.clone(),
                    ok: false,
                    entries: json!([]),
                    n_processed: 0,
                    elapsed_ms,
                    sandbox: out.sandbox,
                    error: Some("deadline exceeded".into()),
                    error_class: Some("timeout".into()),
                    payload: json!({}),
                });
            }

            match serde_json::from_str::<Value>(out.stdout.trim()) {
                Ok(parsed) => {
                    return Ok(result_from_executor(
                        inv, &self.device_id, parsed, elapsed_ms, out.sandbox));
                }
                Err(e) => {
                    // Auto-guarigione SOLO se manca un modulo DELLO SHIM: quell'
                    // import è al caricamento del modulo (prima di run_stdio →
                    // prima di qualsiasi side effect), quindi il retry è sicuro
                    // anche per futuri executor MUTANTI (rilievo #5). Un import
                    // fallito altrove NON viene ritentato: il refetch non
                    // aiuterebbe e un side effect parziale non va ripetuto.
                    if !refreshed {
                        if let Some(module) = missing_module(&out.stderr) {
                            let dir = executors::ensure_shim(
                                &self.server, &self.server_pubkey, &self.paths.cache_dir,
                            )
                            .await?;
                            if dir.join(format!("{module}.py")).is_file() {
                                tracing::warn!(
                                    executor = %inv.executor, module = %module,
                                    "modulo shim mancante: shim stantio rigenerato, riprovo"
                                );
                                self.shim_dir = Some(dir);
                                refreshed = true;
                                continue;
                            }
                        }
                    }
                    return Err(anyhow::anyhow!(
                        "output executor non-JSON: {e}; stdout={:?} stderr={:?}",
                        out.stdout,
                        out.stderr
                    ));
                }
            }
        }
    }

    /// Consegna (o ri-consegna) i result nello spool. Best-effort: un POST
    /// fallito lascia il file per il giro successivo. Il server e' idempotente
    /// (§6.4), quindi una doppia consegna non produce doppio side-effect.
    async fn flush_pending(&self) {
        let dir = results_dir(&self.paths);
        for inv_id in pending_result_ids(&self.paths) {
            let path = dir.join(format!("{inv_id}.json"));
            let body = match std::fs::read(&path) {
                Ok(b) => b,
                Err(_) => continue,
            };
            match self.signed_body_post("/agent/result", body).send().await {
                Ok(resp) if resp.status().is_success() => {
                    let _ = std::fs::remove_file(&path);
                    tracing::info!(invocation = %inv_id, "result consegnato");
                }
                Ok(resp) => {
                    tracing::warn!(invocation = %inv_id, "result rifiutato: HTTP {}", resp.status());
                    // 4xx (es. invocazione sconosciuta/dispositivo revocato):
                    // ritentare all'infinito è inutile. Scarta solo su 4xx.
                    if resp.status().is_client_error() {
                        let _ = std::fs::remove_file(&path);
                    }
                }
                Err(e) => {
                    tracing::debug!(invocation = %inv_id, "result non consegnato (server giù?): {e:#}");
                }
            }
        }
    }

}

/// Task heartbeat indipendente (§B5). Batte subito (device online appena il
/// runner parte) poi ogni `HEARTBEAT_EVERY`. Vive quanto il processo: il loop
/// principale non lo attende mai. Un fallimento e' solo un warn — il giro
/// successivo riprova, e un device momentaneamente muto e' meno grave di uno
/// mai visto.
fn spawn_heartbeat(http: reqwest::Client, server: String, device_id: String, id: Identity) {
    tokio::spawn(async move {
        // `interval` completa il PRIMO tick immediatamente → primo heartbeat
        // senza attesa iniziale.
        let mut ticker = tokio::time::interval(HEARTBEAT_EVERY);
        loop {
            ticker.tick().await;
            if let Err(e) = send_heartbeat(&http, &server, &device_id, &id).await {
                tracing::warn!("heartbeat fallito: {e:#}");
            }
        }
    });
}

/// POST /agent/heartbeat firmato — free function riusabile dal task dedicato
/// (non ha `&self`). Stesso schema di firma di `signed_body_post`.
async fn send_heartbeat(
    http: &reqwest::Client,
    server: &str,
    device_id: &str,
    id: &Identity,
) -> Result<()> {
    let profile = collect_profile();
    let body = HeartbeatRequest { device_id, profile };
    let bytes = serde_json::to_vec(&body)?;
    let sig = id.sign_b64(&bytes);
    let url = format!("{}/agent/heartbeat", server.trim_end_matches('/'));
    let resp = http
        .post(&url)
        .header(SIG_HEADER, sig)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(bytes)
        .send()
        .await?;
    if !resp.status().is_success() {
        bail!("heartbeat HTTP {}", resp.status());
    }
    Ok(())
}

/// Traduce l'output dell'executor (shape §2.6: entries | results) nel result
/// di rete (§6.3). `ok`/`entries`/`n_processed` derivano onestamente (§2.8).
/// Estrae il nome del modulo mancante da uno stderr Python
/// («ModuleNotFoundError: No module named 'X'»). None se non è quel caso —
/// così l'auto-guarigione scatta SOLO su modulo assente (import al caricamento),
/// non su altri errori di import a esecuzione avviata.
fn missing_module(stderr: &str) -> Option<String> {
    let marker = "No module named '";
    let start = stderr.find(marker)? + marker.len();
    let rest = &stderr[start..];
    let end = rest.find('\'')?;
    Some(rest[..end].to_string())
}

fn result_from_executor(
    inv: &Invocation,
    device_id: &str,
    parsed: Value,
    elapsed_ms: i64,
    sandbox: String,
) -> InvocationResult {
    let ok = parsed.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
    let entries = parsed
        .get("entries")
        .or_else(|| parsed.get("results"))
        .cloned()
        .unwrap_or_else(|| json!([]));
    let n_processed = parsed
        .get("ok_count")
        .or_else(|| parsed.get("n_processed"))
        .and_then(|v| v.as_i64())
        .unwrap_or_else(|| entries.as_array().map(|a| a.len() as i64).unwrap_or(0));
    let error = parsed.get("error").and_then(|v| v.as_str()).map(String::from);
    let error_class = parsed.get("error_class").and_then(|v| v.as_str()).map(String::from);
    InvocationResult {
        invocation_id: inv.invocation_id.clone(),
        device_id: device_id.to_string(),
        ok,
        entries,
        n_processed,
        elapsed_ms,
        sandbox,
        error,
        error_class,
        payload: parsed, // output COMPLETO: il runtime lo consuma come locale
    }
}

/// Profilo carico per il placement L2 (§10). Solo interi (canonical JSON).
fn collect_profile() -> Value {
    let ncpu = std::thread::available_parallelism().map(|n| n.get() as i64).unwrap_or(1);
    json!({
        "cpu_count": ncpu,
        "os_family": std::env::consts::OS,
        "os_arch": std::env::consts::ARCH,
    })
}

// --- spool dei result in attesa di consegna (§12) ---------------------------

fn results_dir(paths: &Paths) -> PathBuf {
    paths.spool_dir.join("results")
}

/// invocation_id dei result presenti nello spool (crash-safe: sopravvivono al
/// riavvio del client → contano come "già eseguiti").
fn pending_result_ids(paths: &Paths) -> HashSet<String> {
    let dir = results_dir(paths);
    let mut out = HashSet::new();
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            if let Some(name) = e.file_name().to_str() {
                if let Some(id) = name.strip_suffix(".json") {
                    out.insert(id.to_string());
                }
            }
        }
    }
    out
}

/// GC dello spool: elimina result (e .tmp orfani) piu' vecchi della
/// retention (`METNOS_SPOOL_RETENTION_DAYS`, default 14). Oltre quella
/// finestra il server ha da tempo chiuso il turno con timeout onesto:
/// ri-consegnarli non osserva piu' nulla. Ritorna il numero di file rimossi.
fn prune_stale_spool(paths: &Paths) -> usize {
    let days: u64 = std::env::var("METNOS_SPOOL_RETENTION_DAYS")
        .ok().and_then(|v| v.parse().ok()).unwrap_or(14);
    let max_age = std::time::Duration::from_secs(days * 86400);
    let mut removed = 0;
    if let Ok(entries) = std::fs::read_dir(results_dir(paths)) {
        for e in entries.flatten() {
            let stale = e.metadata().and_then(|m| m.modified()).ok()
                .and_then(|t| t.elapsed().ok())
                .map(|age| age > max_age)
                .unwrap_or(false);
            if stale && std::fs::remove_file(e.path()).is_ok() {
                tracing::warn!(file = %e.path().display(),
                               retention_days = days,
                               "result stale rimosso dallo spool");
                removed += 1;
            }
        }
    }
    removed
}

/// Scrive il body del result nello spool in modo atomico (tmp + rename).
fn write_pending_result(paths: &Paths, invocation_id: &str, body: &[u8]) -> Result<()> {
    let dir = results_dir(paths);
    std::fs::create_dir_all(&dir)?;
    let final_path = dir.join(format!("{invocation_id}.json"));
    let tmp = dir.join(format!("{invocation_id}.json.tmp"));
    std::fs::write(&tmp, body)?;
    std::fs::rename(&tmp, &final_path).context("rename result spool")?;
    Ok(())
}
