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
use crate::{executors, pyenv, sandbox_linux};

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
        let mut backoff = Duration::from_secs(1);
        let mut last_heartbeat = Instant::now() - HEARTBEAT_EVERY;
        let mut cursor: Option<String> = None;

        loop {
            // Ri-consegna i result rimasti nello spool (server tornato su).
            self.flush_pending().await;

            if last_heartbeat.elapsed() >= HEARTBEAT_EVERY {
                if let Err(e) = self.heartbeat().await {
                    tracing::warn!("heartbeat fallito: {e:#}");
                }
                last_heartbeat = Instant::now();
            }

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
        // Auto-update §5.5: rilevazione qui, lo swap firmato del binario e' W4.
        if let Some(v) = &parsed.server_client_version {
            if v.as_str() != env!("CARGO_PKG_VERSION") {
                tracing::info!(
                    running = env!("CARGO_PKG_VERSION"), available = %v,
                    "client aggiornabile (self_update: W4, non ancora attivo)"
                );
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
        // Gate W3.0 (§16.1): su Windows NON esiste ancora un sandbox reale
        // (sandbox_windows.rs = W3.1). Fail-closed: rifiuta PRIMA di
        // scaricare/eseguire qualunque cosa, salvo opt-in esplicito
        // METNOS_SANDBOX=off. Su unix il degrade-con-warn resta (parità con
        // runtime/sandbox.py; bwrap può mancare ed è il comportamento in
        // esercizio su .33). Un device Windows appaiato è così SICURO perché
        // RIFIUTA, non «non operativo di fatto».
        #[cfg(windows)]
        if !sandbox_linux::sandbox_disabled() {
            tracing::error!(executor = %inv.executor,
                "esecuzione RIFIUTATA: nessun sandbox su Windows (arriva con \
                 W3.1); opt-in esplicito con METNOS_SANDBOX=off");
            return Ok(InvocationResult {
                invocation_id: inv.invocation_id.clone(),
                device_id: self.device_id.clone(),
                ok: false,
                entries: json!([]),
                n_processed: 0,
                elapsed_ms: 0,
                sandbox: "refused".into(),
                error: Some("nessun sandbox disponibile su questo dispositivo \
                             (arriva con W3.1); opt-in: METNOS_SANDBOX=off".into()),
                error_class: Some("sandbox_unavailable".into()),
            });
        }

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

        // Shim + interprete: risolti UNA volta per processo (cache).
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
        let shim = self.shim_dir.clone().unwrap();
        let python = self.python.clone().unwrap();
        tracing::info!(executor = %inv.executor, "esecuzione");

        let args_json = serde_json::to_string(&inv.args)?;
        let extra_env: Vec<(String, String)> =
            inv.env_injections.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let limits = sandbox_linux::Limits {
            wall: Duration::from_millis(inv.deadline_ms.max(1000)),
        };
        let start = Instant::now();
        let out = sandbox_linux::run_sandboxed(
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
            });
        }

        let parsed: Value = serde_json::from_str(out.stdout.trim()).map_err(|e| {
            anyhow::anyhow!(
                "output executor non-JSON: {e}; stdout={:?} stderr={:?}",
                out.stdout,
                out.stderr
            )
        })?;
        Ok(result_from_executor(inv, &self.device_id, parsed, elapsed_ms, out.sandbox))
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

    async fn heartbeat(&self) -> Result<()> {
        let profile = collect_profile();
        let body = HeartbeatRequest { device_id: &self.device_id, profile };
        let value = serde_json::to_value(&body)?;
        let resp = self.signed_post("/agent/heartbeat", &value)?.send().await?;
        if !resp.status().is_success() {
            bail!("heartbeat HTTP {}", resp.status());
        }
        Ok(())
    }
}

/// Traduce l'output dell'executor (shape §2.6: entries | results) nel result
/// di rete (§6.3). `ok`/`entries`/`n_processed` derivano onestamente (§2.8).
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
