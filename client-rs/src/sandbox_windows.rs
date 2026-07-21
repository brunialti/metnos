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
//! Onestà sul livello di protezione (§9 design doc): il Job Object e'
//! contenimento di RISORSE e PRIVILEGI, NON isolamento del filesystem.
//! L'isolamento vero (capability→ACL) e' l'AppContainer (W4, `appcontainer.rs`),
//! attivato dal gate `METNOS_SANDBOX_APPCONTAINER` (default ON su Windows) e
//! stratificato SOTTO questo job (i due strati COESISTONO). Label onesta nel
//! result: `sandbox:"appcontainer"` SOLO se il container e' davvero costruito,
//! altrimenti `"job-object"` con `sandbox_downgrade_reason` (§2.8).
//!
//! Sequenza (l'ordine E' il contratto — evita la finestra in cui il figlio
//! gira fuori dal job): CreateJobObjectW → SetInformationJobObject → spawn
//! CREATE_SUSPENDED|CREATE_NO_WINDOW → AssignProcessToJobObject → risolvi il
//! thread primario via Toolhelp32 (CreateProcess non lo espone a std/tokio)
//! → ResumeThread → wait con deadline → timeout: TerminateJobObject.

use anyhow::{bail, Context, Result};
use std::os::windows::io::RawHandle;
use std::path::{Path, PathBuf};
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

use crate::appcontainer;
use crate::executors::{CachedExecutor, Capability};
use crate::sandbox_common::HintGrant;
// Tipi condivisi col modulo linux (§16.2: "minimo diff" = ri-esportati, non
// duplicati). sandbox_linux.rs compila su entrambe le piattaforme.
pub use crate::sandbox_linux::{Limits, SandboxOutput};
use crate::sandbox_linux::pythonpath_sep;

const DEFAULT_MEM_LIMIT_MB: usize = 512;
const ACTIVE_PROCESS_LIMIT: u32 = 8;

/// Wrapper RAII su un HANDLE Win32: chiude alla `Drop`, cosi' un `?` in
/// qualunque punto della sequenza non perde l'handle (leak) ne' lascia il
/// job/thread/snapshot orfano.
pub(crate) struct OwnedHandle(HANDLE);

impl OwnedHandle {
    /// HANDLE grezzo per le API che lo USANO senza prenderne possesso
    /// (AssignProcessToJobObject/TerminateJobObject dal percorso AppContainer,
    /// W4). Il possesso resta all'`OwnedHandle`: CloseHandle avviene alla Drop.
    pub(crate) fn raw(&self) -> HANDLE {
        self.0
    }
}

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
/// `pub(crate)`: lo riusa anche il percorso AppContainer (W4), che crea il
/// proprio job dentro il thread bloccante (l'HANDLE non e' Send).
pub(crate) fn create_job() -> Result<OwnedHandle> {
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
///
/// Il Job Object e' una primitiva del kernel Windows, sempre disponibile
/// (a differenza di `bwrap` su Linux, un tool userspace che puo' mancare):
/// per costruzione E' IL DEFAULT, nessun opt-in richiesto — simmetrico a
/// come Linux usa bwrap-se-disponibile senza flag. `METNOS_SANDBOX=off` ha
/// lo STESSO significato su entrambe le piattaforme: salta il contenimento
/// ed esegue diretto (opt-out esplicito per debug), MAI l'unico modo di
/// ottenere un'esecuzione. (Fix 3/7: il gate `#[cfg(windows)]` pre-W3.1 in
/// `runner.rs` invertiva questa semantica — rifiutava di default e
/// richiedeva `off` per arrivare qui. Rimosso: era un fossile della
/// finestra prima che questo modulo esistesse, §16.1/§16.2 storico.)
/// B.5 (fase 7): livello di contenimento corrente per l'heartbeat — gemello
/// windows di `sandbox_linux::sandbox_level`. "none" con METNOS_SANDBOX=off;
/// "appcontainer" se il gate W4 e' attivo E il profilo si costruisce davvero
/// (probe ONESTA cacheata, `appcontainer::probe_supported`); altrimenti
/// "job-object". Telemetria per il gate `min_sandbox` server-side (W4.4).
pub fn sandbox_level() -> &'static str {
    if crate::sandbox_linux::sandbox_disabled() {
        "none"
    } else if appcontainer::probe_supported() {
        "appcontainer"
    } else {
        "job-object"
    }
}

