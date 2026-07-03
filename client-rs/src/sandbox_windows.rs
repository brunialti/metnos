//! sandbox_windows.rs — esecuzione sandboxed di un executor su Windows
//! (§16.2 design doc, W3.1).
//!
//! Contenimento via Job Object: `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` garantisce
//! la morte dell'intero albero anche a crash del client (chiusura dell'handle
//! del job = SIGKILL implicito), cap memoria (`ProcessMemoryLimit`) e cap
//! processi (`ActiveProcessLimit`, anti fork-bomb). Al timeout,
//! `TerminateJobObject` e' il gemello Windows del SIGKILL-al-process-group
//! di `sandbox_linux.rs`.
//!
//! Onestà sul livello di protezione (§9 design doc): questo e' contenimento
//! di RISORSE e PRIVILEGI (token ristretto quando disponibile), NON
//! isolamento del filesystem. L'isolamento vero (capability→ACL) arriva con
//! AppContainer in W4 — qui `exec.capabilities` non e' ancora tradotto in
//! permessi. Label onesta nel result: `sandbox:"job-object"`.
//!
//! Sequenza (l'ordine E' il contratto — evita la finestra in cui il figlio
//! gira fuori dal job): CreateJobObjectW → SetInformationJobObject → spawn
//! CREATE_SUSPENDED|CREATE_NO_WINDOW → AssignProcessToJobObject → risolvi il
//! thread primario via Toolhelp32 (CreateProcess non lo espone a std/tokio)
//! → ResumeThread → wait con deadline → timeout: TerminateJobObject.

use anyhow::{bail, Context, Result};
use std::os::windows::io::RawHandle;
use std::path::Path;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, BOOL, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_PROCESS_MEMORY,
};
use windows_sys::Win32::System::Threading::{
    OpenThread, ResumeThread, CREATE_NO_WINDOW, CREATE_SUSPENDED, THREAD_SUSPEND_RESUME,
};

use crate::executors::CachedExecutor;
// Tipi condivisi col modulo linux (§16.2: "minimo diff" = ri-esportati, non
// duplicati). sandbox_linux.rs compila su entrambe le piattaforme.
pub use crate::sandbox_linux::{Limits, SandboxOutput};
use crate::sandbox_linux::pythonpath_sep;

const DEFAULT_MEM_LIMIT_MB: usize = 512;
const ACTIVE_PROCESS_LIMIT: u32 = 8;

/// Wrapper RAII su un HANDLE Win32: chiude alla `Drop`, cosi' un `?` in
/// qualunque punto della sequenza non perde l'handle (leak) ne' lascia il
/// job/thread/snapshot orfano.
struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe { CloseHandle(self.0) };
        }
    }
}

fn win_err(op: &str) -> anyhow::Error {
    anyhow::anyhow!("{op} fallita (GetLastError={})", unsafe { GetLastError() })
}

fn check_bool(op: &str, ok: BOOL) -> Result<()> {
    if ok == 0 {
        bail!(win_err(op));
    }
    Ok(())
}

/// Crea un Job Object con i limiti di contenimento (§16.2): kill-on-close
/// (l'albero muore anche se il client crasha), memoria e conteggio processi.
fn create_job() -> Result<OwnedHandle> {
    let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if job.is_null() {
        return Err(win_err("CreateJobObjectW"));
    }
    let job = OwnedHandle(job);

    let mem_mb: usize = std::env::var("METNOS_EXEC_MEM_MB")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_MEM_LIMIT_MB);

    let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    info.BasicLimitInformation.ActiveProcessLimit = ACTIVE_PROCESS_LIMIT;
    info.ProcessMemoryLimit = mem_mb.saturating_mul(1024 * 1024);

    let ok = unsafe {
        SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    };
    check_bool("SetInformationJobObject", ok)?;
    Ok(job)
}

/// Risolve e riprende il thread primario del processo appena creato
/// CREATE_SUSPENDED. `CreateProcessW` restituirebbe l'handle del thread
/// direttamente, ma std/tokio non lo espongono: Toolhelp32 e' la via
/// documentata per recuperarlo dal solo PID (contratto §16.2).
fn resume_primary_thread(pid: u32) -> Result<()> {
    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snap == INVALID_HANDLE_VALUE {
        return Err(win_err("CreateToolhelp32Snapshot"));
    }
    let snap = OwnedHandle(snap);

    let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
    entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;

    let mut ok = unsafe { Thread32First(snap.0, &mut entry) };
    let mut found_tid: Option<u32> = None;
    while ok != 0 {
        if entry.th32OwnerProcessID == pid {
            found_tid = Some(entry.th32ThreadID);
            break;
        }
        ok = unsafe { Thread32Next(snap.0, &mut entry) };
    }
    let tid = found_tid
        .with_context(|| format!("nessun thread trovato per il processo {pid} (Toolhelp32)"))?;

    let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, tid) };
    if thread.is_null() {
        return Err(win_err("OpenThread"));
    }
    let thread = OwnedHandle(thread);

    let prev = unsafe { ResumeThread(thread.0) };
    if prev == u32::MAX {
        return Err(win_err("ResumeThread"));
    }
    Ok(())
}

