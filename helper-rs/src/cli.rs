//! I comandi dell'aiutante: installare, servire, disinstallare.
//!
//! Tre verbi e nient'altro. La riga di comando di un componente privilegiato
//! e' una superficie come le altre: ogni opzione in piu' e' un altro modo di
//! farlo comportare diversamente da come e' stato installato.
//!
//! Il parsing e' scritto a mano invece che con una libreria di analisi degli
//! argomenti. Non e' avarizia di dipendenze fine a se stessa: sono tre verbi e
//! due valori, e una libreria porterebbe con se' funzionalita' che nessuno ha
//! chiesto — abbreviazioni, prefissi ambigui, file di risposta — dentro il
//! processo con i privilegi di sistema.

/// Che cosa e' stato chiesto sulla riga di comando.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Installa e appaia. Si esegue una volta, con il consenso.
    Install {
        owner_sid: String,
        public_key_hex: String,
        /// La chiave pubblica del SERVER, in base64url.
        ///
        /// Un'ancora di fiducia DIVERSA da quella del proprietario, e con un
        /// compito diverso: quella dice chi puo' chiedere, questa dice che
        /// cosa ci si puo' installare sopra. Serve a poter aggiornare questo
        /// programma senza disturbare nessuno — e a non poterlo fare con
        /// qualcosa che il server non ha firmato.
        server_key_b64: String,
        /// L'indirizzo del server: a chi chiedere se c'e' una versione nuova.
        server_url: String,
        /// Dove scrivere il motivo, se l'installazione non riesce.
        ///
        /// Chi ci lancia non puo' leggere quello che stampiamo: l'elevazione
        /// passa da Windows, che non gira l'uscita a nessuno. Senza questo
        /// file l'unica cosa che arriva a chi ha premuto il bottone e' un
        /// numero, e un numero non dice quale passo e' andato storto — lo
        /// abbiamo scoperto il 19/8/2026, con «codice 3» e nient'altro.
        /// Facoltativo: se manca, si stampa e basta, come prima.
        error_file: String,
    },
    /// Il ciclo del servizio. Lo lancia Windows, non una persona.
    Serve,
    /// Toglie tutto. Lo lancia Windows dalla lista dei programmi.
    Uninstall,
    /// Che cosa c'e' installato e per chi: una domanda che il proprietario
    /// deve poter fare senza leggere file di configurazione.
    Status,
}

/// Perche' la riga di comando non e' utilizzabile.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// Nessun verbo.
    Missing,
    /// Un verbo che non esiste.
    UnknownCommand(String),
    /// Un'opzione che non esiste.
    UnknownOption(String),
    /// Un'opzione senza il suo valore.
    MissingValue(String),
    /// Manca un valore obbligatorio.
    MissingRequired(&'static str),
}

impl ParseError {
    pub fn message(&self) -> String {
        match self {
            ParseError::Missing => "Serve un comando: install, serve, uninstall, status.".into(),
            ParseError::UnknownCommand(c) => {
                format!("Comando sconosciuto «{c}». Sono: install, serve, uninstall, status.")
            }
            ParseError::UnknownOption(o) => format!("Opzione sconosciuta «{o}»."),
            ParseError::MissingValue(o) => format!("All'opzione «{o}» manca il valore."),
            ParseError::MissingRequired(o) => format!("Manca l'opzione obbligatoria «{o}»."),
        }
    }
}

/// Legge la riga di comando.
///
/// Un'opzione sconosciuta e' un ERRORE, non qualcosa da ignorare. Ignorarla
/// significherebbe che un comando scritto male fa una cosa diversa da quella
/// che sembra — e su un componente privilegiato «sembra» non basta.
pub fn parse(args: &[String]) -> Result<Command, ParseError> {
    let mut iter = args.iter();
    let verbo = iter.next().ok_or(ParseError::Missing)?;

    match verbo.as_str() {
        "serve" | "service" => Ok(Command::Serve),
        "uninstall" => Ok(Command::Uninstall),
        "status" => Ok(Command::Status),
        "install" => {
            let mut owner_sid = None;
            let mut public_key_hex = None;
            let mut server_key_b64 = None;
            let mut server_url = None;
            let mut error_file = None;
            while let Some(opzione) = iter.next() {
                match opzione.as_str() {
                    "--owner-sid" => {
                        owner_sid = Some(
                            iter.next()
                                .ok_or_else(|| ParseError::MissingValue("--owner-sid".into()))?
                                .clone(),
                        );
                    }
                    "--public-key" => {
                        public_key_hex = Some(
                            iter.next()
                                .ok_or_else(|| ParseError::MissingValue("--public-key".into()))?
                                .clone(),
                        );
                    }
                    "--server-key" => {
                        server_key_b64 = Some(
                            iter.next()
                                .ok_or_else(|| ParseError::MissingValue("--server-key".into()))?
                                .clone(),
                        );
                    }
                    "--error-file" => {
                        error_file = Some(
                            iter.next()
                                .ok_or_else(|| ParseError::MissingValue("--error-file".into()))?
                                .clone(),
                        );
                    }
                    "--server-url" => {
                        server_url = Some(
                            iter.next()
                                .ok_or_else(|| ParseError::MissingValue("--server-url".into()))?
                                .clone(),
                        );
                    }
                    altro => return Err(ParseError::UnknownOption(altro.to_string())),
                }
            }
            Ok(Command::Install {
                owner_sid: owner_sid.ok_or(ParseError::MissingRequired("--owner-sid"))?,
                public_key_hex: public_key_hex
                    .ok_or(ParseError::MissingRequired("--public-key"))?,
                server_key_b64: server_key_b64
                    .ok_or(ParseError::MissingRequired("--server-key"))?,
                server_url: server_url.ok_or(ParseError::MissingRequired("--server-url"))?,
                error_file: error_file.unwrap_or_default(),
            })
        }
        altro => Err(ParseError::UnknownCommand(altro.to_string())),
    }
}

