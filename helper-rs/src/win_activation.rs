//! Windows implementation of managed package start (ADR 0211).
//!
//! The caller supplies only an exact package identity and a closed lifetime.
//! Registry discovery, target validation, process identity, task naming, and
//! every command argument are owned by this module.

#![cfg(windows)]

use std::io;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use sha2::{Digest, Sha256};
use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_MORE_DATA, ERROR_NO_MORE_ITEMS, ERROR_SUCCESS, INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegEnumKeyExW, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_LOCAL_MACHINE,
    KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY, REG_SZ,
};
use windows_sys::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, CREATE_NO_WINDOW, DETACHED_PROCESS,
    PROCESS_QUERY_LIMITED_INFORMATION,
};

use crate::activation::{self, Registration};
use crate::protocol::StartLifetime;
use crate::service::Outcome;

const UNINSTALL_ROOT: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall";

struct RegistryKey(HKEY);

impl Drop for RegistryKey {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn open_key(parent: HKEY, path: &str, access: u32) -> io::Result<RegistryKey> {
    let mut key: HKEY = std::ptr::null_mut();
    let status = unsafe { RegOpenKeyExW(parent, wide(path).as_ptr(), 0, access, &mut key) };
    if status != ERROR_SUCCESS {
        return Err(io::Error::from_raw_os_error(status as i32));
    }
    Ok(RegistryKey(key))
}

fn subkey_names(key: HKEY) -> Vec<String> {
    let mut names = Vec::new();
    let mut index = 0u32;
    loop {
        let mut capacity = 256usize;
        let name = loop {
            let mut buffer = vec![0u16; capacity];
            let mut length = buffer.len() as u32;
            let status = unsafe {
                RegEnumKeyExW(
                    key,
                    index,
                    buffer.as_mut_ptr(),
                    &mut length,
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            };
            if status == ERROR_NO_MORE_ITEMS {
                return names;
            }
            if status == ERROR_MORE_DATA && capacity < 4096 {
                capacity *= 2;
                continue;
            }
            if status != ERROR_SUCCESS {
                break None;
            }
            break Some(String::from_utf16_lossy(&buffer[..length as usize]));
        };
        if let Some(name) = name {
            names.push(name);
        }
        index += 1;
    }
}

fn string_value(key: HKEY, name: &str) -> Option<String> {
    let name = wide(name);
    let mut kind = 0u32;
    let mut bytes = 0u32;
    let first = unsafe {
        RegQueryValueExW(
            key,
            name.as_ptr(),
            std::ptr::null(),
            &mut kind,
            std::ptr::null_mut(),
            &mut bytes,
        )
    };
    if first != ERROR_SUCCESS || kind != REG_SZ || bytes < 2 || bytes > 64 * 1024 {
        return None;
    }
    let mut buffer = vec![0u16; (bytes as usize + 1) / 2];
    let second = unsafe {
        RegQueryValueExW(
            key,
            name.as_ptr(),
            std::ptr::null(),
            &mut kind,
            buffer.as_mut_ptr().cast::<u8>(),
            &mut bytes,
        )
    };
    if second != ERROR_SUCCESS || kind != REG_SZ {
        return None;
    }
    let length = buffer
        .iter()
        .position(|value| *value == 0)
        .unwrap_or(buffer.len());
    Some(String::from_utf16_lossy(&buffer[..length]))
}

fn registrations_in_view(view: u32) -> Vec<Registration> {
    let Ok(root) = open_key(HKEY_LOCAL_MACHINE, UNINSTALL_ROOT, KEY_READ | view) else {
        return Vec::new();
    };
    let mut registrations = Vec::new();
    for subkey in subkey_names(root.0) {
        let path = format!(r"{UNINSTALL_ROOT}\{subkey}");
        let Ok(key) = open_key(HKEY_LOCAL_MACHINE, &path, KEY_READ | view) else {
            continue;
        };
        let Some(package_id) = string_value(key.0, "WinGetPackageIdentifier") else {
            continue;
        };
        registrations.push(Registration {
            package_id,
            installer_type: string_value(key.0, "WinGetInstallerType").unwrap_or_default(),
            install_location: string_value(key.0, "InstallLocation")
                .map(PathBuf::from)
                .unwrap_or_default(),
            target_full_path: string_value(key.0, "PortableTargetFullPath")
                .map(PathBuf::from)
                .unwrap_or_default(),
        });
    }
    registrations
}

fn registered_packages() -> Vec<Registration> {
    let mut entries = registrations_in_view(KEY_WOW64_64KEY);
    entries.extend(registrations_in_view(KEY_WOW64_32KEY));
    entries
}

fn canonical_target(package_id: &str) -> Result<PathBuf, &'static str> {
    let selected = activation::select_target(package_id, &registered_packages())
        .map_err(|error| error.code())?;
    let root = std::fs::canonicalize(&selected.install_location)
        .map_err(|_| "package_install_location_unavailable")?;
    let executable = match selected.executable {
        Some(path) => std::fs::canonicalize(path).map_err(|_| "package_target_unavailable")?,
        None => activation::discover_unique_executable(&root).map_err(|error| error.code())?,
    };
    if !executable.is_file()
        || executable
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| !value.eq_ignore_ascii_case("exe"))
            .unwrap_or(true)
        || !activation::target_is_contained(&root, &executable)
    {
        return Err("package_target_invalid");
    }
    Ok(executable)
}

