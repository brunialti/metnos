use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

#[cfg(windows)]
mod appcontainer;
mod appx_activation;
mod config;
mod executors;
// Fuori Windows non c'e' nessun aiutante elevato con cui parlare, ma i due
// moduli restano compilati e PROVATI anche qui: sono meta' di un contratto
// fra due programmi separati, e la meta' che si puo' provare ovunque e'
// quella che deve essere giusta. Senza il permesso, sedici avvisi di codice
// inutilizzato seppellirebbero quelli veri.
#[cfg_attr(not(windows), allow(dead_code))]
mod frame;
#[cfg_attr(not(windows), allow(dead_code))]
mod helper_client;
mod helper_setup;
#[cfg(windows)]
mod helper_win;
mod identity;
mod pairing;
mod proclock;
mod pyenv;
mod runner;
// Logica PURA condivisa fra i due sandbox (hint→root, capability→ACL, encoder
// Win32): compila su entrambe le piattaforme, testabile sotto Linux (W4).
mod sandbox_common;
mod sandbox_linux;
#[cfg(windows)]
mod sandbox_windows;
mod selfupdate;
mod state;
mod update_state;
mod wire;

#[derive(Parser)]
#[command(
    name = "metnos-client",
    version,
    about = "Metnos remote executor client"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Who this device is: fingerprint, public key and — on Windows —
    /// the owner SID. The last two are what installing the elevated
    /// helper asks for (creates a key on first call).
    Whoami,
    /// Pair this device with a Metnos server using a one-shot token.
    Register {
        #[arg(long)]
        server: String,
        #[arg(long)]
        token: String,
    },
    /// Long-running daemon: connect, heartbeat, execute commands.
    Run {
        #[arg(long)]
        server: Option<String>,
    },
    /// Talk to the elevated Windows helper (ADR 0210 D).
    ///
    /// Prints exactly one JSON line on the last line of stdout. It exists so
    /// the judgment about WHO is on the other end of the channel lives in one
    /// place, in Rust: an executor that opened the pipe itself would be a
    /// second copy of a security check, and the second copy is the one that
    /// drifts.
    Helper {
        #[command(subcommand)]
        what: HelperCmd,
    },
    /// Resolve and activate a typed AppX package in this user's desktop
    /// session. This accepts no path, command, or argument string.
    PackageApp {
        #[command(subcommand)]
        what: PackageAppCmd,
    },
    /// Unpair this device: forget the server pairing and (on Windows) clean up
    /// the AppContainer sandbox — revoke every ACL grant recorded on user
    /// directories and delete the container profile (W4.4).
    Unpair,
}

#[derive(Subcommand)]
enum HelperCmd {
    /// Verify the genuine helper and report its local build/protocol state.
    Check,
    /// Is this package installed, and in which version? Changes nothing.
    Query {
        #[arg(long)]
        package_id: String,
    },
    Install {
        #[arg(long)]
        package_id: String,
        #[arg(long)]
        version: Option<String>,
    },
    Uninstall {
        #[arg(long)]
        package_id: String,
    },
    /// Start one package resolved by the helper from trusted installation
    /// metadata. No executable path or arguments cross the channel.
    Start {
        #[arg(long)]
        package_id: String,
        #[arg(long, value_enum)]
        lifetime: HelperStartLifetime,
    },
    /// Stop only the exact process identified by a managed-start receipt.
    Stop {
        #[arg(long)]
        package_id: String,
        #[arg(long)]
        pid: u32,
        #[arg(long)]
        creation_time: u64,
    },
    /// Bring the helper onto this machine and install it. Windows asks for
    /// confirmation once; from then on nothing asks again.
    Setup,
}

