//! Il registro dell'aiutante, separato da quello del client (ADR 0210 D5).
//!
//! Separato per una ragione precisa: il client e' il processo che l'aiutante
//! serve, e un registro che il servito puo' riscrivere non e' una prova. Qui
//! scrive soltanto il servizio di sistema; il proprietario lo legge.
//!
//! Si registrano anche i RIFIUTI, e non e' un dettaglio: un rifiuto che non
//! lascia traccia e' indistinguibile da un attacco che nessuno ha notato.
//! Le richieste malformate, le firme sbagliate, i chiamati non autorizzati
//! sono esattamente cio' che si vuole poter contare a posteriori.
//!
//! Una riga per evento, in aggiunta e mai riscritta. Un formato piu' ricco
//! sarebbe piu' codice dentro un componente privilegiato, in cambio di niente.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

/// Che cosa e' successo. Codici stabili e non tradotti: il registro si legge
/// a distanza di tempo, spesso da una persona diversa da chi c'era.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Event {
    /// Consenso dato: l'aiutante e' stato appaiato a un proprietario.
    Paired,
    /// Richiesta accettata ed eseguita.
    Executed,
    /// Richiesta rifiutata prima di qualunque effetto.
    Refused,
}

impl Event {
    fn code(self) -> &'static str {
        match self {
            Event::Paired => "paired",
            Event::Executed => "executed",
            Event::Refused => "refused",
        }
    }
}

/// Scrive una riga nel registro.
///
/// `detail` porta il codice del rifiuto o l'esito dell'operazione. Non porta
/// mai l'uscita completa del gestore di pacchetti: quella puo' contenere
/// percorsi e nomi che non servono a capire che cosa e' stato autorizzato, e
/// un registro che cresce senza controllo e' un registro che qualcuno un
/// giorno cancella.
pub fn record(path: &Path, event: Event, package_id: &str, detail: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let quando = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    // Separatore che non puo' comparire nei campi: un identificativo non
    // contiene tabulazioni, e un dettaglio ripulito nemmeno.
    writeln!(
        file,
        "{}\t{}\t{}\t{}",
        quando,
        event.code(),
        package_id,
        pulisci(detail)
    )?;
    file.sync_all()
}

/// Toglie cio' che spezzerebbe una riga e tronca: una riga di registro deve
/// restare una riga.
fn pulisci(valore: &str) -> String {
    valore
        .chars()
        .map(|c| {
            if c == '\n' || c == '\r' || c == '\t' {
                ' '
            } else {
                c
            }
        })
        .take(200)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temporaneo(nome: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!("metnos-audit-{nome}-{}.log", std::process::id()));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn un_evento_finisce_nel_registro() {
        let p = temporaneo("scritto");
        record(&p, Event::Executed, "Microsoft.PowerToys", "rc=0").unwrap();
        let testo = std::fs::read_to_string(&p).unwrap();
        assert!(testo.contains("executed"));
        assert!(testo.contains("Microsoft.PowerToys"));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn anche_un_rifiuto_lascia_traccia() {
        // Il punto: un rifiuto che non si registra e' indistinguibile da un
        // attacco che nessuno ha notato.
        let p = temporaneo("rifiuto");
        record(&p, Event::Refused, "X.Y", "untrusted_signature").unwrap();
        let testo = std::fs::read_to_string(&p).unwrap();
        assert!(testo.contains("refused"));
        assert!(testo.contains("untrusted_signature"));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn il_registro_si_aggiunge_e_non_si_riscrive() {
        let p = temporaneo("aggiunto");
        record(&p, Event::Paired, "-", "S-1-5-21-1-2-3-1001").unwrap();
        record(&p, Event::Executed, "A.Uno", "rc=0").unwrap();
        record(&p, Event::Refused, "B.Due", "replayed_request").unwrap();
        let righe: Vec<String> = std::fs::read_to_string(&p)
            .unwrap()
            .lines()
            .map(String::from)
            .collect();
        assert_eq!(righe.len(), 3);
        assert!(righe[0].contains("paired"));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn una_riga_resta_una_riga() {
        // Un dettaglio con un a-capo dentro spezzerebbe il registro in due
        // eventi, e uno dei due sarebbe illeggibile.
        let p = temporaneo("riga-sola");
        record(&p, Event::Refused, "X.Y", "prima\nseconda\tterza\rquarta").unwrap();
        assert_eq!(std::fs::read_to_string(&p).unwrap().lines().count(), 1);
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn un_dettaglio_lunghissimo_viene_troncato() {
        let p = temporaneo("troncato");
        record(&p, Event::Executed, "X.Y", &"a".repeat(5000)).unwrap();
        let riga = std::fs::read_to_string(&p).unwrap();
        assert!(riga.len() < 400, "riga di registro troppo lunga");
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn i_campi_restano_distinguibili() {
        let p = temporaneo("campi");
        record(&p, Event::Executed, "Microsoft.PowerToys", "rc=0").unwrap();
        let riga = std::fs::read_to_string(&p).unwrap();
        let campi: Vec<&str> = riga.trim_end().split('\t').collect();
        assert_eq!(campi.len(), 4);
        assert_eq!(campi[1], "executed");
        assert_eq!(campi[2], "Microsoft.PowerToys");
        let _ = std::fs::remove_file(&p);
    }
}