/// Environment dell'executor (§16.2): costruito UNA volta e condiviso dai due
/// percorsi — il job-object (tokio `Command`) e l'AppContainer (blocco UTF-16
/// per `CreateProcessW`). Stessi valori del wiring precedente, ora in un solo
/// posto per evitare drift fra i due spawn.
///
/// - `USERPROFILE/HOMEDRIVE/HOMEPATH` (0.2.13): senza, `~`/`Path.home()` nei
///   moduli shim crashavano — «scrivi in ~/x sul PC» era rotto.
/// - `SystemRoot/windir/ComSpec/...` (0.2.14): senza, i tool nativi che toccano
///   WMI fallivano «Impossibile trovare il modulo specificato» (tasklist rc=1).
/// - `LOCALAPPDATA/APPDATA/USERNAME/ALLUSERSPROFILE` (W4): l'AppContainer monta
///   lo storage redirette sotto `%LOCALAPPDATA%\Packages\<nome>` durante
///   CreateProcessW; senza `LOCALAPPDATA` nel blocco env fallisce con
///   `ERROR_ENVVAR_NOT_FOUND` (203). Innocui per il path job-object.
/// - `TEMP/TMP` puntati allo scratch della sandbox.
fn build_env(
    shim_dir: &Path,
    exec_dir: &Path,
    scratch: &Path,
    extra_env: &[(String, String)],
) -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = Vec::new();
    let pythonpath =
        format!("{}{}{}", shim_dir.display(), pythonpath_sep(), exec_dir.display());
    env.push((
        "PATH".into(),
        std::env::var("PATH").unwrap_or_else(|_| r"C:\Windows\System32".into()),
    ));
    env.push(("PYTHONPATH".into(), pythonpath));
    env.push(("METNOS_RUNTIME".into(), shim_dir.display().to_string()));
    env.push(("PYTHONDONTWRITEBYTECODE".into(), "1".into()));
    env.push(("PYTHONUTF8".into(), "1".into()));
    env.push(("PYTHONIOENCODING".into(), "utf-8".into()));
    for var in ["USERPROFILE", "HOMEDRIVE", "HOMEPATH"] {
        if let Ok(v) = std::env::var(var) {
            env.push((var.to_string(), v));
        }
    }
    for var in [
        "SystemRoot", "windir", "ComSpec", "SystemDrive", "ProgramFiles",
        "ProgramData", "NUMBER_OF_PROCESSORS",
    ] {
        if let Ok(v) = std::env::var(var) {
            env.push((var.to_string(), v));
        }
    }
    // W4 (AppContainer): CreateProcessW monta lo storage redirette del container
    // sotto `%LOCALAPPDATA%\Packages\<nome>` → senza LOCALAPPDATA nel blocco env
    // fallisce con ERROR_ENVVAR_NOT_FOUND (203). Gli altri completano l'ambiente
    // utente. Incondizionati: innocui anche per il percorso job-object.
    for var in ["LOCALAPPDATA", "APPDATA", "USERNAME", "ALLUSERSPROFILE"] {
        if let Ok(v) = std::env::var(var) {
            env.push((var.to_string(), v));
        }
    }
    env.push(("TEMP".into(), scratch.display().to_string()));
    env.push(("TMP".into(), scratch.display().to_string()));
    for (k, v) in extra_env {
        env.push((k.clone(), v.clone()));
    }
    env
}