#[derive(Subcommand)]
enum PackageAppCmd {
    /// Verify that the exact package has one launchable application.
    Query {
        #[arg(long)]
        package_id: String,
    },
    /// Activate that application in the current user's desktop session.
    Start {
        #[arg(long)]
        package_id: String,
        #[arg(long, value_enum)]
        lifetime: HelperStartLifetime,
    },
    /// Restore the package-process cohort captured by a managed-start receipt.
    Stop {
        #[arg(long)]
        package_id: String,
        #[arg(long)]
        pid: u32,
        #[arg(long)]
        creation_time: u64,
        #[arg(long)]
        activation_boundary: Option<u64>,
        #[arg(long = "preexisting-process")]
        preexisting_processes: Vec<appx_activation::ProcessIdentity>,
    },
}

#[derive(Clone, Copy, clap::ValueEnum)]
enum HelperStartLifetime {
    Session,
    Persistent,
}

impl HelperStartLifetime {
    fn as_str(self) -> &'static str {
        match self {
            Self::Session => "session",
            Self::Persistent => "persistent",
        }
    }
}

/// Bring the helper onto this machine, and install it.
///
/// One JSON line, like every other helper command: whoever called needs to
/// tell «installed» from «the person said no» from «it never got here», and a
/// refusal is an answer, not a crash.
async fn run_helper_setup(id: &identity::Identity, st: &state::State) -> serde_json::Value {
    let (server, pubkey) = match (st.server_url.as_deref(), st.server_public_key.as_deref()) {
        (Some(s), Some(k)) if !s.is_empty() && !k.is_empty() => (s, k),
        // Without the pinned key there is no way to tell the real artifact
        // from any other, and installing the most privileged component of the
        // system on a guess is not a thing to do.
        _ => {
            return serde_json::json!({
                "ok": false, "error_code": "not_paired",
                "detail": "no paired server to fetch a signed helper from"
            })
        }
    };

    let dir = match config::Paths::resolve() {
        Ok(p) => p.data_dir.join("staging"),
        Err(e) => {
            return serde_json::json!({
                "ok": false, "error_code": "no_data_dir", "detail": e.to_string()
            })
        }
    };

    let fetched = match helper_setup::fetch(server, pubkey, &dir).await {
        Ok(f) => f,
        Err(e) => {
            return serde_json::json!({
                "ok": false, "error_code": "fetch_failed",
                "detail": format!("{e:#}")
            })
        }
    };

    #[cfg(windows)]
    let sid = match helper_win::sid_corrente() {
        Ok(s) => s,
        Err(e) => {
            return serde_json::json!({
                "ok": false, "error_code": "no_owner_sid",
                "detail": e.to_string()
            })
        }
    };
    #[cfg(not(windows))]
    let sid = String::new();

    match helper_setup::install_elevated(&fetched, &sid, &id.fingerprint(), pubkey, server) {
        Ok(helper_setup::Outcome::Installed) => serde_json::json!({
            "ok": true, "installed": true, "version": fetched.version
        }),
        // Non e' un guasto: e' la risposta che la persona ha dato a Windows.
        Ok(helper_setup::Outcome::Refused) => serde_json::json!({
            "ok": false, "error_code": "consent_refused", "installed": false
        }),
        // Il MOTIVO, non il numero. Chi legge il messaggio finale vede
        // `detail` e nient'altro: mostrargli «uscito con codice 3» era
        // riprodurre in altre parole lo stesso vicolo cieco del 19/8/2026 —
        // si sapeva che aveva fallito, non dove. Il numero resta accanto,
        // per chi legge i registri.
        Ok(helper_setup::Outcome::Failed(code, motivo)) => serde_json::json!({
            "ok": false, "error_code": "install_failed", "exit_code": code,
            "detail": motivo.unwrap_or_else(
                || format!("uscito con codice {code}, senza dire perche'"))
        }),
        Err(e) => serde_json::json!({
            "ok": false, "error_code": "elevation_failed",
            "detail": format!("{e:#}")
        }),
    }
}

