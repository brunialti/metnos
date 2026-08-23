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
use crate::protocol::{self, Action, Response, WireRequest};

/// A system action result with a stable, language-neutral failure code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outcome {
    pub ok: bool,
    pub error_code: Option<&'static str>,
    pub exit_code: Option<i32>,
    pub detail: String,
    pub payload: Option<serde_json::Value>,
}

impl Outcome {
    pub fn success(detail: impl Into<String>) -> Self {
        Self {
            ok: true,
            error_code: None,
            exit_code: None,
            detail: detail.into(),
            payload: None,
        }
    }

    pub fn failure(code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            ok: false,
            error_code: Some(code),
            exit_code: None,
            detail: detail.into(),
            payload: None,
        }
    }

    pub fn success_with_payload(payload: serde_json::Value) -> Self {
        Self {
            ok: true,
            error_code: None,
            exit_code: None,
            detail: String::new(),
            payload: Some(payload),
        }
    }
}

impl From<(Option<i32>, String)> for Outcome {
    fn from((exit_code, detail): (Option<i32>, String)) -> Self {
        Self {
            ok: exit_code == Some(0),
            error_code: if exit_code == Some(0) {
                None
            } else {
                Some("package_operation_failed")
            },
            exit_code,
            detail,
            payload: None,
        }
    }
}

/// Applica una richiesta, dall'inizio alla fine.
///
/// `run` riceve la riga di comando gia' costruita e validata. Non puo'
/// riceverne una diversa: il chiamante non ha modo di influenzarla.
pub fn handle(
    request: &WireRequest,
    caller_sid: &str,
    pairing_path: &Path,
    journal_path: &Path,
    audit_path: &Path,
    run: impl FnOnce(&Action) -> Outcome,
) -> Response {
    let Some(pairing) = Pairing::load(pairing_path) else {
        // Nessun consenso registrato: non c'e' niente da autorizzare. Si
        // registra comunque, perche' una richiesta a un aiutante non appaiato
        // e' esattamente il genere di cosa che si vuole poter contare.
        let _ = audit::record(
            audit_path,
            Event::Refused,
            request.package_id(),
            "not_paired",
        );
        return Response {
            error_code: Some("not_paired".into()),
            ..Response::stamped()
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
                request.package_id(),
                "journal_unavailable",
            );
            return Response {
                error_code: Some("journal_unavailable".into()),
                ..Response::stamped()
            };
        }
    };

    if let Err(refusal) = authorize(&pairing, caller_sid, request, |k| journal.already_used(k)) {
        let _ = audit::record(
            audit_path,
            Event::Refused,
            request.package_id(),
            refusal.code(),
        );
        return Response::refused(refusal);
    }

    if journal.consume(request.idempotency_key()).is_err() {
        let _ = audit::record(
            audit_path,
            Event::Refused,
            request.package_id(),
            "journal_write_failed",
        );
        return Response {
            error_code: Some("journal_write_failed".into()),
            ..Response::stamped()
        };
    }

    // A plain version query is local and side-effect free. Supplying the
    // expected client build requests one signed update check, but only after
    // the normal caller, signature and replay gates above. The real package
    // operation is never sent until this handshake has completed.
    if request.is_version_query() {
        let outcome = request.action().map(|action| run(&action));
        let ok = outcome.as_ref().map_or(true, |value| value.ok);
        let error_code = outcome
            .as_ref()
            .and_then(|value| value.error_code)
            .map(str::to_string);
        let detail = outcome
            .as_ref()
            .map(|value| coda_utile(&value.detail))
            .unwrap_or_default();
        let _ = audit::record(
            audit_path,
            Event::Executed,
            request.package_id(),
            if outcome.is_some() {
                "lazy_update_check"
            } else {
                protocol::helper_version()
            },
        );
        return Response {
            ok,
            error_code,
            detail,
            payload: outcome.and_then(|value| value.payload),
            ..Response::stamped()
        };
    }

    let action = request.action().expect("non-version request has an action");
    let outcome = run(&action);
    let detail = coda_utile(&outcome.detail);
    let _ = audit::record(
        audit_path,
        Event::Executed,
        request.package_id(),
        &format!("rc={} {}", outcome.exit_code.unwrap_or(-1), detail),
    );
    Response {
        ok: outcome.ok,
        error_code: outcome.error_code.map(str::to_string),
        exit_code: outcome.exit_code,
        detail,
        payload: outcome.payload,
        ..Response::stamped()
    }
}