/// Directory ESISTENTE piu' profonda che copre `cand`: se `cand` e' una dir
/// esistente la ritorna, se e' un file (o un target ancora da creare) risale
/// ai genitori fino alla prima dir reale. `~` espanso alla home del device.
/// `None` se nessun antenato esiste (radice irraggiungibile → grant inutile).
fn deepest_existing_dir(cand: &str) -> Option<PathBuf> {
    let expanded = if let Some(rest) =
        cand.strip_prefix("~/").or_else(|| cand.strip_prefix("~\\"))
    {
        dirs::home_dir()?.join(rest)
    } else if cand == "~" {
        dirs::home_dir()?
    } else {
        PathBuf::from(cand)
    };
    let mut p: &Path = &expanded;
    loop {
        if p.is_dir() {
            return Some(p.to_path_buf());
        }
        match p.parent() {
            Some(par) if !par.as_os_str().is_empty() => p = par,
            _ => return None,
        }
    }
}

/// Grant ACL derivati dai path-target CONCRETI dell'invocazione (§W4: la
/// sandbox forte concede esattamente le directory che il comando tocca, non
/// gli hint illustrativi del manifest). Accesso = write se l'executor dichiara
/// `fs:write`, altrimenti read; un executor senza capability fs non riceve
/// grant (`caps_fs_access` → None). Ogni target e' risolto alla sua dir
/// esistente piu' profonda (creabile un file dentro, leggibile una dir).
fn arg_target_grants(args_json: &str, caps: &[Capability]) -> Vec<HintGrant> {
    let write = match crate::sandbox_common::caps_fs_access(caps) {
        Some(w) => w,
        None => return Vec::new(),
    };
    let mut out: Vec<HintGrant> = Vec::new();
    for cand in crate::sandbox_common::extract_path_args(args_json) {
        if let Some(root) = deepest_existing_dir(&cand) {
            if !out.iter().any(|g| g.root == root) {
                out.push(HintGrant { root, write });
            }
        }
    }
    out
}