/// Runs one helper command and produces the JSON line to print.
///
/// The refusal is an answer, not a crash: whoever asked needs to know that the
/// helper is missing as clearly as it needs to know that an install failed,
/// and both travel on the same shape.
#[cfg(windows)]
fn helper_exchange(
    pipe: &str,
    expected_executable: &str,
    request: &str,
) -> Result<serde_json::Value, helper_client::ChannelRefusal> {
    let response = helper_win::chiedi(pipe, expected_executable, request)?;
    Ok(serde_json::from_str(&response)
        .unwrap_or_else(|_| serde_json::json!({"ok": false, "error_code": "malformed_response"})))
}

#[cfg(any(windows, test))]
fn stamp_helper_alignment(mut response: serde_json::Value) -> serde_json::Value {
    if let Some(object) = response.as_object_mut() {
        let helper_protocol = object
            .get("protocol_version")
            .and_then(|value| value.as_u64())
            .unwrap_or(0) as u32;
        let helper_version = object
            .get("helper_version")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let protocol_current = helper_protocol == helper_client::PROTOCOL_VERSION;
        let version_current = !helper_version.is_empty()
            && !selfupdate::version_gt(env!("CARGO_PKG_VERSION"), helper_version);
        object.insert(
            "client_protocol_version".into(),
            serde_json::json!(helper_client::PROTOCOL_VERSION),
        );
        object.insert(
            "client_version".into(),
            serde_json::json!(env!("CARGO_PKG_VERSION")),
        );
        object.insert("version_current".into(), serde_json::json!(version_current));
        object.insert(
            "aligned".into(),
            serde_json::json!(protocol_current && version_current),
        );
    }
    response
}

#[cfg(test)]
mod lazy_helper_alignment_tests {
    use super::stamp_helper_alignment;

    fn stamped(helper_version: &str, protocol_version: u32) -> serde_json::Value {
        stamp_helper_alignment(serde_json::json!({
            "ok": true,
            "helper_version": helper_version,
            "protocol_version": protocol_version,
        }))
    }

    #[test]
    fn an_older_helper_requires_the_lazy_update() {
        let response = stamped("0.2.1", crate::helper_client::PROTOCOL_VERSION);
        assert_eq!(response["version_current"], false);
        assert_eq!(response["aligned"], false);
    }

    #[test]
    fn the_same_or_newer_compatible_helper_needs_no_network_check() {
        for version in [env!("CARGO_PKG_VERSION"), "99.0.0"] {
            let response = stamped(version, crate::helper_client::PROTOCOL_VERSION);
            assert_eq!(response["version_current"], true);
            assert_eq!(response["aligned"], true);
        }
    }

    #[test]
    fn protocol_mismatch_still_fails_closed() {
        let response = stamped(env!("CARGO_PKG_VERSION"), 0);
        assert_eq!(response["version_current"], true);
        assert_eq!(response["aligned"], false);
    }
}

#[cfg(windows)]
fn helper_version_request(
    pipe: &str,
    expected_executable: &str,
    id: &identity::Identity,
    expected_version: Option<&str>,
) -> Result<serde_json::Value, helper_client::ChannelRefusal> {
    let request =
        helper_client::build_request(id, helper_client::Operation::Version, "", expected_version)
            .map_err(|_| helper_client::ChannelRefusal::NotAvailable)?;
    helper_exchange(pipe, expected_executable, &request).map(stamp_helper_alignment)
}

#[cfg(windows)]
fn mark_update_pending(mut response: serde_json::Value) -> serde_json::Value {
    if let Some(object) = response.as_object_mut() {
        object.insert("ok".into(), serde_json::json!(false));
        object.insert(
            "error_code".into(),
            serde_json::json!("helper_update_pending"),
        );
        object.insert("aligned".into(), serde_json::json!(false));
    }
    response
}

