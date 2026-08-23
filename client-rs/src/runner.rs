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
use serde::{Deserialize, Serialize};
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
// A live device must return to the work queue before an ordinary remote turn
// expires. Jitter makes the effective ceiling 12.5 s, while still avoiding a
// reconnect storm when the server is unavailable.
const BACKOFF_MAX: Duration = Duration::from_secs(10);
// B.1 (fase 7): tetto del set dedup locale. Il dedup PRIMARIO e' server-side
// (idempotenza per invocation_id): dimenticare gli id piu' vecchi non
// rischia un doppio side-effect, evita solo un giro di rete.
const EXECUTED_CAP: usize = 100_000;
const INVOCATION_MAX_AGE: Duration = Duration::from_secs(48 * 3600);
const INVOCATION_MAX_FUTURE: Duration = Duration::from_secs(5 * 60);

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
        Self {
            set: HashSet::new(),
            order: VecDeque::new(),
            cap,
        }
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
    /// La pulizia ACL in corso, finche' non e' stata attesa.
    #[cfg(windows)]
    pulizia_acl: Option<tokio::task::JoinHandle<Result<crate::appcontainer::CleanupReport>>>,
    /// Perche' la pulizia ACL non e' riuscita, se non e' riuscita.
    ///
    /// Si conserva: un fallimento vale per OGNI esecuzione successiva, non
    /// solo per la prima che l'ha scoperto.
    #[cfg(windows)]
    acl_errore: Option<String>,
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
        // Revoca fail-closed degli ACL lasciati da un giro precedente finito
        // male, prima di riusare il SID AppContainer stabile.
        //
        // Parte in disparte e non davanti alla rete. Il vincolo da rispettare
        // e' «nessun executor gira con permessi vecchi addosso», e quel
        // vincolo riguarda l'ESECUZIONE, non il collegamento: metterlo prima
        // del primo contatto col server significava che un debito arretrato
        // rendeva il computer invisibile per minuti — e, peggio, che un
        // client appena aggiornato non faceva in tempo a confermarsi e veniva
        // riportato indietro. Misurato dal vivo il 18/8/2026: 75 cartelle da
        // ripulire, fra cui Documenti e Download, oltre venti minuti, e
        // l'aggiornamento annullato per questo.
        //
        // L'attesa e' spostata dove il vincolo vive davvero: `execute`.
        #[cfg(windows)]
        let pulizia_acl = Some(tokio::task::spawn_blocking(
            crate::appcontainer::cleanup_all_grants,
        ));
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(POLL_BLOCK_MS / 1000 + 15))
            .build()?;
        // Ledger persistente: chiude il replay di una risposta poll firmata
        // catturata prima del restart. L'invocation_id incorpora inoltre il
        // timestamp server e ha una finestra assoluta verificata in handle().
        let mut executed = BoundedSet::new(EXECUTED_CAP);
        for id in load_executed_ledger(&paths)? {
            executed.insert(id);
        }
        // I result non ancora consegnati (crash precedente) contano come
        // "già eseguiti": non ri-eseguire, solo ri-consegnare.
        for id in pending_result_ids(&paths) {
            executed.insert(id);
        }
        // Recupera il confine write-ahead prima di accettare nuovo lavoro.
        // I mutanti rimasti STARTED non vengono mai rieseguiti alla cieca.
        for id in recover_started(&paths, &device_id) {
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
            #[cfg(windows)]
            pulizia_acl,
            #[cfg(windows)]
            acl_errore: None,
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
            tracing::warn!(
                turns = blobs_pruned,
                "history: blob undo stale rimossi (oltre retention)"
            );
        }
        // Heartbeat su task tokio SEPARATO (§B5): il loop principale si blocca
        // per decine di secondi durante il primo download+estrazione del runtime
        // python (pyenv::resolve) e durante l'esecuzione di un executor lungo.
        // Con l'heartbeat inline il device appariva «offline» per ~1min al primo
        // giro. Un task dedicato batte ogni HEARTBEAT_EVERY a prescindere da cosa
        // fa il loop di poll/execute. Runtime multi-thread (tokio full) → i due
        // task girano davvero in parallelo anche se l'estrazione occupa un worker.
        spawn_heartbeat(
            self.http.clone(),
            self.server.clone(),
            self.device_id.clone(),
            self.id.clone(),
        );

        // Verify and rebuild the signed Python runtime before accepting work.
        // Doing this lazily inside the first invocation made a healthy device
        // consume that invocation's watchdog budget after every client update.
        // A failure remains non-fatal here: execute() retries the same verified
        // resolution and reports the real error through the invocation result.
        match pyenv::resolve(&self.server, &self.server_pubkey, &self.paths.cache_dir).await {
            Ok(env) => {
                tracing::info!(python = %env.python.display(), source = %env.source,
                               "signed Python runtime ready before polling");
                self.python = Some(env.python);
            }
            Err(e) => {
                tracing::warn!("Python runtime preflight failed; first execution will retry: {e:#}")
            }
        }

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
                    tracing::warn!(
                        "poll fallito (server giu'?): {e:#}; ritento fra {:?}",
                        pause
                    );
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
                match crate::selfupdate::maybe_update(&self.server, &self.server_pubkey, &marker)
                    .await
                {
                    Ok(true) => crate::selfupdate::exit_for_update(),
                    Ok(false) => {}
                    Err(e) => {
                        tracing::warn!("self-update fallito (riprovo al prossimo poll): {:#}", e)
                    }
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
        if let Err(e) = validate_invocation_freshness(&inv.invocation_id) {
            tracing::error!(invocation = %inv.invocation_id,
                            "invocazione firmata ma stale/malformata: RIFIUTO ({e:#})");
            return Ok(());
        }

        // Persistito e sincronizzato PRIMA di entrare nel sandbox: un crash
        // successivo non può far apparire ineseguita una possibile mutazione.
        write_started(&self.paths, &inv)?;

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
        let watchdog_wait =
            Duration::from_millis(inv.deadline_ms.max(1000)).saturating_add(WATCHDOG_GRACE);
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
        record_executed(&self.paths, &inv.invocation_id)?;
        remove_started(&self.paths, &inv.invocation_id);
        self.executed.insert(inv.invocation_id.clone());

        // 3. Prova la consegna (idempotente lato server); l'esito è gestito da
        //    flush_pending — un fallimento lascia il result nello spool.
        self.flush_pending().await;
        Ok(())
    }

    /// Nessun executor gira con addosso permessi lasciati da un giro
    /// precedente. Il vincolo e' questo, e questo e' il punto in cui vale.
    ///
    /// Aspetta la pulizia partita all'avvio. Di norma e' finita da un pezzo e
    /// non costa niente; quando c'e' un debito arretrato, si aspetta qui —
    /// dove ferma UN'esecuzione — invece che all'avvio, dove fermava il
    /// collegamento al server e faceva sembrare spento il computer.
    ///
    /// Fallire e' definitivo: se i permessi vecchi non si sono potuti
    /// togliere, non si esegue niente, adesso e da adesso in poi.
    #[cfg(windows)]
    async fn attendi_pulizia_acl(&mut self) -> Result<()> {
        if let Some(attesa) = self.pulizia_acl.take() {
            // L'esito si SCRIVE sempre, prima di uscire. Qui c'era il
            // difetto: se il compito cadeva (panico, runtime in chiusura),
            // l'errore usciva con `?` prima di essere registrato — e siccome
            // l'attesa era gia' stata consumata, l'esecuzione SUCCESSIVA non
            // trovava nulla da aspettare ne' nulla da rimproverare, e
            // proseguiva. Fallire apriva la porta invece di chiuderla, che e'
            // l'esatto contrario di cio' che questa funzione promette
            // (trovato dalla revisione, 19/8/2026).
            self.acl_errore = match attesa.await {
                Ok(Ok(r)) if r.failed == 0 => {
                    tracing::info!(
                        revocati = r.revoked,
                        scartati = r.dropped,
                        "pulizia ACL completata"
                    );
                    None
                }
                Ok(Ok(r)) => Some(format!(
                    "{} permessi della sandbox non si sono potuti togliere, e \
finche' restano non eseguo niente su questo computer: {}",
                    r.failed,
                    r.failed_paths.join(" · ")
                )),
                Ok(Err(e)) => Some(format!("pulizia ACL non riuscita: {e:#}")),
                Err(e) => Some(format!("pulizia ACL: il compito e' caduto: {e}")),
            };
        }
        match &self.acl_errore {
            Some(motivo) => bail!("{motivo}"),
            None => Ok(()),
        }
    }

    async fn execute(&mut self, inv: &Invocation) -> Result<InvocationResult> {
        #[cfg(windows)]
        self.attendi_pulizia_acl().await?;

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
        if inv.operation == "reverse" && !exec.module_reverse {
            bail!("il manifest firmato non autorizza module.reverse");
        }

        #[cfg(windows)]
        let managed_provider_json = self.collect_managed_providers(inv, &exec).await?;

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
            let env =
                pyenv::resolve(&self.server, &self.server_pubkey, &self.paths.cache_dir).await?;
            tracing::info!(python = %env.python.display(), source = %env.source, "interprete risolto (cache)");
            self.python = Some(env.python);
        }
        let python = self.python.clone().unwrap();
        tracing::info!(executor = %inv.executor, "esecuzione");

        let args_json = serde_json::to_string(&inv.args)?;
        let mut extra_env: Vec<(String, String)> = inv
            .env_injections
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        // Runtime-owned and derived only from the signed wire field.  It is
        // appended after env_injections so an injected value cannot change
        // which entrypoint run_stdio dispatches.
        extra_env.push(("METNOS_EXECUTOR_OPERATION".into(), inv.operation.clone()));
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
        extra_env.push((
            "METNOS_USER_STATE".into(),
            shimdata.join("state").display().to_string(),
        ));
        extra_env.push((
            "METNOS_USER_CONFIG".into(),
            shimdata.join("config").display().to_string(),
        ));
        // PATH_WORKSPACE (mnestoma/scheduler DB) e' derivato dall'install-root,
        // NON da _home() → sfugge ai redirect USER_* sopra. Anch'esso sotto
        // shimdata: un solo grant sulla radice copre tutto l'albero creato da
        // ensure_dirs.
        extra_env.push((
            "METNOS_WORKSPACE".into(),
            shimdata.join("workspace").display().to_string(),
        ));
        // Il percorso di QUESTO binario. Serve agli executor che devono
        // parlare con l'aiutante elevato di Windows (ADR 0210 D): il giudizio
        // su chi c'e' dall'altro capo del canale sta in Rust, in un posto
        // solo, e chi ne ha bisogno lo chiede qui invece di riscriverlo.
        // Un secondo esemplare di un controllo di sicurezza e' quello che
        // diverge.
        if let Ok(exe) = std::env::current_exe() {
            extra_env.push(("METNOS_CLIENT_EXE".into(), exe.display().to_string()));
        }
        #[cfg(windows)]
        if let Some(value) = managed_provider_json {
            extra_env.push(("METNOS_MANAGED_PROVIDER_RESULTS".into(), value));
        }
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
            let out =
                sandbox::run_sandboxed(&exec, &python, &shim, &args_json, &extra_env, &limits)
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
                        inv,
                        &self.device_id,
                        parsed,
                        elapsed_ms,
                        out.sandbox,
                        out.downgrade_reason,
                    ));
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
                                &self.server,
                                &self.server_pubkey,
                                &self.paths.cache_dir,
                            )
                            .await?;
                            if dir.join(format!("{module}.py")).is_file() {
                                tracing::warn!(
                                    executor = %inv.executor, module = %module,
                                    "modulo shim mancante: shim stantio rigenerato, riprovo"
                                );
                                self.shim_dir = Some(dir);
                                self.shim_sha = if sha.is_empty() { None } else { Some(sha) };
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

    #[cfg(windows)]
    async fn collect_managed_providers(
        &self,
        inv: &Invocation,
        exec: &executors::CachedExecutor,
    ) -> Result<Option<String>> {
        use std::collections::{BTreeMap, HashSet};

        let mut requested = Vec::new();
        for dependency in &exec.managed_providers {
            if let Some(selection) = executors::managed_provider_selection(&inv.args, dependency)? {
                requested.push((dependency, selection));
            }
        }
        if requested.is_empty() {
            if !inv.managed_provider_grants.is_empty() {
                bail!("provider grant present without a requested dependency");
            }
            return Ok(None);
        }
        if requested.len() != inv.managed_provider_grants.len() {
            bail!("managed provider grant count does not match the signed manifest");
        }

        let mut seen = HashSet::new();
        let mut output = BTreeMap::new();
        for (dependency, (domains, sensor_types)) in requested {
            let matches: Vec<_> = inv
                .managed_provider_grants
                .iter()
                .filter(|grant| {
                    grant.invocation_id == inv.invocation_id
                        && grant.manifest_sha256 == inv.manifest_sha256
                        && grant.source == "winget"
                        && grant.dependency_key == dependency.key
                        && grant.package_id == dependency.package_id
                        && grant.interface == dependency.interface
                        && grant.assembly == dependency.assembly
                        && grant.entry_type == dependency.entry_type
                        && grant.domains == domains
                        && grant.sensor_types == sensor_types
                })
                .collect();
            if matches.len() != 1 || !seen.insert(dependency.key.clone()) {
                bail!("managed provider grant does not match the signed manifest");
            }
            let grant = (*matches[0]).clone();
            let identity = self.id.clone();
            let response =
                tokio::task::spawn_blocking(move || crate::run_managed_provider(&grant, &identity))
                    .await
                    .context("managed provider worker failed")?;
            output.insert(dependency.key.clone(), response);
        }
        let encoded = serde_json::to_string(&output)?;
        if encoded.len() > 16 * 1024 {
            bail!("managed provider result exceeds the executor environment limit");
        }
        Ok(Some(encoded))
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
                    let status = resp.status();
                    tracing::warn!(invocation = %inv_id, "result rifiutato: HTTP {}", status);
                    // 408/423/425/429 sono temporanei. Gli altri 4xx sono
                    // terminali ma restano in dead-letter: mai perdita muta.
                    if status.is_client_error() && !retryable_result_status(status) {
                        if let Err(e) =
                            move_to_dead_letter(&self.paths, &path, &inv_id, status.as_u16())
                        {
                            tracing::warn!(invocation = %inv_id,
                                "dead-letter fallita, mantengo nello spool: {e:#}");
                        }
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
    let error = parsed
        .get("error")
        .and_then(|v| v.as_str())
        .map(String::from);
    let error_class = parsed
        .get("error_class")
        .and_then(|v| v.as_str())
        .map(String::from);
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
    let ncpu = std::thread::available_parallelism()
        .map(|n| n.get() as i64)
        .unwrap_or(1);
    json!({
        "cpu_count": ncpu,
        "os_family": std::env::consts::OS,
        "os_arch": std::env::consts::ARCH,
        // Versione corrente del client: la UI la mostra per-device e permette
        // di vedere l'esito del self-update (ADR 0184) senza aprire il PC.
        "client_version": env!("CARGO_PKG_VERSION"),
        // Positive protocol negotiation: the server never infers support for
        // a new signed field from the OS or from a version string.
        "protocol_capabilities": ["executor_reverse_v1"],
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

fn started_dir(paths: &Paths) -> PathBuf {
    paths.spool_dir.join("started")
}

fn dead_results_dir(paths: &Paths) -> PathBuf {
    paths.spool_dir.join("dead-results")
}

fn executed_ledger_path(paths: &Paths) -> PathBuf {
    paths.spool_dir.join("executed.log")
}

fn safe_invocation_id(id: &str) -> Result<()> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        bail!("invocation_id non sicuro per spool: {:?}", id);
    }
    Ok(())
}

fn invocation_epoch(id: &str) -> Result<std::time::SystemTime> {
    safe_invocation_id(id)?;
    if id.len() != 28 || !id.starts_with("inv-") {
        bail!("formato invocation_id inatteso");
    }
    let nanos =
        u64::from_str_radix(&id[4..20], 16).context("timestamp invocation_id non valido")?;
    Ok(std::time::UNIX_EPOCH + Duration::from_nanos(nanos))
}

fn validate_invocation_freshness(id: &str) -> Result<()> {
    let issued = invocation_epoch(id)?;
    let now = std::time::SystemTime::now();
    if now
        .duration_since(issued)
        .is_ok_and(|age| age > INVOCATION_MAX_AGE)
    {
        bail!("invocazione oltre la finestra di 48 ore");
    }
    if issued
        .duration_since(now)
        .is_ok_and(|lead| lead > INVOCATION_MAX_FUTURE)
    {
        bail!("invocazione troppo nel futuro");
    }
    Ok(())
}

fn load_executed_ledger(paths: &Paths) -> Result<Vec<String>> {
    let path = executed_ledger_path(paths);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Ok(Vec::new());
    };
    let mut ids = Vec::new();
    for (index, raw) in text.lines().enumerate() {
        let id = raw.trim();
        if id.is_empty() {
            continue;
        }
        invocation_epoch(id)
            .with_context(|| format!("ledger replay corrotto alla riga {}", index + 1))?;
        if !ids.iter().any(|known| known == id) {
            ids.push(id.to_string());
        }
    }
    if ids.len() > EXECUTED_CAP {
        ids.drain(..ids.len() - EXECUTED_CAP);
    }
    Ok(ids)
}

fn record_executed(paths: &Paths, id: &str) -> Result<()> {
    invocation_epoch(id)?;
    std::fs::create_dir_all(&paths.spool_dir)?;
    use std::io::Write as _;
    let path = executed_ledger_path(paths);
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    writeln!(file, "{id}")?;
    file.sync_all()?;
    Ok(())
}

#[derive(Debug, Serialize, Deserialize)]
struct StartedRecord {
    invocation_id: String,
    reversibility: String,
    started_epoch_ms: u128,
}

fn sync_parent(path: &std::path::Path) {
    if let Some(parent) = path.parent() {
        if let Ok(dir) = std::fs::File::open(parent) {
            let _ = dir.sync_all();
        }
    }
}

fn atomic_write(path: &std::path::Path, body: &[u8]) -> Result<()> {
    let parent = path.parent().context("path atomico senza parent")?;
    std::fs::create_dir_all(parent)?;
    let tmp = path.with_extension("tmp");
    let mut file =
        std::fs::File::create(&tmp).with_context(|| format!("creazione {}", tmp.display()))?;
    use std::io::Write as _;
    file.write_all(body)?;
    file.sync_all()?;
    drop(file);
    std::fs::rename(&tmp, path).with_context(|| format!("rename atomico {}", path.display()))?;
    sync_parent(path);
    Ok(())
}

fn write_started(paths: &Paths, inv: &Invocation) -> Result<()> {
    safe_invocation_id(&inv.invocation_id)?;
    let record = StartedRecord {
        invocation_id: inv.invocation_id.clone(),
        reversibility: inv.reversibility.clone(),
        started_epoch_ms: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
    };
    let path = started_dir(paths).join(format!("{}.json", inv.invocation_id));
    atomic_write(&path, &serde_json::to_vec(&record)?)
}

fn remove_started(paths: &Paths, invocation_id: &str) {
    if safe_invocation_id(invocation_id).is_err() {
        return;
    }
    let path = started_dir(paths).join(format!("{invocation_id}.json"));
    if std::fs::remove_file(&path).is_ok() {
        sync_parent(&path);
    }
}

/// Recupera marker lasciati da un crash. I read-only possono essere
/// riconsegnati; per ogni altra classe accoda un esito incerto e deduplica.
fn recover_started(paths: &Paths, device_id: &str) -> Vec<String> {
    let mut recovered = Vec::new();
    let Ok(entries) = std::fs::read_dir(started_dir(paths)) else {
        return recovered;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let fallback_id = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        let parsed = std::fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice::<StartedRecord>(&b).ok());
        let id = parsed
            .as_ref()
            .map(|r| r.invocation_id.as_str())
            .unwrap_or(fallback_id);
        if safe_invocation_id(id).is_err() {
            tracing::error!(file = %path.display(),
                "marker STARTED corrotto/non sicuro: preservato");
            continue;
        }
        if results_dir(paths).join(format!("{id}.json")).is_file() {
            remove_started(paths, id);
            recovered.push(id.to_string());
            continue;
        }
        let read_only = parsed
            .as_ref()
            .map(|r| r.reversibility == "read_only")
            .unwrap_or(false);
        if read_only {
            tracing::warn!(invocation = %id,
                "STARTED read-only dopo crash: riconsegna sicura abilitata");
            remove_started(paths, id);
            continue;
        }

        let result = InvocationResult {
            invocation_id: id.to_string(),
            device_id: device_id.to_string(),
            ok: false,
            entries: json!([]),
            n_processed: 0,
            elapsed_ms: 0,
            sandbox: "unknown_after_restart".into(),
            sandbox_downgrade_reason: None,
            error: Some("client riavviato dopo STARTED: effetto non rieseguito".into()),
            error_class: Some("execution_outcome_unknown".into()),
            payload: json!({
                "effect_status": "unknown",
                "recovery": "write_ahead_no_reexecution"
            }),
        };
        let write_result = serde_json::to_vec(&result.body_value())
            .map_err(anyhow::Error::from)
            .and_then(|body| write_pending_result(paths, id, &body));
        match write_result {
            Ok(()) => {
                tracing::error!(invocation = %id,
                    "STARTED mutante dopo crash: esito incerto accodato, nessuna riesecuzione");
                remove_started(paths, id);
                recovered.push(id.to_string());
            }
            Err(e) => tracing::error!(invocation = %id,
                "recupero STARTED fallito, marker preservato: {e:#}"),
        }
    }
    recovered
}

fn retryable_result_status(status: reqwest::StatusCode) -> bool {
    matches!(status.as_u16(), 408 | 423 | 425 | 429)
}

fn move_to_dead_letter(
    paths: &Paths,
    source: &std::path::Path,
    invocation_id: &str,
    status: u16,
) -> Result<()> {
    safe_invocation_id(invocation_id)?;
    let dir = dead_results_dir(paths);
    std::fs::create_dir_all(&dir)?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let dest = dir.join(format!("{invocation_id}.http-{status}.{stamp}.json"));
    std::fs::rename(source, &dest).context("spostamento result in dead-letter")?;
    sync_parent(&dest);
    Ok(())
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
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(30);
    let max_age = std::time::Duration::from_secs(days * 86400);
    let root = paths.data_dir.join("shimdata").join("_history");
    let mut removed = 0;
    if let Ok(entries) = std::fs::read_dir(&root) {
        for e in entries.flatten() {
            // Ogni <turn> e' una directory; salta i file sciolti.
            if !e.path().is_dir() {
                continue;
            }
            let stale = e
                .metadata()
                .and_then(|m| m.modified())
                .ok()
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
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(14);
    let max_age = std::time::Duration::from_secs(days * 86400);
    let mut removed = 0;
    if let Ok(entries) = std::fs::read_dir(results_dir(paths)) {
        for e in entries.flatten() {
            let stale = e
                .metadata()
                .and_then(|m| m.modified())
                .ok()
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
    safe_invocation_id(invocation_id)?;
    let final_path = results_dir(paths).join(format!("{invocation_id}.json"));
    atomic_write(&final_path, body)
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
            assert!(
                j >= base.mul_f64(0.75) && j < base.mul_f64(1.25),
                "jitter fuori banda [0.75,1.25): {j:?}"
            );
        }
    }

    #[test]
    fn poll_backoff_stays_below_remote_turn_margin() {
        // The server currently gives an ordinary remote invocation 15 seconds
        // beyond its executor deadline. Even with positive jitter, a live
        // client must retry within that margin.
        assert!(BACKOFF_MAX.mul_f64(1.25) < Duration::from_secs(15));
    }

    #[test]
    fn transient_result_4xx_are_retried() {
        for code in [408, 423, 425, 429] {
            assert!(retryable_result_status(
                reqwest::StatusCode::from_u16(code).unwrap()
            ));
        }
        for code in [400, 401, 403, 404, 409, 410, 422] {
            assert!(!retryable_result_status(
                reqwest::StatusCode::from_u16(code).unwrap()
            ));
        }
    }

    #[test]
    fn spool_ids_reject_path_syntax() {
        assert!(safe_invocation_id("inv-123_ABC").is_ok());
        for bad in ["", "../x", "a/b", "a.b", "x\\y"] {
            assert!(safe_invocation_id(bad).is_err(), "accepted {bad:?}");
        }
    }

    #[test]
    fn invocation_ids_have_an_absolute_freshness_window() {
        fn id_at(time: std::time::SystemTime) -> String {
            let nanos = time
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            format!("inv-{nanos:016x}deadbeef")
        }
        let now = std::time::SystemTime::now();
        assert!(validate_invocation_freshness(&id_at(now)).is_ok());
        assert!(
            validate_invocation_freshness(&id_at(now - Duration::from_secs(49 * 3600))).is_err()
        );
        assert!(validate_invocation_freshness(&id_at(now + Duration::from_secs(10 * 60))).is_err());
        assert!(validate_invocation_freshness("inv-x").is_err());
    }
}
