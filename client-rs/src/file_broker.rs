//! Closed filesystem operations. No executor code runs in this broker.
//! Fresh server-signed grants authorize paths; OS permissions still apply.
use anyhow::{anyhow, bail, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::{identity, wire};
pub const CAPABILITY: &str = "filesystem_broker_v1";
const CHUNK: usize = 128 * 1024;
const MAX_READ: usize = 256 * 1024;
const MAX_COPY: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct Contract {
    pub operation: String,
    when: Option<(String, Vec<Value>)>,
}

pub fn parse_contract(manifest: &toml::Value) -> Result<Option<Contract>> {
    let Some(value) = manifest.get("filesystem") else {
        return Ok(None);
    };
    let table = value.as_table().context("ERR_FS_CONTRACT_INVALID")?;
    if table
        .keys()
        .any(|k| !["operation", "protocol", "when"].contains(&k.as_str()))
        || table.get("protocol").and_then(|v| v.as_str()) != Some(CAPABILITY)
    {
        bail!("ERR_FS_CONTRACT_INVALID");
    }
    let operation = table
        .get("operation")
        .and_then(|v| v.as_str())
        .context("ERR_FS_CONTRACT_INVALID")?;
    if !["list", "read", "write", "copy", "delete"].contains(&operation) {
        bail!("ERR_FS_CONTRACT_INVALID");
    }
    let needed = if ["write", "copy", "delete"].contains(&operation) {
        "fs:write"
    } else {
        "fs:read"
    };
    if operation == "copy"
        && !manifest
            .get("capabilities")
            .and_then(|v| v.as_array())
            .is_some_and(|caps| {
                caps.iter()
                    .any(|v| v.get("name").and_then(|n| n.as_str()) == Some("fs:read"))
            })
    {
        bail!("ERR_FS_CONTRACT_INVALID");
    }
    if !manifest
        .get("capabilities")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .any(|v| v.get("name").and_then(|n| n.as_str()) == Some(needed))
        })
        .unwrap_or(false)
    {
        bail!("ERR_FS_CONTRACT_INVALID");
    }
    let when = if let Some(w) = table.get("when") {
        let t = w.as_table().context("ERR_FS_CONTRACT_INVALID")?;
        if t.len() != 2
            || w.get("arg")
                .and_then(|v| v.as_str())
                .is_none_or(str::is_empty)
            || w.get("values")
                .and_then(|v| v.as_array())
                .is_none_or(Vec::is_empty)
        {
            bail!("ERR_FS_CONTRACT_INVALID");
        }
        Some((
            w.get("arg")
                .and_then(|v| v.as_str())
                .context("ERR_FS_CONTRACT_INVALID")?
                .to_owned(),
            serde_json::to_value(
                w.get("values")
                    .and_then(|v| v.as_array())
                    .context("ERR_FS_CONTRACT_INVALID")?,
            )?
            .as_array()
            .unwrap()
            .clone(),
        ))
    } else {
        None
    };
    Ok(Some(Contract {
        operation: operation.into(),
        when,
    }))
}

