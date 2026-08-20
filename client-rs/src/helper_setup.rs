//! Bringing the elevated helper onto the machine, and installing it.
//!
//! The helper is the most privileged thing Metnos puts on someone's computer,
//! so the way it ARRIVES matters as much as what it does. It travels the same
//! signed channel as the client: a descriptor signed by the server, verified
//! against the pinned public key, then a binary checked against the hash that
//! descriptor names. A file that reaches the machine any other way — copied by
//! hand, downloaded from a page — is a program installed by an administrator,
//! which is precisely what Metnos exists to avoid asking for.
//!
//! ## What this does NOT do
//!
//! It brings the helper the FIRST time, and nothing else. It does not update
//! it: the helper asks the server on its own, exactly as this client does for
//! itself — one rule for every piece, and no piece depending on another to
//! stay current. It also means the binary that runs as the system is never
//! replaced by a program that runs without privileges.
//!
//! It does not decide that installing is a good idea either. It is called
//! after the person has said yes to a card, and its own consent step is the
//! Windows prompt — which Windows shows, not us.

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

use crate::identity;

/// The component name as it appears in the signed descriptor. Whoever reads a
/// descriptor must check this: two components published for the same version
/// and system would otherwise share a valid signature, and one could be served
/// in place of the other.
pub const COMPONENT: &str = "helper";

#[derive(Debug, Deserialize)]
struct ComponentDescriptor {
    component: String,
    version: String,
    target: String,
    sha256: String,
    url_path: String,
    sig: String,
}

/// Where the helper binary is expected to run from, per system.
fn target_triple() -> &'static str {
    if cfg!(windows) {
        "x86_64-pc-windows-gnu"
    } else {
        "x86_64-unknown-linux-musl"
    }
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{:02x}", b));
    }
    out
}

/// The helper binary, fetched and verified, waiting to be installed.
pub struct Fetched {
    pub path: PathBuf,
    pub version: String,
    pub sha256: String,
}

/// Fetch the signed helper artifact into `dest_dir`.
///
/// `dest_dir` must be a directory only this user can write. The hash is
/// checked against the signed descriptor before the file is written, and the
/// caller checks it again just before asking for elevation: between those two
/// moments the file sits somewhere another user must not be able to reach.
pub async fn fetch(server: &str, server_pubkey: &str, dest_dir: &Path) -> Result<Fetched> {
    let target = target_triple();
    let url = format!(
        "{}/agent/component/{}/update/{}",
        server.trim_end_matches('/'),
        COMPONENT,
        target
    );
    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()?;
    let resp = http
        .get(&url)
        .send()
        .await
        .with_context(|| format!("GET {url}"))?;
    if !resp.status().is_success() {
        bail!("helper descriptor HTTP {}", resp.status());
    }
    let desc: ComponentDescriptor = resp.json().await.context("parse helper descriptor")?;

    // The signature covers the component name too, so a client descriptor
    // cannot be presented as a helper one.
    let payload = serde_json::json!({
        "component": desc.component,
        "version": desc.version,
        "target": desc.target,
        "sha256": desc.sha256,
    });
    let canon = crate::wire::canonical_bytes(&payload)?;
    identity::verify_b64(server_pubkey, &desc.sig, &canon)
        .context("helper descriptor signature not verified")?;

    // Verified content still has to be the content we asked for.
    if desc.component != COMPONENT {
        bail!(
            "descriptor is for component {}, not {}",
            desc.component,
            COMPONENT
        );
    }
    if desc.target != target {
        bail!("descriptor is for target {}, not {}", desc.target, target);
    }

    let bytes = http
        .get(format!("{}{}", server.trim_end_matches('/'), desc.url_path))
        .send()
        .await
        .context("download helper")?
        .error_for_status()
        .context("download helper (status)")?
        .bytes()
        .await
        .context("download helper (body)")?;
    let got = hex_lower(&Sha256::digest(&bytes));
    if !got.eq_ignore_ascii_case(&desc.sha256) {
        bail!(
            "helper hash mismatch (expected {}, got {})",
            desc.sha256,
            got
        );
    }

    std::fs::create_dir_all(dest_dir).with_context(|| format!("create {}", dest_dir.display()))?;
    let path = dest_dir.join(if cfg!(windows) {
        "metnos-helper.exe"
    } else {
        "metnos-helper"
    });
    std::fs::write(&path, &bytes).with_context(|| format!("write {}", path.display()))?;

    Ok(Fetched {
        path,
        version: desc.version,
        sha256: desc.sha256,
    })
}

