//! Typed AppX/MSIX activation in the interactive user's Windows session.
//!
//! The public executor supplies only `appx:<PackageFullName>`. Windows then
//! verifies that package identity, resolves its application IDs from package
//! registration, and accepts exactly one. No path, executable, command line,
//! argument string, or application-specific table crosses this boundary.

use std::collections::HashSet;

const PREFIX: &str = "appx:";
const PACKAGE_FULL_NAME_MAX_LENGTH: usize = 127;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct ProcessIdentity {
    pub pid: u32,
    pub creation_time: u64,
}

impl std::str::FromStr for ProcessIdentity {
    type Err = &'static str;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let (pid, creation_time) = value
            .split_once(':')
            .ok_or("process identity must be pid:creation-time")?;
        let pid = pid.parse::<u32>().map_err(|_| "invalid process pid")?;
        let creation_time = creation_time
            .parse::<u64>()
            .map_err(|_| "invalid process creation time")?;
        if pid == 0 || creation_time == 0 {
            return Err("process identity values must be positive");
        }
        Ok(Self { pid, creation_time })
    }
}

fn cohort_candidates(
    current: &[ProcessIdentity],
    preexisting: &HashSet<ProcessIdentity>,
    activation_boundary: u64,
) -> Result<Vec<ProcessIdentity>, &'static str> {
    if current.iter().any(|process| {
        !preexisting.contains(process) && process.creation_time < activation_boundary
    }) {
        return Err("package_process_concurrent_change");
    }
    Ok(current
        .iter()
        .copied()
        .filter(|process| {
            !preexisting.contains(process) && process.creation_time >= activation_boundary
        })
        .collect())
}

fn image_is_inside_package(image_path: &str, package_root: &str) -> bool {
    let root = package_root.trim_end_matches(|character| character == '\\' || character == '/');
    if image_path.len() <= root.len() {
        return false;
    }
    let (prefix, suffix) = image_path.split_at(root.len());
    prefix.eq_ignore_ascii_case(root) && (suffix.starts_with('\\') || suffix.starts_with('/'))
}

fn package_full_name(identity: &str) -> Result<&str, &'static str> {
    let Some(value) = identity.strip_prefix(PREFIX) else {
        return Err("package_target_invalid");
    };
    if value.is_empty()
        || value.len() > PACKAGE_FULL_NAME_MAX_LENGTH
        || !value.is_ascii()
        || !value.as_bytes()[0].is_ascii_alphanumeric()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        return Err("package_target_invalid");
    }
    Ok(value)
}

fn failure(code: &'static str) -> serde_json::Value {
    serde_json::json!({
        "ok": false,
        "error_code": code,
        "detail": code,
    })
}

#[cfg(not(windows))]
pub fn query(identity: &str) -> serde_json::Value {
    match package_full_name(identity) {
        Ok(_) => failure("platform_unsupported"),
        Err(code) => failure(code),
    }
}

#[cfg(not(windows))]
pub fn start(identity: &str, _lifetime: &str) -> serde_json::Value {
    query(identity)
}

#[cfg(not(windows))]
pub fn stop(
    identity: &str,
    _pid: u32,
    _creation_time: u64,
    _activation_boundary: Option<u64>,
    _preexisting_processes: &[ProcessIdentity],
) -> serde_json::Value {
    query(identity)
}

#[cfg(windows)]
mod windows {
    use std::collections::HashSet;
    use std::ffi::c_void;
    use std::ptr::{null, null_mut};
    use std::time::Duration;