impl Contract {
    pub fn applies(&self, args: &Value) -> bool {
        if args
            .get("client")
            .and_then(|v| v.as_str())
            .unwrap_or("local")
            != "local"
            || args
                .get("repo")
                .and_then(|v| v.as_str())
                .is_some_and(|v| !v.is_empty())
        {
            return false;
        }
        self.when
            .as_ref()
            .map(|(key, vals)| vals.iter().any(|v| args.get(key) == Some(v)))
            .unwrap_or(true)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Grant {
    pub path: String,
    pub path_style: String,
    pub kind: String,
    pub recursive: i64,
    pub can_read: i64,
    pub can_write: i64,
    pub can_overwrite: i64,
    pub expires_at: Option<u64>,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn token() -> String {
    let mut b = [0u8; 24];
    rand::rngs::OsRng.fill_bytes(&mut b);
    b.iter().map(|v| format!("{v:02x}")).collect()
}

pub fn normalize(value: &str, windows: bool) -> Result<String> {
    if value.is_empty() || value.chars().count() > 2048 || value.chars().any(|c| c.is_control()) {
        bail!("ERR_FS_PATH_INVALID");
    }
    if windows {
        let text = value.replace('/', "\\");
        let b = text.as_bytes();
        if b.len() < 3 || !b[0].is_ascii_alphabetic() || &b[1..3] != b":\\" {
            bail!("ERR_FS_PATH_INVALID");
        }
        let rest = text[3..].trim_end_matches('\\');
        for part in rest.split('\\').filter(|_| !rest.is_empty()) {
            let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
            let reserved = ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
                || (stem.len() == 4
                    && (stem.starts_with("COM") || stem.starts_with("LPT"))
                    && stem.as_bytes()[3].is_ascii_digit()
                    && stem.as_bytes()[3] != b'0');
            if part.is_empty()
                || part == "."
                || part == ".."
                || part.ends_with('.')
                || part.ends_with(' ')
                || part.contains([':', '*', '?', '<', '>', '|', '"'])
                || reserved
            {
                bail!("ERR_FS_PATH_INVALID");
            }
        }
        Ok(format!(
            "{}:\\{}",
            (b[0] as char).to_ascii_uppercase(),
            rest
        ))
    } else {
        if !value.starts_with('/') || value.starts_with("//") {
            bail!("ERR_FS_PATH_INVALID");
        }
        let rest = value.trim_start_matches('/').trim_end_matches('/');
        if !rest.is_empty()
            && rest
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == "..")
        {
            bail!("ERR_FS_PATH_INVALID");
        }
        Ok(format!("/{rest}"))
    }
}

fn allowed(
    grants: &[Grant],
    path: &str,
    write: bool,
    directory: bool,
    recursive: bool,
    overwrite: bool,
) -> bool {
    grants.iter().any(|g| {
        if g.expires_at.is_some_and(|t| t <= now())
            || (if write { g.can_write } else { g.can_read }) != 1
            || (overwrite && g.can_overwrite != 1)
        {
            return false;
        }
        let windows = g.path_style == "windows";
        if !windows && g.path_style != "posix" {
            return false;
        }
        let (Ok(mut target), Ok(mut root)) =
            (normalize(path, windows), normalize(&g.path, windows))
        else {
            return false;
        };
        if windows {
            target = target.to_ascii_lowercase();
            root = root.to_ascii_lowercase();
        }
        let sep = if windows { '\\' } else { '/' };
        if g.kind == "file" {
            return target == root && !directory && !recursive;
        }
        if g.kind != "folder" || (recursive && g.recursive != 1) {
            return false;
        }
        target == root
            || (target.starts_with(&(root.trim_end_matches(sep).to_owned() + &sep.to_string()))
                && (g.recursive == 1
                    || (!directory
                        && target
                            .rsplit_once(sep)
                            .map(|(p, _)| {
                                if p.len() == 2 && windows {
                                    format!("{p}\\")
                                } else if p.is_empty() {
                                    "/".into()
                                } else {
                                    p.into()
                                }
                            })
                            .as_deref()
                            == Some(root.as_str()))))
    })
}

pub struct Session<'a> {
    pub server: &'a str,
    pub public_key: &'a str,
    pub identity: &'a identity::Identity,
    pub http: &'a reqwest::Client,
    pub device_id: &'a str,
    pub invocation: &'a wire::Invocation,
    pub private_dir: &'a Path,
    pub deadline: Instant,
}

impl Session<'_> {
    fn remaining(&self) -> Result<Duration> {
        let left = self.deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            bail!("ERR_FS_INVOCATION_INVALID");
        }
        Ok(left)
    }

    async fn request(&self, action: &str, extra: Value) -> Result<Value> {
        let nonce = token();
        let mut body = json!({"device_id": self.device_id, "invocation_id": self.invocation.invocation_id, "nonce":nonce, "issued_at":now(), "action":action});
        for (key, value) in extra.as_object().context("ERR_FS_TRANSFER_INVALID")? {
            body[key] = value.clone();
        }
        let bytes = wire::canonical_bytes(&body)?;
        let mut response = self
            .http
            .post(format!(
                "{}/agent/filesystem",
                self.server.trim_end_matches('/')
            ))
            .timeout(self.remaining()?.min(Duration::from_secs(10)))
            .header("X-Metnos-Device-Sig", self.identity.sign_b64(&bytes))
            .header("Content-Type", "application/json")
            .body(bytes)
            .send()
            .await?;
        let status = response.status();
        let mut data = Vec::new();
        while let Some(chunk) = response.chunk().await? {
            if data.len() + chunk.len() > 512 * 1024 {
                bail!("ERR_FS_SIZE_LIMIT");
            }
            data.extend_from_slice(&chunk);
        }
        let mut result: Value = serde_json::from_slice(&data)?;
        if !status.is_success() {
            let code = result
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("ERR_FS_PERMISSION_REQUIRED");
            if code.starts_with("ERR_FS_")
                && code.len() < 64
                && code.bytes().all(|b| b.is_ascii_uppercase() || b == b'_')
            {
                bail!("{code}");
            }
            bail!("ERR_FS_PERMISSION_REQUIRED");
        }
        let signature = result
            .as_object_mut()
            .and_then(|v| v.remove("server_sig"))
            .and_then(|v| v.as_str().map(str::to_owned))
            .context("ERR_FS_AUTHORITY_INVALID")?;
        identity::verify_b64(
            self.public_key,
            &signature,
            &wire::canonical_bytes(&result)?,
        )?;
        if result["domain"] != "metnos-filesystem-v1"
            || result["nonce"] != nonce
            || result["device_id"] != self.device_id
            || result["invocation_id"] != self.invocation.invocation_id
            || result["manifest_sha256"] != self.invocation.manifest_sha256
            || result["expires_at"]
                .as_u64()
                .is_none_or(|v| v < now() || v > now() + 10)
        {
            bail!("ERR_FS_AUTHORITY_INVALID");
        }
        Ok(result)
    }

    async fn authority(&self, operation: &str) -> Result<Value> {
        let result = self.request("authority", json!({})).await?;
        if result["operation"] != operation {
            bail!("ERR_FS_AUTHORITY_INVALID");
        }
        Ok(result)
    }

    async fn check(
        &self,
        operation: &str,
        path: &str,
        write: bool,
        directory: bool,
        recursive: bool,
        overwrite: bool,
    ) -> Result<()> {
        let canonical = normalize(path, cfg!(windows))?;
        // Signing material, cached executables and client state are never an
        // ordinary user file, even if a broad folder has been authorized.
        let private = self.private_dir.to_string_lossy();
        let folded = if cfg!(windows) {
            canonical.to_ascii_lowercase()
        } else {
            canonical.clone()
        };
        let reserved = if cfg!(windows) {
            private.to_ascii_lowercase()
        } else {
            private.to_string()
        };
        let sep = if cfg!(windows) { '\\' } else { '/' };
        let is_private = folded == reserved
            || folded.starts_with(&(reserved.trim_end_matches(sep).to_owned() + &sep.to_string()));
        if let Ok(exe) = std::env::current_exe() {
            let exe = exe.to_string_lossy();
            let exe = if cfg!(windows) {
                exe.to_ascii_lowercase()
            } else {
                exe.to_string()
            };
            if folded == exe {
                bail!("ERR_FS_RESERVED_PATH");
            }
        }
        let authority = self.authority(operation).await?;
        if is_private {
            let root = self
                .private_dir
                .join("shimdata")
                .join("_history")
                .join(&self.invocation.turn_id)
                .join("blob");
            let target = Path::new(&canonical);
            let valid = operation == "copy"
                && !write
                && target.parent() == Some(root.as_path())
                && target
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
                && target.extension().is_some_and(|s| s == "bin")
                && authority["undo_sources"]
                    .as_array()
                    .is_some_and(|items| items.iter().any(|item| item["path"] == path));
            if valid {
                return Ok(());
            }
            bail!("ERR_FS_RESERVED_PATH");
        }
        let grants: Vec<Grant> = serde_json::from_value(authority["grants"].clone())?;
        if !allowed(&grants, &canonical, write, directory, recursive, overwrite) {
            bail!("ERR_FS_PERMISSION_REQUIRED");
        }
        Ok(())
    }
}