#[cfg(windows)]
fn ensure_helper_current(
    pipe: &str,
    expected_executable: &str,
    id: &identity::Identity,
) -> Result<serde_json::Value, helper_client::ChannelRefusal> {
    let current = helper_version_request(pipe, expected_executable, id, None)?;
    if current.get("aligned").and_then(|value| value.as_bool()) == Some(true) {
        return Ok(current);
    }

    let helper_version = current
        .get("helper_version")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    if helper_version.is_empty()
        || !selfupdate::version_gt(env!("CARGO_PKG_VERSION"), helper_version)
    {
        return Ok(current);
    }

    // Only this targeted, signed Version request may touch the network. The
    // real action has not been sent and therefore cannot be duplicated.
    match helper_version_request(
        pipe,
        expected_executable,
        id,
        Some(env!("CARGO_PKG_VERSION")),
    ) {
        Ok(response) => {
            let ready = response.get("ok").and_then(|value| value.as_bool()) == Some(true)
                && response.get("aligned").and_then(|value| value.as_bool()) == Some(true);
            return Ok(if ready {
                response
            } else {
                mark_update_pending(response)
            });
        }
        Err(helper_client::ChannelRefusal::NotAvailable) => {}
        Err(error) => return Err(error),
    }

    // Service recovery is configured for a five-second first restart. Wait
    // once, then make at most three local probes; no package action is retried.
    std::thread::sleep(std::time::Duration::from_millis(5_500));
    for attempt in 0..3 {
        match helper_version_request(pipe, expected_executable, id, None) {
            Ok(response) => {
                return Ok(
                    if response.get("aligned").and_then(|value| value.as_bool()) == Some(true) {
                        response
                    } else {
                        mark_update_pending(response)
                    },
                )
            }
            Err(helper_client::ChannelRefusal::NotAvailable) if attempt < 2 => {
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            Err(error) => return Err(error),
        }
    }
    Err(helper_client::ChannelRefusal::NotAvailable)
}

#[cfg(windows)]
fn run_helper(what: HelperCmd, id: &identity::Identity) -> serde_json::Value {
    use helper_client::Operation;

    let esito = (|| -> Result<serde_json::Value, helper_client::ChannelRefusal> {
        let (pipe, atteso) = helper_win::indirizzo()?;
        let helper_state = ensure_helper_current(&pipe, &atteso, id)?;
        if matches!(&what, HelperCmd::Check)
            || helper_state.get("ok").and_then(|value| value.as_bool()) != Some(true)
            || helper_state
                .get("aligned")
                .and_then(|value| value.as_bool())
                != Some(true)
        {
            return Ok(helper_state);
        }
        let richiesta = match what {
            HelperCmd::Check => unreachable!("check returned after the version handshake"),
            HelperCmd::Query { package_id } => {
                helper_client::build_request(id, Operation::Query, &package_id, None)
            }
            HelperCmd::Install {
                package_id,
                version,
            } => helper_client::build_request(
                id,
                Operation::Install,
                &package_id,
                version.as_deref(),
            ),
            HelperCmd::Uninstall { package_id } => {
                helper_client::build_request(id, Operation::Uninstall, &package_id, None)
            }
            HelperCmd::Start {
                package_id,
                lifetime,
            } => {
                let lifetime = match lifetime {
                    HelperStartLifetime::Session => helper_client::StartLifetime::Session,
                    HelperStartLifetime::Persistent => helper_client::StartLifetime::Persistent,
                };
                helper_client::build_start_request(id, &package_id, lifetime)
            }
            HelperCmd::Stop {
                package_id,
                pid,
                creation_time,
            } => helper_client::build_stop_request(id, &package_id, pid, creation_time),
            // `setup` non e' un'operazione DELL'aiutante: e' come l'aiutante
            // arriva. Lo smistamento lo prende prima, e questo ramo esiste
            // perche' il compilatore non lo sappia per caso: se domani
            // qualcuno chiama qui, deve leggerlo, non scoprirlo.
            HelperCmd::Setup => {
                return Ok(serde_json::json!({
                    "ok": false, "error_code": "wrong_entry_point",
                    "detail": "helper setup is installed, not requested over the channel"
                }));
            }
        }
        // A request that cannot be composed never reaches the channel.
        .map_err(|_| helper_client::ChannelRefusal::NotAvailable)?;
        helper_exchange(&pipe, &atteso, &richiesta).map(stamp_helper_alignment)
    })();

    esito.unwrap_or_else(|rifiuto| {
        serde_json::json!({
            "ok": false,
            "error_code": rifiuto.code(),
            "detail": rifiuto.message(),
        })
    })
}

/// Internal provider path used only by the verified invocation runner.
/// It is deliberately absent from the public CLI command vocabulary.
#[cfg(windows)]
pub(crate) fn run_managed_provider(
    grant: &wire::ManagedProviderGrant,
    id: &identity::Identity,
) -> serde_json::Value {
    let result = (|| -> Result<serde_json::Value, helper_client::ChannelRefusal> {
        let (pipe, expected) = helper_win::indirizzo()?;
        let state = ensure_helper_current(&pipe, &expected, id)?;
        if state.get("ok").and_then(serde_json::Value::as_bool) != Some(true)
            || state.get("aligned").and_then(serde_json::Value::as_bool) != Some(true)
        {
            return Ok(state);
        }
        let request = helper_client::build_provider_request(id, grant)
            .map_err(|_| helper_client::ChannelRefusal::NotAvailable)?;
        helper_exchange(&pipe, &expected, &request).map(stamp_helper_alignment)
    })();
    result.unwrap_or_else(|refusal| {
        serde_json::json!({
            "ok": false,
            "error_code": refusal.code(),
            "detail": refusal.message(),
        })
    })
}

/// Fuori Windows non c'e' nessun aiutante elevato, e non e' un guasto: e' la
/// stessa assenza che il canale dichiara quando non e' installato.
#[cfg(not(windows))]
fn run_helper(_what: HelperCmd, _id: &identity::Identity) -> serde_json::Value {
    let rifiuto = helper_client::ChannelRefusal::NotAvailable;
    serde_json::json!({
        "ok": false,
        "error_code": rifiuto.code(),
        "detail": rifiuto.message(),
    })
}

/// Log ANCHE su file (`<data_dir>/client.log`): in Scheduled Task / unit di
/// sistema lo stdout finisce nel nulla e un fallimento in background sarebbe
/// invisibile per costruzione (§2.8 lato client — imparato dal vivo 3/7:
/// task "partita" e client morto senza una riga da nessuna parte).
/// Rotazione minima senza dipendenze: oltre 5 MB il file diventa `.1`.
fn open_log_file(dir: &std::path::Path) -> Option<std::fs::File> {
    let path = dir.join("client.log");
    if let Ok(md) = std::fs::metadata(&path) {
        if md.len() > 5 * 1024 * 1024 {
            let _ = std::fs::rename(&path, dir.join("client.log.1"));
        }
    }
    std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .ok()
}

fn init_tracing(log_file: Option<std::fs::File>) {
    use tracing_subscriber::fmt::writer::MakeWriterExt;
    let filter = || {
        tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "metnos_client=info".into())
    };
    match log_file {
        Some(f) => tracing_subscriber::fmt()
            .with_env_filter(filter())
            .with_ansi(false)
            .with_writer(std::io::stdout.and(std::sync::Mutex::new(f)))
            .init(),
        None => tracing_subscriber::fmt().with_env_filter(filter()).init(),
    }
}

