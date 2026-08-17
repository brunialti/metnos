//! Il ciclo dell'aiutante: riceve, decide, esegue, registra.
//!
//! Tiene insieme i pezzi e non ne duplica nessuno. La sequenza e' fissata qui
//! una volta sola, cosi' non esiste un secondo percorso che possa saltare un
//! controllo — un componente privilegiato con due strade e' un componente con
//! una strada sicura e una da trovare.
//!
//! L'ordine, e il perche':
//!
//! 1. **si legge l'appaiamento**. Senza consenso non si fa niente, e un
//!    consenso non si inventa;
//! 2. **si autorizza** (`pairing::authorize`): forma, chiamante, firma,
//!    ripetizione, in quell'ordine;
//! 3. **si consuma la chiave PRIMA di agire**. Se l'aiutante muore fra le due
//!    cose, l'operazione risulta consumata e non verra' ripetuta;
//! 4. **si esegue**, con la riga di comando costruita dal protocollo;
//! 5. **si registra** in ogni caso — anche i rifiuti, soprattutto i rifiuti.
//!
//! `esegui` prende la funzione che lancia il comando come parametro: cosi' il
//! ciclo si prova per intero su qualunque macchina, senza installare niente.
//! Non e' una comodita' di collaudo: e' l'unico modo di verificare *che cosa
//! verrebbe eseguito* senza eseguirlo davvero.

use std::path::Path;

use crate::audit::{self, Event};
use crate::journal::Journal;
use crate::pairing::{authorize, Pairing};
use crate::protocol::{Request, Response};

/// L'esito grezzo di un comando: codice d'uscita e uscita testuale.
pub type Outcome = (Option<i32>, String);

/// Applica una richiesta, dall'inizio alla fine.
///
/// `run` riceve la riga di comando gia' costruita e validata. Non puo'
/// riceverne una diversa: il chiamante non ha modo di influenzarla.
pub fn handle(
    request: &Request,
    caller_sid: &str,
    pairing_path: &Path,
    journal_path: &Path,
    audit_path: &Path,
    run: impl FnOnce(&[String]) -> Outcome,
) -> Response {
    let Some(pairing) = Pairing::load(pairing_path) else {
        // Nessun consenso registrato: non c'e' niente da autorizzare. Si
        // registra comunque, perche' una richiesta a un aiutante non appaiato
        // e' esattamente il genere di cosa che si vuole poter contare.
        let _ = audit::record(audit_path, Event::Refused, &request.package_id, "not_paired");
        return Response {
            ok: false,
            error_code: Some("not_paired".into()),
            exit_code: None,
            detail: String::new(),
        };
    };

    let mut journal = match Journal::open(journal_path) {
        Ok(j) => j,
        Err(_) => {
            // Senza la memoria di cio' che e' stato fatto, la protezione
            // contro il riascolto sparisce. Meglio rifiutare che eseguire
            // con una difesa in meno e non dirlo.
            let _ = audit::record(
                audit_path,
                Event::Refused,
                &request.package_id,
                "journal_unavailable",
            );
            return Response {
                ok: false,
                error_code: Some("journal_unavailable".into()),
                exit_code: None,
                detail: String::new(),
            };
        }
    };

    if let Err(refusal) = authorize(&pairing, caller_sid, request, |k| journal.already_used(k)) {
        let _ = audit::record(
            audit_path,
            Event::Refused,
            &request.package_id,
            refusal.code(),
        );
        return Response::refused(refusal);
    }

    if journal.consume(&request.idempotency_key).is_err() {
        let _ = audit::record(
            audit_path,
            Event::Refused,
            &request.package_id,
            "journal_write_failed",
        );
        return Response {
            ok: false,
            error_code: Some("journal_write_failed".into()),
            exit_code: None,
            detail: String::new(),
        };
    }

    let (exit_code, output) = run(&request.argv());
    let detail = coda_utile(&output);
    let _ = audit::record(
        audit_path,
        Event::Executed,
        &request.package_id,
        &format!("rc={} {}", exit_code.unwrap_or(-1), detail),
    );
    Response {
        ok: exit_code == Some(0),
        error_code: if exit_code == Some(0) {
            None
        } else {
            Some("package_operation_failed".into())
        },
        exit_code,
        detail,
    }
}