/// Ancestors stay pinned until the operation ends. Final opens refuse links.
struct Guard {
    path: PathBuf,
    parents: Vec<File>,
}
impl Guard {
    fn new(path: &str) -> Result<Self> {
        let canonical = normalize(path, cfg!(windows))?;
        let path = PathBuf::from(&canonical);
        // A volume root is itself a valid listing target.
        let parent = path.parent().unwrap_or(&path);
        let mut parents = Vec::new();
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            use std::os::unix::ffi::OsStrExt;
            let root = std::ffi::CString::new("/")?;
            let fd = unsafe {
                libc::open(
                    root.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            parents.push(unsafe { File::from_raw_fd(fd) });
            for c in parent.components() {
                if let Component::Normal(part) = c {
                    let name = std::ffi::CString::new(part.as_bytes())?;
                    let fd = unsafe {
                        libc::openat(
                            parents.last().unwrap().as_raw_fd(),
                            name.as_ptr(),
                            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                        )
                    };
                    if fd < 0 {
                        return Err(std::io::Error::last_os_error().into());
                    }
                    parents.push(unsafe { File::from_raw_fd(fd) });
                }
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::{
                FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
            };
            let mut at = PathBuf::new();
            for c in parent.components() {
                at.push(c.as_os_str());
                if matches!(c, Component::Prefix(_)) {
                    continue;
                }
                let file = OpenOptions::new()
                    .access_mode(0)
                    .share_mode(FILE_SHARE_READ)
                    .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                    .open(&at)?;
                refuse_links(&file, true)?;
                canonical_handle(&file, &at)?;
                parents.push(file);
            }
        }
        Ok(Self { path, parents })
    }

    fn open(&self, write: bool, create_new: bool) -> Result<File> {
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            use std::os::unix::ffi::OsStrExt;
            let name = std::ffi::CString::new(
                self.path
                    .file_name()
                    .context("ERR_FS_PATH_INVALID")?
                    .as_bytes(),
            )?;
            let flags = (if write { libc::O_RDWR } else { libc::O_RDONLY })
                | libc::O_CLOEXEC
                | libc::O_NOFOLLOW
                | libc::O_NONBLOCK
                | (if create_new {
                    libc::O_CREAT | libc::O_EXCL
                } else {
                    0
                });
            let fd = unsafe {
                libc::openat(
                    self.parents.last().unwrap().as_raw_fd(),
                    name.as_ptr(),
                    flags,
                    0o600,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            let file = unsafe { File::from_raw_fd(fd) };
            refuse_links(&file, false)?;
            Ok(file)
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::{
                FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
            };
            // Stage files need DELETE for a rename through the same handle.
            let mut opts = OpenOptions::new();
            opts.read(true)
                .write(write)
                .create_new(create_new)
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
            if create_new {
                opts.access_mode(0x80000000 | 0x40000000 | 0x00010000);
            }
            let file = opts.open(&self.path)?;
            refuse_links(&file, false)?;
            canonical_handle(&file, &self.path)?;
            Ok(file)
        }
    }

    fn directory(&self) -> Result<File> {
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            use std::os::unix::ffi::OsStrExt;
            let name = std::ffi::CString::new(
                self.path.file_name().map(|v| v.as_bytes()).unwrap_or(b"."),
            )?;
            let fd = unsafe {
                libc::openat(
                    self.parents.last().unwrap().as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            Ok(unsafe { File::from_raw_fd(fd) })
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::{
                FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
            };
            let file = OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(&self.path)?;
            refuse_links(&file, true)?;
            canonical_handle(&file, &self.path)?;
            Ok(file)
        }
    }

    fn stage(&self) -> Result<(Guard, File)> {
        let name = format!(".metnos-transfer-{}", token());
        let stage = Guard {
            path: self.path.with_file_name(name),
            parents: self
                .parents
                .iter()
                .map(File::try_clone)
                .collect::<std::io::Result<_>>()?,
        };
        let file = stage.open(true, true)?;
        Ok((stage, file))
    }

    fn commit(&self, stage: &Guard, file: &File, overwrite: bool) -> Result<()> {
        file.sync_all()?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            use std::os::unix::ffi::OsStrExt;
            let src = std::ffi::CString::new(stage.path.file_name().unwrap().as_bytes())?;
            let dst = std::ffi::CString::new(self.path.file_name().unwrap().as_bytes())?;
            let dir = self.parents.last().unwrap().as_raw_fd();
            let rc = unsafe {
                if overwrite {
                    libc::renameat(dir, src.as_ptr(), dir, dst.as_ptr())
                } else {
                    libc::linkat(dir, src.as_ptr(), dir, dst.as_ptr(), 0)
                }
            };
            if rc != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            if !overwrite {
                unsafe { libc::unlinkat(dir, src.as_ptr(), 0) };
            }
            self.parents.last().unwrap().sync_all()?;
        }
        #[cfg(windows)]
        {
            use std::os::windows::{ffi::OsStrExt, io::AsRawHandle};
            use windows_sys::Win32::Storage::FileSystem::{
                FileRenameInfo, FileRenameInfoEx, SetFileInformationByHandle, FILE_RENAME_INFO,
            };
            // Replace semantics of the file system: a legacy rename is refused
            // while anyone holds the destination, even an indexer or antivirus
            // that allows deletion; POSIX semantics are not. Volumes without
            // them answer "invalid parameter" and take the legacy path.
            const REPLACE_IF_EXISTS: u32 = 0x1;
            const POSIX_SEMANTICS: u32 = 0x2;
            const INVALID_FUNCTION: i32 = 1;
            const ACCESS_DENIED: i32 = 5;
            const SHARING_VIOLATION: i32 = 32;
            const NOT_SUPPORTED: i32 = 50;
            const INVALID_PARAMETER: i32 = 87;
            let name: Vec<u16> = self.path.as_os_str().encode_wide().collect();
            let offset = std::mem::offset_of!(FILE_RENAME_INFO, FileName);
            let size = offset + name.len() * 2;
            let mut storage = vec![0usize; size.div_ceil(std::mem::size_of::<usize>())];
            let info = storage.as_mut_ptr().cast::<FILE_RENAME_INFO>();
            let rename = |class, flags: u32| -> std::io::Result<()> {
                unsafe {
                    (*info).Anonymous.Flags = flags;
                    (*info).RootDirectory = std::ptr::null_mut();
                    (*info).FileNameLength = (name.len() * 2) as u32;
                    std::ptr::copy_nonoverlapping(
                        name.as_ptr(),
                        std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
                        name.len(),
                    );
                    if SetFileInformationByHandle(
                        file.as_raw_handle(),
                        class,
                        info.cast(),
                        size as u32,
                    ) == 0
                    {
                        return Err(std::io::Error::last_os_error());
                    }
                }
                Ok(())
            };
            if !overwrite {
                rename(FileRenameInfo, 0)?;
                return Ok(());
            }
            let mut posix = true;
            let mut attempt = 0;
            loop {
                let result = if posix {
                    rename(FileRenameInfoEx, REPLACE_IF_EXISTS | POSIX_SEMANTICS)
                } else {
                    rename(FileRenameInfo, 1)
                };
                let Err(error) = result else {
                    return Ok(());
                };
                match error.raw_os_error() {
                    Some(INVALID_FUNCTION | NOT_SUPPORTED | INVALID_PARAMETER) if posix => {
                        posix = false;
                    }
                    Some(ACCESS_DENIED | SHARING_VIOLATION) if attempt < 5 => {
                        attempt += 1;
                        std::thread::sleep(Duration::from_millis(200));
                    }
                    _ => return Err(error.into()),
                }
            }
        }
        #[cfg(not(windows))]
        {
            Ok(())
        }
    }

    fn unlink(&self) -> Result<()> {
        #[cfg(unix)]
        {
            use std::os::{fd::AsRawFd, unix::ffi::OsStrExt};
            let name = std::ffi::CString::new(
                self.path
                    .file_name()
                    .context("ERR_FS_PATH_INVALID")?
                    .as_bytes(),
            )?;
            if unsafe { libc::unlinkat(self.parents.last().unwrap().as_raw_fd(), name.as_ptr(), 0) }
                != 0
            {
                return Err(std::io::Error::last_os_error().into());
            }
            self.parents.last().unwrap().sync_all()?;
        }
        #[cfg(windows)]
        {
            fs::remove_file(&self.path)?;
        }
        Ok(())
    }

    fn cleanup(&self) {
        #[cfg(unix)]
        {
            use std::os::{fd::AsRawFd, unix::ffi::OsStrExt};
            if let Some(name) = self
                .path
                .file_name()
                .and_then(|s| std::ffi::CString::new(s.as_bytes()).ok())
            {
                unsafe {
                    libc::unlinkat(self.parents.last().unwrap().as_raw_fd(), name.as_ptr(), 0)
                };
            }
        }
        #[cfg(windows)]
        {
            let _ = fs::remove_file(&self.path);
        }
    }
}

fn refuse_links(file: &File, directory: bool) -> Result<()> {
    let metadata = file.metadata()?;
    if metadata.is_dir() != directory || (!directory && !metadata.is_file()) {
        bail!("ERR_FS_PATH_INVALID");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if !directory && metadata.nlink() != 1 {
            bail!("ERR_FS_LINK_REFUSED");
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_REPARSE_POINT,
        };
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || (!directory && info.nNumberOfLinks != 1)
        {
            bail!("ERR_FS_LINK_REFUSED");
        }
    }
    Ok(())
}

#[cfg(windows)]
fn canonical_handle(file: &File, path: &Path) -> Result<()> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::GetFinalPathNameByHandleW;
    let mut buffer = vec![0u16; 32768];
    let n = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            0,
        )
    } as usize;
    if n == 0 || n >= buffer.len() {
        bail!("ERR_FS_PATH_INVALID");
    }
    let actual = String::from_utf16(&buffer[..n])?;
    let actual = actual.strip_prefix(r"\\?\").unwrap_or(&actual);
    if normalize(actual, true)?.to_ascii_lowercase()
        != normalize(path.to_str().context("ERR_FS_PATH_INVALID")?, true)?.to_ascii_lowercase()
    {
        bail!("ERR_FS_LINK_REFUSED");
    }
    Ok(())
}

fn paths(args: &Value) -> Result<Vec<String>> {
    let values = if let Some(path) = args.get("path").and_then(Value::as_str) {
        vec![path.to_owned()]
    } else if let Some(paths) = args.get("paths").and_then(Value::as_array) {
        paths
            .iter()
            .map(|v| v.as_str().map(str::to_owned).context("ERR_FS_PATH_INVALID"))
            .collect::<Result<_>>()?
    } else if let Some(entries) = args.get("entries").and_then(Value::as_array) {
        entries
            .iter()
            .map(|v| {
                v.get("path")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
                    .context("ERR_FS_PATH_INVALID")
            })
            .collect::<Result<_>>()?
    } else {
        bail!("ERR_FS_PATH_INVALID");
    };
    if values.len() > 100 {
        bail!("ERR_FS_SIZE_LIMIT");
    }
    Ok(values)
}

async fn list(session: &Session<'_>, args: &Value) -> Result<Value> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .context("ERR_FS_PATH_INVALID")?;
    let recursive = args
        .get("recursive")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let maximum = args
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(1000);
    let maximum = if maximum == 0 {
        1000
    } else {
        maximum.min(1000)
    } as usize;
    let depth = args
        .get("max_depth")
        .and_then(Value::as_u64)
        .unwrap_or(10)
        .min(64);
    let depth = if depth == 0 { 64 } else { depth };
    let mut todo = vec![(normalize(path, cfg!(windows))?, 0u64)];
    let mut entries = Vec::new();
    let mut truncated = false;
    let mut output_bytes = 0usize;
    while let Some((current, level)) = todo.pop() {
        session
            .check("list", &current, false, true, recursive, false)
            .await?;
        // Volume root has no parent; represent it as root + '.' internally.
        let root = Path::new(&current).parent().is_none();
        let guard = if root {
            Guard {
                path: PathBuf::from(&current),
                parents: vec![],
            }
        } else {
            Guard::new(&current)?
        };
        let directory = if root {
            #[cfg(unix)]
            {
                File::open(&current)?
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                use windows_sys::Win32::Storage::FileSystem::{
                    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
                };
                let f = OpenOptions::new()
                    .read(true)
                    .share_mode(FILE_SHARE_READ)
                    .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                    .open(&current)?;
                refuse_links(&f, true)?;
                f
            }
        } else {
            guard.directory()?
        };
        #[cfg(unix)]
        let read_path = {
            use std::os::fd::AsRawFd;
            PathBuf::from(format!("/proc/self/fd/{}", directory.as_raw_fd()))
        };
        #[cfg(windows)]
        let read_path = PathBuf::from(&current);
        for entry in fs::read_dir(read_path)? {
            let entry = entry?;
            let info = fs::symlink_metadata(entry.path())?;
            let path = Path::new(&current).join(entry.file_name());
            let link = info.file_type().is_symlink();
            #[cfg(windows)]
            let link = {
                use std::os::windows::fs::MetadataExt;
                link || info.file_attributes() & 0x400 != 0
            };
            let dir = info.is_dir() && !link;
            if entries.len() >= maximum {
                truncated = true;
                break;
            }
            let item = json!({"path":path.to_string_lossy(),"name":entry.file_name().to_string_lossy(),"type":if link {"symlink"}else if dir {"dir"}else {"file"},"size":if dir {0}else{info.len()},"mtime_epoch":info.modified().ok().and_then(|t|t.duration_since(UNIX_EPOCH).ok()).map(|t|t.as_secs())});
            output_bytes += serde_json::to_vec(&item)?.len();
            if output_bytes > 256 * 1024 {
                truncated = true;
                break;
            }
            entries.push(item);
            if recursive && dir {
                if level + 1 < depth {
                    todo.push((path.to_string_lossy().into_owned(), level + 1));
                } else {
                    truncated = true;
                }
            }
        }
        if truncated && (entries.len() >= maximum || output_bytes > 256 * 1024) {
            break;
        }
    }
    let sort = args
        .get("sort_by")
        .and_then(Value::as_str)
        .unwrap_or("name");
    match sort {
        "name" => entries.sort_by_key(|v| v["name"].as_str().unwrap_or("").to_lowercase()),
        "size" => entries.sort_by_key(|v| std::cmp::Reverse(v["size"].as_u64().unwrap_or(0))),
        "mtime" => {
            entries.sort_by_key(|v| std::cmp::Reverse(v["mtime_epoch"].as_u64().unwrap_or(0)))
        }
        _ => bail!("ERR_FS_BROKER_UNSUPPORTED"),
    };
    Ok(
        json!({"ok":true,"ok_count":entries.len(),"entries":entries,"truncated":truncated,"truncated_what":"entries","used":entries.len(),"cap_field":"max_results","cap_value":maximum,"source_complete":!truncated,"metadata":{"path":path,"truncated":truncated,"source_complete":!truncated}}),
    )
}

fn error_code(error: &anyhow::Error) -> String {
    let text = error.to_string();
    if text.starts_with("ERR_FS_")
        && text.len() < 64
        && text.bytes().all(|b| b.is_ascii_uppercase() || b == b'_')
    {
        text
    } else {
        "ERR_FS_IO".into()
    }
}

fn batch(results: Vec<Value>, failed: Vec<Value>, reading: bool) -> Value {
    let mut value = json!({"ok":failed.is_empty(),"ok_count":results.len(),"fail_count":failed.len(),"failed":failed,"dirs_created":[]});
    value[if reading { "entries" } else { "results" }] = Value::Array(results);
    if !failed.is_empty() {
        value["error_code"] = failed[0]["error_code"].clone();
        value["error"] = failed[0]["error"].clone();
        value["error_class"] = json!("filesystem_permission");
    }
    value
}

fn failure(index: usize, path: &str, error: &anyhow::Error) -> Value {
    let code = error_code(error);
    let os_error = error
        .downcast_ref::<std::io::Error>()
        .and_then(std::io::Error::raw_os_error);
    tracing::warn!(index, code = %code, ?os_error, "filesystem broker entry failed");
    json!({"index":index,"path":path,"error_code":code,"error":code})
}

/// `parse="auto"` is the planner's default: plain text needs no normalization,
/// and a document that is not UTF-8 stops later with a precise error.
fn read_supported(args: &Value) -> bool {
    args.get("pattern").is_none()
}

async fn read(session: &Session<'_>, args: &Value) -> Result<Value> {
    if !read_supported(args) {
        bail!("ERR_FS_BROKER_UNSUPPORTED");
    }
    let paths = paths(args)?;
    let mut entries = Vec::new();
    let mut failed = Vec::new();
    let requested = args.get("max_bytes").and_then(Value::as_u64).unwrap_or(0);
    let tail = args.get("tail_bytes").and_then(Value::as_u64).unwrap_or(0);
    if requested > 0 && tail > 0 {
        bail!("ERR_FS_BROKER_UNSUPPORTED");
    }
    let cap = if tail > 0 { tail } else { requested };
    let limit = if cap == 0 {
        MAX_READ
    } else {
        cap.min(MAX_READ as u64) as usize
    };
    for (index, path) in paths.into_iter().enumerate() {
        let result:Result<Value>=async {
            session.check("read",&path,false,false,false,false).await?;
            let guard=Guard::new(&path)?;let mut file=guard.open(false,false)?;
            let before=file_identity(&file)?;let size=file.metadata()?.len();
            let offset=if tail>0 {size.saturating_sub(tail)}else{args.get("offset").and_then(Value::as_u64).unwrap_or(0)};
            file.seek(SeekFrom::Start(offset))?;
            let mut bytes=Vec::new();std::io::Read::by_ref(&mut file).take(limit as u64).read_to_end(&mut bytes)?;
            if before!=file_identity(&file)? {bail!("ERR_FS_SOURCE_CHANGED");}
            session.check("read",&path,false,false,false,false).await?;
            let encoding=args.get("encoding").and_then(Value::as_str).unwrap_or("utf-8");
            let content=match encoding {"binary"=>STANDARD.encode(&bytes),"latin-1"=>bytes.iter().map(|v|char::from(*v)).collect(),"utf-8"=>String::from_utf8(bytes.clone()).map_err(|_|anyhow!("ERR_FS_BINARY_ENCODING_REQUIRED"))?,_=>bail!("ERR_FS_BROKER_UNSUPPORTED")};
            let mut entry=json!({"path":path,"content":content,"size":size,"bytes_read":bytes.len(),"offset":offset,"truncated":offset+(bytes.len() as u64)<size,"encoding":encoding});
            if args.get("parse").and_then(Value::as_str)==Some("json") {
                let parsed:Value=serde_json::from_str(entry["content"].as_str().unwrap())?;
                if let Some(fields)=parsed.as_object(){for (key,value) in fields {if key!="path" {entry[key]=value.clone();}}}else{entry["value"]=parsed;}
            }
            Ok(entry)
        }.await;
        match result {
            Ok(entry) => entries.push(entry),
            Err(error) => failed.push(failure(index, &path, &error)),
        }
    }
    Ok(batch(entries, failed, true))
}

fn file_identity(file: &File) -> Result<(u64, u64, u64, u128)> {
    let info = file.metadata()?;
    let stamp = info
        .modified()?
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok((info.dev(), info.ino(), info.len(), stamp))
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
        };
        let mut data: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut data) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok((
            data.dwVolumeSerialNumber as u64,
            ((data.nFileIndexHigh as u64) << 32) | data.nFileIndexLow as u64,
            info.len(),
            stamp,
        ))
    }
}