/// Resolve the canonical installation root for one exact registered package.
/// Provider adapters use this shared boundary instead of implementing a
/// second registry parser or accepting a path from the caller.
pub(crate) fn canonical_package_root(package_id: &str) -> Result<PathBuf, &'static str> {
    let selected = activation::select_target(package_id, &registered_packages())
        .map_err(|error| error.code())?;
    std::fs::canonicalize(&selected.install_location)
        .map_err(|_| "package_install_location_unavailable")
}

/// Resolve one fixed direct child inside an exact registered package.
pub(crate) fn canonical_direct_child(
    package_id: &str,
    file_name: &str,
) -> Result<PathBuf, &'static str> {
    if file_name.is_empty()
        || file_name.contains(['/', '\\'])
        || file_name == "."
        || file_name == ".."
    {
        return Err("provider_asset_invalid");
    }
    let root = canonical_package_root(package_id)?;
    let asset =
        std::fs::canonicalize(root.join(file_name)).map_err(|_| "provider_asset_missing")?;
    if !asset.is_file()
        || asset.parent().map(activation::windows_path_key)
            != Some(activation::windows_path_key(&root))
        || !activation::target_is_contained(&root, &asset)
    {
        return Err("provider_asset_invalid");
    }
    Ok(asset)
}

fn process_is_running(target: &Path) -> Result<bool, &'static str> {
    let target = activation::windows_path_key(target);
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err("package_process_probe_failed");
    }
    let mut entry: PROCESSENTRY32W = unsafe { std::mem::zeroed() };
    entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
    let mut found = false;
    let mut has_entry = unsafe { Process32FirstW(snapshot, &mut entry) } != 0;
    while has_entry {
        let process =
            unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.th32ProcessID) };
        if !process.is_null() {
            let mut buffer = vec![0u16; 32768];
            let mut length = buffer.len() as u32;
            let read =
                unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) }
                    != 0;
            unsafe {
                CloseHandle(process);
            }
            if read {
                let path = PathBuf::from(String::from_utf16_lossy(&buffer[..length as usize]));
                if activation::windows_path_key(&path) == target {
                    found = true;
                    break;
                }
            }
        }
        has_entry = unsafe { Process32NextW(snapshot, &mut entry) } != 0;
    }
    unsafe {
        CloseHandle(snapshot);
    }
    Ok(found)
}

fn wait_until_running(target: &Path) -> Result<bool, &'static str> {
    for _ in 0..50 {
        if process_is_running(target)? {
            return Ok(true);
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    Ok(false)
}

fn detached_command(program: &Path) -> Command {
    let mut command = Command::new(program);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(DETACHED_PROCESS | CREATE_NO_WINDOW);
    if let Some(parent) = program.parent() {
        command.current_dir(parent);
    }
    command
}

fn task_name(package_id: &str) -> String {
    let digest = hex::encode(Sha256::digest(package_id.as_bytes()));
    format!("Metnos Managed {}", &digest[..24])
}

fn schtasks_path() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("System32")
        .join("schtasks.exe")
}

fn run_schtasks(arguments: &[&str]) -> io::Result<bool> {
    let status = Command::new(schtasks_path())
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .status()?;
    Ok(status.success())
}

fn configure_startup(package_id: &str, target: &Path) -> Result<(), &'static str> {
    let task = task_name(package_id);
    let Some(target) = target.to_str() else {
        return Err("package_target_invalid");
    };
    let created = run_schtasks(&[
        "/Create", "/TN", &task, "/TR", target, "/SC", "ONSTART", "/RU", "SYSTEM", "/RL",
        "HIGHEST", "/F",
    ])
    .map_err(|_| "package_persistence_failed")?;
    if !created {
        return Err("package_persistence_failed");
    }
    Ok(())
}

fn run_startup_task(package_id: &str) -> Result<(), &'static str> {
    let task = task_name(package_id);
    let started = run_schtasks(&["/Run", "/TN", &task]).map_err(|_| "package_start_failed")?;
    if !started {
        return Err("package_start_failed");
    }
    Ok(())
}

/// Resolve and start one exact package without accepting executable input.
pub fn start(package_id: &str, lifetime: StartLifetime) -> Outcome {
    let target = match canonical_target(package_id) {
        Ok(target) => target,
        Err(code) => return Outcome::failure(code, code),
    };

    if lifetime == StartLifetime::Persistent {
        if let Err(code) = configure_startup(package_id, &target) {
            return Outcome::failure(code, code);
        }
    }

    match process_is_running(&target) {
        Ok(true) => {
            return Outcome::success(if lifetime == StartLifetime::Persistent {
                "already_running_persistent"
            } else {
                "already_running"
            });
        }
        Ok(false) => {}
        Err(code) => return Outcome::failure(code, code),
    }

    let started = match lifetime {
        StartLifetime::Session => detached_command(&target)
            .spawn()
            .map(|_| ())
            .map_err(|_| "package_start_failed"),
        StartLifetime::Persistent => run_startup_task(package_id),
    };
    match started {
        Ok(()) => match wait_until_running(&target) {
            Ok(true) => Outcome::success(if lifetime == StartLifetime::Persistent {
                "started_persistent"
            } else {
                "started_session"
            }),
            Ok(false) => Outcome::failure("package_start_unverified", "package_start_unverified"),
            Err(code) => Outcome::failure(code, code),
        },
        Err(code) => Outcome::failure(code, code),
    }
}