pub async fn run_sandboxed(
    exec: &CachedExecutor,
    python: &Path,
    shim_dir: &Path,
    args_json: &str,
    extra_env: &[(String, String)],
    limits: &Limits,
) -> Result<SandboxOutput> {
    let disabled = crate::sandbox_linux::sandbox_disabled();

    // Working dir scratch per-invocazione sotto %TEMP% (§16.2), CONDIVISA dai
    // due percorsi (AppContainer e job-object) e rimossa a fine esecuzione
    // qualunque sia l'esito (guard RAII).
    let scratch = ScratchDir::create()?;
    // Env dell'executor costruito UNA volta: stessi valori per entrambi i path.
    let env_pairs = build_env(shim_dir, &exec.dir, &scratch.path, extra_env);

    // Motivo di eventuale declassamento (§2.8): None finche' il container non
    // fallisce la COSTRUZIONE (dopo lo spawn non si degrada piu').
    let mut downgrade: Option<String> = None;

    // W4 fix (bug scoperto abilitando l'AppContainer in prod, 7/7/2026): un
    // executor che spawna sottoprocessi DI SISTEMA (capability `code:exec` o le
    // storiche `exec_*`: tasklist, ps, pip, tesseract, ...) NON e'
    // AppContainer-izzabile — il token ristretto del container nega a quei tool
    // l'accesso a WMI/RPC e falliscono (`get_processes` → `tasklist rc=1`).
    // Regola capability-driven (§7.3, `needs_system_exec`), non lista di
    // executor. Il Job Object sotto li contiene comunque (albero/memoria/conteggio).
    let system_exec = crate::sandbox_common::needs_system_exec(&exec.capabilities);
    let unanchored_fs_path =
        crate::sandbox_common::caps_fs_access(&exec.capabilities).is_some()
        && crate::sandbox_common::has_unanchored_path_args(args_json);
    let want_appcontainer = !disabled && appcontainer::gate_on();
    if want_appcontainer && system_exec {
        // Declassamento ONESTO (§2.8): il gate e' ON ma questo executor non e'
        // containerizzabile → il result dira' sandbox="job-object" con il motivo.
        downgrade = Some(
            "executor con capability code:exec/exec_*: sottoprocessi di sistema \
             non AppContainer-izzabili (declassato a job-object)"
                .into(),
        );
    }
    if want_appcontainer && !system_exec && unanchored_fs_path {
        // Il significato di un path relativo appartiene all'executor/shim e
        // puo' essere il workspace Metnos, una user-dir localizzata o un alias.
        // Prima dello spawn il client non possiede quella risoluzione: non puo'
        // costruire un ACL stretto e non deve tentare scope illustrativi ampi.
        downgrade = Some(
            "percorso filesystem relativo non ancorabile prima dello spawn; \
             declassato a job-object"
                .into(),
        );
    }

    // --- Percorso AppContainer (W4): isolamento fs/rete DENTRO il job. Gate
    // METNOS_SANDBOX_APPCONTAINER default ON su Windows (7/7/2026): opt-OUT con
    // =0 salta questo blocco e il percorso job-object sotto resta byte-identico a W3.3.
    if want_appcontainer && !system_exec && !unanchored_fs_path {
        let (mut grants, want_net) = crate::sandbox_common::hint_grants(&exec.capabilities);
        // Grant sui path-target CONCRETI dell'invocazione (Documents, Downloads,
        // …): senza, la sandbox forte concederebbe solo gli scope-esempio del
        // manifest e un comando su una dir utente reale fallirebbe Access Denied.
        grants.extend(arg_target_grants(args_json, &exec.capabilities));
        // Dir dati dello shim (METNOS_USER_DATA, iniettata dal runner): config.py
        // ci crea l'albero user a import + ci finiscono i blob undo. Isolata e
        // client-owned → il container puo' scriverla senza aprire ~/.local/share.
        if let Some((_, ud)) = env_pairs.iter().find(|(k, _)| k == "METNOS_USER_DATA") {
            let root = PathBuf::from(ud);
            if !grants.iter().any(|g| g.root == root) {
                grants.push(HintGrant { root, write: true });
            }
        }
        let home = dirs::home_dir();
        let broad_grant = grants.iter().any(|grant| {
            crate::sandbox_common::is_broad_acl_root(
                &grant.root, home.as_deref())
        });
        if broad_grant {
            // SetNamedSecurityInfoW puo' propagare un ACE ereditabile su tutta
            // la home/volume PRIMA dello spawn: la deadline del processo non
            // sarebbe ancora attiva e il poller resterebbe bloccato. Il Job
            // Object conserva contenimento risorse e kill d'albero; il motivo
            // viaggia nel result senza esporre il path utente.
            let reason = "scope filesystem home/volume troppo ampio per un ACL AppContainer; declassato a job-object".to_string();
            tracing::warn!(executor = %exec.name, "{reason}");
            downgrade = Some(reason);
        } else {
            let params = appcontainer::ContainerParams {
                python: python.to_path_buf(),
                entry: exec.entry.clone(),
                shim_dir: shim_dir.to_path_buf(),
                exec_dir: exec.dir.clone(),
                scratch_dir: scratch.path.clone(),
                env_pairs: env_pairs.clone(),
                args_json: args_json.to_string(),
                deadline_ms: limits.wall.as_millis().min(u128::from(u64::MAX)) as u64,
                grants,
                want_net,
            };
            // FFI bloccante (job + pipe + CreateProcessW): fuori dai worker async.
            match tokio::task::spawn_blocking(move || appcontainer::run_in_container(params)).await {
                Ok(appcontainer::Outcome::Ran { stdout, stderr, timed_out }) => {
                    if timed_out {
                        tracing::warn!(
                            executor = %exec.name, wall_s = limits.wall.as_secs(),
                            "deadline superata (appcontainer): albero terminato via job"
                        );
                    }
                    return Ok(SandboxOutput {
                        stdout,
                        stderr,
                        timed_out,
                        sandbox: "appcontainer".into(),
                        downgrade_reason: None,
                    });
                }
                Ok(appcontainer::Outcome::Unsupported(reason)) => {
                    tracing::warn!(
                        executor = %exec.name,
                        "AppContainer non costruito: degrado ONESTO a job-object ({reason})"
                    );
                    downgrade = Some(reason);
                }
                Err(join_err) => {
                    let reason = format!("task appcontainer interrotto: {join_err}");
                    tracing::warn!("{reason}");
                    downgrade = Some(reason);
                }
            }
        }
    }

    // --- Percorso job-object (W3.3, INVARIATO): default e fallback onesto.
    let use_job_object = !disabled;
    let sandbox_label = if use_job_object { "job-object" } else { "none" };
    if !use_job_object {
        // Asimmetria onesta (§12): su Linux "off" toglie SOLO il wrapping
        // bwrap, il kill-al-timeout resta forte (process group, primitiva
        // POSIX indipendente). Su Windows il Job Object E' il meccanismo di
        // tree-kill: "off" lo toglie e con esso la garanzia sull'albero —
        // kill_on_drop resta come rete (SOLO sul figlio diretto).
        tracing::warn!(
            "Job Object non attivo (METNOS_SANDBOX off): esecuzione diretta di {} \
             (kill-al-timeout ridotto al solo processo diretto, niente albero)",
            exec.name
        );
    }

    let job = if use_job_object {
        Some(create_job().context("creazione job object")?)
    } else {
        None
    };

    let mut cmd = Command::new(python);
    cmd.arg(&exec.entry);
    cmd.env_clear();
    for (k, v) in &env_pairs {
        cmd.env(k, v);
    }
    cmd.current_dir(&scratch.path);
    // CREATE_SUSPENDED solo se dobbiamo assegnare il job PRIMA del resume
    // (evita la finestra in cui il figlio gira fuori dal contenimento);
    // senza Job Object non serve sospendere nulla.
    cmd.creation_flags(if use_job_object {
        CREATE_SUSPENDED | CREATE_NO_WINDOW
    } else {
        CREATE_NO_WINDOW
    });
    cmd.kill_on_drop(true);
    cmd.stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn().context("spawn sandboxed executor (windows)")?;

    if let Some(job) = &job {
        let pid = child.id().context("PID del processo appena creato assente")?;
        let raw_handle: RawHandle = child
            .raw_handle()
            .context("raw handle del processo appena creato assente")?;
        // AssignProcessToJobObject PRIMA del resume: il figlio non gira mai
        // fuori dal contenimento, nemmeno per un istante.
        let ok = unsafe { AssignProcessToJobObject(job.0, raw_handle as HANDLE) };
        check_bool("AssignProcessToJobObject", ok)?;
        resume_primary_thread(pid).context("resume del thread primario")?;
    }

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
                sandbox: sandbox_label.into(),
                downgrade_reason: downgrade.clone(),
            }
        }
        Err(_) => {
            // Deadline superata: gemello Windows del SIGKILL-al-gruppo unix,
            // SOLO se il job esiste. KILL_ON_JOB_CLOSE coprirebbe anche il
            // drop dell'handle a fine funzione, ma terminiamo esplicitamente
            // per non aspettare. Senza job, resta kill_on_drop (§12, sopra).
            if let Some(job) = &job {
                let ok = unsafe { TerminateJobObject(job.0, 137) };
                if ok == 0 {
                    tracing::warn!(
                        executor = %exec.name,
                        "TerminateJobObject fallita (GetLastError={}); kill_on_drop \
                         del Child resta come rete di sicurezza",
                        unsafe { GetLastError() }
                    );
                }
            }
            tracing::warn!(
                executor = %exec.name, wall_s = limits.wall.as_secs(),
                "deadline superata: {}", if job.is_some() { "job object terminato" }
                                          else { "kill_on_drop sul processo diretto" }
            );
            SandboxOutput {
                stdout: String::new(),
                stderr: "deadline exceeded".into(),
                timed_out: true,
                sandbox: sandbox_label.into(),
                downgrade_reason: downgrade.clone(),
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
