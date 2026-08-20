//! Pure selection rules for managed package start (ADR 0211).
//!
//! Windows registry access and process creation live in `win_activation`.
//! This module only decides whether machine-owned package metadata describes
//! one supported executable, so its security boundary is testable on every
//! platform.

use std::path::{Path, PathBuf};

/// The registration fields needed by a target resolver.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Registration {
    pub package_id: String,
    pub installer_type: String,
    pub install_location: PathBuf,
    pub target_full_path: PathBuf,
}

/// A target selected from one or more registry views.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    pub install_location: PathBuf,
    pub executable: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolveError {
    NotRegistered,
    UnsupportedInstaller,
    MissingTarget,
    AmbiguousTarget,
    InvalidTarget,
}

impl ResolveError {
    pub fn code(self) -> &'static str {
        match self {
            ResolveError::NotRegistered => "package_not_registered",
            ResolveError::UnsupportedInstaller => "package_start_unsupported",
            ResolveError::MissingTarget => "package_target_missing",
            ResolveError::AmbiguousTarget => "package_target_ambiguous",
            ResolveError::InvalidTarget => "package_target_invalid",
        }
    }
}

/// Normalised textual identity used only for comparisons and deduplication.
///
/// Windows paths are case-insensitive and may use either slash. Registry
/// views can therefore describe the same target with different spelling.
pub fn windows_path_key(path: &Path) -> String {
    let mut value = path.to_string_lossy().replace('/', "\\");
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        value = format!(r"\\{rest}");
    } else if let Some(rest) = value.strip_prefix(r"\\?\") {
        value = rest.to_string();
    }
    while value.len() > 3 && value.ends_with('\\') {
        value.pop();
    }
    value.to_ascii_lowercase()
}

