#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

#[cfg(not(windows))]
fn main() { eprintln!("Windows AppContainer is required"); std::process::exit(125); }

#[cfg(windows)]
mod windows_helper {
    use base64::Engine;
    use serde::{Deserialize, Serialize};
    use sha2::{Digest, Sha256};
    use std::fs;
    use std::io::{Read, Write};
    use std::os::windows::fs::MetadataExt;
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};
    use std::time::Instant;

    #[path = "../../appcontainer.rs"] pub(crate) mod appcontainer;
    #[path = "../../sandbox_common.rs"] pub(crate) mod sandbox_common;

    pub(crate) mod executors {
        #[derive(Debug, Clone)]
        pub struct Capability { pub name: String, pub hint: Vec<String> }
    }
    pub(crate) mod config {
        use anyhow::Result;
        use std::path::PathBuf;
        use std::sync::OnceLock;
        static DATA_DIR: OnceLock<PathBuf> = OnceLock::new();
        pub struct Paths { pub data_dir: PathBuf }
        pub fn bind_request(request_id: &str) -> Result<()> {
            let safe = request_id.strip_prefix("sha256:").ok_or_else(|| anyhow::anyhow!("request id"))?;
            DATA_DIR.set(std::env::temp_dir().join("metnos-birth-helper-state").join(safe))
                .map_err(|_| anyhow::anyhow!("request state already bound"))
        }
        pub fn request_dir() -> Option<PathBuf> { DATA_DIR.get().cloned() }
        impl Paths {
            pub fn resolve() -> Result<Self> {
                Ok(Self { data_dir: DATA_DIR.get().ok_or_else(|| anyhow::anyhow!("request state unbound"))?.clone() })
            }
        }
    }
    pub(crate) mod sandbox_windows {
        use anyhow::{bail, Result};
        use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE};
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
            JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOB_OBJECT_LIMIT_PROCESS_MEMORY,
        };
        pub(crate) struct OwnedHandle(HANDLE);
        impl OwnedHandle { pub(crate) fn raw(&self) -> HANDLE { self.0 } }
        impl Drop for OwnedHandle { fn drop(&mut self) { unsafe { CloseHandle(self.0); } } }
        pub(crate) fn create_job() -> Result<OwnedHandle> {
            let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if raw.is_null() { bail!("CreateJobObjectW:{}", unsafe { GetLastError() }); }
            let job = OwnedHandle(raw);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
                | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
            info.BasicLimitInformation.ActiveProcessLimit = 32;
            info.ProcessMemoryLimit = 256 * 1024 * 1024;
            info.JobMemoryLimit = 256 * 1024 * 1024;
            let ok = unsafe { SetInformationJobObject(raw, JobObjectExtendedLimitInformation,
                &info as *const _ as *const _, std::mem::size_of_val(&info) as u32) };
            if ok == 0 { bail!("SetInformationJobObject:{}", unsafe { GetLastError() }); }
            Ok(job)
        }
    }

    const PROFILE: &str = "Metnos.ExecutorBirth.V1";
    const LIMIT: usize = 1024 * 1024;
    const REPARSE: u32 = 0x400;

    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Config { schema_version: u32, runtime_root: String, runtime_binary: String,
                    runtime_binary_hash: String }
    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Request { schema_version: u32, request_id: String, candidate_id: String,
                     phase: String, private_root: String, entrypoint: String,
                     arguments: Vec<String> }
    #[derive(Serialize)]
    struct Attestation<'a> {
        backend: &'a str, helper_binary_hash: String, runtime_binary_hash: String,
        profile_name: &'a str, appcontainer_sid: String, network_capability: bool,
        assigned_before_resume: bool, active_processes: u32, tree_empty: bool,
        termination_attested: bool, memory_limit_bytes: usize, process_limit: u32,
        stdout_limit_bytes: usize, stderr_limit_bytes: usize,
    }
    #[derive(Serialize)]
    struct Response<'a> {
        schema_version: u32, request_id: &'a str, candidate_id: &'a str, status: &'a str,
        error_code: Option<&'a str>, exit_code: Option<i32>, stdout_base64: String,
        stderr_base64: String, stdout_bytes: usize, stderr_bytes: usize,
        stdout_truncated: bool, stderr_truncated: bool, elapsed_ms: u128,
        attestation: Attestation<'a>,
    }

    fn digest(bytes: &[u8]) -> String { format!("sha256:{:x}", Sha256::digest(bytes)) }
    fn canonical<T: serde::Serialize>(value: &T) -> Vec<u8> {
        serde_json::to_vec(&serde_json::to_value(value).unwrap()).unwrap()
    }
    fn regular_no_reparse(path: &Path) -> anyhow::Result<fs::Metadata> {
        let md = fs::symlink_metadata(path)?;
        anyhow::ensure!(md.is_file() && md.file_attributes() & REPARSE == 0, "not regular");
        Ok(md)
    }
    fn config_acl_is_admin_system_only(path: &Path) -> anyhow::Result<()> {
        use windows_sys::Win32::Foundation::LocalFree;
        use windows_sys::Win32::Security::Authorization::{
            GetNamedSecurityInfoW, SE_FILE_OBJECT,
        };
        use windows_sys::Win32::Security::{
            CreateWellKnownSid, EqualSid, GetAce, ACCESS_ALLOWED_ACE, ACL,
            DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID,
            WinBuiltinAdministratorsSid, WinLocalSystemSid,
        };
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let code = unsafe { GetNamedSecurityInfoW(wide.as_ptr(), SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION, std::ptr::null_mut(), std::ptr::null_mut(),
            &mut dacl, std::ptr::null_mut(), &mut descriptor) };
        anyhow::ensure!(code == 0 && !dacl.is_null() && !descriptor.is_null(), "config ACL unavailable");
        struct Descriptor(PSECURITY_DESCRIPTOR);
        impl Drop for Descriptor { fn drop(&mut self) { unsafe { LocalFree(self.0); } } }
        let _descriptor = Descriptor(descriptor);
        fn well_known(kind: i32) -> anyhow::Result<Vec<u8>> {
            let mut bytes = vec![0u8; 68]; let mut size = bytes.len() as u32;
            let ok = unsafe { CreateWellKnownSid(kind, std::ptr::null_mut(),
                bytes.as_mut_ptr() as PSID, &mut size) };
            anyhow::ensure!(ok != 0, "well-known SID unavailable"); bytes.truncate(size as usize); Ok(bytes)
        }
        let admins = well_known(WinBuiltinAdministratorsSid)?;
        let system = well_known(WinLocalSystemSid)?;
        let write_mask = 0x500D_0116u32; // GENERIC_WRITE/ALL + file writes + DELETE/WRITE_DAC/OWNER
        let mut trusted_writer = false;
        let ace_count = unsafe { (*dacl).AceCount } as u32;
        for index in 0..ace_count {
            let mut raw: *mut core::ffi::c_void = std::ptr::null_mut();
            anyhow::ensure!(unsafe { GetAce(dacl, index, &mut raw) } != 0 && !raw.is_null(), "config ACE unavailable");
            let header = unsafe { &*(raw as *const windows_sys::Win32::Security::ACE_HEADER) };
            if header.AceType != 0 { continue; } // ACCESS_ALLOWED_ACE_TYPE
            let ace = unsafe { &*(raw as *const ACCESS_ALLOWED_ACE) };
            if ace.Mask & write_mask == 0 { continue; }
            let sid = &ace.SidStart as *const u32 as PSID;
            let trusted = unsafe { EqualSid(sid, admins.as_ptr() as PSID) != 0
                || EqualSid(sid, system.as_ptr() as PSID) != 0 };
            anyhow::ensure!(trusted, "config ACL has untrusted writer");
            trusted_writer = true;
        }
        anyhow::ensure!(trusted_writer, "config ACL lacks admin/SYSTEM writer");
        Ok(())
    }
    fn valid_digest(value: &str) -> bool {
        value.len() == 71 && value.starts_with("sha256:")
            && value[7..].bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }
    fn descendants_no_reparse(root: &Path) -> anyhow::Result<()> {
        for entry in fs::read_dir(root)? {
            let entry = entry?; let path = entry.path(); let md = fs::symlink_metadata(&path)?;
            anyhow::ensure!(md.file_attributes() & REPARSE == 0, "reparse point");
            if md.is_dir() { descendants_no_reparse(&path)?; }
        }
        Ok(())
    }
    fn unavailable<'a>(req: &'a Request, started: Instant, helper_hash: String,
                       runtime_hash: String, code: &'a str) -> Response<'a> {
        Response { schema_version: 1, request_id: &req.request_id, candidate_id: &req.candidate_id,
            status: "test_environment_unavailable", error_code: Some(code), exit_code: None,
            stdout_base64: String::new(), stderr_base64: String::new(), stdout_bytes: 0,
            stderr_bytes: 0, stdout_truncated: false, stderr_truncated: false,
            elapsed_ms: started.elapsed().as_millis(), attestation: Attestation {
                backend: "windows-appcontainer-job-v1", helper_binary_hash: helper_hash,
                runtime_binary_hash: runtime_hash, profile_name: PROFILE,
                appcontainer_sid: String::new(), network_capability: false,
                assigned_before_resume: false, active_processes: 0, tree_empty: false,
                termination_attested: false, memory_limit_bytes: 256*1024*1024,
                process_limit: 32, stdout_limit_bytes: LIMIT, stderr_limit_bytes: LIMIT } }
    }

    pub fn run() -> anyhow::Result<()> {
        let started = Instant::now();
        let args: Vec<String> = std::env::args().collect();
        anyhow::ensure!(args.len() == 5 && args[1] == "--config" && args[3] == "--config-hash",
                        "arguments invalid");
        anyhow::ensure!(valid_digest(&args[4]), "config hash invalid");
        let config_path = PathBuf::from(&args[2]);
        anyhow::ensure!(config_path.is_absolute(), "config path relative");
        regular_no_reparse(&config_path)?;
        config_acl_is_admin_system_only(&config_path)?;
        let helper_exe = std::env::current_exe()?.canonicalize()?;
        let install_root = helper_exe.parent().ok_or_else(|| anyhow::anyhow!("helper root unavailable"))?;
        anyhow::ensure!(config_path.canonicalize()?.starts_with(install_root), "config outside install root");
        let config_raw = fs::read(&config_path)?;
        anyhow::ensure!(digest(&config_raw) == args[4], "config hash mismatch");
        let config_value: serde_json::Value = serde_json::from_slice(&config_raw)?;
        anyhow::ensure!(canonical(&config_value) == config_raw, "config noncanonical");
        let config: Config = serde_json::from_value(config_value)?;
        anyhow::ensure!(config.schema_version == 1,
                        "config noncanonical");
        anyhow::ensure!(valid_digest(&config.runtime_binary_hash), "runtime hash invalid");
        let runtime_root = PathBuf::from(&config.runtime_root).canonicalize()?;
        let runtime = PathBuf::from(&config.runtime_binary).canonicalize()?;
        anyhow::ensure!(runtime_root.is_absolute() && runtime.starts_with(&runtime_root), "runtime escapes root");
        regular_no_reparse(&runtime)?;
        anyhow::ensure!(digest(&fs::read(&runtime)?) == config.runtime_binary_hash, "runtime hash mismatch");
        let helper_hash = digest(&fs::read(helper_exe)?);

        let mut wire = Vec::new(); std::io::stdin().take(1024 * 1024).read_to_end(&mut wire)?;
        let req_value: serde_json::Value = serde_json::from_slice(&wire)?;
        anyhow::ensure!(canonical(&req_value) == wire, "request noncanonical");
        let req: Request = serde_json::from_value(req_value)?;
        anyhow::ensure!(req.schema_version == 1, "request schema invalid");
        anyhow::ensure!(valid_digest(&req.request_id) && valid_digest(&req.candidate_id), "binding invalid");
        config::bind_request(&req.request_id)?;
        anyhow::ensure!(matches!(req.phase.as_str(), "candidate"|"reference"|"equivalence"), "phase invalid");
        anyhow::ensure!(req.arguments.len() <= 32 && req.arguments.iter().all(|v| !v.contains('\0') && v.as_bytes().len() <= 4096), "arguments invalid");
        anyhow::ensure!(!req.entrypoint.contains('\\') && !req.entrypoint.starts_with('/')
            && req.entrypoint.ends_with(".py") && req.entrypoint.split('/').all(|p| !matches!(p, ""|"."|"..")), "entrypoint invalid");
        let private_root = PathBuf::from(&req.private_root).canonicalize()?;
        anyhow::ensure!(private_root.is_absolute(), "private root relative");
        let mut top: Vec<_> = fs::read_dir(&private_root)?.map(|e| e.map(|x| x.file_name())).collect::<Result<_,_>>()?;
        top.sort(); anyhow::ensure!(top == vec!["candidate", "work"], "private layout invalid");
        let candidate = private_root.join("candidate"); let work = private_root.join("work");
        descendants_no_reparse(&candidate)?; descendants_no_reparse(&work)?;
        let entry = candidate.join(req.entrypoint.replace('/', "\\")); regular_no_reparse(&entry)?;
        let env = vec![
            ("APPDATA".into(), work.display().to_string()), ("HOME".into(), work.display().to_string()),
            ("LANG".into(), "C.UTF-8".into()), ("LC_ALL".into(), "C.UTF-8".into()),
            ("LOCALAPPDATA".into(), work.display().to_string()), ("PATH".into(), String::new()),
            ("TEMP".into(), work.display().to_string()), ("TMP".into(), work.display().to_string()),
            ("TZ".into(), "UTC".into()), ("USERPROFILE".into(), work.display().to_string()),
        ];
        let outcome = appcontainer::run_in_container(appcontainer::ContainerParams {
            profile_name: PROFILE.into(), python: runtime, entry, shim_dir: candidate.clone(),
            exec_dir: candidate, scratch_dir: work, env_pairs: env, args_json: String::new(),
            command_args: req.arguments.clone(), deadline_ms: 10_000, grants: Vec::new(),
            want_net: false, stdout_limit: LIMIT, stderr_limit: LIMIT,
        });
        if let Some(state_dir) = config::request_dir() { let _ = fs::remove_dir_all(state_dir); }
        let response = match outcome {
            appcontainer::Outcome::Unsupported(_) => unavailable(&req, started, helper_hash,
                config.runtime_binary_hash, "windows_sandbox_unavailable"),
            appcontainer::Outcome::Ran { stdout: _, stderr: _, timed_out, exit_code,
                    stdout_truncated, stderr_truncated, appcontainer_sid,
                    active_processes, termination_attested, assigned_before_resume,
                    startup_attested, stdout_raw, stderr_raw } => {
                let out = stdout_raw; let err = stderr_raw;
                let failed = timed_out || stdout_truncated || stderr_truncated || exit_code != 0;
                let unattested = !startup_attested || !assigned_before_resume || !termination_attested || active_processes != 0;
                Response { schema_version: 1, request_id: &req.request_id,
                    candidate_id: &req.candidate_id, status: if unattested {"test_environment_unavailable"} else if failed {"failed"} else {"passed"},
                    error_code: if unattested {Some("process_termination_unattested")} else if timed_out {Some("phase_timeout")} else if stdout_truncated || stderr_truncated {Some("output_limit_exceeded")} else if exit_code != 0 {Some("candidate_process_failed")} else {None},
                    exit_code: if unattested {None} else {Some(exit_code)}, stdout_base64: base64::engine::general_purpose::STANDARD.encode(&out),
                    stderr_base64: base64::engine::general_purpose::STANDARD.encode(&err), stdout_bytes: out.len(), stderr_bytes: err.len(),
                    stdout_truncated, stderr_truncated, elapsed_ms: started.elapsed().as_millis(),
                    attestation: Attestation { backend: "windows-appcontainer-job-v1", helper_binary_hash: helper_hash,
                        runtime_binary_hash: config.runtime_binary_hash, profile_name: PROFILE, appcontainer_sid,
                        network_capability: false, assigned_before_resume, active_processes,
                        tree_empty: termination_attested && active_processes == 0, termination_attested, memory_limit_bytes: 256*1024*1024,
                        process_limit: 32, stdout_limit_bytes: LIMIT, stderr_limit_bytes: LIMIT } }
            }
        };
        std::io::stdout().write_all(&canonical(&response))?;
        Ok(())
    }
}

#[cfg(windows)] use windows_helper::{config, executors, sandbox_common, sandbox_windows};

#[cfg(windows)]
fn main() { if let Err(error) = windows_helper::run() { eprintln!("{error:#}"); std::process::exit(125); } }