/// Sgancia il processo dalla console (§B6, solo Windows). Chiamata all'avvio
/// del daemon `run`: la Scheduled Task lancia un exe console-subsystem che
/// altrimenti lascia una finestra aperta per tutta la sua vita. Gli altri
/// subcomandi (whoami/register/errore-lock) NON la chiamano → conservano
/// l'output interattivo.
#[cfg(windows)]
fn detach_console() {
    unsafe { windows_sys::Win32::System::Console::FreeConsole() };
}
#[cfg(not(windows))]
fn detach_console() {}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let paths = config::Paths::resolve()?;
    paths.ensure()?;
    init_tracing(open_log_file(&paths.data_dir));

    let out = run_cmd(cli, paths).await;
    if let Err(ref e) = out {
        // L'errore fatale DEVE finire nel log file, non solo su stderr
        // (che in task context non legge nessuno).
        tracing::error!("fatal: {e:#}");
    }
    out
}

async fn run_cmd(cli: Cli, paths: config::Paths) -> Result<()> {
    let id = identity::Identity::load_or_create(&paths.key_file)?;
    let mut st = state::State::load_or_default(&paths.state_file)?;

    match cli.cmd {
        Cmd::Whoami => {
            println!("device fingerprint: {}", id.fingerprint());
            // I due valori che l'installazione dell'aiutante elevato
            // richiede (ADR 0210 D). Stanno qui perche' «chi sono io» e'
            // esattamente la domanda: chiederli a due comandi diversi, uno
            // dei quali non esisteva, e' il motivo per cui la parte D era
            // completa nel codice e non eseguibile da nessuno.
            // Il valore che l'installazione dell'aiutante elevato richiede
            // (ADR 0210 D): e' l'impronta qui sopra, che E' la chiave. Si
            // ripete sotto il nome con cui la chiede l'altro programma,
            // perche' chi installa non debba indovinare che sono la stessa
            // cosa.
            println!("public key:         {}", id.fingerprint());
            #[cfg(windows)]
            match helper_win::sid_corrente() {
                Ok(sid) => println!("owner SID:          {sid}"),
                Err(e) => println!("owner SID:          non leggibile ({e})"),
            }
            println!("data dir:           {}", paths.data_dir.display());
            println!("cache dir:          {}", paths.cache_dir.display());
            if st.is_paired() {
                println!(
                    "device id:          {}",
                    st.device_id.as_deref().unwrap_or("?")
                );
                println!(
                    "device name:        {}",
                    st.device_name.as_deref().unwrap_or("?")
                );
                println!(
                    "server:             {}",
                    st.server_url.as_deref().unwrap_or("?")
                );
                println!(
                    "paired_at:          {}",
                    st.paired_at.as_deref().unwrap_or("?")
                );
            } else {
                println!("status:             not paired");
            }
        }
        Cmd::Register { server, token } => {
            if st.is_paired() {
                tracing::warn!(
                    device_id = %st.device_id.as_deref().unwrap_or("?"),
                    "already paired; re-registering will keep the same key"
                );
            }
            let resp = pairing::register(&server, &token, &id)
                .await
                .context("register failed")?;
            st.device_id = Some(resp.device_id.clone());
            st.device_name = Some(resp.name.clone());
            st.server_url = Some(server.clone());
            st.fingerprint = Some(resp.fingerprint.clone());
            st.paired_at = Some(resp.paired_at.clone());
            st.server_public_key = resp.server_public_key.clone();
            st.save(&paths.state_file)?;
            if resp.server_public_key.is_none() {
                tracing::warn!(
                    "il server non ha fornito server_public_key: \
                     le invocazioni non potranno essere verificate (run rifiutera')"
                );
            }
            println!(
                "paired: device_id={} name={} fingerprint={} owner={}",
                resp.device_id,
                resp.name,
                &resp.fingerprint[..16],
                resp.owner_user_id
            );
        }
        Cmd::Run { server } => {
            // Self-update ROBUSTO: recovery+macchina a stati PRIMA di tutto.
            // Se una probation non confermata va in rollback, esce qui (il
            // supervisor rilancerà il binario known-good) senza toccare il lock.
            let exe = std::env::current_exe().context("current_exe")?;
            selfupdate::apply_startup_recovery(&exe, &selfupdate::marker_path(&paths.data_dir));

            // Single-instance (§12): un secondo `run` con la stessa identita'
            // e' spreco di poll + race su spool/cache. Il lock vive fino
            // all'uscita del processo. Niente respawn (BUG-A rimosso) → il
            // vecchio processo è già morto quando il supervisor rilancia: nessuna
            // race sul lock, acquisizione diretta.
            let _lock = match proclock::acquire(&paths.data_dir) {
                Ok(l) => l,
                Err(e) => {
                    // Lock gia' tenuto = un altro supervisore ha il client vivo.
                    // Esci con codice 3: il launcher NON deve respawnare in loop
                    // (bug 9/7 sul PC). Errori di I/O sul lock restano exit 1.
                    if e.downcast_ref::<proclock::AlreadyRunning>().is_some() {
                        eprintln!("{e}");
                        std::process::exit(proclock::EXIT_ALREADY_RUNNING);
                    }
                    return Err(e);
                }
            };
            // §B6: solo DOPO il lock (l'errore «gia' attivo» deve restare
            // visibile in console). Il daemon di background non deve tenere
            // una finestra aperta: il log su file (§2.8) resta la fonte di
            // verita', stdout dopo il detach va nel nulla ed e' accettabile.
            detach_console();
            let url = server
                .or(st.server_url.clone())
                .ok_or_else(|| anyhow::anyhow!("no server (pair first or pass --server)"))?;
            let r = runner::Runner::new(url, &st, id, paths).context("init runner")?;
            r.run().await?;
        }
        Cmd::Helper { what } => {
            // Ultima riga di stdout, sempre e comunque: chi legge non deve
            // distinguere fra «e' andata» e «non c'e' l'aiutante».
            let esito = match what {
                // `setup` non parla col canale: lo mette al mondo. Ed e'
                // l'unico che va in rete, quindi l'unico asincrono.
                HelperCmd::Setup => run_helper_setup(&id, &st).await,
                altro => run_helper(altro, &id),
            };
            println!("{esito}");
        }
        Cmd::PackageApp { what } => {
            // The final line is always structured, matching the helper CLI
            // adapter used by executors. This path stays unprivileged and in
            // the interactive user's session by construction.
            let result = match what {
                PackageAppCmd::Query { package_id } => appx_activation::query(&package_id),
                PackageAppCmd::Start {
                    package_id,
                    lifetime,
                } => appx_activation::start(&package_id, lifetime.as_str()),
                PackageAppCmd::Stop {
                    package_id,
                    pid,
                    creation_time,
                    activation_boundary,
                    preexisting_processes,
                } => appx_activation::stop(
                    &package_id,
                    pid,
                    creation_time,
                    activation_boundary,
                    &preexisting_processes,
                ),
            };
            println!("{result}");
        }
        Cmd::Unpair => {
            // 1. Pulizia sandbox (solo Windows, W4.4): revoca gli ACE concessi
            //    al SID del container sulle dir utente + rimuove il profilo. Su
            //    altre piattaforme non c'e' AppContainer: nulla da pulire.
            #[cfg(windows)]
            {
                match appcontainer::cleanup_all_grants() {
                    Ok(r) => println!(
                        "sandbox: {} concessioni ACL registrate, {} revocate, \
                         {} scartate (path assente), {} non revocate; profilo rimosso={}",
                        r.total, r.revoked, r.dropped, r.failed, r.profile_removed
                    ),
                    Err(e) => {
                        tracing::warn!("pulizia sandbox AppContainer fallita: {e:#}");
                        eprintln!("attenzione: pulizia sandbox non completata: {e:#}");
                    }
                }
            }
            // 2. Dimentica il pairing: il device torna non-appaiato. L'identita'
            //    (la chiave) resta, cosi' un nuovo `register` e' possibile.
            if paths.state_file.exists() {
                let was = st.device_id.clone().unwrap_or_else(|| "?".into());
                std::fs::remove_file(&paths.state_file)
                    .with_context(|| format!("rimozione state {}", paths.state_file.display()))?;
                println!("pairing rimosso (device {was} non piu' appaiato)");
            } else {
                println!("nessun pairing da rimuovere");
            }
        }
    }
    Ok(())
}