/// Convert an already-canonical Windows local path for an older consumer.
///
/// `std::fs::canonicalize` deliberately returns the extended-length
/// `\\?\C:\...` spelling on Windows.  That is the right representation for
/// containment checks, but .NET Framework's assembly loader does not accept
/// it.  Strip only the verbatim prefix of a local drive path after canonical
/// validation; UNC, device-namespace, relative, and malformed paths remain
/// refusals.
pub fn windows_local_interop_path(path: &Path) -> Result<PathBuf, ResolveError> {
    let value = path.to_str().ok_or(ResolveError::InvalidTarget)?;
    let value = value.replace('/', "\\");
    let value = value.strip_prefix(r"\\?\").unwrap_or(&value);
    let bytes = value.as_bytes();
    if bytes.len() < 3
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || bytes[2] != b'\\'
        || value.contains('\0')
    {
        return Err(ResolveError::InvalidTarget);
    }
    Ok(PathBuf::from(value))
}

/// Select exactly one portable WinGet target without guessing.
pub fn select_target(
    package_id: &str,
    registrations: &[Registration],
) -> Result<Target, ResolveError> {
    let matching: Vec<&Registration> = registrations
        .iter()
        .filter(|entry| entry.package_id.eq_ignore_ascii_case(package_id))
        .collect();
    if matching.is_empty() {
        return Err(ResolveError::NotRegistered);
    }

    let portable: Vec<&Registration> = matching
        .into_iter()
        .filter(|entry| entry.installer_type.eq_ignore_ascii_case("portable"))
        .collect();
    if portable.is_empty() {
        return Err(ResolveError::UnsupportedInstaller);
    }

    let mut targets: Vec<Target> = portable
        .into_iter()
        .filter(|entry| !entry.install_location.as_os_str().is_empty())
        .map(|entry| Target {
            install_location: entry.install_location.clone(),
            executable: (!entry.target_full_path.as_os_str().is_empty())
                .then(|| entry.target_full_path.clone()),
        })
        .collect();
    if targets.is_empty() {
        return Err(ResolveError::MissingTarget);
    }

    targets.sort_by_key(|target| {
        (
            windows_path_key(&target.install_location),
            target
                .executable
                .as_deref()
                .map(windows_path_key)
                .unwrap_or_default(),
        )
    });
    targets.dedup_by(|left, right| {
        left.executable.as_deref().map(windows_path_key)
            == right.executable.as_deref().map(windows_path_key)
            && windows_path_key(&left.install_location) == windows_path_key(&right.install_location)
    });
    if targets.len() != 1 {
        return Err(ResolveError::AmbiguousTarget);
    }
    Ok(targets.remove(0))
}

/// Find the only executable in a registered portable package directory.
///
/// Archive-style WinGet packages keep their file list in a private index and
/// do not publish `PortableTargetFullPath`. The safe generic fallback is the
/// unique `.exe` below the registered install root. Zero or multiple matches
/// are refusals; names never influence the choice.
pub fn discover_unique_executable(root: &Path) -> Result<PathBuf, ResolveError> {
    const MAX_ENTRIES: usize = 4096;

    let root = std::fs::canonicalize(root).map_err(|_| ResolveError::InvalidTarget)?;
    let mut directories = vec![root.clone()];
    let mut seen = std::collections::HashSet::new();
    let mut executables = Vec::new();
    let mut entries_seen = 0usize;

    while let Some(directory) = directories.pop() {
        if !seen.insert(windows_path_key(&directory)) {
            continue;
        }
        let entries = std::fs::read_dir(&directory).map_err(|_| ResolveError::InvalidTarget)?;
        for entry in entries {
            entries_seen += 1;
            if entries_seen > MAX_ENTRIES {
                return Err(ResolveError::InvalidTarget);
            }
            let path =
                std::fs::canonicalize(entry.map_err(|_| ResolveError::InvalidTarget)?.path())
                    .map_err(|_| ResolveError::InvalidTarget)?;
            if !target_is_contained(&root, &path) {
                return Err(ResolveError::InvalidTarget);
            }
            if path.is_dir() {
                directories.push(path);
            } else if path.is_file()
                && path
                    .extension()
                    .and_then(|value| value.to_str())
                    .is_some_and(|value| value.eq_ignore_ascii_case("exe"))
            {
                executables.push(path);
            }
        }
    }

    executables.sort_by_key(|path| windows_path_key(path));
    executables.dedup_by(|left, right| windows_path_key(left) == windows_path_key(right));
    match executables.len() {
        0 => Err(ResolveError::MissingTarget),
        1 => Ok(executables.remove(0)),
        _ => Err(ResolveError::AmbiguousTarget),
    }
}

/// The canonical executable must stay inside the canonical installation root.
pub fn target_is_contained(install_location: &Path, executable: &Path) -> bool {
    let root = windows_path_key(install_location);
    let target = windows_path_key(executable);
    if root.is_empty() || target.is_empty() || root == target {
        return false;
    }
    target
        .strip_prefix(&root)
        .map(|tail| tail.starts_with('\\'))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registration(id: &str, kind: &str, root: &str, target: &str) -> Registration {
        Registration {
            package_id: id.into(),
            installer_type: kind.into(),
            install_location: root.into(),
            target_full_path: target.into(),
        }
    }

    #[test]
    fn exact_package_identity_selects_one_portable_target() {
        let entries = [
            registration(
                "Other.App",
                "portable",
                r"C:\Apps\Other",
                r"C:\Apps\Other\x.exe",
            ),
            registration(
                "Vendor.Sensor",
                "Portable",
                r"C:\Program Files\WinGet\Sensor",
                r"C:\Program Files\WinGet\Sensor\sensor.exe",
            ),
        ];
        let target = select_target("vendor.sensor", &entries).unwrap();
        assert_eq!(
            target.executable,
            Some(PathBuf::from(r"C:\Program Files\WinGet\Sensor\sensor.exe"))
        );
    }

    #[test]
    fn unsupported_installer_is_not_guessed() {
        let entries = [registration(
            "Vendor.App",
            "msi",
            r"C:\Program Files\Vendor",
            r"C:\Program Files\Vendor\app.exe",
        )];
        assert_eq!(
            select_target("Vendor.App", &entries),
            Err(ResolveError::UnsupportedInstaller)
        );
    }

    #[test]
    fn duplicate_registry_views_collapse_only_when_identity_matches() {
        let first = registration(
            "Vendor.App",
            "portable",
            r"C:\Program Files\WinGet\App",
            r"C:\Program Files\WinGet\App\app.exe",
        );
        let same = registration(
            "vendor.app",
            "PORTABLE",
            r"c:/program files/winget/app/",
            r"c:/program files/winget/app/app.exe",
        );
        assert!(select_target("Vendor.App", &[first.clone(), same]).is_ok());

        let other = registration(
            "Vendor.App",
            "portable",
            r"D:\Portable\App",
            r"D:\Portable\App\app.exe",
        );
        assert_eq!(
            select_target("Vendor.App", &[first, other]),
            Err(ResolveError::AmbiguousTarget)
        );
    }

    #[test]
    fn an_archive_without_a_registered_target_keeps_its_trusted_root() {
        let entries = [registration(
            "Vendor.App",
            "portable",
            r"C:\Program Files\WinGet\App",
            "",
        )];
        let target = select_target("Vendor.App", &entries).unwrap();
        assert_eq!(target.executable, None);
    }

    #[test]
    fn archive_discovery_requires_exactly_one_executable() {
        let root = std::env::temp_dir().join(format!(
            "metnos-activation-{}-{:?}",
            std::process::id(),
            std::thread::current().id(),
        ));
        let nested = root.join("bin");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::write(root.join("library.dll"), b"dll").unwrap();

        assert_eq!(
            discover_unique_executable(&root),
            Err(ResolveError::MissingTarget),
        );

        let expected = nested.join("sensor.EXE");
        std::fs::write(&expected, b"exe").unwrap();

        assert_eq!(
            discover_unique_executable(&root).unwrap(),
            std::fs::canonicalize(&expected).unwrap(),
        );

        std::fs::write(root.join("second.exe"), b"exe").unwrap();
        assert_eq!(
            discover_unique_executable(&root),
            Err(ResolveError::AmbiguousTarget),
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn containment_uses_windows_boundaries_not_string_prefixes() {
        assert!(target_is_contained(
            Path::new(r"C:\Apps\Tool"),
            Path::new(r"c:/apps/tool/bin/tool.exe"),
        ));
        assert!(!target_is_contained(
            Path::new(r"C:\Apps\Tool"),
            Path::new(r"C:\Apps\Toolbox\tool.exe"),
        ));
        assert!(!target_is_contained(
            Path::new(r"C:\Apps\Tool"),
            Path::new(r"C:\Windows\tool.exe"),
        ));
    }

    #[test]
    fn canonical_local_path_is_spelled_for_dotnet_without_widening_scope() {
        assert_eq!(
            windows_local_interop_path(Path::new(r"\\?\C:\Apps\Sensor\sensor.dll")),
            Ok(PathBuf::from(r"C:\Apps\Sensor\sensor.dll")),
        );
        assert_eq!(
            windows_local_interop_path(Path::new(r"d:/Apps/Sensor/sensor.dll")),
            Ok(PathBuf::from(r"d:\Apps\Sensor\sensor.dll")),
        );
        for refused in [
            r"\\?\UNC\server\share\sensor.dll",
            r"\\server\share\sensor.dll",
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\sensor.dll",
            r"sensor.dll",
        ] {
            assert_eq!(
                windows_local_interop_path(Path::new(refused)),
                Err(ResolveError::InvalidTarget),
                "accepted {refused}",
            );
        }
    }
}
