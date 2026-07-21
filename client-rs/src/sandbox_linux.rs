//! sandbox_linux.rs — esecuzione sandboxed di un executor (§9 design doc).
//!
//! Forza «forte» su Linux: bubblewrap (namespace mount + PID + no-new-privs).
//! I bind derivano dalle `capabilities` del manifest — MAI piu' larghi di
//! quanto dichiarato. Se `bwrap` non c'e' o `METNOS_SANDBOX` e' off, si
//! esegue senza wrapping (parita' col fallback graceful di runtime/sandbox.py),
//! logando la degradazione (§2.8). landlock/seccomp custom: TODO W6.

#[cfg(unix)]
use anyhow::{Context, Result};
#[cfg(unix)]
use std::path::{Path, PathBuf};
#[cfg(unix)]
use std::process::Stdio;
use std::time::Duration;
#[cfg(unix)]
use tokio::io::AsyncWriteExt;
#[cfg(unix)]
use tokio::process::Command;

#[cfg(unix)]
use crate::executors::{Capability, CachedExecutor};

pub struct Limits {
    pub wall: Duration,
}

pub struct SandboxOutput {
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub sandbox: String,
    /// Motivo del declassamento del livello di sandbox, quando avvenuto (W4):
    /// popolato SOLO su Windows quando l'AppContainer non si costruisce e si
    /// degrada a job-object (§2.8, mai silenzioso). Su Linux sempre `None`
    /// (bwrap non ha un livello superiore da cui degradare).
    pub downgrade_reason: Option<String>,
}

/// Separatore PYTHONPATH per piattaforma (§16.2 W3.1: fix del bug che
/// hardcodava ':' anche per il target windows). Punto CONDIVISO: questo
/// modulo compila su entrambe le piattaforme (verificato dal gate
/// `cargo build --target x86_64-pc-windows-gnu`), quindi e' la sede
/// naturale — sandbox_windows.rs lo importa da qui.
pub fn pythonpath_sep() -> char {
    if cfg!(windows) { ';' } else { ':' }
}

/// Path di sistema montati read-only in ogni sandbox (solo quelli esistenti).
#[cfg(unix)]
const SYSTEM_RO: &[&str] = &[
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc",
];

/// Esegue `exec` con `python` passando `args_json` su stdin, dentro la sandbox.
/// `extra_env`: coppie (chiave, valore) iniettate (env_injections + PYTHONPATH).
/// Solo unix: il dispatch per-piattaforma (runner.rs) instrada a
/// `sandbox_windows::run_sandboxed` su Windows (§16.2 W3.1).
#[cfg(unix)]
pub async fn run_sandboxed(
    exec: &CachedExecutor,
    python: &Path,
    shim_dir: &Path,
    args_json: &str,
    extra_env: &[(String, String)],
    limits: &Limits,
) -> Result<SandboxOutput> {
    let use_bwrap = bwrap_available() && !sandbox_disabled();
    let sandbox_label = if use_bwrap { "bwrap" } else { "none" };
    if !use_bwrap {
        tracing::warn!(
            "sandbox non attiva (bwrap assente o METNOS_SANDBOX off): \
             esecuzione diretta di {}",
            exec.name
        );
    }

    let mut cmd = if use_bwrap {
        let mut c = Command::new("bwrap");
        for a in bwrap_args(exec, python, shim_dir, extra_env) {
            c.arg(a);
        }
        c.arg(python);
        c
    } else {
        Command::new(python)
    };
    cmd.arg(&exec.entry);

    // PYTHONPATH: shim (executor_helpers + messages) + dir executor. METNOS_RUNTIME
    // = shim dir cosi' il bootstrap sys.path degli executor la trova.
    let pythonpath = format!("{}{}{}", shim_dir.display(), pythonpath_sep(), exec.dir.display());
    cmd.env_clear();
    cmd.env("PATH", std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".into()));
    cmd.env("PYTHONPATH", &pythonpath);
    cmd.env("METNOS_RUNTIME", shim_dir);
    cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    // UTF-8 esplicito (§16.2): su Linux e' innocuo, previene comunque
    // ambiguita' di encoding indipendentemente dal LANG del processo padre.
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("LANG", std::env::var("LANG").unwrap_or_else(|_| "C.UTF-8".into()));
    // Home reale (0.2.13, come sandbox_windows): `~` e Path.home() nei moduli
    // shim; bwrap comunque limita i bind alle capabilities del manifest.
    if let Ok(v) = std::env::var("HOME") {
        cmd.env("HOME", v);
    }
    for (k, v) in extra_env {
        cmd.env(k, v);
    }

    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());

    // Kill REALE al timeout (§12), due strati:
    // - process group dedicato: kill(-pid) abbatte l'ALBERO anche senza bwrap
    //   (con bwrap basta il figlio diretto: --die-with-parent smonta il ns);
    // - kill_on_drop: qualunque drop del Child (timeout incluso) manda
    //   SIGKILL al figlio diretto e lo reappa in background (no zombie).
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.as_std_mut().process_group(0);
    }
    cmd.kill_on_drop(true);

    let mut child = cmd.spawn().context("spawn sandboxed executor")?;
    #[cfg(unix)]
    let child_pid = child.id();
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(args_json.as_bytes()).await.ok();
        drop(stdin);
    }

    match tokio::time::timeout(limits.wall, child.wait_with_output()).await {
        Ok(out) => {
            let out = out.context("wait executor")?;
            Ok(SandboxOutput {
                stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
                stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
                timed_out: false,
                sandbox: sandbox_label.into(),
                downgrade_reason: None,
            })
        }
        Err(_) => {
            // Deadline superata: SIGKILL all'intero process group; il drop
            // del future ha gia' armato kill_on_drop sul figlio diretto.
            #[cfg(unix)]
            if let Some(pid) = child_pid {
                unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
            }
            tracing::warn!(
                executor = %exec.name, wall_s = limits.wall.as_secs(),
                "deadline superata: process group terminato (SIGKILL)"
            );
            Ok(SandboxOutput {
                stdout: String::new(),
                stderr: "deadline exceeded".into(),
                timed_out: true,
                sandbox: sandbox_label.into(),
                downgrade_reason: None,
            })
        }
    }
}

