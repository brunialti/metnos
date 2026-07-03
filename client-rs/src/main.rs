use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

mod config;
mod executors;
mod identity;
mod pairing;
mod proclock;
mod pyenv;
mod runner;
mod sandbox_linux;
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
            // Single-instance (§12): un secondo `run` con la stessa identita'
            // e' spreco di poll + race su spool/cache. Il lock vive fino
            // all'uscita del processo.
            let _lock = proclock::acquire(&paths.data_dir)?;
            let url = server.or(st.server_url.clone())
                .ok_or_else(|| anyhow::anyhow!("no server (pair first or pass --server)"))?;
            let r = runner::Runner::new(url, &st, id, paths)
                .context("init runner")?;
            r.run().await?;
        }
    }
    Ok(())
}
