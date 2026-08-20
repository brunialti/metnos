//! Windows host for signed, read-only managed-provider profiles.
//!
//! Package-specific assembly and type names are signed profile data. The host
//! owns the fixed interface operations, bounds, validation, and output shape;
//! no package ID selects a code branch.

#![cfg(windows)]

use std::io::Read;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

use crate::protocol::{HardwareDomain, ProviderInterface, SensorKind};
use crate::service::Outcome;

const TIMEOUT: Duration = Duration::from_secs(8);
const MAX_OUTPUT: u64 = 64 * 1024;
const MAX_DIAGNOSTIC: u64 = 4 * 1024;

const HARDWARE_SENSOR_SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$computer = $null

function Write-Stage([string]$name) {
  [Console]::Error.WriteLine('METNOS_PROVIDER_STAGE:' + $name)
}

function Get-PropertyName([string]$domain) {
  switch ($domain) {
    'battery' { return 'IsBatteryEnabled' }
    'controller' { return 'IsControllerEnabled' }
    'cpu' { return 'IsCpuEnabled' }
    'gpu' { return 'IsGpuEnabled' }
    'memory' { return 'IsMemoryEnabled' }
    'motherboard' { return 'IsMotherboardEnabled' }
    'network' { return 'IsNetworkEnabled' }
    'power_monitor' { return 'IsPowerMonitorEnabled' }
    'power_supply' { return 'IsPsuEnabled' }
    'storage' { return 'IsStorageEnabled' }
    default { return $null }
  }
}

function Get-HardwareDomain([string]$hardwareType, [string]$inherited) {
  switch ($hardwareType) {
    'Battery' { return 'battery' }
    'Cooler' { return 'controller' }
    'Cpu' { return 'cpu' }
    'GpuAmd' { return 'gpu' }
    'GpuIntel' { return 'gpu' }
    'GpuNvidia' { return 'gpu' }
    'Memory' { return 'memory' }
    'Motherboard' { return 'motherboard' }
    'SuperIO' { return 'motherboard' }
    'EmbeddedController' { return 'motherboard' }
    'Network' { return 'network' }
    'PowerMonitor' { return 'power_monitor' }
    'Psu' { return 'power_supply' }
    'Storage' { return 'storage' }
    default { return $inherited }
  }
}

function Get-Unit([string]$kind) {
  switch ($kind) {
    'voltage' { return 'V' }
    'current' { return 'A' }
    'power' { return 'W' }
    'clock' { return 'MHz' }
    'temperature' { return ([char]0x00B0).ToString() + 'C' }
    'load' { return '%' }
    'frequency' { return 'Hz' }
    'fan' { return 'RPM' }
    'flow' { return 'L/h' }
    'control' { return '%' }
    'level' { return '%' }
    'factor' { return '' }
    'data' { return 'GB' }
    'small_data' { return 'MB' }
    'throughput' { return 'B/s' }
    'time_span' { return 's' }
    'timing' { return 'ns' }
    'energy' { return 'mWh' }
    'noise' { return 'dBA' }
    'conductivity' { return ([char]0x00B5).ToString() + 'S/cm' }
    'humidity' { return '%' }
    default { return $null }
  }
}