#[cfg(unix)]
fn bwrap_args(
    exec: &CachedExecutor,
    python: &Path,
    shim_dir: &Path,
    extra_env: &[(String, String)],
) -> Vec<String> {
    let mut a: Vec<String> = vec![
        "--die-with-parent".into(),
        "--unshare-pid".into(),
        "--new-session".into(),
        "--proc".into(),
        "/proc".into(),
        "--dev".into(),
        "/dev".into(),
        // tmpfs per /tmp (scratch effimero).
        "--tmpfs".into(),
        "/tmp".into(),
    ];

    for p in SYSTEM_RO {
        if Path::new(p).exists() {
            a.push("--ro-bind".into());
            a.push((*p).into());
            a.push((*p).into());
        }
    }
    // Interprete (se fuori da /usr, es. python-build-standalone in cache).
    bind_ancestor_ro(&mut a, python);
    // Codice executor + shim: read-only.
    ro_bind_dir(&mut a, &exec.dir);
    ro_bind_dir(&mut a, shim_dir);

    // Parita' col percorso Windows: config.py e i blob undo vivono nella
    // radice client-owned annunciata dal runner. Senza questo bind, il client
    // Linux vedeva METNOS_USER_DATA nell'env ma non nel namespace bwrap.
    if let Some((_, value)) = extra_env.iter().find(|(key, _)| key == "METNOS_USER_DATA") {
        let root = PathBuf::from(value);
        if root.is_dir() {
            a.push("--bind".into());
            a.push(root.display().to_string());
            a.push(root.display().to_string());
        }
    }

    // Capabilities → bind. fs:read → ro-bind; fs:write → bind (rw); network
    // → --share-net (default e' unshare). code:exec hint ["*"] = nessun bind
    // extra (l'executor usa PATH gia' montato read-only).
    let mut share_net = false;
    for cap in &exec.capabilities {
        apply_capability(&mut a, cap, &mut share_net);
    }
    if !share_net {
        a.push("--unshare-net".into());
    }
    a
}

#[cfg(unix)]
fn apply_capability(a: &mut Vec<String>, cap: &Capability, share_net: &mut bool) {
    let kind = cap.name.split(':').next().unwrap_or("");
    let mode = cap.name.split(':').nth(1).unwrap_or("");
    match kind {
        "network" => *share_net = true,
        "fs" => {
            for hint in &cap.hint {
                // Derivazione hint→radice CONDIVISA con il path Windows (W4.2):
                // stessa funzione, un solo comportamento (§9.3 mapping bilingue).
                if let Some(root) = crate::sandbox_common::glob_root(hint) {
                    if root.exists() {
                        if mode == "write" {
                            a.push("--bind".into());
                        } else {
                            a.push("--ro-bind".into());
                        }
                        a.push(root.display().to_string());
                        a.push(root.display().to_string());
                    }
                }
            }
        }
        // code:exec, mem, ...: nessun bind extra (gia' coperto da SYSTEM_RO).
        _ => {}
    }
}