/// Le ultime righe utili dell'uscita.
///
/// Il verdetto lo scrivono in fondo, tutti i gestori di pacchetti; la prima
/// riga e' l'insegna di cio' che hanno trovato, e mostrarla come esito dice
/// l'opposto di cio' che e' successo. E' la stessa lezione imparata sul lato
/// Python nella stessa giornata.
fn coda_utile(output: &str) -> String {
    let righe: Vec<&str> = output
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    let utili = if righe.len() > 1 {
        &righe[1..]
    } else {
        &righe[..]
    };
    let inizio = utili.len().saturating_sub(3);
    utili[inizio..].join(" · ").chars().take(300).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{
        Action, ManagedStartRequest, ManagedStopRequest, Operation, Request, Source, StartLifetime,
        WireRequest,
    };
    use ed25519_dalek::{Signer, SigningKey};
    use std::path::PathBuf;

    struct Banco {
        radice: PathBuf,
        chiave: SigningKey,
        sid: String,
    }

    impl Banco {
        fn nuovo(nome: &str) -> Self {
            let radice =
                std::env::temp_dir().join(format!("metnos-helper-{nome}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&radice);
            std::fs::create_dir_all(&radice).unwrap();
            let chiave = SigningKey::from_bytes(&[3u8; 32]);
            let sid = "S-1-5-21-1-2-3-1001".to_string();
            let pairing = Pairing {
                owner_sid: sid.clone(),
                public_key_hex: hex::encode(chiave.verifying_key().to_bytes()),
                server_public_key_b64: String::new(),
                server_url: String::new(),
                consented_at: 1_786_000_000,
            };
            pairing.save(&radice.join("pairing.json")).unwrap();
            Banco {
                radice,
                chiave,
                sid,
            }
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
            r.signature = hex::encode(self.chiave.sign(r.canonical_body().as_bytes()).to_bytes());
            r
        }

        fn chi_sei(&self, chiave_idem: &str) -> Request {
            let mut r = self.richiesta("Segnaposto", chiave_idem);
            r.operation = Operation::Version;
            r.package_id = String::new();
            r.signature = hex::encode(self.chiave.sign(r.canonical_body().as_bytes()).to_bytes());
            r
        }

        fn chi_sei_aspettando(&self, chiave_idem: &str, version: &str) -> Request {
            let mut request = self.chi_sei(chiave_idem);
            request.version = Some(version.into());
            request.signature = hex::encode(
                self.chiave
                    .sign(request.canonical_body().as_bytes())
                    .to_bytes(),
            );
            request
        }

        fn managed_start(&self, id: &str, key: &str) -> ManagedStartRequest {
            let mut request = ManagedStartRequest {
                source: Source::Winget,
                package_id: id.into(),
                lifetime: StartLifetime::Session,
                idempotency_key: key.into(),
                signature: String::new(),
            };
            request.signature = hex::encode(
                self.chiave
                    .sign(request.canonical_body().as_bytes())
                    .to_bytes(),
            );
            request
        }

        fn managed_stop(&self, id: &str, key: &str) -> ManagedStopRequest {
            let mut request = ManagedStopRequest {
                source: Source::Winget,
                package_id: id.into(),
                pid: 4242,
                creation_time: 133_700_000_000_000_000,
                idempotency_key: key.into(),
                signature: String::new(),
            };
            request.signature = hex::encode(
                self.chiave
                    .sign(request.canonical_body().as_bytes())
                    .to_bytes(),
            );
            request
        }

        fn applica(
            &self,
            r: &Request,
            sid: &str,
            esito: (Option<i32>, String),
        ) -> (Response, Vec<Vec<String>>) {
            let eseguiti = std::cell::RefCell::new(Vec::new());
            let wire = WireRequest::Package(r.clone());
            let risposta = handle(
                &wire,
                sid,
                &self.radice.join("pairing.json"),
                &self.radice.join("consumed.log"),
                &self.radice.join("audit.log"),
                |action| {
                    if let Action::PackageCommand(argv) = action {
                        eseguiti.borrow_mut().push(argv.clone());
                    }
                    esito.into()
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
    fn managed_start_uses_the_same_authorization_and_replay_gate() {
        let bank = Banco::nuovo("managed-start");
        let request = WireRequest::ManagedStart(bank.managed_start(
            "LibreHardwareMonitor.LibreHardwareMonitor",
            "abcdef0123456789abcdef0123456789",
        ));
        let runs = std::cell::Cell::new(0);
        let apply = || {
            handle(
                &request,
                &bank.sid,
                &bank.radice.join("pairing.json"),
                &bank.radice.join("consumed.log"),
                &bank.radice.join("audit.log"),
                |action| {
                    assert_eq!(
                        action,
                        &Action::ManagedStart {
                            package_id: "LibreHardwareMonitor.LibreHardwareMonitor".into(),
                            lifetime: StartLifetime::Session,
                        }
                    );
                    runs.set(runs.get() + 1);
                    Outcome::success("started_session")
                },
            )
        };

        assert!(apply().ok);
        let replay = apply();
        assert_eq!(replay.error_code.as_deref(), Some("replayed_request"));
        assert_eq!(runs.get(), 1);
    }

    #[test]
    fn managed_start_signature_cannot_be_reused_for_persistence() {
        let bank = Banco::nuovo("managed-start-lifetime");
        let mut request = bank.managed_start(
            "LibreHardwareMonitor.LibreHardwareMonitor",
            "abcdef0123456789abcdef0123456788",
        );
        request.lifetime = StartLifetime::Persistent;
        let wire = WireRequest::ManagedStart(request);
        let response = handle(
            &wire,
            &bank.sid,
            &bank.radice.join("pairing.json"),
            &bank.radice.join("consumed.log"),
            &bank.radice.join("audit.log"),
            |_| panic!("an invalid managed-start signature reached execution"),
        );
        assert_eq!(response.error_code.as_deref(), Some("untrusted_signature"));
    }

    #[test]
    fn managed_stop_uses_the_same_authorization_and_exact_action() {
        let bank = Banco::nuovo("managed-stop");
        let request = WireRequest::ManagedStop(bank.managed_stop(
            "LibreHardwareMonitor.LibreHardwareMonitor",
            "abcdef0123456789abcdef0123456787",
        ));
        let response = handle(
            &request,
            &bank.sid,
            &bank.radice.join("pairing.json"),
            &bank.radice.join("consumed.log"),
            &bank.radice.join("audit.log"),
            |action| {
                assert_eq!(
                    action,
                    &Action::ManagedStop {
                        package_id: "LibreHardwareMonitor.LibreHardwareMonitor".into(),
                        pid: 4242,
                        creation_time: 133_700_000_000_000_000,
                    }
                );
                Outcome::success_with_payload(serde_json::json!({"stopped": true}))
            },
        );
        assert!(response.ok);
        assert_eq!(response.payload, Some(serde_json::json!({"stopped": true})));
    }

    #[test]
    fn una_richiesta_di_un_altro_utente_non_esegue_niente() {
        let b = Banco::nuovo("altro-utente");
        let r = b.richiesta("X.Y", "0123456789abcdef0123456789abcdef");
        let (risposta, eseguiti) = b.applica(&r, "S-1-5-21-9-9-9-9999", (Some(0), "".into()));

        assert!(!risposta.ok);
        assert!(
            eseguiti.is_empty(),
            "ha eseguito per un utente non autorizzato"
        );
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
        assert!(
            b.registro().contains("not_paired"),
            "una richiesta a un aiutante non appaiato va contata"
        );
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

    #[test]
    fn chiedere_chi_sei_non_esegue_niente() {
        // Il punto del verbo: risponde senza toccare la macchina. Se un
        // giorno eseguisse qualcosa, la voce piu' innocua del vocabolario
        // sarebbe diventata una strada per eseguire.
        let banco = Banco::nuovo("versione-non-esegue");
        let (risposta, eseguiti) = banco.applica(
            &banco.chi_sei("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"),
            &banco.sid,
            (Some(0), String::new()),
        );
        assert!(risposta.ok, "rifiutata: {:?}", risposta.error_code);
        assert!(eseguiti.is_empty(), "ha eseguito {eseguiti:?}");
        assert_eq!(risposta.helper_version, protocol::helper_version());
        assert_eq!(risposta.protocol_version, protocol::PROTOCOL_VERSION);
    }

    #[test]
    fn expected_version_runs_only_the_internal_update_check() {
        let bank = Banco::nuovo("lazy-update");
        let request = WireRequest::Package(
            bank.chi_sei_aspettando("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa4", "0.2.44"),
        );
        let actions = std::cell::RefCell::new(Vec::new());
        let response = handle(
            &request,
            &bank.sid,
            &bank.radice.join("pairing.json"),
            &bank.radice.join("consumed.log"),
            &bank.radice.join("audit.log"),
            |action| {
                actions.borrow_mut().push(action.clone());
                Outcome::success("helper_current")
            },
        );

        assert!(response.ok);
        assert_eq!(actions.into_inner(), vec![Action::HelperUpdateCheck]);
    }

    #[test]
    fn ogni_risposta_dice_chi_l_ha_scritta() {
        // Anche i rifiuti: e' quando qualcosa non va che serve sapere con
        // chi si stava parlando, ed e' li' che una seconda domanda potrebbe
        // non arrivare mai.
        let banco = Banco::nuovo("versione-anche-nei-rifiuti");
        let (risposta, eseguiti) = banco.applica(
            &banco.richiesta("Qualcosa", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2"),
            "S-1-5-21-9-9-9-9999",
            (Some(0), String::new()),
        );
        assert!(!risposta.ok);
        assert!(eseguiti.is_empty());
        assert_eq!(risposta.helper_version, protocol::helper_version());
    }

    #[test]
    fn chiedere_chi_sei_passa_dagli_stessi_controlli() {
        // Non e' una porta di servizio: senza consenso non risponde nemmeno
        // «chi sono».
        let banco = Banco::nuovo("versione-stessi-controlli");
        std::fs::remove_file(banco.radice.join("pairing.json")).unwrap();
        let (risposta, eseguiti) = banco.applica(
            &banco.chi_sei("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa3"),
            &banco.sid,
            (Some(0), String::new()),
        );
        assert!(!risposta.ok);
        assert_eq!(risposta.error_code.as_deref(), Some("not_paired"));
        assert!(eseguiti.is_empty());
    }
}
