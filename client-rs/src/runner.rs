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
use std::collections::{HashSet, VecDeque};
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
// B.3 (fase 7): cap del backoff su errore di poll. 60s = un server in
// manutenzione lunga non riceve piu' di un tentativo al minuto per device.
const BACKOFF_MAX: Duration = Duration::from_secs(60);
// B.1 (fase 7): tetto del set dedup locale. Il dedup PRIMARIO e' server-side
// (idempotenza per invocation_id): dimenticare gli id piu' vecchi non
// rischia un doppio side-effect, evita solo un giro di rete.
const EXECUTED_CAP: usize = 4096;

/// Set con ordine di inserimento e capienza fissa (B.1): prima era un
/// HashSet illimitato — un daemon che vive settimane cresceva senza tetto.
/// Gli invocation_id sono monotoni (time-ordered), quindi eviction FIFO =
/// eviction dei piu' vecchi.
struct BoundedSet {
    set: HashSet<String>,
    order: VecDeque<String>,
    cap: usize,
}

impl BoundedSet {
    fn new(cap: usize) -> Self {
        Self { set: HashSet::new(), order: VecDeque::new(), cap }
    }

    fn contains(&self, id: &str) -> bool {
        self.set.contains(id)
    }

    fn insert(&mut self, id: String) {
        if !self.set.insert(id.clone()) {
            return; // gia' presente: l'ordine originale resta valido
        }
        self.order.push_back(id);
        while self.order.len() > self.cap {
            if let Some(old) = self.order.pop_front() {
                self.set.remove(&old);
            }
        }
    }
}