/// Le ultime righe utili dell'uscita.
///
/// Il verdetto lo scrivono in fondo, tutti i gestori di pacchetti; la prima
/// riga e' l'insegna di cio' che hanno trovato, e mostrarla come esito dice
/// l'opposto di cio' che e' successo. E' la stessa lezione imparata sul lato
/// Python nella stessa giornata.
fn coda_utile(output: &str) -> String {
    let righe: Vec<&str> = output.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    let utili = if righe.len() > 1 { &righe[1..] } else { &righe[..] };
    let inizio = utili.len().saturating_sub(3);
    utili[inizio..].join(" · ").chars().take(300).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{Operation, Source};
    use ed25519_dalek::{Signer, SigningKey};
    use std::path::PathBuf;

    struct Banco {
        radice: PathBuf,
        chiave: SigningKey,
        sid: String,
    }

    impl Banco {
        fn nuovo(nome: &str) -> Self {
            let radice = std::env::temp_dir()
                .join(format!("metnos-helper-{nome}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&radice);
            std::fs::create_dir_all(&radice).unwrap();
            let chiave = SigningKey::from_bytes(&[3u8; 32]);
            let sid = "S-1-5-21-1-2-3-1001".to_string();
            let pairing = Pairing {
                owner_sid: sid.clone(),
                public_key_hex: hex::encode(chiave.verifying_key().to_bytes()),
                consented_at: 1_786_000_000,
            };
            pairing.save(&radice.join("pairing.json")).unwrap();
            Banco { radice, chiave, sid }
        }

        fn richiesta(&self, id: &str, chiave_idem: &str) -> Request {
            let mut r = Request {
                operation: Operation::Install,
                source: Source::Winget,
                package_id: id.into(),
                version: None,
                idempotency_key: chiave_idem.into(),
                signature: String::new(),
            };
            r.signature =
                hex::encode(self.chiave.sign(r.canonical_body().as_bytes()).to_bytes());
            r
        }

        fn applica(&self, r: &Request, sid: &str, esito: Outcome) -> (Response, Vec<Vec<String>>) {
            let eseguiti = std::cell::RefCell::new(Vec::new());
            let risposta = handle(
                r,
                sid,
                &self.radice.join("pairing.json"),
                &self.radice.join("consumed.log"),
                &self.radice.join("audit.log"),
                |argv| {
                    eseguiti.borrow_mut().push(argv.to_vec());
                    esito
                },
            );
            (risposta, eseguiti.into_inner())
        }

        fn registro(&self) -> String {
            std::fs::read_to_string(self.radice.join("audit.log")).unwrap_or_default()
        }
    }

    impl Drop for Banco {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.radice);
        }
    }

    #[test]
    fn una_richiesta_valida_esegue_e_si_registra() {
        let b = Banco::nuovo("valida");
        let r = b.richiesta("Microsoft.PowerToys", "0123456789abcdef0123456789abcdef");
        let (risposta, eseguiti) = b.applica(&r, &b.sid, (Some(0), "ok".into()));

        assert!(risposta.ok);
        assert_eq!(eseguiti.len(), 1);
        assert!(eseguiti[0].contains(&"install".to_string()));
        assert!(b.registro().contains("executed"));
    }

    #[test]
    fn una_richiesta_di_un_altro_utente_non_esegue_niente() {
        let b = Banco::nuovo("altro-utente");
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let (risposta, eseguiti) = b.applica(&r, "S-1-5-21-9-9-9-9999", (Some(0), "".into()));

        assert!(!risposta.ok);
        assert!(eseguiti.is_empty(), "ha eseguito per un utente non autorizzato");
        assert!(b.registro().contains("refused"));
    }

    #[test]
    fn una_richiesta_ripetuta_non_esegue_due_volte() {
        // E' il caso che il registro delle chiavi esiste per chiudere: la
        // stessa richiesta, rimandata.
        let b = Banco::nuovo("ripetuta");
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let (primo, e1) = b.applica(&r, &b.sid, (Some(0), "".into()));
        let (secondo, e2) = b.applica(&r, &b.sid, (Some(0), "".into()));

        assert!(primo.ok);
        assert_eq!(e1.len(), 1);
        assert!(!secondo.ok);
        assert_eq!(secondo.error_code.as_deref(), Some("replayed_request"));
        assert!(e2.is_empty(), "la seconda volta ha eseguito");
    }

    #[test]
    fn senza_consenso_non_si_fa_niente() {
        let b = Banco::nuovo("senza-consenso");
        std::fs::remove_file(b.radice.join("pairing.json")).unwrap();
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let (risposta, eseguiti) = b.applica(&r, &b.sid, (Some(0), "".into()));

        assert_eq!(risposta.error_code.as_deref(), Some("not_paired"));
        assert!(eseguiti.is_empty());
        assert!(b.registro().contains("not_paired"),
                "una richiesta a un aiutante non appaiato va contata");
    }

    #[test]
    fn la_chiave_si_consuma_anche_quando_il_comando_fallisce() {
        // Altrimenti un comando che fallisce lascerebbe la richiesta
        // ripetibile, e ripetere e' cio' che si vuole impedire.
        let b = Banco::nuovo("fallito");
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let (primo, _) = b.applica(&r, &b.sid, (Some(1), "errore".into()));
        assert!(!primo.ok);

        let (secondo, eseguiti) = b.applica(&r, &b.sid, (Some(0), "".into()));
        assert_eq!(secondo.error_code.as_deref(), Some("replayed_request"));
        assert!(eseguiti.is_empty());
    }

    #[test]
    fn un_identificativo_malformato_non_raggiunge_il_comando() {
        let b = Banco::nuovo("malformato");
        let mut r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        r.package_id = "--force".into();
        let (risposta, eseguiti) = b.applica(&r, &b.sid, (Some(0), "".into()));

        assert_eq!(risposta.error_code.as_deref(), Some("malformed_package_id"));
        assert!(eseguiti.is_empty());
    }

    #[test]
    fn la_diagnosi_viene_dal_fondo_non_dallinsegna() {
        let b = Banco::nuovo("diagnosi");
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let uscita = "Trovato X [X.Y] Versione 1.0\nScarico...\nInstallazione non riuscita.";
        let (risposta, _) = b.applica(&r, &b.sid, (Some(42), uscita.into()));

        assert!(risposta.detail.contains("non riuscita"));
        assert!(!risposta.detail.contains("Trovato X"));
        assert_eq!(risposta.exit_code, Some(42));
    }

    #[test]
    fn ogni_esito_lascia_una_riga_e_una_sola() {
        let b = Banco::nuovo("righe");
        for (i, id) in ["A.Uno", "B.Due"].iter().enumerate() {
            let chiave = format!("{:0>32}", i + 1);
            let r = b.richiesta(id, &chiave);
            b.applica(&r, &b.sid, (Some(0), "".into()));
        }
        assert_eq!(b.registro().lines().count(), 2);
    }
}