$requestedDomains = @($env:METNOS_PROVIDER_DOMAINS -split ',' | Where-Object { $_ })
$requestedTypes = @($env:METNOS_PROVIDER_SENSOR_TYPES -split ',' | Where-Object { $_ })
try {
  Write-Stage 'assembly'
  try {
    # WinGet installs archive members with their Mark-of-the-Web intact on
    # some systems.  The ordinary .NET Framework loader rejects that local
    # DLL even though the helper has already resolved it as the canonical
    # direct child of the exact registered package.  UnsafeLoadFrom is the
    # framework API specifically intended for this case; it preserves the
    # load-from context (and dependency probing beside the assembly) without
    # mutating the installed package to remove its provenance marker.
    $assembly = [System.Reflection.Assembly]::UnsafeLoadFrom(
      $env:METNOS_PROVIDER_ASSEMBLY
    )
  } catch {
    exit 20
  }
  Write-Stage 'instance'
  try {
    $entryType = $assembly.GetType($env:METNOS_PROVIDER_ENTRY_TYPE, $false, $false)
    if ($null -eq $entryType) { exit 23 }
    $computer = [Activator]::CreateInstance($entryType)
  } catch {
    exit 23
  }
  Write-Stage 'configure'
  try {
    foreach ($domain in $requestedDomains) {
      $propertyName = Get-PropertyName $domain
      if ($null -eq $propertyName) { exit 24 }
      $property = $entryType.GetProperty($propertyName)
      if ($null -eq $property -or -not $property.CanWrite -or
          $property.PropertyType -ne [bool]) { exit 24 }
      $property.SetValue($computer, $true, $null)
    }
  } catch {
    exit 24
  }

  Write-Stage 'open'
  try {
    $computer.Open()
  } catch {
    exit 21
  }

  Write-Stage 'read'
  $rows = New-Object System.Collections.ArrayList
  $script:probeFailed = $false
  function Visit-Hardware(
      [object]$hardware, [System.Collections.ArrayList]$result,
      [string]$inheritedDomain) {
    if ($result.Count -ge 32) { return }
    $domain = Get-HardwareDomain ([string]$hardware.HardwareType) $inheritedDomain
    try {
      $hardware.Update()
      $sensors = @($hardware.Sensors)
      $children = @($hardware.SubHardware)
    } catch {
      $script:probeFailed = $true
      return
    }
    foreach ($sensor in $sensors) {
      if ($result.Count -ge 32) { break }
      $kind = [regex]::Replace(
        [string]$sensor.SensorType, '(?<!^)([A-Z])', '_$1'
      ).ToLowerInvariant()
      if (($requestedDomains -contains $domain) -and
          ($requestedTypes -contains $kind) -and
          $null -ne $sensor.Value) {
        $unit = Get-Unit $kind
        if ($null -eq $unit) { continue }
        [void]$result.Add([pscustomobject]@{
          domain = $domain
          kind = $kind
          name = [string]$sensor.Name
          identifier = [string]$sensor.Identifier
          value = [double]$sensor.Value
          unit = $unit
        })
      }
    }
    foreach ($sub in $children) { Visit-Hardware $sub $result $domain }
  }
  foreach ($hardware in @($computer.Hardware)) {
    Visit-Hardware $hardware $rows ''
  }
  if ($rows.Count -eq 0 -and $script:probeFailed) { exit 22 }
  $json = [pscustomobject]@{ sensors = @($rows) } |
    ConvertTo-Json -Compress -Depth 4
  Write-Stage 'close'
  $computer.Close()
  $computer = $null
  Write-Stage 'emit'
  [Console]::Out.WriteLine($json)
  Write-Stage 'done'
} finally {
  if ($null -ne $computer) {
    Write-Stage 'cleanup'
    try { $computer.Close() } catch { }
  }
}
"#;

fn powershell_path() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe")
}

fn timeout_code(stderr: &[u8]) -> &'static str {
    let diagnostic = String::from_utf8_lossy(stderr);
    let last_stage = diagnostic
        .lines()
        .rev()
        .find_map(|line| line.strip_prefix("METNOS_PROVIDER_STAGE:"));
    match last_stage {
        Some("assembly") => "provider_timeout_assembly",
        Some("instance") => "provider_timeout_instance",
        Some("configure") => "provider_timeout_configure",
        Some("open") => "provider_timeout_open",
        Some("read") => "provider_timeout_read",
        Some("close") | Some("cleanup") => "provider_timeout_close",
        Some("emit") | Some("done") => "provider_timeout_exit",
        _ => "provider_timeout",
    }
}