pub struct Runner {
    server: String,
    device_id: String,
    server_pubkey: String,
    id: Identity,
    paths: Paths,
    http: reqwest::Client,
    /// invocation_id già eseguiti in questo processo: non ri-eseguire (§6.4).
    /// Bounded (B.1): oltre EXECUTED_CAP dimentica i più vecchi — il dedup
    /// vero resta l'idempotenza server per invocation_id.
    executed: BoundedSet,
    capabilities: Vec<String>,
    /// Cache per-processo: lo shim (executor_helpers+messages) e l'interprete
    /// python si risolvono UNA volta, non ad ogni execute. Content-addressing
    /// (0.2.15): `shim_sha` = sha del bundle CARICATO; il poll annuncia lo
    /// sha corrente del server e su drift lo shim viene invalidato (fix ai
    /// moduli runtime raggiungono i device senza restart del daemon).
    shim_dir: Option<PathBuf>,
    shim_sha: Option<String>,
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
        let mut executed = BoundedSet::new(EXECUTED_CAP);
        for id in pending_result_ids(&paths) {
            executed.insert(id);
        }
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
            shim_sha: None,
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
        // GC blob undo (task #6): stessa filosofia dello spool GC.
        let blobs_pruned = prune_history_blobs(&self.paths);
        if blobs_pruned > 0 {
            tracing::warn!(turns = blobs_pruned, "history: blob undo stale rimossi (oltre retention)");
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

        // Self-update: conferma la probation al PRIMO poll riuscito (il binario
        // in prova ha raggiunto il server → funziona). Prima di allora un
        // crash/uscita farebbe rollback al known-good (apply_startup_recovery).
        let upd_marker = crate::selfupdate::marker_path(&self.paths.data_dir);
        let self_exe = std::env::current_exe().ok();
        let mut update_confirmed = false;

        loop {
            // Ri-consegna i result rimasti nello spool (server tornato su).
            self.flush_pending().await;

            match self.poll(cursor.as_deref()).await {
                Ok(Some(inv)) => {
                    backoff = Duration::from_secs(1);
                    if !update_confirmed {
                        if let Some(e) = &self_exe {
                            crate::selfupdate::confirm_running(&upd_marker, e);
                        }
                        update_confirmed = true;
                    }
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
                    if !update_confirmed {
                        if let Some(e) = &self_exe {
                            crate::selfupdate::confirm_running(&upd_marker, e);
                        }
                        update_confirmed = true;
                    }
                }
                Err(e) => {
                    // B.3: jitter sul backoff — N client che perdono il server
                    // nello stesso istante non devono ritentare in fase
                    // (assalto sincrono al suo ritorno).
                    let pause = with_jitter(backoff);
                    tracing::warn!("poll fallito (server giu'?): {e:#}; ritento fra {:?}", pause);
                    tokio::time::sleep(pause).await;
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

    async fn poll(&mut self, cursor: Option<&str>) -> Result<Option<Invocation>> {
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
        // Self-update ROBUSTO: su mismatch scarica il descrittore FIRMATO,
        // verifica con la pubkey pinnata, swap, scrive il marker probation e
        // ESCE (exit_for_update). NIENTE respawn (BUG-A): rilancia il supervisor.
        // Idempotente per sha: nessun loop se il binario e' gia' quello pubblicato.
        if let Some(v) = &parsed.server_client_version {
            if v.as_str() != env!("CARGO_PKG_VERSION") {
                let marker = crate::selfupdate::marker_path(&self.paths.data_dir);
                match crate::selfupdate::maybe_update(&self.server, &self.server_pubkey, &marker).await {
                    Ok(true) => crate::selfupdate::exit_for_update(),
                    Ok(false) => {}
                    Err(e) => tracing::warn!("self-update fallito (riprovo al prossimo poll): {:#}", e),
                }
            }
        }
        // Content-addressing shim (0.2.15): il server annuncia lo sha del
        // bundle runtime corrente; se differisce da quello CARICATO, invalida
        // la cache per-processo -> il prossimo execute ri-scarica lo shim
        // fresco (i fix runtime arrivano ai device senza restart del daemon).
        if let Some(server_sha) = parsed.shim_sha256.as_deref() {
            if !server_sha.is_empty() {
                if let Some(loaded) = self.shim_sha.as_deref() {
                    if loaded != server_sha {
                        tracing::info!(
                            server = &server_sha[..12.min(server_sha.len())],
                            caricato = &loaded[..12.min(loaded.len())],
                            "shim drift: invalido la cache, re-pull al prossimo execute"
                        );
                        self.shim_dir = None;
                        self.shim_sha = None;
                    }
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
        // Ultima rete del worker: il timeout primario vive nel sandbox e
        // termina l'albero dell'executor. Le API Win32 usate per costruire un
        // AppContainer e drenare le pipe sono pero' bloccanti; se una di esse
        // non ritorna, il future resta appeso, il client continua a mandare
        // heartbeat ma non torna piu' a /agent/poll. Fail-stop dopo un piccolo
        // margine oltre la deadline: l'uscita del processo chiude il Job Object
        // (kill-on-close) e il launcher Windows lo riavvia in ~2 s. Non
        // continuiamo nello stesso processo perche' una task spawn_blocking non
        // e' cancellabile in sicurezza, soprattutto per executor mutanti.
        const WATCHDOG_GRACE: Duration = Duration::from_secs(10);
        let watchdog_wait = Duration::from_millis(inv.deadline_ms.max(1000))
            .saturating_add(WATCHDOG_GRACE);
        let watchdog_inv = inv.invocation_id.clone();
        let watchdog = tokio::spawn(async move {
            tokio::time::sleep(watchdog_wait).await;
            tracing::error!(
                invocation = %watchdog_inv,
                deadline_ms = watchdog_wait.as_millis(),
                "watchdog executor: sandbox non rientrato; fail-stop del client"
            );
            std::process::exit(124);
        });
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
                sandbox_downgrade_reason: None,
                error: Some(format!("{e:#}")),
                error_class: Some("device_error".into()),
                payload: json!({}),
            },
        };
        watchdog.abort();

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

        // Shim: content-addressed dal 0.2.15 — il poll annuncia lo sha del
        // bundle server; su drift `handle_poll` invalida shim_dir e qui si
        // ri-scarica. L'auto-guarigione su import fallito (sotto) resta come
        // rete per i server vecchi che non annunciano lo sha.
        if self.shim_dir.is_none() {
            let (dir, sha) =
                executors::ensure_shim(&self.server, &self.server_pubkey, &self.paths.cache_dir)
                    .await?;
            self.shim_dir = Some(dir);
            self.shim_sha = if sha.is_empty() { None } else { Some(sha) };
        }
        if self.python.is_none() {
            let env = pyenv::resolve(&self.server, &self.paths.cache_dir).await?;
            tracing::info!(python = %env.python.display(), source = %env.source, "interprete risolto (cache)");
            self.python = Some(env.python);
        }
        let python = self.python.clone().unwrap();
        tracing::info!(executor = %inv.executor, "esecuzione");

        let args_json = serde_json::to_string(&inv.args)?;
        let mut extra_env: Vec<(String, String)> =
            inv.env_injections.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        // Dir dati dello shim isolata e client-owned (§W4): config.py::ensure_dirs
        // ci crea a import l'albero user (DATA/STATE/CONFIG) e i blob undo ci
        // restano fra i turni. Senza il redirect lo shim toccherebbe
        // ~/.local/{share,state}/metnos e ~/.config/metnos, fuori dagli ACL del
        // container AppContainer → Access Denied. `data_dir` e' persistente (a
        // differenza dello scratch per-invocazione) quindi l'undo sopravvive.
        // Tutte e tre sotto `shimdata`: un solo grant sulla radice le copre.
        let shimdata = self.paths.data_dir.join("shimdata");
        if let Err(e) = std::fs::create_dir_all(&shimdata) {
            tracing::warn!(dir = %shimdata.display(), "creazione shimdata fallita: {e:#}");
        }
        extra_env.push(("METNOS_USER_DATA".into(), shimdata.display().to_string()));
        extra_env.push(("METNOS_USER_STATE".into(), shimdata.join("state").display().to_string()));
        extra_env.push(("METNOS_USER_CONFIG".into(), shimdata.join("config").display().to_string()));
        // PATH_WORKSPACE (mnestoma/scheduler DB) e' derivato dall'install-root,
        // NON da _home() → sfugge ai redirect USER_* sopra. Anch'esso sotto
        // shimdata: un solo grant sulla radice copre tutto l'albero creato da
        // ensure_dirs.
        extra_env.push(("METNOS_WORKSPACE".into(), shimdata.join("workspace").display().to_string()));
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
                    sandbox_downgrade_reason: out.downgrade_reason,
                    error: Some("deadline exceeded".into()),
                    error_class: Some("timeout".into()),
                    payload: json!({}),
                });
            }

            match serde_json::from_str::<Value>(out.stdout.trim()) {
                Ok(parsed) => {
                    return Ok(result_from_executor(
                        inv, &self.device_id, parsed, elapsed_ms,
                        out.sandbox, out.downgrade_reason));
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
                            let (dir, sha) = executors::ensure_shim(
                                &self.server, &self.server_pubkey, &self.paths.cache_dir,
                            )
                            .await?;
                            if dir.join(format!("{module}.py")).is_file() {
                                tracing::warn!(
                                    executor = %inv.executor, module = %module,
                                    "modulo shim mancante: shim stantio rigenerato, riprovo"
                                );
                                self.shim_dir = Some(dir);
                                self.shim_sha =
                                    if sha.is_empty() { None } else { Some(sha) };
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
    sandbox_downgrade_reason: Option<String>,
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
        sandbox_downgrade_reason,
        error,
        error_class,
        payload: parsed, // output COMPLETO: il runtime lo consuma come locale
    }
}

/// Jitter moltiplicativo in [0.75, 1.25) (B.3). Niente crate rand: i
/// nanosecondi del clock bastano come sorgente di rumore per de-fasare i
/// retry — non serve qualita' crittografica.
fn with_jitter(d: Duration) -> Duration {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|t| t.subsec_nanos())
        .unwrap_or(0);
    let factor = 0.75 + (nanos % 1000) as f64 / 2000.0;
    d.mul_f64(factor)
}

/// Profilo carico per il placement L2 (§10). Solo interi (canonical JSON).
fn collect_profile() -> Value {
    let ncpu = std::thread::available_parallelism().map(|n| n.get() as i64).unwrap_or(1);
    json!({
        "cpu_count": ncpu,
        "os_family": std::env::consts::OS,
        "os_arch": std::env::consts::ARCH,
        // Versione corrente del client: la UI la mostra per-device e permette
        // di vedere l'esito del self-update (ADR 0184) senza aprire il PC.
        "client_version": env!("CARGO_PKG_VERSION"),
        // B.5 (fase 7): livello sandbox che run_sandboxed userebbe ORA su
        // questo device -> devices.profile_json (telemetria per il gate
        // min_sandbox, W4).
        "sandbox_level": sandbox::sandbox_level(),
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
/// GC dei blob undo sul device (task #6 fase7): il backup pre-mutazione lo
/// scrive lo shim in `$METNOS_HISTORY_DIR/<turn>/blob/<sha>.bin`; col redirect
/// W4 `METNOS_HISTORY_DIR` cade sotto `<data_dir>/shimdata/_history` (default =
/// PATH_USER_DATA/_history, e PATH_USER_DATA=shimdata). Il reaper del server non
/// raggiunge il device → i blob si accumulerebbero. Qui potiamo per turno le
/// dir oltre la retention undo (`METNOS_HISTORY_RETENTION_DAYS`, default 30).
/// Onesto (§2.8): warn per-dir, mai silenzioso. Startup-only come lo spool GC —
/// il device si riavvia spesso (self-update); l'accumulo per-turno e' lento.
fn prune_history_blobs(paths: &Paths) -> usize {
    let days: u64 = std::env::var("METNOS_HISTORY_RETENTION_DAYS")
        .ok().and_then(|v| v.parse().ok()).unwrap_or(30);
    let max_age = std::time::Duration::from_secs(days * 86400);
    let root = paths.data_dir.join("shimdata").join("_history");
    let mut removed = 0;
    if let Ok(entries) = std::fs::read_dir(&root) {
        for e in entries.flatten() {
            // Ogni <turn> e' una directory; salta i file sciolti.
            if !e.path().is_dir() {
                continue;
            }
            let stale = e.metadata().and_then(|m| m.modified()).ok()
                .and_then(|t| t.elapsed().ok())
                .map(|age| age > max_age)
                .unwrap_or(false);
            if stale && std::fs::remove_dir_all(e.path()).is_ok() {
                tracing::warn!(turn = %e.path().display(),
                               retention_days = days,
                               "blob undo stale rimossi dal device");
                removed += 1;
            }
        }
    }
    removed
}

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_set_evicts_oldest_fifo() {
        let mut s = BoundedSet::new(3);
        for id in ["a", "b", "c"] {
            s.insert(id.to_string());
        }
        assert!(s.contains("a") && s.contains("b") && s.contains("c"));
        s.insert("d".to_string()); // evince "a" (il piu' vecchio)
        assert!(!s.contains("a"), "l'id piu' vecchio va sfrattato");
        assert!(s.contains("b") && s.contains("c") && s.contains("d"));
    }

    #[test]
    fn bounded_set_duplicate_insert_no_growth() {
        // Un id gia' presente non fa crescere la coda ne' cambia l'ordine di
        // eviction (dedup §6.4: ri-consegnare non e' ri-eseguire).
        let mut s = BoundedSet::new(2);
        s.insert("a".to_string());
        s.insert("a".to_string());
        s.insert("b".to_string());
        s.insert("c".to_string()); // evince "a", NON "b"
        assert!(!s.contains("a"));
        assert!(s.contains("b") && s.contains("c"));
    }

    #[test]
    fn with_jitter_stays_in_band() {
        let base = Duration::from_secs(8);
        for _ in 0..50 {
            let j = with_jitter(base);
            assert!(j >= base.mul_f64(0.75) && j < base.mul_f64(1.25),
                    "jitter fuori banda [0.75,1.25): {j:?}");
        }
    }
}