    use windows_sys::core::{GUID, HRESULT, PCWSTR};
    use windows_sys::Win32::Foundation::{
        CloseHandle, APPMODEL_ERROR_NO_APPLICATION, APPMODEL_ERROR_NO_PACKAGE,
        ERROR_INSUFFICIENT_BUFFER, ERROR_INVALID_PARAMETER, ERROR_SUCCESS, FILETIME, HANDLE,
        RPC_E_CHANGED_MODE, WAIT_OBJECT_0,
    };
    use windows_sys::Win32::Storage::FileSystem::SYNCHRONIZE;
    use windows_sys::Win32::Storage::Packaging::Appx::{
        ClosePackageInfo, GetApplicationUserModelId, GetPackageApplicationIds, GetPackageFullName,
        GetPackagePathByFullName, OpenPackageInfoByFullName, VerifyApplicationUserModelId,
        VerifyPackageFullName, _PACKAGE_INFO_REFERENCE,
    };
    use windows_sys::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_LOCAL_SERVER,
        COINIT_APARTMENTTHREADED,
    };
    use windows_sys::Win32::System::ProcessStatus::K32EnumProcesses;
    use windows_sys::Win32::System::SystemInformation::GetSystemTimeAsFileTime;
    use windows_sys::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, TerminateProcess,
        WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };
    use windows_sys::Win32::UI::Shell::ApplicationActivationManager;

    use super::{failure, image_is_inside_package, package_full_name, ProcessIdentity};

    const IID_IAPPLICATION_ACTIVATION_MANAGER: GUID =
        GUID::from_u128(0x2e941141_7f97_4756_ba1d_9decde894a3d);

    #[repr(C)]
    struct ActivationManagerVtable {
        query_interface:
            unsafe extern "system" fn(*mut c_void, *const GUID, *mut *mut c_void) -> HRESULT,
        add_ref: unsafe extern "system" fn(*mut c_void) -> u32,
        release: unsafe extern "system" fn(*mut c_void) -> u32,
        activate_application:
            unsafe extern "system" fn(*mut c_void, PCWSTR, PCWSTR, u32, *mut u32) -> HRESULT,
        activate_for_file: usize,
        activate_for_protocol: usize,
    }

    struct ComApartment(bool);

    impl ComApartment {
        fn enter() -> Result<Self, &'static str> {
            let result = unsafe { CoInitializeEx(null(), COINIT_APARTMENTTHREADED as u32) };
            if result >= 0 {
                Ok(Self(true))
            } else if result == RPC_E_CHANGED_MODE {
                // This process already has a valid COM apartment of another
                // kind. It may still create the local activation server, but
                // it must not balance another component's initialization.
                Ok(Self(false))
            } else {
                Err("package_start_failed")
            }
        }
    }

    impl Drop for ComApartment {
        fn drop(&mut self) {
            if self.0 {
                unsafe { CoUninitialize() };
            }
        }
    }

    struct ActivationManager(*mut c_void);

    impl ActivationManager {
        fn create() -> Result<Self, &'static str> {
            let mut pointer = null_mut();
            let result = unsafe {
                CoCreateInstance(
                    &ApplicationActivationManager,
                    null_mut(),
                    CLSCTX_LOCAL_SERVER,
                    &IID_IAPPLICATION_ACTIVATION_MANAGER,
                    &mut pointer,
                )
            };
            if result < 0 || pointer.is_null() {
                Err("package_start_failed")
            } else {
                Ok(Self(pointer))
            }
        }

        fn activate(&self, application_id: &[u16]) -> Result<u32, &'static str> {
            let vtable = unsafe { *(self.0 as *mut *const ActivationManagerVtable) };
            if vtable.is_null() {
                return Err("package_start_failed");
            }
            let mut pid = 0u32;
            let result = unsafe {
                ((*vtable).activate_application)(
                    self.0,
                    application_id.as_ptr(),
                    null(),
                    0,
                    &mut pid,
                )
            };
            if result < 0 || pid == 0 {
                Err("package_start_failed")
            } else {
                Ok(pid)
            }
        }
    }

    impl Drop for ActivationManager {
        fn drop(&mut self) {
            if self.0.is_null() {
                return;
            }
            let vtable = unsafe { *(self.0 as *mut *const ActivationManagerVtable) };
            if !vtable.is_null() {
                unsafe { ((*vtable).release)(self.0) };
            }
        }
    }

    struct PackageInfo(*mut _PACKAGE_INFO_REFERENCE);

    impl Drop for PackageInfo {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe { ClosePackageInfo(self.0) };
            }
        }
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn filetime_value(value: FILETIME) -> u64 {
        ((value.dwHighDateTime as u64) << 32) | value.dwLowDateTime as u64
    }

    fn now_filetime() -> u64 {
        let mut value: FILETIME = unsafe { std::mem::zeroed() };
        unsafe { GetSystemTimeAsFileTime(&mut value) };
        filetime_value(value)
    }

    fn application_id(identity: &str) -> Result<String, &'static str> {
        let full_name = package_full_name(identity)?;
        let full_name_wide = wide(full_name);
        if unsafe { VerifyPackageFullName(full_name_wide.as_ptr()) } != ERROR_SUCCESS {
            return Err("package_target_invalid");
        }

        let mut reference = null_mut();
        let open = unsafe { OpenPackageInfoByFullName(full_name_wide.as_ptr(), 0, &mut reference) };
        if open != ERROR_SUCCESS {
            return Err(if open == windows_sys::Win32::Foundation::ERROR_NOT_FOUND {
                "package_not_registered"
            } else {
                "package_operation_failed"
            });
        }
        let reference = PackageInfo(reference);

        let mut byte_length = 0u32;
        let first = unsafe {
            GetPackageApplicationIds(reference.0, &mut byte_length, null_mut(), null_mut())
        };
        if first != ERROR_INSUFFICIENT_BUFFER || byte_length == 0 || byte_length > 1024 * 1024 {
            return Err("package_target_missing");
        }

        // The returned buffer starts with an array of PCWSTR values. A usize
        // vector guarantees pointer alignment while still exposing its bytes
        // to the Win32 API.
        let word_bytes = std::mem::size_of::<usize>();
        let words = byte_length as usize / word_bytes + 1;
        let mut buffer = vec![0usize; words];
        let mut count = 0u32;
        let second = unsafe {
            GetPackageApplicationIds(
                reference.0,
                &mut byte_length,
                buffer.as_mut_ptr().cast::<u8>(),
                &mut count,
            )
        };
        if second != ERROR_SUCCESS {
            return Err("package_operation_failed");
        }
        if count == 0 {
            return Err("package_target_missing");
        }
        if count != 1 {
            return Err("package_target_ambiguous");
        }
        if byte_length as usize > buffer.len() * word_bytes || (byte_length as usize) < word_bytes {
            return Err("package_target_invalid");
        }

        let string_pointer = buffer[0] as *const u16;
        let base = buffer.as_ptr() as usize;
        let end = base + byte_length as usize;
        let start = string_pointer as usize;
        if string_pointer.is_null() || start < base || start >= end || start % 2 != 0 {
            return Err("package_target_invalid");
        }
        let maximum = (end - start) / std::mem::size_of::<u16>();
        let mut length = 0usize;
        while length < maximum && unsafe { *string_pointer.add(length) } != 0 {
            length += 1;
        }
        if length == 0 || length == maximum {
            return Err("package_target_invalid");
        }
        let value =
            String::from_utf16(unsafe { std::slice::from_raw_parts(string_pointer, length) })
                .map_err(|_| "package_target_invalid")?;
        let value_wide = wide(&value);
        if unsafe { VerifyApplicationUserModelId(value_wide.as_ptr()) } != ERROR_SUCCESS {
            return Err("package_target_invalid");
        }
        Ok(value)
    }

    fn process_application_id(process: HANDLE) -> Result<Option<String>, &'static str> {
        let mut length = 0u32;
        let first = unsafe { GetApplicationUserModelId(process, &mut length, null_mut()) };
        if first == APPMODEL_ERROR_NO_APPLICATION {
            return Ok(None);
        }
        if first != ERROR_INSUFFICIENT_BUFFER || length == 0 || length > 1024 {
            return Err("package_process_probe_failed");
        }
        let mut buffer = vec![0u16; length as usize];
        let second =
            unsafe { GetApplicationUserModelId(process, &mut length, buffer.as_mut_ptr()) };
        if second != ERROR_SUCCESS || length == 0 || length as usize > buffer.len() {
            return Err("package_process_probe_failed");
        }
        let used = length as usize;
        let value = String::from_utf16(&buffer[..used.saturating_sub(1)])
            .map_err(|_| "package_process_probe_failed")?;
        Ok(Some(value))
    }

    fn process_creation_time(process: HANDLE) -> Result<u64, &'static str> {
        let mut created: FILETIME = unsafe { std::mem::zeroed() };
        let mut exited: FILETIME = unsafe { std::mem::zeroed() };
        let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
        let mut user: FILETIME = unsafe { std::mem::zeroed() };
        if unsafe { GetProcessTimes(process, &mut created, &mut exited, &mut kernel, &mut user) }
            == 0
        {
            return Err("package_process_probe_failed");
        }
        let value = filetime_value(created);
        if value == 0 {
            Err("package_process_probe_failed")
        } else {
            Ok(value)
        }
    }

    fn process_package_full_name(process: HANDLE) -> Result<Option<String>, &'static str> {
        let mut length = 0u32;
        let first = unsafe { GetPackageFullName(process, &mut length, null_mut()) };
        if first == APPMODEL_ERROR_NO_PACKAGE {
            return Ok(None);
        }
        if first != ERROR_INSUFFICIENT_BUFFER || length == 0 || length > 1024 {
            return Err("package_process_probe_failed");
        }
        let mut buffer = vec![0u16; length as usize];
        let second = unsafe { GetPackageFullName(process, &mut length, buffer.as_mut_ptr()) };
        if second != ERROR_SUCCESS || length == 0 || length as usize > buffer.len() {
            return Err("package_process_probe_failed");
        }
        let value = String::from_utf16(&buffer[..length as usize - 1])
            .map_err(|_| "package_process_probe_failed")?;
        Ok(Some(value))
    }

    fn package_install_path(full_name: &str) -> Result<String, &'static str> {
        let full_name_wide = wide(full_name);
        let mut length = 0u32;
        let first =
            unsafe { GetPackagePathByFullName(full_name_wide.as_ptr(), &mut length, null_mut()) };
        if first != ERROR_INSUFFICIENT_BUFFER || length == 0 || length > 32_768 {
            return Err("package_install_location_unavailable");
        }
        let mut buffer = vec![0u16; length as usize];
        let second = unsafe {
            GetPackagePathByFullName(full_name_wide.as_ptr(), &mut length, buffer.as_mut_ptr())
        };
        if second != ERROR_SUCCESS || length == 0 || length as usize > buffer.len() {
            return Err("package_install_location_unavailable");
        }
        String::from_utf16(&buffer[..length as usize - 1])
            .map_err(|_| "package_install_location_unavailable")
    }

    fn process_image_path(process: HANDLE) -> Result<String, &'static str> {
        let mut buffer = vec![0u16; 32_768];
        let mut length = buffer.len() as u32;
        if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
            || length == 0
            || length as usize > buffer.len()
        {
            return Err("package_process_probe_failed");
        }
        String::from_utf16(&buffer[..length as usize]).map_err(|_| "package_process_probe_failed")
    }

    fn process_belongs_to_package(
        process: HANDLE,
        full_name: &str,
        package_root: &str,
    ) -> Result<bool, &'static str> {
        match process_package_full_name(process) {
            Ok(Some(value)) => return Ok(value.eq_ignore_ascii_case(full_name)),
            Ok(None) | Err(_) => {}
        }
        // Full-trust applications distributed in MSIX may hand activation to
        // a process without package identity. Its kernel image path is still
        // authoritative when it lies beneath the registered immutable package
        // root returned by GetPackagePathByFullName.
        Ok(image_is_inside_package(
            &process_image_path(process)?,
            package_root,
        ))
    }

    fn package_processes(
        full_name: &str,
        package_root: &str,
    ) -> Result<Vec<ProcessIdentity>, &'static str> {
        let mut capacity = 4096usize;
        let process_ids = loop {
            let mut ids = vec![0u32; capacity];
            let mut needed = 0u32;
            let bytes = (ids.len() * std::mem::size_of::<u32>()) as u32;
            if unsafe { K32EnumProcesses(ids.as_mut_ptr(), bytes, &mut needed) } == 0 {
                return Err("package_process_probe_failed");
            }
            if needed < bytes {
                ids.truncate(needed as usize / std::mem::size_of::<u32>());
                break ids;
            }
            capacity = capacity
                .checked_mul(2)
                .filter(|value| *value <= 65_536)
                .ok_or("package_process_probe_failed")?;
        };

        let mut identities = Vec::new();
        for pid in process_ids.into_iter().filter(|pid| *pid != 0) {
            let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
            if process.is_null() {
                continue;
            }
            let candidate: Result<Option<ProcessIdentity>, &'static str> = (|| {
                if process_belongs_to_package(process, full_name, package_root)? {
                    Ok(Some(ProcessIdentity {
                        pid,
                        creation_time: process_creation_time(process)?,
                    }))
                } else {
                    Ok(None)
                }
            })();
            unsafe { CloseHandle(process) };
            match candidate {
                Ok(Some(identity)) => identities.push(identity),
                // A protected or exiting process is unrelated unless it can
                // be positively tied to this package. One uninspectable
                // system process must not hide a different, fully verified
                // candidate later in the enumeration.
                Ok(None) | Err(_) => continue,
            }
        }
        identities.sort_unstable_by_key(|identity| (identity.creation_time, identity.pid));
        identities.dedup();
        Ok(identities)
    }

    fn open_verified_package_process(
        full_name: &str,
        package_root: &str,
        identity: ProcessIdentity,
        access: u32,
    ) -> Result<Option<HANDLE>, &'static str> {
        let process = unsafe { OpenProcess(access, 0, identity.pid) };
        if process.is_null() {
            return if unsafe { windows_sys::Win32::Foundation::GetLastError() }
                == ERROR_INVALID_PARAMETER
            {
                Ok(None)
            } else {
                Err("package_process_probe_failed")
            };
        }
        let result = (|| {
            if !process_belongs_to_package(process, full_name, package_root)?
                || process_creation_time(process)? != identity.creation_time
            {
                return Err("package_process_identity_mismatch");
            }
            Ok(())
        })();
        if let Err(code) = result {
            unsafe { CloseHandle(process) };
            return Err(code);
        }
        Ok(Some(process))
    }

    fn open_verified_process(
        application_id: &str,
        pid: u32,
        access: u32,
    ) -> Result<Option<(HANDLE, u64)>, &'static str> {
        let process = unsafe { OpenProcess(access, 0, pid) };
        if process.is_null() {
            return if unsafe { windows_sys::Win32::Foundation::GetLastError() }
                == ERROR_INVALID_PARAMETER
            {
                Ok(None)
            } else {
                Err("package_process_probe_failed")
            };
        }
        let result = (|| {
            let actual =
                process_application_id(process)?.ok_or("package_process_identity_mismatch")?;
            if !actual.eq_ignore_ascii_case(application_id) {
                return Err("package_process_identity_mismatch");
            }
            Ok(process_creation_time(process)?)
        })();
        match result {
            Ok(created) => Ok(Some((process, created))),
            Err(code) => {
                unsafe { CloseHandle(process) };
                Err(code)
            }
        }
    }

    pub fn query(identity: &str) -> serde_json::Value {
        match application_id(identity) {
            Ok(aumid) => serde_json::json!({
                "ok": true,
                "package_id": identity,
                "application_id": aumid,
                "lifetimes": ["session"],
            }),
            Err(code) => failure(code),
        }
    }

    pub fn start(identity: &str, lifetime: &str) -> serde_json::Value {
        if lifetime != "session" {
            return failure("package_persistence_unsupported");
        }
        let application_id = match application_id(identity) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let full_name = match package_full_name(identity) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let package_root = match package_install_path(full_name) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let preexisting_processes = match package_processes(full_name, &package_root) {
            Ok(value) if value.len() <= 64 => value,
            Ok(_) => return failure("package_process_snapshot_too_large"),
            Err(code) => return failure(code),
        };
        let boundary = now_filetime();
        let application_id_wide = wide(&application_id);
        let apartment = match ComApartment::enter() {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let manager = match ActivationManager::create() {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let pid = match manager.activate(&application_id_wide) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        drop(manager);
        drop(apartment);

        let process =
            match open_verified_process(&application_id, pid, PROCESS_QUERY_LIMITED_INFORMATION) {
                Ok(Some(value)) => value,
                Ok(None) => return failure("package_start_unverified"),
                Err(code) => return failure(code),
            };
        let (handle, creation_time) = process;
        unsafe { CloseHandle(handle) };
        let activation_process = ProcessIdentity { pid, creation_time };
        let created_process =
            creation_time >= boundary && !preexisting_processes.contains(&activation_process);
        serde_json::json!({
            "ok": true,
            "package_id": identity,
            "payload": {
                "created_process": created_process,
                "persistent_registration_changed": false,
                "process": {
                    "pid": pid,
                    "creation_time": creation_time,
                },
                "activation_boundary": boundary,
                "preexisting_processes": preexisting_processes
                    .iter()
                    .map(|process| serde_json::json!({
                        "pid": process.pid,
                        "creation_time": process.creation_time,
                    }))
                    .collect::<Vec<_>>(),
            },
        })
    }

    fn stop_process_cohort(
        identity: &str,
        activation_process: ProcessIdentity,
        activation_boundary: u64,
        preexisting_processes: &[ProcessIdentity],
    ) -> serde_json::Value {
        if activation_boundary == 0
            || activation_process.creation_time < activation_boundary
            || preexisting_processes.len() > 64
        {
            return failure("package_stop_receipt_invalid");
        }
        let preexisting: HashSet<ProcessIdentity> = preexisting_processes.iter().copied().collect();
        if preexisting.len() != preexisting_processes.len()
            || preexisting.contains(&activation_process)
        {
            return failure("package_stop_receipt_invalid");
        }
        let full_name = match package_full_name(identity) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let package_root = match package_install_path(full_name) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };

        let mut terminated_count = 0usize;
        for _ in 0..20 {
            let current = match package_processes(full_name, &package_root) {
                Ok(value) => value,
                Err(code) => return failure(code),
            };
            let candidates =
                match super::cohort_candidates(&current, &preexisting, activation_boundary) {
                    Ok(value) => value,
                    Err(code) => return failure(code),
                };
            if candidates.is_empty() {
                return if terminated_count > 0 {
                    serde_json::json!({
                        "ok": true,
                        "package_id": identity,
                        "payload": {
                            "restored": true,
                            "stopped": true,
                            "terminated_count": terminated_count,
                        },
                    })
                } else {
                    // A disappeared activation PID is not proof that a
                    // brokered/full-trust successor has also gone. Without a
                    // positively identified target or a termination receipt,
                    // the user-visible postcondition is unknown, never true.
                    failure("package_stop_target_missing")
                };
            }
            for candidate in candidates {
                let process = match open_verified_package_process(
                    full_name,
                    &package_root,
                    candidate,
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE,
                ) {
                    Ok(Some(value)) => value,
                    Ok(None) => continue,
                    Err(code) => return failure(code),
                };
                let terminated = unsafe { TerminateProcess(process, 1) } != 0
                    && unsafe { WaitForSingleObject(process, 5000) } == WAIT_OBJECT_0;
                unsafe { CloseHandle(process) };
                if !terminated {
                    return failure("package_stop_unverified");
                }
                terminated_count += 1;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        failure("package_stop_unverified")
    }

    pub fn stop(
        identity: &str,
        pid: u32,
        creation_time: u64,
        activation_boundary: Option<u64>,
        preexisting_processes: &[ProcessIdentity],
    ) -> serde_json::Value {
        if let Some(boundary) = activation_boundary {
            return stop_process_cohort(
                identity,
                ProcessIdentity { pid, creation_time },
                boundary,
                preexisting_processes,
            );
        }
        let application_id = match application_id(identity) {
            Ok(value) => value,
            Err(code) => return failure(code),
        };
        let process = match open_verified_process(
            &application_id,
            pid,
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE,
        ) {
            Ok(Some(value)) => value,
            Ok(None) => return failure("package_stop_target_missing"),
            Err(code) => return failure(code),
        };
        let (handle, actual_creation) = process;
        let result = if actual_creation != creation_time {
            failure("package_process_identity_mismatch")
        } else if unsafe { TerminateProcess(handle, 1) } == 0 {
            failure("package_stop_failed")
        } else if unsafe { WaitForSingleObject(handle, 5000) } != WAIT_OBJECT_0 {
            failure("package_stop_unverified")
        } else {
            serde_json::json!({
                "ok": true,
                "package_id": identity,
                "payload": {"restored": true, "stopped": true},
            })
        };
        unsafe { CloseHandle(handle) };
        result
    }
}

#[cfg(windows)]
pub use windows::{query, start, stop};

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::{cohort_candidates, image_is_inside_package, package_full_name, ProcessIdentity};

    #[test]
    fn typed_appx_identity_accepts_a_package_full_name() {
        assert_eq!(
            package_full_name("appx:Microsoft.WindowsNotepad_11.2606.15.0_x64__8wekyb3d8bbwe"),
            Ok("Microsoft.WindowsNotepad_11.2606.15.0_x64__8wekyb3d8bbwe")
        );
    }

    #[test]
    fn raw_inventory_ids_paths_and_commands_are_not_launch_identities() {
        for value in [
            r"MSIX\Microsoft.WindowsNotepad_11.0_x64__8wekyb3d8bbwe",
            r"appx:C:\Windows\notepad.exe",
            "appx:Vendor.App --flag",
            "appx:Vendor.*",
            "Vendor.App",
        ] {
            assert!(package_full_name(value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn package_full_name_length_is_bounded() {
        assert!(package_full_name(&format!("appx:{}", "A".repeat(127))).is_ok());
        assert!(package_full_name(&format!("appx:{}", "A".repeat(128))).is_err());
    }

    #[test]
    fn process_identity_parser_is_closed_and_positive() {
        assert_eq!(
            "42:133700000000000000".parse::<ProcessIdentity>(),
            Ok(ProcessIdentity {
                pid: 42,
                creation_time: 133700000000000000,
            })
        );
        for value in ["", "42", "0:1", "1:0", "1:2:3", "x:2"] {
            assert!(
                value.parse::<ProcessIdentity>().is_err(),
                "accepted {value}"
            );
        }
    }

    #[test]
    fn cohort_follows_a_post_activation_process_handoff() {
        let preexisting = HashSet::new();
        let successor = ProcessIdentity {
            pid: 23904,
            creation_time: 133700000000000200,
        };

        assert_eq!(
            cohort_candidates(&[successor], &preexisting, 133700000000000100),
            Ok(vec![successor])
        );
    }

    #[test]
    fn cohort_never_selects_preexisting_or_unexplained_older_processes() {
        let old = ProcessIdentity {
            pid: 40,
            creation_time: 133600000000000000,
        };
        let preexisting = HashSet::from([old]);
        assert_eq!(
            cohort_candidates(&[old], &preexisting, 133700000000000000),
            Ok(vec![])
        );
        let unexplained = ProcessIdentity {
            pid: 41,
            creation_time: 133699999999999999,
        };
        assert_eq!(
            cohort_candidates(&[unexplained], &preexisting, 133700000000000000),
            Err("package_process_concurrent_change")
        );
    }

    #[test]
    fn only_images_below_the_registered_package_root_are_members() {
        let root = r"C:\Program Files\WindowsApps\Vendor.App_1.0_x64__publisher";
        assert!(image_is_inside_package(
            r"c:\PROGRAM FILES\WindowsApps\Vendor.App_1.0_x64__publisher\bin\App.exe",
            root,
        ));
        assert!(!image_is_inside_package(root, root));
        assert!(!image_is_inside_package(
            r"C:\Program Files\WindowsApps\Vendor.App_1.0_x64__publisher-evil\App.exe",
            root,
        ));
        assert!(!image_is_inside_package(
            r"C:\Windows\System32\App.exe",
            root,
        ));
    }
}
