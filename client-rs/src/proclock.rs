//! proclock.rs — lock single-instance del client (§12 design doc).
//!
//! Due poller con la STESSA identita' (unit systemd/Scheduled Task + lancio
//! manuale) non eseguono lavoro doppio grazie al claim atomico server-side,
//! ma sprecano poll e possono fare race su spool/cache. Il lock chiude il
//! caso alla RADICE, cross-platform: `flock` su unix, `LockFileEx` su
//! Windows (fs2). Advisory: vale fra processi metnos-client, che e' il caso.
//!
//! Il lock vive per l'intera durata del processo (drop = rilascio, anche su
//! crash: il kernel rilascia i lock del processo morto — niente stale lock
//! da ripulire a mano, a differenza di un pid-file).

use anyhow::{Context, Result};
use fs2::FileExt;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;

pub struct ProcLock {
    _file: File, // tenuto vivo: il lock cade col drop / morte del processo
}

/// Acquisisce il lock esclusivo `<data_dir>/client.lock`. Se un altro
/// `metnos-client run` lo tiene, errore ONESTO (niente attesa silenziosa).
pub fn acquire(data_dir: &Path) -> Result<ProcLock> {
    std::fs::create_dir_all(data_dir)
        .with_context(|| format!("mkdir {}", data_dir.display()))?;
    let path = data_dir.join("client.lock");
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(false)  // lock file: apri/crea senza troncare il contenuto
        .write(true)
        .open(&path)
        .with_context(|| format!("apertura lock {}", path.display()))?;
    file.try_lock_exclusive().map_err(|_| {
        anyhow::anyhow!(
            "metnos-client run e' gia' attivo su questo dispositivo \
             (lock {}). Ferma l'istanza esistente (systemd --user / \
             Scheduled Task) prima di lanciarne un'altra.",
            path.display()
        )
    })?;
    // Solo diagnostica umana: la verita' e' il lock del kernel, non il pid.
    let _ = file.set_len(0);
    let _ = writeln!(file, "{}", std::process::id());
    Ok(ProcLock { _file: file })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn second_acquire_fails_while_held() {
        let dir = std::env::temp_dir().join(format!(
            "metnos-proclock-test-{}", std::process::id()));
        let first = acquire(&dir).expect("primo lock");
        assert!(acquire(&dir).is_err(), "il secondo lock deve fallire");
        drop(first);
        let third = acquire(&dir).expect("lock riacquisibile dopo il rilascio");
        drop(third);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
