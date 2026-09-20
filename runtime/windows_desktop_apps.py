# SPDX-License-Identifier: MIT
"""User-session desktop apps registered in the Windows Start menu.

Only a digest of a registered shortcut's target/arguments crosses the executor
boundary. No caller-supplied path, command, script or launch argument is used.
Explorer owns activation, so the UI does not inherit the executor's job or
the elevated helper's session 0. The process receipt is checked independently.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

IDENTITY_RE = re.compile(r"^desktop:[0-9a-f]{64}$")

# All PowerShell source is fixed. The request is JSON data on stdin, never
# interpolated into source text. Paths/arguments below come from registered
# shortcuts only; they are neither returned to the planner nor accepted from it.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$effectsAttempted = $false
function Reply($value) { $value | ConvertTo-Json -Depth 8 -Compress; exit }
function Fail($code) { Reply @{ok=$false; error_code=$code; effects_attempted=$effectsAttempted} }
try {
  $wsh = New-Object -ComObject WScript.Shell
  $records = @{}
  $count = 0
  foreach ($root in (@([Environment]::GetFolderPath('StartMenu'), [Environment]::GetFolderPath('CommonStartMenu')) | Select-Object -Unique)) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    foreach ($file in (Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -File)) {
      $count++
      if ($count -gt 2048) { Fail 'package_inventory_too_large' }
      $link = $wsh.CreateShortcut($file.FullName)
      $target = [Environment]::ExpandEnvironmentVariables([string]$link.TargetPath)
      if (-not [IO.Path]::IsPathRooted($target) -or $target.StartsWith('\\') -or [IO.Path]::GetExtension($target) -ine '.exe') { continue }
      if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { continue }
      $target = [IO.Path]::GetFullPath($target)
      $material = $target.ToUpperInvariant() + [char]0 + [string]$link.Arguments + [char]0 + ([string]$link.WorkingDirectory).ToUpperInvariant()
      $hash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($material))
      $identity = 'desktop:' + ([BitConverter]::ToString($hash)).Replace('-','').ToLowerInvariant()
      $row = @{id=$identity; name=$file.BaseName; target=$target; shortcut=$file.FullName; arguments=[string]$link.Arguments; directory=[string]$link.WorkingDirectory}
      if (-not $records.ContainsKey($identity)) { $records[$identity] = @() }
      $records[$identity] += $row
    }
  }
  if ($request.operation -eq 'find') {
    $name = [string]$request.name
    $all = @($records.Values | ForEach-Object { $_ })
    $matches = @($all | Where-Object { $_.name.Equals($name, [StringComparison]::OrdinalIgnoreCase) -or $_.id -eq $name })
    if ($matches.Count -eq 0) {
      $matches = @($all | Where-Object { $_.name.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    }
    $seen = @{}
    $entries = @($matches | Sort-Object name,id | ForEach-Object {
      if (-not $seen.ContainsKey($_.id)) {
        $seen[$_.id] = $true
        @{name=$_.name; resolved_id=$_.id; version=''; source='windows_start_menu'}
      }
    })
    if ($entries.Count -gt 64) { Fail 'package_inventory_too_large' }
    Reply @{ok=$true; entries=$entries}
  }
  $rows = $records[[string]$request.package_id]
  if (-not $rows) { Fail 'package_not_registered' }
  $selected = @($rows | Sort-Object shortcut)[0]
  $target = $selected.target
  $session = (Get-Process -Id $PID).SessionId
  function MatchingProcesses {
    @(foreach ($process in (Get-Process)) {
      if ($process.SessionId -ne $session -or $process.ProcessName -ine [IO.Path]::GetFileNameWithoutExtension($target)) { continue }
      try {
        if ($process.HasExited) { continue }
        $path = $process.Path
        if (-not $path) { Fail 'package_process_probe_failed' }
        if ($path -ieq $target) { $process }
      } catch { Fail 'package_process_probe_failed' }
    })
  }
  function Receipt($process) {
    @{pid=[long]$process.Id; creation_time=[long]$process.StartTime.ToUniversalTime().ToFileTimeUtc()}
  }
  function DesktopShell {
    $shell = New-Object -ComObject Shell.Application
    $window = 0
    $desktop = $shell.Windows().FindWindowSW(0, $null, 8, [ref]$window, 1)
    if (-not $desktop) { Fail 'package_start_failed' }
    $application = $desktop.Document.Application
    if (-not $application) { Fail 'package_start_failed' }
    $application
  }
  if ($request.operation -eq 'query') {
    $null = DesktopShell
    $processes = @(MatchingProcesses | ForEach-Object { Receipt $_ })
    if ($processes.Count -gt 64) { Fail 'package_process_probe_failed' }
    Reply @{ok=$true; name=$selected.name; lifetimes=@('session'); processes=$processes}
  }
  if ($request.operation -eq 'close') {
    # Snapshot-bound identities, never executable names or guessed PIDs.
    # Hold each kernel handle before checking and before sending any effect.
    $targets = @()
    foreach ($expected in $request.processes) {
      $process = Get-Process -Id ([int]$expected.pid) -ErrorAction SilentlyContinue
      if (-not $process) { continue }
      $null = $process.Handle
      if ($process.HasExited) { continue }
      if ($process.SessionId -ne $session -or $process.Path -ine $target -or (Receipt $process).creation_time -ne [long]$expected.creation_time) {
        Fail 'package_process_identity_mismatch'
      }
      $targets += $process
    }
    foreach ($process in $targets) {
      if ($process.HasExited) { continue }
      $effectsAttempted = $true
      if ($request.force -eq $true) { $process.Kill() }
      else { $null = $process.CloseMainWindow() }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
      $remaining = @($targets | Where-Object { -not $_.HasExited })
      if ($remaining.Count -eq 0) { break }
      Start-Sleep -Milliseconds 100
    } while ($timer.ElapsedMilliseconds -lt 5000)
    # A prompt, a tray icon, or an independent new instance is not a stopped app.
    $stillRunning = @(MatchingProcesses).Count
    Reply @{ok=($stillRunning -eq 0); effects_attempted=$effectsAttempted;
      error_code=$(if ($stillRunning -eq 0) { '' } else { 'package_close_unverified' });
      payload=@{closed=($stillRunning -eq 0); remaining=$stillRunning}}
  }
  if ($request.operation -eq 'stop') {
    $process = Get-Process -Id ([int]$request.pid) -ErrorAction SilentlyContinue
    if (-not $process) { Fail 'package_stop_target_missing' }
    # Hold the kernel handle across identity check and termination; PID reuse
    # cannot turn the checked process into a different kill target.
    $null = $process.Handle
    if ($process.SessionId -ne $session -or $process.Path -ine $target -or (Receipt $process).creation_time -ne [long]$request.creation_time) {
      Fail 'package_process_identity_mismatch'
    }
    $effectsAttempted = $true
    $process.Kill()
    if (-not $process.WaitForExit(5000)) { Fail 'package_stop_unverified' }
    foreach ($remaining in (MatchingProcesses)) {
      $identity = Receipt $remaining
      $existed = @($request.preexisting_processes | Where-Object { $_.pid -eq $identity.pid -and $_.creation_time -eq $identity.creation_time }).Count -ne 0
      if (-not $existed) { Fail 'package_stop_unverified' }
    }
    Reply @{ok=$true; payload=@{restored=$true; stopped=$true}}
  }
  if ($request.operation -ne 'start' -or $request.lifetime -ne 'session') { Fail 'package_persistence_unsupported' }
  $before = @(MatchingProcesses | ForEach-Object { Receipt $_ })
  if ($before.Count -gt 64) { Fail 'package_process_probe_failed' }
  $boundary = [DateTime]::UtcNow.ToFileTimeUtc()
  $application = DesktopShell
  # Ask the existing interactive Explorer process, never this executor's job.
  $effectsAttempted = $true
  # Use the metadata just hashed, not a shortcut that could change afterwards.
  $application.ShellExecute($target, $selected.arguments, $selected.directory, 'open', 1)
  for ($attempt=0; $attempt -lt 60; $attempt++) {
    $visible = @(MatchingProcesses | Where-Object { $_.MainWindowHandle -ne 0 })
    if ($visible.Count -eq 1) {
      $receipt = Receipt $visible[0]
      $existed = @($before | Where-Object { $_.pid -eq $receipt.pid -and $_.creation_time -eq $receipt.creation_time }).Count -ne 0
      if (-not $existed -and $receipt.creation_time -lt $boundary) { Fail 'package_process_concurrent_change' }
      Reply @{ok=$true; payload=@{process=$receipt; created_process=(-not $existed); visible_window=$true; persistent_registration_changed=$false; activation_boundary=$boundary; preexisting_processes=$before}}
    }
    Start-Sleep -Milliseconds 100
  }
  Fail 'package_start_unverified'
} catch { Fail 'package_operation_failed' }
"""