#[cfg(unix)]
fn ro_bind_dir(a: &mut Vec<String>, dir: &Path) {
    a.push("--ro-bind".into());
    a.push(dir.display().to_string());
    a.push(dir.display().to_string());
}

#[cfg(unix)]
fn bind_ancestor_ro(a: &mut Vec<String>, file: &Path) {
    // Monta l'ancestor non ancora coperto da /usr, per interpreti in cache.
    if let Ok(canon) = file.canonicalize() {
        let s = canon.to_string_lossy();
        if !SYSTEM_RO.iter().any(|p| s.starts_with(p)) {
            if let Some(parent) = canon.parent().and_then(|p| p.parent()) {
                a.push("--ro-bind".into());
                a.push(parent.display().to_string());
                a.push(parent.display().to_string());
            }
        }
    }
}

// La derivazione hint→radice vive ora in `sandbox_common::glob_root` (W4.2:
// stessa logica condivisa col path Windows). `apply_capability` sopra la
// richiama; qui non ne resta una copia locale.

#[cfg(unix)]
pub fn bwrap_available() -> bool {
    which("bwrap").is_some()
}

pub fn sandbox_disabled() -> bool {
    matches!(
        std::env::var("METNOS_SANDBOX").unwrap_or_default().to_lowercase().as_str(),
        "0" | "off" | "no" | "false"
    )
}

/// B.5 (fase 7): livello di contenimento che `run_sandboxed` userebbe ORA su
/// questo device — stessa logica di `sandbox_label` dentro run_sandboxed, ma
/// interrogabile a freddo per l'heartbeat (telemetria del gate min_sandbox,
/// W4). Su unix: "bwrap" quando disponibile e non disabilitato, altrimenti
/// "none".
#[cfg(unix)]
pub fn sandbox_level() -> &'static str {
    if bwrap_available() && !sandbox_disabled() { "bwrap" } else { "none" }
}

#[cfg(unix)]
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).find_map(|dir| {
        let full = dir.join(name);
        full.is_file().then_some(full)
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::executors::CachedExecutor;
    use std::time::Duration;

    #[test]
    fn bwrap_binds_client_owned_user_data_rw() {
        let root = std::env::temp_dir().join(format!(
            "metnos-shimdata-bind-test-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let exec = CachedExecutor {
            name: "bind_test".into(),
            dir: root.clone(),
            entry: root.join("executor.py"),
            capabilities: vec![],
        };
        let extra = vec![(
            "METNOS_USER_DATA".to_string(), root.display().to_string())];

        let args = bwrap_args(&exec, Path::new("/usr/bin/python3"), &root, &extra);
        let expected = root.display().to_string();
        assert!(args.windows(3).any(|values| {
            values == ["--bind", expected.as_str(), expected.as_str()]
        }));
        let _ = std::fs::remove_dir_all(&root);
    }

    /// §12: al timeout il processo executor DEVE morire davvero (SIGKILL al
    /// process group), non restare appeso. Percorso degradato (bwrap OFF =
    /// il caso rischioso): uno script che scrive un heartbeat su file ogni
    /// 50ms viene interrotto; il file smette di crescere.
    #[tokio::test]
    async fn timeout_kills_executor_process() {
        let python = PathBuf::from("/usr/bin/python3");
        if !python.is_file() {
            eprintln!("skip: /usr/bin/python3 assente");
            return;
        }
        std::env::set_var("METNOS_SANDBOX", "off");

        let dir = std::env::temp_dir().join(format!(
            "metnos-kill-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let beat = dir.join("beat.txt");
        let script = dir.join("sleeper.py");
        std::fs::write(&script, format!(
            "import time\nwhile True:\n    open({beat:?}, 'a').write('x')\n    time.sleep(0.05)\n",
            beat = beat.to_str().unwrap(),
        )).unwrap();

        let exec = CachedExecutor {
            name: "sleeper_test".into(),
            dir: dir.clone(),
            entry: script.clone(),
            capabilities: vec![],
        };
        let out = run_sandboxed(
            &exec, &python, &dir, "{}", &[],
            &Limits { wall: Duration::from_millis(400) },
        ).await.expect("run_sandboxed");
        assert!(out.timed_out, "atteso timeout");

        // Il processo e' morto: il file heartbeat smette di crescere.
        tokio::time::sleep(Duration::from_millis(300)).await;
        let size_a = std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0);
        tokio::time::sleep(Duration::from_millis(500)).await;
        let size_b = std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0);
        assert_eq!(size_a, size_b,
                   "l'executor scrive ancora dopo il timeout: kill mancato");

        let _ = std::fs::remove_dir_all(&dir);
    }
}
