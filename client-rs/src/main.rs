use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

mod config;
mod identity;
mod pairing;
mod state;

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

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "metnos_client=info".into()),
        )
        .init();

    let cli = Cli::parse();
    let paths = config::Paths::resolve()?;
    paths.ensure()?;
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
            st.save(&paths.state_file)?;
            println!("paired: device_id={} name={} fingerprint={} owner={}",
                     resp.device_id, resp.name, &resp.fingerprint[..16], resp.owner_user_id);
        }
        Cmd::Run { server } => {
            let url = server.or(st.server_url.clone())
                .ok_or_else(|| anyhow::anyhow!("no server (pair first or pass --server)"))?;
            tracing::info!(%url, "run: daemon loop not yet implemented (W1-2 MVP)");
            anyhow::bail!("daemon loop not wired yet (next milestone)");
        }
    }
    Ok(())
}