/// Esegue `exec` con `python` passando `args_json` su stdin, in un Job
/// Object. Firma identica a `sandbox_linux::run_sandboxed` (§16.2).
pub async fn run_sandboxed(
    exec: &CachedExecutor,
    python: &Path,
    shim_dir: &Path,
    args_json: &str,
    extra_env: &[(String, String)],
    limits: &Limits,
) -> Result<SandboxOutput> {
    let job = create_job().context("creazione job object")?;

    // Working dir scratch per-invocazione sotto %TEMP% (§16.2), rimossa a
    // fine esecuzione qualunque sia l'esito (guard RAII).
    let scratch = ScratchDir::create()?;

    let mut cmd = Command::new(python);
    cmd.arg(&exec.entry);

    let pythonpath = format!("{}{}{}", shim_dir.display(), pythonpath_sep(), exec.dir.display());
    cmd.env_clear();
    cmd.env(
        "PATH",
        std::env::var("PATH").unwrap_or_else(|_| r"C:\Windows\System32".into()),
    );
    cmd.env("PYTHONPATH", &pythonpath);
    cmd.env("METNOS_RUNTIME", shim_dir);
    cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    cmd.current_dir(&scratch.path);
    cmd.creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW);
    cmd.kill_on_drop(true);
    cmd.stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn().context("spawn sandboxed executor (windows)")?;
    let pid = child.id().context("PID del processo appena creato assente")?;
    let raw_handle: RawHandle = child
        .raw_handle()
        .context("raw handle del processo appena creato assente")?;

    // AssignProcessToJobObject PRIMA del resume: il figlio non gira mai
    // fuori dal contenimento, nemmeno per un istante.
    let ok = unsafe { AssignProcessToJobObject(job.0, raw_handle as HANDLE) };
    check_bool("AssignProcessToJobObject", ok)?;

    resume_primary_thread(pid).context("resume del thread primario")?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(args_json.as_bytes()).await.ok();
        drop(stdin);
    }

    let out = match tokio::time::timeout(limits.wall, child.wait_with_output()).await {
        Ok(out) => {
            let out = out.context("wait executor (windows)")?;
            SandboxOutput {
                stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
                stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
                timed_out: false,
                sandbox: "job-object".into(),
            }
        }
        Err(_) => {
            // Deadline superata: gemello Windows del SIGKILL-al-gruppo unix.
            // KILL_ON_JOB_CLOSE coprirebbe anche il drop del job handle a
            // fine funzione, ma terminiamo esplicitamente per non aspettare.
            let ok = unsafe { TerminateJobObject(job.0, 137) };
            if ok == 0 {
                tracing::warn!(
                    executor = %exec.name,
                    "TerminateJobObject fallita (GetLastError={}); kill_on_drop \
                     del Child resta come rete di sicurezza",
                    unsafe { GetLastError() }
                );
            }
            tracing::warn!(
                executor = %exec.name, wall_s = limits.wall.as_secs(),
                "deadline superata: job object terminato"
            );
            SandboxOutput {
                stdout: String::new(),
                stderr: "deadline exceeded".into(),
                timed_out: true,
                sandbox: "job-object".into(),
            }
        }
    };
    Ok(out)
    // `job` e `scratch` droppano qui: CloseHandle(job) con KILL_ON_JOB_CLOSE
    // (rete di sicurezza se per qualche motivo un discendente e' sopravvissuto)
    // + rimozione della dir scratch.
}

/// Dir scratch per-invocazione sotto `%TEMP%`, rimossa alla `Drop` (§16.2).
struct ScratchDir {
    path: std::path::PathBuf,
}

impl ScratchDir {
    fn create() -> Result<Self> {
        let path = std::env::temp_dir().join(format!(
            "metnos-exec-{}-{}",
            std::process::id(),
            unique_suffix()
        ));
        std::fs::create_dir_all(&path)
            .with_context(|| format!("creazione scratch dir {}", path.display()))?;
        Ok(Self { path })
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// Suffisso univoco senza dipendere da `rand`/orologio ad-hoc: indirizzo di
/// una allocazione stack, sufficiente a evitare collisioni fra invocazioni
/// concorrenti nello stesso processo (il PID gia' distingue fra processi).
fn unique_suffix() -> usize {
    let x = 0u8;
    &x as *const u8 as usize
}
