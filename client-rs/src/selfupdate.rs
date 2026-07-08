//! Self-update del client — ROBUSTO, "a prova di tutto" (8/7/2026).
//!
//! Flusso: il poll porta `server_client_version`; su mismatch il runner chiama
//! `maybe_update`, che:
//!   1. scarica il DESCRITTORE firmato da `/agent/client/update/{target}` e ne
//!      verifica la firma con la pubkey server PINNATA (autenticità, non solo
//!      integrità);
//!   2. IDEMPOTENZA: se lo sha256 del PROPRIO eseguibile coincide con quello
//!      del descrittore, il binario è già quello pubblicato → nessun loop;
//!   3. scarica il binario in `<exe>.new`, verifica lo sha256;
//!   4. swap: `<exe>` → `<exe>.old`, `<exe>.new` → `<exe>` (su Windows un
//!      eseguibile IN ESECUZIONE si può rinominare, non sovrascrivere);
//!   5. scrive il marker `Probation` e ritorna `true`: il chiamante ESCE
//!      (exit 0). NIENTE self-respawn (era BUG-A: spawn da processo morente →
//!      handle invalidi post-FreeConsole). Rilancia il SUPERVISOR: systemd
//!      `Restart=always` (Linux) / Scheduled Task watchdog (Windows).
//!
//! ROBUSTEZZA (macchina a stati in `update_state.rs`):
//!   - PROBATION: il nuovo binario deve confermare (`confirm_running`, al primo
//!     poll riuscito) → cancella `.old`. Se muore prima, il boot successivo
//!     (`apply_startup_recovery`) fa ROLLBACK al `.old` known-good.
//!   - Invariante: un update rotto NON lascia mai il device senza binario buono.
//!   - Crash-safe: se l'exe manca a metà swap, il supervisor lo ripristina
//!     (systemd `ExecStartPre` / recovery al boot).

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

use crate::identity;
use crate::update_state::{decide_boot, BootAction, Phase, UpdateState};

#[derive(Debug, Deserialize)]
struct UpdateDescriptor {
    version: String,
    target: String,
    sha256: String,
    url_path: String,
    sig: String,
}