async fn backup(
    session: &Session<'_>,
    source: &mut File,
    operation: &str,
    path: &str,
) -> Result<String> {
    source.seek(SeekFrom::Start(0))?;
    let before = file_identity(source)?;
    if before.2 > MAX_COPY {
        bail!("ERR_FS_SIZE_LIMIT");
    }
    let turn = &session.invocation.turn_id;
    if turn.is_empty()
        || turn.len() > 128
        || !turn
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.:-".contains(&b))
    {
        bail!("ERR_FS_INVOCATION_INVALID");
    }
    let dir = session
        .private_dir
        .join("shimdata")
        .join("_history")
        .join(turn)
        .join("blob");
    fs::create_dir_all(&dir)?;
    let temp = dir.join(token());
    let result: Result<String> = async {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp)?;
        let mut hash = Sha256::new();
        let mut bytes = vec![0; CHUNK];
        loop {
            session
                .check(operation, path, true, false, false, operation != "delete")
                .await?;
            let n = source.read(&mut bytes)?;
            if n == 0 {
                break;
            }
            file.write_all(&bytes[..n])?;
            hash.update(&bytes[..n]);
        }
        if file_identity(source)? != before {
            bail!("ERR_FS_SOURCE_CHANGED");
        }
        file.sync_all()?;
        drop(file);
        let digest = format!("{:x}", hash.finalize());
        let path = dir.join(format!("{digest}.bin"));
        match fs::hard_link(&temp, &path) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                let mut stored = Guard::new(path.to_str().context("ERR_FS_PATH_INVALID")?)?
                    .open(false, false)?;
                let mut check = Sha256::new();
                loop {
                    session.remaining()?;
                    let n = stored.read(&mut bytes)?;
                    if n == 0 {
                        break;
                    }
                    check.update(&bytes[..n]);
                }
                if format!("{:x}", check.finalize()) != digest {
                    bail!("ERR_FS_HASH_MISMATCH");
                }
            }
            Err(error) => return Err(error.into()),
        }
        source.seek(SeekFrom::Start(0))?;
        Ok(path.to_string_lossy().into_owned())
    }
    .await;
    let _ = fs::remove_file(&temp);
    result
}

