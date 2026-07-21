//! update_state.rs — macchina a stati del self-update ROBUSTO ("a prova di
//! tutto", 8/7/2026). Logica PURA + I/O del marker: testabile su host.
//!
//! Rimpiazza il self-respawn fragile (BUG-A: il client spawnava un figlio da un
//! processo morente → CreateProcessW falliva con handle invalidi post-FreeConsole).
//! Nuovo modello:
//!   1. il client rileva un update, scarica+verifica, fa lo SWAP, scrive un
//!      marker `Probation` e ESCE (exit 0) — NIENTE respawn;
//!   2. il SUPERVISOR (systemd `Restart=always` / Task watchdog) rilancia il
//!      nuovo binario;
//!   3. il nuovo binario, al boot, è in PROBATION: se raggiunge il primo poll
//!      riuscito CONFERMA (cancella `.old`); se muore prima, il boot successivo
//!      vede la probation non confermata e fa ROLLBACK al `.old` known-good.
//!
//! Invariante di sicurezza: un update che non parte o crasha NON lascia mai il
//! device senza un binario funzionante — si torna al `.old` confermato.

use serde::{Deserialize, Serialize};
use std::path::Path;

/// Fase del ciclo di self-update.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Nessun aggiornamento in volo (stato stabile).
    #[default]
    None,
    /// Nuovo binario appena swappato, in prova finché non conferma.
    Probation,
}

/// Stato persistito accanto all'eseguibile (`update_state.json`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct UpdateState {
    pub phase: Phase,
    /// Versione attesa del binario in prova (diagnostica + coerenza).
    #[serde(default)]
    pub target_version: String,
    /// Boot consumati in probation. Incrementato+persistito PRIMA di rischiare,
    /// così un crash rapidissimo non azzera il conteggio e il rollback scatta.
    #[serde(default)]
    pub boots: u32,
}

impl UpdateState {
    /// Carica dal disco; file assente o corrotto → stato stabile (tollerante:
    /// un marker illeggibile non deve bloccare l'avvio).
    pub fn load(path: &Path) -> Self {
        std::fs::read(path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default()
    }

    /// Scrive atomicamente (tmp + rename), creando la dir se manca.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let bytes = serde_json::to_vec_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, &bytes)?;
        std::fs::rename(&tmp, path)
    }

    /// Marca l'ingresso in probation dopo uno swap riuscito.
    pub fn enter_probation(target_version: &str) -> Self {
        UpdateState {
            phase: Phase::Probation,
            target_version: target_version.to_string(),
            boots: 0,
        }
    }
}

/// Massimo numero di boot in probation prima del rollback. 2 = il nuovo binario
/// ha diritto a UN boot per confermare (raggiungere il primo poll); se torna al
/// boot (boots>=2) senza aver confermato, è inaffidabile → rollback al `.old`.
pub const MAX_PROBATION_BOOTS: u32 = 2;

/// Azione decisa al boot dalla macchina a stati (pura).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BootAction {
    /// Avvio normale del client.
    Proceed,
    /// Il binario in prova non ha confermato → ripristina il `.old`.
    Rollback,
}

/// Decisione di boot PURA. Ritorna `(stato_da_persistire, azione)`.
///
/// - `None` → Proceed, stato invariato.
/// - `Probation` → incrementa `boots` (il chiamante lo persiste PRIMA di agire):
///     - `boots < MAX` → Proceed (primo boot del nuovo binario: confermerà al
///       primo poll);
///     - `boots >= MAX` → Rollback, e lo stato torna `None` (dopo il ripristino
///       non c'è più update in volo).
pub fn decide_boot(st: UpdateState) -> (UpdateState, BootAction) {
    match st.phase {
        Phase::None => (st, BootAction::Proceed),
        Phase::Probation => {
            let boots = st.boots.saturating_add(1);
            if boots >= MAX_PROBATION_BOOTS {
                (UpdateState::default(), BootAction::Rollback)
            } else {
                (
                    UpdateState { boots, ..st },
                    BootAction::Proceed,
                )
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn none_proceeds_unchanged() {
        let (st, act) = decide_boot(UpdateState::default());
        assert_eq!(act, BootAction::Proceed);
        assert_eq!(st, UpdateState::default());
    }

    #[test]
    fn first_probation_boot_proceeds_and_counts() {
        let st = UpdateState::enter_probation("0.2.20");
        let (next, act) = decide_boot(st);
        assert_eq!(act, BootAction::Proceed, "primo boot del nuovo binario: prova");
        assert_eq!(next.phase, Phase::Probation);
        assert_eq!(next.boots, 1, "boots persistito a 1 prima di rischiare");
        assert_eq!(next.target_version, "0.2.20");
    }

    #[test]
    fn second_probation_boot_rolls_back() {
        // Il nuovo binario ha già consumato un boot (boots=1) senza confermare
        // (crash pre-poll) → al boot successivo rollback.
        let st = UpdateState { phase: Phase::Probation, target_version: "0.2.20".into(), boots: 1 };
        let (next, act) = decide_boot(st);
        assert_eq!(act, BootAction::Rollback);
        assert_eq!(next, UpdateState::default(), "dopo rollback nessun update in volo");
    }

    #[test]
    fn confirm_path_clears_probation() {
        // Simula la conferma: dopo il primo poll riuscito lo stato torna None.
        let confirmed = UpdateState::default();
        let (st, act) = decide_boot(confirmed.clone());
        assert_eq!(act, BootAction::Proceed);
        assert_eq!(st, confirmed);
    }

    #[test]
    fn roundtrip_disk() {
        let dir = std::env::temp_dir().join(format!("metnos-ust-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("update_state.json");
        let st = UpdateState::enter_probation("0.2.20");
        st.save(&path).expect("save");
        assert_eq!(UpdateState::load(&path), st, "round-trip preserva lo stato");
        // File assente → stato stabile (tollerante).
        let _ = std::fs::remove_file(&path);
        assert_eq!(UpdateState::load(&path), UpdateState::default());
        // JSON corrotto → stato stabile.
        std::fs::write(&path, b"{ not json").unwrap();
        assert_eq!(UpdateState::load(&path), UpdateState::default());
    }
}