/// Verify the file on disk still hashes to what the server signed.
///
/// Called immediately before elevation. It does not defend against something
/// already running as this user — that could raise its own prompt anyway — but
/// it does mean the file is not replaced between download and launch by
/// anything with weaker access.
pub fn still_intact(fetched: &Fetched) -> Result<bool> {
    let bytes =
        std::fs::read(&fetched.path).with_context(|| format!("read {}", fetched.path.display()))?;
    Ok(hex_lower(&Sha256::digest(&bytes)).eq_ignore_ascii_case(&fetched.sha256))
}

/// What came of asking for elevation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Installed. The helper is there from now on.
    Installed,
    /// The person said no to the Windows prompt. Not a failure: an answer.
    Refused,
    /// It ran, and it failed. The code is the helper's own; the string is
    /// the reason it wrote down, when it managed to write one.
    Failed(u32, Option<String>),
}

/// Install the fetched helper, raising exactly one Windows consent prompt.
///
/// `ShellExecuteExW` with the verb `runas` is the only legitimate way for an
/// unprivileged process to ask for elevation: Windows itself draws the prompt
/// and Windows itself decides. We never see a password, and a refusal comes
/// back as an ordinary answer rather than an error — the person is allowed to
/// say no, and saying no must not look like something broke.
///
/// This is the single prompt of the whole design. Once the helper is
/// installed, later package installations ask for nothing (ADR 0210 D).
#[cfg(windows)]
pub fn install_elevated(
    fetched: &Fetched,
    owner_sid: &str,
    public_key_hex: &str,
    server_key_b64: &str,
    server_url: &str,
) -> Result<Outcome> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::{CloseHandle, ERROR_CANCELLED, HANDLE};
    use windows_sys::Win32::System::Com::{
        CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, WaitForSingleObject, INFINITE,
    };
    use windows_sys::Win32::UI::Shell::{
        ShellExecuteExW, SEE_MASK_NOASYNC, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_HIDE;

    // The values reach the helper as command-line arguments, and a shell verb
    // does not re-parse them for us. Both are validated shapes — a SID and a
    // base64url key — so anything outside those alphabets is refused here
    // rather than quoted and hoped for.
    if !owner_sid
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        bail!("owner SID has characters that do not belong in one");
    }
    // Exactly the shape the helper accepts: 64 hex characters. Checking it
    // here means a wrong format is refused BEFORE the person is shown a
    // Windows prompt, instead of after answering it.
    if public_key_hex.len() != 64 || !public_key_hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        bail!("public key is not 64 hex characters");
    }
    // La chiave del server e' cio' con cui l'aiutante decidera' se un
    // aggiornamento e' autentico: senza, non si aggiornerebbe mai e
    // resterebbe indietro in silenzio.
    if server_key_b64.is_empty()
        || !server_key_b64
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        bail!("server key is not base64url");
    }
    // The address the helper will ask for updates. Plain HTTP is fine and is
    // in fact the normal case — the Metnos server sits on the home network.
    // What protects an update is the server's SIGNATURE, not the channel;
    // demanding an encrypted one here would remove the ability to install
    // without adding a defence. No spaces or quotes: it goes on a command
    // line.
    if !(server_url.starts_with("http://") || server_url.starts_with("https://"))
        || server_url.bytes().any(|b| b <= b' ' || b == b'"')
    {
        bail!("server url is not a plain http(s) address");
    }

    if !still_intact(fetched)? {
        bail!("the fetched helper changed on disk before it could be installed");
    }

    fn wide(s: &std::ffi::OsStr) -> Vec<u16> {
        s.encode_wide().chain(std::iter::once(0)).collect()
    }

    // Dove l'aiutante scrivera' il motivo, se fallisce. Senza, l'unica cosa
    // che torna a chi ha premuto il bottone e' un numero d'uscita — e un
    // numero non dice quale passo e' andato storto. L'elevazione passa da
    // Windows, che non gira a nessuno cio' che il programma stampa: un file
    // e' l'unico canale di ritorno che resta.
    //
    // Sta accanto al binario appena scaricato, in una cartella che solo
    // questo utente scrive.
    let motivo_path = fetched
        .path
        .with_file_name(format!("helper-install-{}.err", std::process::id()));
    let _ = std::fs::remove_file(&motivo_path);

    let verb = wide(std::ffi::OsStr::new("runas"));
    let file = wide(fetched.path.as_os_str());
    // Il percorso va fra virgolette: una cartella con spazi diventerebbe due
    // argomenti, e l'aiutante scriverebbe il motivo altrove — o da nessuna
    // parte.
    let params = wide(std::ffi::OsStr::new(&format!(
        "install --owner-sid {owner_sid} --public-key {public_key_hex} \
--server-key {server_key_b64} --server-url {server_url} \
--error-file \"{}\"",
        motivo_path.display()
    )));

    let mut info: SHELLEXECUTEINFOW = unsafe { std::mem::zeroed() };
    info.cbSize = std::mem::size_of::<SHELLEXECUTEINFOW>() as u32;
    // Keep the process handle so the exit code can be read: without it we
    // would report «installed» the moment the prompt was answered, which is
    // before the install has actually run.
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    info.lpVerb = verb.as_ptr();
    info.lpFile = file.as_ptr();
    info.lpParameters = params.as_ptr();
    info.nShow = SW_HIDE as i32;

    // ShellExecuteEx passa dalla shell, e la shell e' COM. Senza
    // inizializzare COM in QUESTO thread la chiamata fallisce subito, con un
    // codice che non dice niente di utile — ed e' un fallimento che si vede
    // solo su una macchina vera, mai in prova.
    //
    // Se COM risulta gia' inizializzato non e' un errore: si prosegue e non si
    // chiude niente, perche' a chiuderlo sarebbe chi lo ha aperto.
    let com = unsafe {
        CoInitializeEx(
            std::ptr::null(),
            (COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) as u32,
        )
    };
    let nostro = com >= 0;

    let ok = unsafe { ShellExecuteExW(&mut info) };
    let chiudi_com = || {
        if nostro {
            unsafe { CoUninitialize() };
        }
    };
    if ok == 0 {
        let err = std::io::Error::last_os_error();
        chiudi_com();
        if err.raw_os_error() == Some(ERROR_CANCELLED as i32) {
            return Ok(Outcome::Refused);
        }
        return Err(err).context("could not ask Windows for elevation");
    }

    let process: HANDLE = info.hProcess;
    if process.is_null() {
        chiudi_com();
        bail!("elevation returned no process to wait for");
    }
    unsafe { WaitForSingleObject(process, INFINITE) };
    let mut code: u32 = 1;
    let read = unsafe { GetExitCodeProcess(process, &mut code) };
    unsafe { CloseHandle(process) };
    chiudi_com();
    if read == 0 {
        bail!("the helper ran but its outcome could not be read");
    }
    if code == 0 {
        let _ = std::fs::remove_file(&motivo_path);
        return Ok(Outcome::Installed);
    }
    // Il motivo scritto dall'aiutante, se c'e'. Se manca resta il numero, che
    // e' meglio di niente ma non di molto.
    let motivo = std::fs::read_to_string(&motivo_path)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());
    let _ = std::fs::remove_file(&motivo_path);
    Ok(Outcome::Failed(code, motivo))
}

/// Outside Windows there is no elevated helper, and that is not a fault.
#[cfg(not(windows))]
pub fn install_elevated(
    _f: &Fetched,
    _sid: &str,
    _key: &str,
    _srv: &str,
    _url: &str,
) -> Result<Outcome> {
    bail!("the elevated helper exists only on Windows")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_component_name_is_the_one_the_server_publishes() {
        // The publisher writes this key into the manifest; a mismatch would
        // show up as «no helper for this system» on a mirror that has one.
        assert_eq!(COMPONENT, "helper");
    }

    #[test]
    fn a_changed_file_is_not_installed() {
        let dir = std::env::temp_dir().join(format!("metnos-hs-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("metnos-helper.exe");
        std::fs::write(&path, b"the verified bytes").unwrap();
        let atteso = hex_lower(&Sha256::digest(b"the verified bytes"));
        let f = Fetched {
            path: path.clone(),
            version: "0.0.1".into(),
            sha256: atteso,
        };
        assert!(still_intact(&f).unwrap());

        std::fs::write(&path, b"something else entirely").unwrap();
        assert!(
            !still_intact(&f).unwrap(),
            "a swapped file passed as intact"
        );
        std::fs::remove_dir_all(&dir).ok();
    }
}