def _call(request: dict) -> dict:
    unknown_result = {"ok": False, "error_code": "package_operation_failed",
                      "effects_attempted": request.get("operation") in {"start", "stop", "close"}}
    if not sys.platform.startswith("win"):
        return {"ok": False, "error_code": "platform_unsupported"}
    root = os.environ.get("SystemRoot")
    if not root:
        return {"ok": False, "error_code": "package_operation_failed"}
    executable = Path(root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        completed = subprocess.run(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
            timeout=15, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or len(completed.stdout) > 65536:
            return unknown_result
        answer = json.loads(completed.stdout.strip().lstrip("\ufeff"))
        if isinstance(answer, dict) and type(answer.get("ok")) is bool:
            return answer
    except OSError:
        return {"ok": False, "error_code": "package_operation_failed"}
    except (ValueError, subprocess.SubprocessError):
        return unknown_result
    return unknown_result


def find(name: str) -> dict:
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return {"ok": False, "error_code": "package_target_invalid"}
    result = _call({"operation": "find", "name": name})
    if result.get("ok") is True:
        entries = result.get("entries")
        if (not isinstance(entries, list) or len(entries) > 64 or any(
                not isinstance(row, dict)
                or not isinstance(row.get("name"), str) or not row["name"]
                or not isinstance(row.get("resolved_id"), str)
                or not IDENTITY_RE.fullmatch(row["resolved_id"])
                for row in entries)):
            return {"ok": False, "error_code": "package_inventory_invalid"}
    return result


def call(package_id: str, operation: str, *arguments: str) -> dict:
    if not isinstance(package_id, str) or not IDENTITY_RE.fullmatch(package_id):
        return {"ok": False, "error_code": "package_target_invalid"}
    request = {"operation": operation, "package_id": package_id}
    if operation == "start" and tuple(arguments) == ("--lifetime", "session"):
        request["lifetime"] = "session"
    elif (operation == "stop" and len(arguments) >= 6 and len(arguments) % 2 == 0
          and arguments[:6:2] == ("--pid", "--creation-time", "--activation-boundary")):
        try:
            pid, created, boundary = int(arguments[1]), int(arguments[3]), int(arguments[5])
            previous = []
            for index in range(6, len(arguments), 2):
                if arguments[index] != "--preexisting-process":
                    raise ValueError()
                old_pid, old_time = map(int, arguments[index + 1].split(":"))
                if not 0 < old_pid <= 0xFFFFFFFF or not 0 < old_time <= 0x7FFFFFFFFFFFFFFF:
                    raise ValueError()
                previous.append({"pid": old_pid, "creation_time": old_time})
        except ValueError:
            return {"ok": False, "error_code": "package_stop_receipt_invalid"}
        if (not 0 < pid <= 0xFFFFFFFF or not 0 < boundary <= created <= 0x7FFFFFFFFFFFFFFF
                or len(previous) > 64 or {"pid": pid, "creation_time": created} in previous):
            return {"ok": False, "error_code": "package_stop_receipt_invalid"}
        request.update(pid=pid, creation_time=created, activation_boundary=boundary,
                       preexisting_processes=previous)
    elif operation != "query" or arguments:
        return {"ok": False, "error_code": "package_target_invalid"}
    return _call(request)


def valid_processes(processes) -> bool:
    """Bounded exact process identities returned by the installed-app query."""
    if not isinstance(processes, list) or len(processes) > 64:
        return False
    seen = set()
    for process in processes:
        if not isinstance(process, dict) or set(process) != {"pid", "creation_time"}:
            return False
        pid, created = process["pid"], process["creation_time"]
        if (type(pid) is not int or not 0 < pid <= 0xFFFFFFFF
                or type(created) is not int or not 0 < created <= 0x7FFFFFFFFFFFFFFF
                or (pid, created) in seen):
            return False
        seen.add((pid, created))
    return True


def close(package_id: str, processes: list[dict], *, force: bool = False) -> dict:
    if (not isinstance(package_id, str) or not IDENTITY_RE.fullmatch(package_id)
            or not valid_processes(processes) or type(force) is not bool):
        return {"ok": False, "error_code": "package_stop_receipt_invalid"}
    return _call({"operation": "close", "package_id": package_id,
                  "processes": processes, "force": force})
