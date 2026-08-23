//! Typed AppX/MSIX activation in the interactive user's Windows session.
//!
//! The public executor supplies only `appx:<PackageFullName>`. Windows then
//! verifies that package identity, resolves its application IDs from package
//! registration, and accepts exactly one. No path, executable, command line,
//! argument string, or application-specific table crosses this boundary.

const PREFIX: &str = "appx:";
const PACKAGE_FULL_NAME_MAX_LENGTH: usize = 127;

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
pub fn stop(identity: &str, _pid: u32, _creation_time: u64) -> serde_json::Value {
    query(identity)
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;
    use std::ptr::{null, null_mut};

    use windows_sys::core::{GUID, HRESULT, PCWSTR};
    use windows_sys::Win32::Foundation::{
        CloseHandle, APPMODEL_ERROR_NO_APPLICATION, ERROR_INSUFFICIENT_BUFFER,
        ERROR_INVALID_PARAMETER, ERROR_SUCCESS, FILETIME, HANDLE, RPC_E_CHANGED_MODE,
        WAIT_OBJECT_0,
    };
    use windows_sys::Win32::Storage::FileSystem::SYNCHRONIZE;
    use windows_sys::Win32::Storage::Packaging::Appx::{
        ClosePackageInfo, GetApplicationUserModelId, GetPackageApplicationIds,
        OpenPackageInfoByFullName, VerifyApplicationUserModelId, VerifyPackageFullName,
        _PACKAGE_INFO_REFERENCE,
    };
    use windows_sys::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_LOCAL_SERVER,
        COINIT_APARTMENTTHREADED,
    };
    use windows_sys::Win32::System::SystemInformation::GetSystemTimeAsFileTime;
    use windows_sys::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, TerminateProcess, WaitForSingleObject,
        PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };
    use windows_sys::Win32::UI::Shell::ApplicationActivationManager;

    use super::{failure, package_full_name};

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
        let created_process = creation_time >= boundary;
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
            },
        })
    }

    pub fn stop(identity: &str, pid: u32, creation_time: u64) -> serde_json::Value {
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
            Ok(None) => {
                return serde_json::json!({
                    "ok": true,
                    "package_id": identity,
                    "payload": {"stopped": false},
                })
            }
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
                "payload": {"stopped": true},
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
    use super::package_full_name;

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
}
