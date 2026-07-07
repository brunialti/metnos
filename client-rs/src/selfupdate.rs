//! Self-update del client (W4, 5/7/2026 — «l'upgrade del client remoto deve
//! essere automatico»).
//!
//! Flusso: il poll porta `server_client_version`; su mismatch il runner chiama
//! `maybe_update`, che:
//!   1. scarica il DESCRITTORE firmato da `/agent/client/update/{target}` e ne
//!      verifica la firma con la pubkey server PINNATA (stessa ancora di
//!      fiducia di shim e invocazioni — autenticità, non solo integrità);
//!   2. IDEMPOTENZA: se lo sha256 del PROPRIO eseguibile coincide con quello
//!      del descrittore, il binario è già quello pubblicato → nessun loop
//!      (il caso «stessa build, version string diversa» non ricicla);
//!   3. scarica il binario nel percorso `<exe>.new`, verifica lo sha256;
//!   4. swap atomico: `<exe>` → `<exe>.old`, `<exe>.new` → `<exe>` (su
//!      Windows un eseguibile IN ESECUZIONE si può rinominare, non
//!      sovrascrivere: è la tecnica standard);
//!   5. ritorna `true`: il chiamante rilascia il lock, spawna il nuovo
//!      binario con gli stessi argomenti e esce.
//!
//! Rollback manuale: `<exe>.old` resta sul disco fino allo swap successivo.

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::path::Path;

use crate::identity;

#[derive(Debug, Deserialize)]
struct UpdateDescriptor {
    version: String,
    target: String,
    sha256: String,
    url_path: String,
    sig: String,
}

fn build_target() -> &'static str {
    // Coerente coi target del mirror (build-client.sh).
    if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-gnu"
    } else {
        "x86_64-unknown-linux-musl"
    }
}

