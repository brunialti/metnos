//! Le richieste gia' eseguite, perche' nessuna si possa rigiocare (D3.4).
//!
//! Senza questo registro una richiesta catturata vale per sempre: basta
//! rimandarla. E installare non e' un'operazione che si possa ripetere senza
//! conseguenze sul sistema — ripeterla e' esattamente cio' che un attacco
//! vorrebbe poter fare.
//!
//! Il registro sta dalla parte dell'AIUTANTE, non del chiamante. Un contatore
//! tenuto da chi chiede non protegge da chi quel chiamante lo ha sostituito.
//!
//! File semplice, una chiave per riga: il formato piu' povero che risolve il
//! problema. Un database qui sarebbe superficie in piu' su un componente che
//! gira con i privilegi di sistema.

use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

/// Quante chiavi si conservano. Oltre, le piu' vecchie escono.
///
/// Un registro che cresce senza fine e' un file che un giorno riempie il
/// disco di sistema. Il taglio e' un compromesso dichiarato: una richiesta
/// piu' vecchia di diecimila operazioni tornerebbe accettabile — ma per
/// arrivarci servirebbe che l'aiutante ne abbia eseguite diecimila, e a quel
/// punto una richiesta di quell'eta' porta comunque una firma che il
/// riappaiamento ha invalidato.
const MAX_KEYS: usize = 10_000;

pub struct Journal {
    path: PathBuf,
    seen: HashSet<String>,
}

impl Journal {
    /// Apre il registro, leggendo cio' che c'e' gia'.
    ///
    /// Un registro illeggibile NON viene ignorato: senza la memoria di cio'
    /// che e' stato fatto, ogni richiesta e' una richiesta nuova, e la
    /// protezione contro il riascolto sparisce in silenzio. Meglio un errore
    /// che una difesa che sembra esserci.
    pub fn open(path: &Path) -> std::io::Result<Self> {
        let mut seen = HashSet::new();
        if path.exists() {
            let file = File::open(path)?;
            for line in BufReader::new(file).lines() {
                let line = line?;
                let key = line.trim();
                if !key.is_empty() {
                    seen.insert(key.to_string());
                }
            }
        }
        Ok(Journal {
            path: path.to_path_buf(),
            seen,
        })
    }

    /// Vero se questa chiave e' gia' stata usata.
    pub fn already_used(&self, key: &str) -> bool {
        self.seen.contains(key)
    }

    /// Segna la chiave come consumata, PRIMA di agire.
    ///
    /// L'ordine conta: si scrive prima e si esegue dopo. Se l'aiutante muore
    /// fra le due cose, la richiesta risulta consumata e non verra' ripetuta
    /// — chi ha chiesto vedra' un'operazione senza esito e potra' rifarla in
    /// modo esplicito. L'ordine opposto lascerebbe una richiesta eseguita e
    /// non registrata, cioe' ripetibile: fra un'operazione persa e una
    /// ripetuta, su un sistema che si modifica, si perde.
    pub fn consume(&mut self, key: &str) -> std::io::Result<()> {
        self.seen.insert(key.to_string());
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(file, "{key}")?;
        file.sync_all()?;
        if self.seen.len() > MAX_KEYS {
            self.compact()?;
        }
        Ok(())
    }

    /// Riscrive il registro tenendo le chiavi piu' recenti.
    fn compact(&mut self) -> std::io::Result<()> {
        let file = File::open(&self.path)?;
        let keys: Vec<String> = BufReader::new(file)
            .lines()
            .map_while(Result::ok)
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty())
            .collect();
        let tenute: Vec<&String> = keys
            .iter()
            .skip(keys.len().saturating_sub(MAX_KEYS))
            .collect();

        // Si scrive accanto e si sposta: un'interruzione a meta' lascerebbe
        // un registro troncato, cioe' richieste vecchie di nuovo accettabili.
        let tmp = self.path.with_extension("compacting");
        {
            let mut out = File::create(&tmp)?;
            for k in &tenute {
                writeln!(out, "{k}")?;
            }
            out.sync_all()?;
        }
        std::fs::rename(&tmp, &self.path)?;
        self.seen = tenute.into_iter().cloned().collect();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporaneo(nome: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!("metnos-helper-test-{nome}-{}", std::process::id()));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn una_chiave_nuova_non_risulta_usata() {
        let p = temporaneo("nuova");
        let j = Journal::open(&p).unwrap();
        assert!(!j.already_used("abc"));
    }

    #[test]
    fn una_chiave_consumata_risulta_usata() {
        let p = temporaneo("consumata");
        let mut j = Journal::open(&p).unwrap();
        j.consume("abc").unwrap();
        assert!(j.already_used("abc"));
    }

    #[test]
    fn la_memoria_sopravvive_alla_chiusura() {
        // E' il punto: un registro tenuto solo in memoria non protegge da un
        // riascolto dopo un riavvio, che e' proprio quando serve.
        let p = temporaneo("persistente");
        {
            let mut j = Journal::open(&p).unwrap();
            j.consume("chiave-uno").unwrap();
        }
        let j = Journal::open(&p).unwrap();
        assert!(j.already_used("chiave-uno"));
        assert!(!j.already_used("chiave-due"));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn il_registro_non_cresce_senza_fine() {
        let p = temporaneo("compattato");
        let mut j = Journal::open(&p).unwrap();
        for i in 0..(MAX_KEYS + 50) {
            j.consume(&format!("chiave-{i}")).unwrap();
        }
        assert!(
            j.seen.len() <= MAX_KEYS,
            "registro cresciuto oltre il tetto"
        );
        // Le piu' recenti restano: sono quelle che possono essere rigiocate.
        assert!(j.already_used(&format!("chiave-{}", MAX_KEYS + 49)));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn dopo_la_compattazione_la_memoria_regge_una_riapertura() {
        let p = temporaneo("riletto");
        {
            let mut j = Journal::open(&p).unwrap();
            for i in 0..(MAX_KEYS + 10) {
                j.consume(&format!("k{i}")).unwrap();
            }
        }
        let j = Journal::open(&p).unwrap();
        assert!(j.already_used(&format!("k{}", MAX_KEYS + 9)));
        let _ = std::fs::remove_file(&p);
    }
}