/// `a` strettamente più recente di `b` (semver numerico a punti, es.
/// "0.2.20" > "0.2.18"). Confronto lessicografico dei componenti numerici:
/// per versioni a 3 parti coincide con l'ordinamento semver. Componenti non
/// numerici → 0 (degradazione prudente).
fn version_gt(a: &str, b: &str) -> bool {
    let parse = |v: &str| -> Vec<u64> {
        v.split('.').map(|s| s.trim().parse().unwrap_or(0)).collect()
    };
    parse(a) > parse(b)
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

/// Percorso del marker di stato del self-update, accanto ai dati del client.
pub fn marker_path(data_dir: &Path) -> PathBuf {
    data_dir.join("update_state.json")
}

/// Controlla il descrittore firmato e, se il binario pubblicato è diverso dal
/// proprio, lo scarica, fa lo swap e scrive il marker PROBATION. Ritorna true se
/// il chiamante deve USCIRE (exit 0): il file exe è GIÀ il nuovo binario e il
/// supervisor rilancerà. NIENTE respawn.
pub async fn maybe_update(server: &str, server_pubkey: &str, marker: &Path) -> Result<bool> {
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

    // NO-DOWNGRADE (a prova di tutto): applica SOLO versioni strettamente più
    // recenti. Senza, un `manifest.latest` più basso (rollback lato server,
    // ordine di deploy, race) farebbe RETROCEDERE il client — potenzialmente a
    // un binario col vecchio respawn rotto. La sola disuguaglianza di stringa
    // non basta: serve l'ordinamento.
    let current = env!("CARGO_PKG_VERSION");
    if !version_gt(&desc.version, current) {
        tracing::debug!(current, available = %desc.version,
            "self-update no-op: versione pubblicata non più recente");
        return Ok(false);
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
    // Marker PROBATION: il nuovo binario dovrà confermare al primo poll, o il
    // boot successivo farà rollback al `.old`. Scritto DOPO lo swap riuscito.
    UpdateState::enter_probation(&desc.version)
        .save(marker)
        .with_context(|| format!("scrittura marker {}", marker.display()))?;
    tracing::info!(version = %desc.version,
        "self-update: swap completato, marker probation scritto, esco (rilancia il supervisor)");
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

/// Uscita pulita dopo un self-update: il file exe è GIÀ il nuovo binario, il
/// marker è `Probation`. NIENTE respawn (era BUG-A) — esce e basta; il
/// supervisor (systemd `Restart=always` / Task watchdog) rilancia il nuovo
/// binario in un processo FRESCO (nessun handle ereditato, nessuna race col
/// lock: il vecchio processo è già morto).
pub fn exit_for_update() -> ! {
    tracing::info!("self-update: esco, il supervisor rilancia il nuovo binario");
    std::process::exit(0);
}

/// Se l'exe manca ma `.old` esiste (crash a metà swap: rename via ma non ancora
/// rimpiazzato), lo ripristina. Ritorna true se ha ripristinato. Idempotente.
fn restore_exe_if_missing(exe: &Path) -> bool {
    if exe.exists() {
        return false;
    }
    let old = exe.with_extension("old");
    if !old.exists() {
        return false;
    }
    match std::fs::rename(&old, exe) {
        Ok(()) => {
            tracing::warn!("recovery: exe assente, ripristinato da .old");
            true
        }
        Err(e) => {
            tracing::error!("recovery: ripristino .old fallito: {e}");
            false
        }
    }
}

/// `<exe>` (nuovo, in prova) → `<exe>.failed`, `<exe>.old` (known-good) → `<exe>`.
/// Ripristina il binario confermato quando la probation fallisce.
fn rollback_binary(exe: &Path) -> Result<()> {
    let old = exe.with_extension("old");
    if !old.exists() {
        bail!("rollback impossibile: {} assente (nessun binario known-good)", old.display());
    }
    let failed = exe.with_extension("failed");
    let _ = std::fs::remove_file(&failed);
    if exe.exists() {
        std::fs::rename(exe, &failed)
            .with_context(|| format!("rename {} -> {}", exe.display(), failed.display()))?;
    }
    std::fs::rename(&old, exe)
        .with_context(|| format!("rename {} -> {}", old.display(), exe.display()))?;
    Ok(())
}

/// Recovery + macchina a stati al BOOT, PRIMA del loop del runner.
///
/// 1. Crash-safe: se l'exe manca ma `.old` esiste (crash a metà swap), lo
///    ripristina — nessun device senza binario. (Su Linux il supervisor ha
///    anche `ExecStartPre`; questa è la rete lato-client.)
/// 2. Probation: `decide_boot` incrementa e persiste `boots` PRIMA di agire; se
///    la probation non è confermata dopo un boot → ROLLBACK al `.old` e USCITA
///    (il supervisor rilancia il binario known-good). Altrimenti prosegue: il
///    runner confermerà al primo poll (`confirm_running`).
///
/// `exe` = percorso dell'eseguibile corrente; `marker` = `marker_path`.
pub fn apply_startup_recovery(exe: &Path, marker: &Path) {
    // (1) Rete crash-safe: exe assente + .old presente → ripristina.
    restore_exe_if_missing(exe);

    // (2) Macchina a stati probation.
    let st = UpdateState::load(marker);
    if st.phase == Phase::None {
        return; // stato stabile, niente da fare
    }
    let (next, action) = decide_boot(st);
    // Persisti SUBITO il conteggio boot: un crash successivo non deve azzerarlo.
    if let Err(e) = next.save(marker) {
        tracing::error!("recovery: salvataggio marker fallito: {e}");
    }
    match action {
        BootAction::Proceed => {
            tracing::info!(
                target = %next.target_version, boots = next.boots,
                "self-update: nuovo binario in PROBATION, confermo al primo poll"
            );
        }
        BootAction::Rollback => {
            tracing::error!(
                "self-update: binario in prova non ha confermato → ROLLBACK al known-good"
            );
            match rollback_binary(exe) {
                Ok(()) => tracing::warn!("rollback eseguito, esco (rilancia il supervisor)"),
                Err(e) => {
                    // Nessun .old: resta sul binario in prova (best-effort),
                    // ma azzera il marker per non ripetere il tentativo in loop.
                    tracing::error!("rollback fallito: {e:#}; proseguo col binario corrente");
                    let _ = UpdateState::default().save(marker);
                    return;
                }
            }
            std::process::exit(0);
        }
    }
}

/// Conferma il self-update: chiamato dal runner dopo il PRIMO poll riuscito.
/// Se in probation, la promuove a stabile e cancella `.old`/`.failed` (lo spazio
/// e il binario di rollback non servono più). No-op se non in probation.
pub fn confirm_running(marker: &Path, exe: &Path) {
    let st = UpdateState::load(marker);
    if st.phase != Phase::Probation {
        return;
    }
    if let Err(e) = UpdateState::default().save(marker) {
        tracing::error!("conferma self-update: salvataggio marker fallito: {e}");
        return;
    }
    let _ = std::fs::remove_file(exe.with_extension("old"));
    let _ = std::fs::remove_file(exe.with_extension("failed"));
    tracing::info!(target = %st.target_version,
        "self-update CONFERMATO (primo poll riuscito): swap definitivo");
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

    #[test]
    fn rollback_restores_old_and_parks_failed() {
        let dir = tdir("rb");
        let exe = dir.join("metnos-client");
        std::fs::write(&exe, b"NEW-BAD").unwrap();
        std::fs::write(exe.with_extension("old"), b"OLD-GOOD").unwrap();
        rollback_binary(&exe).unwrap();
        assert_eq!(std::fs::read(&exe).unwrap(), b"OLD-GOOD", "exe torna al known-good");
        assert_eq!(std::fs::read(exe.with_extension("failed")).unwrap(), b"NEW-BAD",
                   "il binario in prova finito da parte come .failed");
        assert!(!exe.with_extension("old").exists(), ".old consumato");
    }

    #[test]
    fn rollback_without_old_is_error() {
        let dir = tdir("rb2");
        let exe = dir.join("metnos-client");
        std::fs::write(&exe, b"NEW").unwrap();
        assert!(rollback_binary(&exe).is_err(), "senza .old non si può rollbackare");
        assert_eq!(std::fs::read(&exe).unwrap(), b"NEW", "exe intatto");
    }

    #[test]
    fn restore_exe_if_missing_recovers_from_old() {
        let dir = tdir("re");
        let exe = dir.join("metnos-client");
        // exe assente (crash a metà swap), .old presente.
        std::fs::write(exe.with_extension("old"), b"GOOD").unwrap();
        assert!(restore_exe_if_missing(&exe), "ripristina");
        assert_eq!(std::fs::read(&exe).unwrap(), b"GOOD");
        // idempotente: exe presente → no-op.
        assert!(!restore_exe_if_missing(&exe));
    }

    #[test]
    fn version_gt_orders_semver_and_blocks_downgrade() {
        assert!(version_gt("0.2.20", "0.2.18"), "newer patch");
        assert!(version_gt("0.3.0", "0.2.99"), "newer minor");
        assert!(version_gt("1.0.0", "0.9.9"), "newer major");
        assert!(!version_gt("0.2.18", "0.2.20"), "downgrade bloccato");
        assert!(!version_gt("0.2.20", "0.2.20"), "stessa versione: non più recente");
        assert!(version_gt("0.2.20", "0.2.9"), "20 > 9 numerico (non stringa!)");
    }

    #[test]
    fn confirm_running_clears_probation_and_deletes_old() {
        let dir = tdir("cf");
        let exe = dir.join("metnos-client");
        let marker = dir.join("update_state.json");
        std::fs::write(&exe, b"NEW").unwrap();
        std::fs::write(exe.with_extension("old"), b"OLD").unwrap();
        std::fs::write(exe.with_extension("failed"), b"OLDER").unwrap();
        UpdateState::enter_probation("0.2.20").save(&marker).unwrap();

        confirm_running(&marker, &exe);
        assert_eq!(UpdateState::load(&marker).phase, Phase::None, "probation confermata");
        assert!(!exe.with_extension("old").exists(), ".old cancellato");
        assert!(!exe.with_extension("failed").exists(), ".failed cancellato");
        assert_eq!(std::fs::read(&exe).unwrap(), b"NEW", "binario nuovo confermato");

        // No-op se non in probation (idempotente).
        std::fs::write(exe.with_extension("old"), b"X").unwrap();
        confirm_running(&marker, &exe);
        assert!(exe.with_extension("old").exists(), "fuori probation: non tocca .old");
    }
}
