//! sandbox_linux.rs — esecuzione sandboxed di un executor (§9 design doc).
//!
//! Forza «forte» su Linux: bubblewrap (namespace mount + PID + no-new-privs).
//! I bind derivano dalle `capabilities` del manifest — MAI piu' larghi di
//! quanto dichiarato. Se `bwrap` non c'e' o `METNOS_SANDBOX` e' off, si
//! esegue senza wrapping (parita' col fallback graceful di runtime/sandbox.py),
//! logando la degradazione (§2.8). landlock/seccomp custom: TODO W6.

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use crate::executors::{Capability, CachedExecutor};

pub struct Limits {
    pub wall: Duration,
}

pub struct SandboxOutput {
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub sandbox: String,
}

/// Path di sistema montati read-only in ogni sandbox (solo quelli esistenti).
const SYSTEM_RO: &[&str] = &[
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc",
];

/// Esegue `exec` con `python` passando `args_json` su stdin, dentro la sandbox.
/// `extra_env`: coppie (chiave, valore) iniettate (env_injections + PYTHONPATH).
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
        for a in bwrap_args(exec, python, shim_dir) {
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
    let pythonpath = format!("{}:{}", shim_dir.display(), exec.dir.display());
    cmd.env_clear();
    cmd.env("PATH", std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".into()));
    cmd.env("PYTHONPATH", &pythonpath);
    cmd.env("METNOS_RUNTIME", shim_dir);
    cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    cmd.env("LANG", std::env::var("LANG").unwrap_or_else(|_| "C.UTF-8".into()));
    for (k, v) in extra_env {
        cmd.env(k, v);
    }

    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());

    let mut child = cmd.spawn().context("spawn sandboxed executor")?;
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
            })
        }
        Err(_) => {
            // deadline superata: kill (§12).
            Ok(SandboxOutput {
                stdout: String::new(),
                stderr: "deadline exceeded".into(),
                timed_out: true,
                sandbox: sandbox_label.into(),
            })
        }
    }
}

fn bwrap_args(exec: &CachedExecutor, python: &Path, shim_dir: &Path) -> Vec<String> {
    let mut a: Vec<String> = Vec::new();
    a.push("--die-with-parent".into());
    a.push("--unshare-pid".into());
    a.push("--new-session".into());
    a.push("--proc".into());
    a.push("/proc".into());
    a.push("--dev".into());
    a.push("/dev".into());
    // tmpfs per /tmp (scratch effimero).
    a.push("--tmpfs".into());
    a.push("/tmp".into());

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

fn apply_capability(a: &mut Vec<String>, cap: &Capability, share_net: &mut bool) {
    let kind = cap.name.split(':').next().unwrap_or("");
    let mode = cap.name.split(':').nth(1).unwrap_or("");
    match kind {
        "network" => *share_net = true,
        "fs" => {
            for hint in &cap.hint {
                if let Some(root) = glob_root(hint) {
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

fn ro_bind_dir(a: &mut Vec<String>, dir: &Path) {
    a.push("--ro-bind".into());
    a.push(dir.display().to_string());
    a.push(dir.display().to_string());
}

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

/// Radice non-glob di un hint (`~/notes/**` → `~/notes`), con `~` espanso.
fn glob_root(hint: &str) -> Option<PathBuf> {
    if hint == "*" {
        return None; // troppo largo per un bind mirato
    }
    let mut h = hint.to_string();
    for sep in ["/**", "/*", "**"] {
        if let Some(idx) = h.find(sep) {
            h.truncate(idx);
            break;
        }
    }
    if h.starts_with("~/") {
        if let Some(home) = dirs::home_dir() {
            return Some(home.join(&h[2..]));
        }
    }
    if h.starts_with('/') {
        return Some(PathBuf::from(h));
    }
    None
}

pub fn bwrap_available() -> bool {
    which("bwrap").is_some()
}

fn sandbox_disabled() -> bool {
    matches!(
        std::env::var("METNOS_SANDBOX").unwrap_or_default().to_lowercase().as_str(),
        "0" | "off" | "no" | "false"
    )
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).find_map(|dir| {
        let full = dir.join(name);
        full.is_file().then_some(full)
    })
}