async fn write(session: &Session<'_>, args: &Value) -> Result<Value> {
    let specs = if let Some(files) = args.get("files").and_then(Value::as_array) {
        files.clone()
    } else if args.get("path").is_some() {
        vec![args.clone()]
    } else {
        bail!("ERR_FS_BROKER_UNSUPPORTED");
    };
    if specs.len() > 100 {
        bail!("ERR_FS_SIZE_LIMIT");
    }
    let mut results = Vec::new();
    let mut failed = Vec::new();
    for (index, spec) in specs.into_iter().enumerate() {
        let path = spec.get("path").and_then(Value::as_str).unwrap_or("");
        let result:Result<Value>=async {
            let content=spec.get("content").and_then(Value::as_str).context("ERR_FS_BROKER_UNSUPPORTED")?;
            let encoding=spec.get("encoding").or_else(||args.get("encoding")).and_then(Value::as_str).unwrap_or("utf-8");
            let data=match encoding {"binary"=>STANDARD.decode(content)?,"utf-8"=>content.as_bytes().to_vec(),_=>bail!("ERR_FS_BROKER_UNSUPPORTED")};
            if data.len()>MAX_READ{bail!("ERR_FS_SIZE_LIMIT");}
            let mode=spec.get("mode").or_else(||args.get("mode")).and_then(Value::as_str).unwrap_or("overwrite");
            if !["overwrite","append","fail_if_exists","skip_if_exists"].contains(&mode){bail!("ERR_FS_BROKER_UNSUPPORTED");}
            session.check("write",path,true,false,false,false).await?;
            let guard=Guard::new(path)?;
            let mut previous=match guard.open(false,false) {Ok(f)=>Some(f),Err(e) if is_missing(&e)=>None,Err(e)=>return Err(e)};
            if previous.is_some()&&mode=="fail_if_exists"{bail!("ERR_FS_DEST_EXISTS");}
            if previous.is_some()&&mode=="skip_if_exists"{return Ok(json!({"path":path,"created":false,"skipped":true,"bytes_written":0}));}
            session.check("write",path,true,false,false,previous.is_some()).await?;
            let prior=previous.as_ref().map(file_identity).transpose()?;
            let blob=if let Some(file)=previous.as_mut(){Some(backup(session,file,"write",path).await?)}else{None};
            let (stage,mut out)=guard.stage()?;
            let work:Result<()>=async {
                if mode=="append" {if let Some(file)=previous.as_mut(){let mut buffer=vec![0;CHUNK];loop {session.check("write",path,true,false,false,true).await?;let n=file.read(&mut buffer)?;if n==0 {break;}out.write_all(&buffer[..n])?;}}}
                out.write_all(&data)?;
                if out.metadata()?.len()>MAX_COPY {bail!("ERR_FS_SIZE_LIMIT");}
                session.check("write",path,true,false,false,previous.is_some()).await?;
                if previous.as_ref().map(file_identity).transpose()?!=prior {bail!("ERR_FS_SOURCE_CHANGED");}
                let current=match guard.open(false,false){Ok(file)=>Some(file_identity(&file)?),Err(e) if is_missing(&e)=>None,Err(e)=>return Err(e)};
                if current!=prior {bail!("ERR_FS_SOURCE_CHANGED");}
                drop(previous.take());guard.commit(&stage,&out,blob.is_some())?;Ok(())
            }.await;
            drop(out);if work.is_err(){stage.cleanup();}work?;
            Ok(json!({"path":path,"created":blob.is_none(),"prev_blob_path":blob,"bytes_written":data.len(),"encoding":encoding}))
        }.await;
        match result {
            Ok(value) => results.push(value),
            Err(error) => failed.push(failure(index, path, &error)),
        }
    }
    Ok(batch(results, failed, false))
}