fn run_bounded(
    script: &str,
    assembly: &Path,
    entry_type: &str,
    domains: &[HardwareDomain],
    sensor_types: &[SensorKind],
) -> Result<String, &'static str> {
    let mut command = Command::new(powershell_path());
    command
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ])
        .env("METNOS_PROVIDER_ASSEMBLY", assembly)
        .env("METNOS_PROVIDER_ENTRY_TYPE", entry_type)
        .env(
            "METNOS_PROVIDER_DOMAINS",
            domains
                .iter()
                .map(|value| value.as_str())
                .collect::<Vec<_>>()
                .join(","),
        )
        .env(
            "METNOS_PROVIDER_SENSOR_TYPES",
            sensor_types
                .iter()
                .map(|value| value.as_str())
                .collect::<Vec<_>>()
                .join(","),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW);
    if let Some(parent) = assembly.parent() {
        command.current_dir(parent);
    }
    let mut child = command.spawn().map_err(|_| "provider_start_failed")?;
    let stdout = child.stdout.take().ok_or("provider_output_invalid")?;
    let stderr = child.stderr.take().ok_or("provider_output_invalid")?;
    let stdout_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout
            .take(MAX_OUTPUT + 1)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let stderr_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr
            .take(MAX_DIAGNOSTIC + 1)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let started = Instant::now();
    let (status, timed_out) = loop {
        match child.try_wait() {
            Ok(Some(status)) => break (Some(status), false),
            Ok(None) if started.elapsed() < TIMEOUT => {
                std::thread::sleep(Duration::from_millis(25));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                break (None, true);
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break (None, false);
            }
        }
    };
    let stdout = stdout_reader
        .join()
        .map_err(|_| "provider_output_invalid")?
        .map_err(|_| "provider_output_invalid")?;
    let stderr = stderr_reader
        .join()
        .map_err(|_| "provider_output_invalid")?
        .map_err(|_| "provider_output_invalid")?;
    if stdout.len() as u64 > MAX_OUTPUT || stderr.len() as u64 > MAX_DIAGNOSTIC {
        return Err("provider_output_too_large");
    }
    if timed_out {
        return Err(timeout_code(&stderr));
    }
    let status = status.ok_or("provider_wait_failed")?;
    if !status.success() {
        return Err(match status.code() {
            Some(20) => "provider_assembly_load_failed",
            Some(21) => "provider_open_failed",
            Some(22) => "provider_read_failed",
            Some(23) => "provider_instance_failed",
            Some(24) => "provider_configuration_failed",
            _ => "provider_failed",
        });
    }
    String::from_utf8(stdout).map_err(|_| "provider_output_invalid")
}

/// Execute one standard interface against an exact signed package profile.
pub fn read(
    package_id: &str,
    interface: ProviderInterface,
    assembly: &str,
    entry_type: &str,
    domains: &[HardwareDomain],
    sensor_types: &[SensorKind],
) -> Outcome {
    let result = match interface {
        ProviderInterface::HardwareSensorsV1 => {
            let asset = crate::win_activation::canonical_direct_child(package_id, assembly);
            asset.and_then(|path| {
                // Keep the canonical extended-length path for the security
                // checks above.  Only the already-validated local drive path
                // is respelled for .NET Framework, which cannot consume the
                // `\\?\C:\...` form returned by Rust canonicalization.
                let loader_path = crate::activation::windows_local_interop_path(&path)
                    .map_err(|error| error.code())?;
                let output = run_bounded(
                    HARDWARE_SENSOR_SCRIPT,
                    &loader_path,
                    entry_type,
                    domains,
                    sensor_types,
                )?;
                let line = output
                    .lines()
                    .rev()
                    .find(|line| !line.trim().is_empty())
                    .ok_or("provider_output_invalid")?;
                crate::provider::hardware_payload(line.trim(), domains, sensor_types)
            })
        }
    };
    match result {
        Ok(payload) => Outcome::success_with_payload(payload),
        Err(code) => Outcome::failure(code, code),
    }
}