/// Come si usa, in una schermata.
pub fn usage() -> String {
    "metnos-helper — il componente amministrativo di Metnos\n\
\n\
  install --owner-sid <SID> --public-key <chiave> --server-key <chiave> \\\n\
          --server-url <indirizzo>\n\
      Installa e concede il permesso. Si esegue UNA volta sola: da quel\n\
      momento le installazioni di programmi non chiederanno piu' niente.\n\
      Windows chiede la conferma da amministratore.\n\
\n\
  status\n\
      Dice se e' installato e per chi.\n\
\n\
  uninstall\n\
      Toglie tutto. Di norma lo lancia Windows da Impostazioni > App.\n\
\n\
  serve\n\
      Il ciclo del servizio. Lo avvia Windows, non serve lanciarlo a mano.\n"
        .into()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(valori: &[&str]) -> Vec<String> {
        valori.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn i_tre_verbi_semplici_si_leggono() {
        assert_eq!(parse(&a(&["serve"])), Ok(Command::Serve));
        assert_eq!(parse(&a(&["service"])), Ok(Command::Serve));
        assert_eq!(parse(&a(&["uninstall"])), Ok(Command::Uninstall));
        assert_eq!(parse(&a(&["status"])), Ok(Command::Status));
    }

    #[test]
    fn linstallazione_vuole_proprietario_e_due_chiavi() {
        // Tre valori, tre cose diverse: chi possiede l'aiutante, chi puo'
        // chiedergli qualcosa, e da chi accetta di farsi sostituire.
        let c = parse(&a(&[
            "install",
            "--owner-sid",
            "S-1-5-21-1-2-3-1001",
            "--public-key",
            "aabb",
            "--server-key",
            "ssss",
            "--server-url",
            "https://s",
        ]))
        .unwrap();
        assert_eq!(
            c,
            Command::Install {
                owner_sid: "S-1-5-21-1-2-3-1001".into(),
                public_key_hex: "aabb".into(),
                server_key_b64: "ssss".into(),
                server_url: "https://s".into(),
                error_file: String::new(),
            }
        );
    }

    #[test]
    fn senza_la_chiave_del_server_non_si_installa() {
        // Un aiutante senza ancora di fiducia non potrebbe mai aggiornarsi,
        // e resterebbe indietro in silenzio: meglio non nascere.
        assert_eq!(
            parse(&a(&[
                "install",
                "--owner-sid",
                "S-1-5-21-1-2-3-1001",
                "--public-key",
                "aabb",
            ])),
            Err(ParseError::MissingRequired("--server-key"))
        );
    }

    #[test]
    fn lordine_delle_opzioni_non_conta() {
        let a1 = parse(&a(&[
            "install",
            "--owner-sid",
            "S",
            "--public-key",
            "K",
            "--server-key",
            "V",
            "--server-url",
            "U",
        ]));
        let a2 = parse(&a(&[
            "install",
            "--server-url",
            "U",
            "--server-key",
            "V",
            "--public-key",
            "K",
            "--owner-sid",
            "S",
        ]));
        assert_eq!(a1, a2);
    }

    #[test]
    fn senza_proprietario_non_si_installa() {
        assert_eq!(
            parse(&a(&["install", "--public-key", "K"])),
            Err(ParseError::MissingRequired("--owner-sid"))
        );
    }

    #[test]
    fn senza_chiave_non_si_installa() {
        assert_eq!(
            parse(&a(&["install", "--owner-sid", "S"])),
            Err(ParseError::MissingRequired("--public-key"))
        );
    }

    #[test]
    fn unopzione_sconosciuta_e_un_errore_non_qualcosa_da_ignorare() {
        // Ignorarla significherebbe che un comando scritto male fa una cosa
        // diversa da quella che sembra, e su un componente privilegiato
        // «sembra» non basta.
        assert_eq!(
            parse(&a(&[
                "install",
                "--owner-sid",
                "S",
                "--public-key",
                "K",
                "--force"
            ])),
            Err(ParseError::UnknownOption("--force".into()))
        );
    }

    #[test]
    fn unopzione_senza_valore_e_un_errore() {
        assert_eq!(
            parse(&a(&["install", "--owner-sid"])),
            Err(ParseError::MissingValue("--owner-sid".into()))
        );
    }

    #[test]
    fn un_verbo_sconosciuto_non_diventa_un_altro() {
        // Niente abbreviazioni e niente prefissi: «unin» non e' «uninstall».
        for cattivo in ["unin", "exec", "run", "INSTALL", "--help", ""] {
            assert!(
                matches!(parse(&a(&[cattivo])), Err(ParseError::UnknownCommand(_))),
                "accettato il verbo {cattivo:?}"
            );
        }
    }

    #[test]
    fn una_riga_vuota_chiede_un_comando() {
        assert_eq!(parse(&[]), Err(ParseError::Missing));
    }

    #[test]
    fn ogni_errore_si_spiega_in_italiano() {
        for errore in [
            ParseError::Missing,
            ParseError::UnknownCommand("x".into()),
            ParseError::UnknownOption("--x".into()),
            ParseError::MissingValue("--x".into()),
            ParseError::MissingRequired("--x"),
        ] {
            let testo = errore.message();
            assert!(!testo.is_empty());
            assert!(testo.ends_with('.'), "{testo}");
        }
    }

    #[test]
    fn le_istruzioni_dicono_che_si_installa_una_volta_sola() {
        let testo = usage();
        assert!(testo.contains("UNA volta sola"));
        assert!(testo.contains("Impostazioni"));
    }
}