fn is_missing(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<std::io::Error>()
        .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound)
}

async fn copy(session: &Session<'_>, args: &Value) -> Result<Value> {
    let authority = session.authority("copy").await?;
    let entries = authority
        .get("copy_entries")
        .and_then(Value::as_array)
        .context("ERR_FS_AUTHORITY_INVALID")?;
    let mut results = Vec::new();
    let mut failed = Vec::new();
    for (index, entry) in entries.iter().enumerate() {
        match copy_one(session, args, entry, &authority).await {
            Ok(value) => results.push(value),
            Err(error) => failed.push(failure(index, entry["src"].as_str().unwrap_or(""), &error)),
        }
    }
    Ok(batch(results, failed, false))
}

async fn copy_one(
    session: &Session<'_>,
    args: &Value,
    entry: &Value,
    authority: &Value,
) -> Result<Value> {
    let index = entry["index"].as_u64().context("ERR_FS_TRANSFER_INVALID")?;
    let src = entry["src"].as_str().context("ERR_FS_TRANSFER_INVALID")?;
    let dst = entry["dst"].as_str().context("ERR_FS_TRANSFER_INVALID")?;
    let overwrite = args
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let remote_src = entry["source_scope"] == "server";
    let remote_dst = entry["destination_scope"] == "server";
    let src_guard = if !remote_src {
        session
            .check("copy", src, false, false, false, false)
            .await?;
        Some(Guard::new(src)?)
    } else {
        None
    };
    let mut source = src_guard
        .as_ref()
        .map(|g| g.open(false, false))
        .transpose()?;
    let original = source.as_ref().map(file_identity).transpose()?;
    if original.is_some_and(|info| info.2 > MAX_COPY) {
        bail!("ERR_FS_SIZE_LIMIT");
    }
    let dst_guard = if !remote_dst {
        session
            .check("copy", dst, true, false, false, false)
            .await?;
        Some(Guard::new(dst)?)
    } else {
        None
    };
    let mut previous = match dst_guard.as_ref().map(|g| g.open(false, false)).transpose() {
        Ok(value) => value,
        Err(error) if is_missing(&error) => None,
        Err(error) => return Err(error),
    };
    if previous.is_some() && !overwrite {
        bail!("ERR_FS_DEST_EXISTS");
    }
    let before = previous.as_ref().map(file_identity).transpose()?;
    let blob = if let Some(file) = previous.as_mut() {
        Some(backup(session, file, "copy", dst).await?)
    } else {
        None
    };
    let stage = dst_guard.as_ref().map(Guard::stage).transpose()?;
    let (stage_guard, mut output) = match stage {
        Some((g, f)) => (Some(g), Some(f)),
        None => (None, None),
    };
    let result:Result<Value>=async {
        let mut offset=0u64;let mut hash=Sha256::new();let mut source_identity:Option<String>=None;
        loop {
            let bytes=if let Some(file)=source.as_mut(){
                session.check("copy",src,false,false,false,false).await?;
                let mut bytes=vec![0u8;CHUNK];let n=file.read(&mut bytes)?;bytes.truncate(n);bytes
            }else{
                let chunk=session.request("read",json!({"index":index,"offset":offset})).await?;
                let observed=chunk["source_identity"].as_str().context("ERR_FS_TRANSFER_INVALID")?;
                if source_identity.as_ref().is_some_and(|v|v!=observed){bail!("ERR_FS_SOURCE_CHANGED");}
                source_identity=Some(observed.into());STANDARD.decode(chunk["content"].as_str().context("ERR_FS_TRANSFER_INVALID")?)?
            };
            if bytes.len()>CHUNK||offset+bytes.len() as u64>MAX_COPY{bail!("ERR_FS_SIZE_LIMIT");}
            if let Some(out)=output.as_mut(){session.check("copy",dst,true,false,false,before.is_some()).await?;out.write_all(&bytes)?;}
            else{session.request("write",json!({"index":index,"offset":offset,"content":STANDARD.encode(&bytes)})).await?;}
            hash.update(&bytes);offset+=bytes.len() as u64;if bytes.is_empty(){break;}
        }
        if source.as_ref().map(file_identity).transpose()?!=original {bail!("ERR_FS_SOURCE_CHANGED");}
        let digest=format!("{:x}",hash.finalize());
        if let Some(undos)=authority["undo_sources"].as_array(){for item in undos {if item["path"]==src&&item["sha256"]!=digest {bail!("ERR_FS_HASH_MISMATCH");}}}
        let mut value=json!({"src":src,"dst":dst,"path":dst,"copy":true,"created":blob.is_none(),"prev_blob_path":blob,"size":offset,"sha256":digest,"source_scope":entry["source_scope"],"destination_scope":entry["destination_scope"]});
        if let Some(out)=output.as_mut(){
            out.flush()?;out.seek(SeekFrom::Start(0))?;let mut verify=Sha256::new();let mut bytes=vec![0;CHUNK];
            loop{session.check("copy",dst,true,false,false,before.is_some()).await?;let n=out.read(&mut bytes)?;if n==0{break;}verify.update(&bytes[..n]);}
            if format!("{:x}",verify.finalize())!=digest{bail!("ERR_FS_HASH_MISMATCH");}
            session.check("copy",dst,true,false,false,before.is_some()).await?;
            let current=match dst_guard.as_ref().unwrap().open(false,false){Ok(file)=>Some(file_identity(&file)?),Err(error) if is_missing(&error)=>None,Err(error)=>return Err(error)};
            if current!=before {bail!("ERR_FS_SOURCE_CHANGED");}
            drop(previous.take());dst_guard.as_ref().unwrap().commit(stage_guard.as_ref().unwrap(),out,overwrite)?;
        }else{
            let receipt=session.request("commit",json!({"index":index,"size":offset,"sha256":digest})).await?;
            if receipt["committed"]!=true||receipt["size"]!=offset||receipt["sha256"]!=digest {bail!("ERR_FS_TRANSFER_INVALID");}
            value["created"]=receipt["created"].clone();value["prev_blob_path"]=receipt["prev_blob_path"].clone();
        }
        Ok(value)
    }.await;
    drop(output.take());
    if result.is_err() {
        if let Some(guard) = stage_guard.as_ref() {
            guard.cleanup();
        } else {
            let _ = session.request("abort", json!({"index":index})).await;
        }
    }
    result
}

