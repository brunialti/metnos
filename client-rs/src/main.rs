use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

#[cfg(windows)]
mod appcontainer;
mod config;
mod executors;
mod identity;
mod pairing;
mod proclock;
mod pyenv;
mod runner;
// Logica PURA condivisa fra i due sandbox (hint→root, capability→ACL, encoder
// Win32): compila su entrambe le piattaforme, testabile sotto Linux (W4).
mod sandbox_common;
mod sandbox_linux;
mod selfupdate;
mod update_state;
#[cfg(windows)]
mod sandbox_windows;
mod state;
mod wire;

#[derive(Parser)]
#[command(name = "metnos-client", version, about = "Metnos remote executor client")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Print the device fingerprint (creates a key on first call).
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
    /// Unpair this device: forget the server pairing and (on Windows) clean up
    /// the AppContainer sandbox — revoke every ACL grant recorded on user
    /// directories and delete the container profile (W4.4).
    Unpair,
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
    std::fs::OpenOptions::new().create(true).append(true).open(&path).ok()
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
            println!("data dir:           {}", paths.data_dir.display());
            println!("cache dir:          {}", paths.cache_dir.display());
            if st.is_paired() {
                println!("device id:          {}", st.device_id.as_deref().unwrap_or("?"));
                println!("device name:        {}", st.device_name.as_deref().unwrap_or("?"));
                println!("server:             {}", st.server_url.as_deref().unwrap_or("?"));
                println!("paired_at:          {}", st.paired_at.as_deref().unwrap_or("?"));
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
            let resp = pairing::register(&server, &token, &id).await
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
            println!("paired: device_id={} name={} fingerprint={} owner={}",
                     resp.device_id, resp.name, &resp.fingerprint[..16], resp.owner_user_id);
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
            let url = server.or(st.server_url.clone())
                .ok_or_else(|| anyhow::anyhow!("no server (pair first or pass --server)"))?;
            let r = runner::Runner::new(url, &st, id, paths)
                .context("init runner")?;
            r.run().await?;
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
                std::fs::remove_file(&paths.state_file).with_context(|| {
                    format!("rimozione state {}", paths.state_file.display())
                })?;
                println!("pairing rimosso (device {was} non piu' appaiato)");
            } else {
                println!("nessun pairing da rimuovere");
            }
        }
    }
    Ok(())
}