fn hex_lower(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

fn sha256_file(p: &Path) -> Result<String> {
    let bytes = std::fs::read(p).with_context(|| format!("read {}", p.display()))?;
    Ok(hex_lower(&Sha256::digest(&bytes)))
}

/// Controlla il descrittore firmato e, se il binario pubblicato è diverso dal
/// proprio, lo scarica e fa lo swap. Ritorna true se il chiamante deve
/// respawnare (il file exe è GIÀ il nuovo binario).
pub async fn maybe_update(server: &str, server_pubkey: &str) -> Result<bool> {
    let target = build_target();
    let url = format!("{}/agent/client/update/{}", server.trim_end_matches('/'), target);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()?;
    let resp = client.get(&url).send().await.with_context(|| format!("GET {}", url))?;
    if !resp.status().is_success() {
        bail!("update descriptor HTTP {}", resp.status());
    }
    let desc: UpdateDescriptor = resp.json().await.context("parse update descriptor")?;

    // Firma: il server firma canonical({"sha256","target","version"}).
    let payload = serde_json::json!({
        "version": desc.version, "target": desc.target, "sha256": desc.sha256,
    });
    let canon = crate::wire::canonical_bytes(&payload)?;
    identity::verify_b64(server_pubkey, &desc.sig, &canon)
        .context("firma update descriptor non verificata")?;
    if desc.target != target {
        bail!("descrittore per target diverso: {}", desc.target);
    }

    let exe = std::env::current_exe().context("current_exe")?;
    let own_sha = sha256_file(&exe)?;
    if own_sha.eq_ignore_ascii_case(&desc.sha256) {
        tracing::debug!(version = %desc.version, "binario già aggiornato (sha combacia)");
        return Ok(false);
    }
    tracing::info!(
        running = env!("CARGO_PKG_VERSION"), available = %desc.version,
        "self-update: scarico il nuovo client"
    );

    // Download → <exe>.new + verifica sha256.
    let new_path = exe.with_extension("new");
    let bytes = client
        .get(format!("{}{}", server.trim_end_matches('/'), desc.url_path))
        .send().await.context("download binario")?
        .error_for_status().context("download binario (status)")?
        .bytes().await.context("download binario (body)")?;
    let got_sha = hex_lower(&Sha256::digest(&bytes));
    if !got_sha.eq_ignore_ascii_case(&desc.sha256) {
        bail!("sha256 del binario scaricato non combacia (atteso {}, avuto {})",
              desc.sha256, got_sha);
    }
    std::fs::write(&new_path, &bytes)
        .with_context(|| format!("write {}", new_path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&new_path, std::fs::Permissions::from_mode(0o755))?;
    }

    swap_binary(&exe, &new_path)?;
    tracing::info!(version = %desc.version, "self-update: swap completato, respawn");
    Ok(true)
}

/// `<exe>` → `<exe>.old` (rimuovendo un .old precedente), `<exe>.new` → `<exe>`.
/// Su Windows l'immagine in esecuzione si può RINOMINARE ma non sovrascrivere.
fn swap_binary(exe: &Path, new_path: &Path) -> Result<()> {
    let old_path = exe.with_extension("old");
    let _ = std::fs::remove_file(&old_path); // best-effort (Windows: .old di un run precedente non è più in esecuzione)
    std::fs::rename(exe, &old_path)
        .with_context(|| format!("rename {} -> {}", exe.display(), old_path.display()))?;
    if let Err(e) = std::fs::rename(new_path, exe) {
        // Ripristina il vecchio: mai lasciare il client senza binario.
        let _ = std::fs::rename(&old_path, exe);
        return Err(e).with_context(|| format!("rename {} -> {}", new_path.display(), exe.display()));
    }
    Ok(())
}

/// Respawn: spawna lo STESSO percorso exe (ora il nuovo binario) con gli
/// argomenti originali e esce. Il figlio ritenta il lock single-instance
/// per qualche secondo (il padre lo rilascia uscendo).
pub fn respawn_and_exit() -> ! {
    let exe = std::env::current_exe().expect("current_exe");
    let args: Vec<String> = std::env::args().skip(1).collect();
    // BUG 7/7/2026 (0.2.17→0.2.18): il daemon chiama FreeConsole (B6) → i suoi
    // std-handle sono INVALIDI; senza redirezione `spawn()` li fa ereditare al
    // figlio e CreateProcessW fallisce con «Handle non valido (os error 6)»,
    // lasciando il client giù. Stdio::null() dà al figlio handle freschi sul
    // device NUL: nessuna eredità di handle invalidi. Cross-platform (innocuo su
    // Linux, dove non c'è FreeConsole). Il figlio è comunque un daemon (log su
    // file/tracing), non serve stdout.
    match std::process::Command::new(&exe)
        .args(&args)
        .env("METNOS_RESPAWNED", "1")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(_) => tracing::info!("self-update: nuovo processo avviato, esco"),
        Err(e) => tracing::error!("self-update: spawn fallito: {} (la scheduled task/il supervisore rilancerà)", e),
    }
    std::process::exit(0);
}

pub fn lock_retry_window() -> Option<std::time::Duration> {
    // Dopo un respawn il padre potrebbe non aver ancora rilasciato il lock:
    // il figlio ritenta per una finestra breve invece di morire subito.
    if std::env::var("METNOS_RESPAWNED").ok().as_deref() == Some("1") {
        Some(std::time::Duration::from_secs(20))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("metnos-selfupd-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn swap_keeps_a_binary_in_place() {
        let dir = tdir("a");
        let exe = dir.join("metnos-client");
        let new = exe.with_extension("new");
        std::fs::write(&exe, b"OLD").unwrap();
        std::fs::write(&new, b"NEW").unwrap();
        swap_binary(&exe, &new).unwrap();
        assert_eq!(std::fs::read(&exe).unwrap(), b"NEW");
        assert_eq!(std::fs::read(exe.with_extension("old")).unwrap(), b"OLD");
        assert!(!new.exists());
    }

    #[test]
    fn swap_restores_old_on_failure() {
        let dir = tdir("b");
        let exe = dir.join("metnos-client");
        std::fs::write(&exe, b"OLD").unwrap();
        // .new inesistente → il secondo rename fallisce → OLD ripristinato.
        let missing = exe.with_extension("new");
        let err = swap_binary(&exe, &missing);
        assert!(err.is_err());
        assert_eq!(std::fs::read(&exe).unwrap(), b"OLD");
    }
}