async fn delete(session: &Session<'_>, args: &Value) -> Result<Value> {
    let paths = paths(args)?;
    let mut results = Vec::new();
    let mut failed = Vec::new();
    for (index, path) in paths.into_iter().enumerate() {
        let work: Result<Value> = async {
            if args.get("location").and_then(Value::as_str) == Some("server") {
                return Ok(
                    session.request("delete", json!({"index":index})).await?["result"].clone(),
                );
            }
            session
                .check("delete", &path, true, false, false, false)
                .await?;
            let guard = Guard::new(&path)?;
            let mut file = guard.open(false, false)?;
            let before = file_identity(&file)?;
            let blob = backup(session, &mut file, "delete", &path).await?;
            session
                .check("delete", &path, true, false, false, false)
                .await?;
            let current = guard.open(false, false)?;
            if file_identity(&current)? != before {
                bail!("ERR_FS_SOURCE_CHANGED");
            }
            drop(current);
            drop(file);
            guard.unlink()?;
            Ok(json!({"path":path,"blob_path":blob,"deleted":true}))
        }
        .await;
        match work {
            Ok(value) => results.push(value),
            Err(error) => failed.push(failure(index, &path, &error)),
        }
    }
    Ok(batch(results, failed, false))
}

pub async fn execute(session: &Session<'_>, contract: &Contract) -> Value {
    let args = &session.invocation.args;
    let result = match contract.operation.as_str() {
        "list" => list(session, args).await,
        "read" => read(session, args).await,
        "write" => write(session, args).await,
        "copy" => copy(session, args).await,
        "delete" => delete(session, args).await,
        _ => Err(anyhow!("ERR_FS_CONTRACT_INVALID")),
    };
    match result {
        Ok(v) => v,
        Err(e) => {
            let text = e.to_string();
            let code = if text.starts_with("ERR_FS_")
                && text.len() < 64
                && text.bytes().all(|b| b.is_ascii_uppercase() || b == b'_')
            {
                text
            } else {
                "ERR_FS_IO".into()
            };
            tracing::warn!(code=%code,error=%e,"filesystem broker refused operation");
            json!({"ok":false,"error_code":code,"error":code,"error_class":"filesystem_permission","results":[]})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grant(path: &str, windows: bool) -> Grant {
        Grant {
            path: path.into(),
            path_style: if windows { "windows" } else { "posix" }.into(),
            kind: "folder".into(),
            recursive: 0,
            can_read: 1,
            can_write: 0,
            can_overwrite: 0,
            expires_at: None,
        }
    }

    #[test]
    fn the_default_parse_mode_reads_plain_text() {
        assert!(read_supported(&json!({"path": "a.txt", "parse": "auto"})));
        assert!(read_supported(&json!({"path": "a.txt"})));
        assert!(!read_supported(&json!({"path": "a.txt", "pattern": "x"})));
    }

    #[test]
    fn ambiguous_windows_paths_are_refused_on_every_platform() {
        for path in [
            "C:",
            "C:relative",
            r"\\host\share",
            r"\\?\C:\foo",
            r"C:\a\..\b",
            r"C:\a:stream",
            r"C:\NUL.txt",
            r"C:\COM1",
            "C:\\foo. ",
            r"C:\a\\b",
        ] {
            assert!(normalize(path, true).is_err(), "{path}");
        }
        assert_eq!(normalize("c:/", true).unwrap(), "C:\\");
        assert_eq!(
            normalize("C:/Users/Alice/file.txt", true).unwrap(),
            r"C:\Users\Alice\file.txt"
        );
    }

    #[test]
    fn grants_respect_components_depth_expiry_and_overwrite() {
        let mut g = grant(r"C:\Users\Alice", true);
        assert!(allowed(
            &[g.clone()],
            r"c:\users\ALICE\file.txt",
            false,
            false,
            false,
            false
        ));
        for path in [r"C:\Users\Alice2\file.txt", r"C:\Users\Alice\sub\file.txt"] {
            assert!(!allowed(&[g.clone()], path, false, false, false, false));
        }
        assert!(!allowed(
            &[g.clone()],
            r"C:\Users\Alice\sub",
            false,
            true,
            false,
            false
        ));
        g.can_write = 1;
        assert!(allowed(
            &[g.clone()],
            r"C:\Users\Alice\a",
            true,
            false,
            false,
            false
        ));
        assert!(!allowed(
            &[g.clone()],
            r"C:\Users\Alice\a",
            true,
            false,
            false,
            true
        ));
        g.can_overwrite = 1;
        g.recursive = 1;
        assert!(allowed(
            &[g.clone()],
            r"C:\Users\Alice\sub\a",
            true,
            false,
            false,
            true
        ));
        g.expires_at = Some(now() - 1);
        assert!(!allowed(
            &[g],
            r"C:\Users\Alice\a",
            false,
            false,
            false,
            false
        ));
        assert!(allowed(
            &[grant("/", false)],
            "/file",
            false,
            false,
            false,
            false
        ));
        assert!(allowed(
            &[grant("C:\\", true)],
            r"C:\file",
            false,
            false,
            false,
            false
        ));
    }

    #[test]
    fn a_copy_contract_needs_both_capabilities_and_a_nonempty_condition() {
        let text = r#"
            [[capabilities]]
            name = "fs:write"
            [filesystem]
            protocol = "filesystem_broker_v1"
            operation = "copy"
            when = {arg="copy", values=[true]}
        "#;
        let mut manifest: toml::Value = toml::from_str(text).unwrap();
        assert!(parse_contract(&manifest).is_err());
        manifest["capabilities"]
            .as_array_mut()
            .unwrap()
            .push(toml::from_str("name='fs:read'").unwrap());
        let c = parse_contract(&manifest).unwrap().unwrap();
        assert!(c.applies(&json!({"copy":true})));
        assert!(!c.applies(&json!({"copy":false})));
        assert!(!c.applies(&json!({"copy":true,"client":"gdrive"})));
        manifest["filesystem"]["when"]["values"] = toml::Value::Array(vec![]);
        assert!(parse_contract(&manifest).is_err());
    }

    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("metnos-broker-test-{}", token()));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn volume_root_can_be_opened_for_listing() {
        #[cfg(unix)]
        let root = "/".to_owned();
        #[cfg(windows)]
        let root = std::env::var("SystemDrive").unwrap() + "\\";
        assert!(Guard::new(&root)
            .unwrap()
            .directory()
            .unwrap()
            .metadata()
            .unwrap()
            .is_dir());
    }

    #[test]
    fn stage_is_atomic_and_cannot_replace_an_existing_file_without_overwrite() {
        let temp = Temp::new();
        let target = temp.0.join("target");
        let guard = Guard::new(target.to_str().unwrap()).unwrap();
        let (stage, mut file) = guard.stage().unwrap();
        file.write_all(b"new content").unwrap();
        assert!(!target.exists());
        guard.commit(&stage, &file, false).unwrap();
        drop(file);
        assert_eq!(fs::read(&target).unwrap(), b"new content");
        let (stage, mut file) = guard.stage().unwrap();
        file.write_all(b"replacement").unwrap();
        assert!(guard.commit(&stage, &file, false).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"new content");
        guard.commit(&stage, &file, true).unwrap();
        drop(file);
        assert_eq!(fs::read(&target).unwrap(), b"replacement");
    }

    #[test]
    #[cfg(unix)]
    fn native_reads_refuse_symlink_ancestors_final_links_and_hardlinks() {
        use std::os::unix::fs::symlink;
        let temp = Temp::new();
        let real = temp.0.join("real");
        fs::create_dir(&real).unwrap();
        fs::write(real.join("file"), b"private").unwrap();
        symlink(&real, temp.0.join("alias")).unwrap();
        assert!(Guard::new(temp.0.join("alias/file").to_str().unwrap()).is_err());
        symlink(real.join("file"), temp.0.join("link")).unwrap();
        assert!(Guard::new(temp.0.join("link").to_str().unwrap())
            .unwrap()
            .open(false, false)
            .is_err());
        fs::hard_link(real.join("file"), temp.0.join("hard")).unwrap();
        assert!(Guard::new(temp.0.join("hard").to_str().unwrap())
            .unwrap()
            .open(false, false)
            .is_err());
    }
}
