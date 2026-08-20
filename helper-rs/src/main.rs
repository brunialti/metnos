//! metnos-helper — l'aiutante elevato di Metnos su Windows (ADR 0210).
//!
//! Il software piu' privilegiato che Metnos installa su una macchina altrui.
//! Fa tre cose, e nessuna e' «esegui questo»: dice se un pacchetto e'
//! installato, lo installa, lo rimuove.
//!
//! Struttura, e il perche' di ognuna:
//!
//! - `protocol` — il vocabolario chiuso e la sua validazione. Logica pura,
//!   nessuna API di sistema, quindi si prova ovunque. E' il modulo su cui
//!   poggia tutto il resto.
//! - `journal` — le chiavi gia' consumate, perche' una richiesta catturata
//!   non si possa rigiocare.
//! - `channel` — come si chiama il canale locale e chi puo' parlarci. La
//!   parte che si prova ovunque; quella che apre la pipe sta sotto
//!   `cfg(windows)` e riceve da qui un nome gia' verificato.
//! - `frame` — dove finisce un messaggio. Copia byte-identica nel client:
//!   e' l'unica cosa che i due programmi devono sapere allo stesso modo
//!   per non aspettarsi a vicenda.
//! - `pairing` — chi puo' chiedere, e con quale prova.
//! - `audit` — il registro proprio, separato da quello del client.
//! - `service` — il ciclo: autorizza, consuma, esegue, registra.
//! - `setup` — che cosa vuol dire installarsi e togliersi.
//! - `cli` — i tre verbi della riga di comando.
//!
//! I moduli `win_*` sono l'unica parte che non si prova su un'altra macchina.
//! Ricevono valori gia' decisi e si limitano a chiamare Windows: la parte non
//! provabile e' cosi' la piu' piccola e la piu' stupida possibile.

mod activation;
mod audit;
mod channel;
mod cli;
mod frame;
mod journal;
mod pairing;
mod protocol;
mod provider;
mod selfupdate;
mod service;
mod setup;
#[cfg(windows)]
mod win_activation;
#[cfg(windows)]
mod win_pipe;
#[cfg(windows)]
mod win_provider;
#[cfg(windows)]
mod win_serve;
#[cfg(windows)]
mod win_service;
#[cfg(windows)]
mod win_setup;

use std::process::ExitCode;

fn main() -> ExitCode {
    let argomenti: Vec<String> = std::env::args().skip(1).collect();
    let comando = match cli::parse(&argomenti) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{}\n", e.message());
            eprint!("{}", cli::usage());
            return ExitCode::from(2);
        }
    };

    match comando {
        cli::Command::Status => stato(),
        cli::Command::Install {
            owner_sid,
            public_key_hex,
            server_key_b64,
            server_url,
            error_file,
        } => installa(
            &owner_sid,
            &public_key_hex,
            &server_key_b64,
            &server_url,
            &error_file,
        ),
        cli::Command::Uninstall => disinstalla(),
        cli::Command::Serve => servi(),
    }
}

/// Che cosa c'e' installato e per chi.
///
/// Una domanda che il proprietario deve poter fare senza aprire file di
/// configurazione: un componente privilegiato di cui non si sa dire lo stato
/// e' un componente di cui non si sa dire se e' ancora quello che si era
/// autorizzato.
fn stato() -> ExitCode {
    match pairing::Pairing::load(&pairing::pairing_path()) {
        Some(p) => {
            println!("Installato e appaiato.");
            println!("  proprietario : {}", p.owner_sid);
            println!(
                "  chiave       : {}…",
                &p.public_key_hex[..16.min(p.public_key_hex.len())]
            );
            println!("  consenso dato: {}", p.consented_at);
            println!(
                "  registro     : {}",
                pairing::data_dir().join("audit.log").display()
            );
            ExitCode::SUCCESS
        }
        None => {
            println!("Non installato: nessun consenso registrato su questa macchina.");
            ExitCode::from(1)
        }
    }
}

/// Scrive il motivo del fallimento dove chi ci ha lanciato potra' leggerlo.
///
/// Stampare non basta: l'elevazione passa da Windows, che non gira l'uscita a
/// nessuno. Chi ha premuto il bottone riceverebbe un numero, e un numero non
/// dice quale passo e' andato storto. Best-effort: se il file non si scrive,
/// l'installazione fallisce comunque per la sua ragione, non per questa.
#[cfg(windows)]
fn annota_motivo(error_file: &str, motivo: &str) {
    if !error_file.is_empty() {
        let _ = std::fs::write(error_file, motivo);
    }
}

#[cfg(windows)]
fn installa(
    owner_sid: &str,
    public_key_hex: &str,
    server_key_b64: &str,
    server_url: &str,
    error_file: &str,
) -> ExitCode {
    let percorso_appaiamento = pairing::pairing_path();
    let adesso = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // Prima si decide, poi si tocca il sistema: un appaiamento rifiutato non
    // deve lasciare un servizio registrato a meta'.
    let appaiamento = match setup::prepare_pairing(
        owner_sid,
        public_key_hex,
        server_key_b64,
        server_url,
        &percorso_appaiamento,
        adesso,
    ) {
        Ok(p) => p,
        Err(e) => {
            annota_motivo(error_file, &format!("appaiamento rifiutato: {}", e.code()));
            eprintln!("Installazione non eseguita: {}", e.code());
            if e == setup::SetupRefusal::AlreadyPaired {
                eprintln!(
                    "Questo computer ha gia' un aiutante appaiato. Per cambiare \
proprietario si disinstalla e si reinstalla, cosi' il passaggio e' esplicito."
                );
            }
            return ExitCode::from(2);
        }
    };

    println!("{}\n", setup::consent_text(owner_sid));

    // Se c'e' gia' un aiutante in esecuzione, tiene aperto il proprio file e
    // Windows non lo lascia sostituire. Installare sopra un'installazione viva
    // e' il caso normale — un aggiornamento, o un secondo tentativo.
    win_setup::ferma_servizio_se_gira();
    let eseguibile = match win_setup::installa_eseguibile() {
        Ok(p) => p,
        Err(e) => {
            annota_motivo(error_file, &format!("copia dell'eseguibile fallita: {e}"));
            eprintln!("Copia dell'eseguibile fallita: {e}");
            return ExitCode::from(3);
        }
    };
    if let Err(e) = win_setup::registra_servizio(&eseguibile) {
        annota_motivo(error_file, &format!("registrazione del servizio: {e}"));
        eprintln!("{e}");
        return ExitCode::from(3);
    }
    // Un componente privilegiato presente e INVISIBILE e' peggio di uno
    // assente: il proprietario non saprebbe che c'e' ne' come toglierlo.
    if let Err(e) = win_setup::registra_fra_i_programmi(&eseguibile, env!("CARGO_PKG_VERSION")) {
        annota_motivo(
            error_file,
            &format!("registrazione fra i programmi installati: {e}"),
        );
        eprintln!("{e}");
        eprintln!("Annullo: un aiutante che non compare fra i programmi non si puo' togliere.");
        win_setup::disinstalla(&pairing::data_dir());
        return ExitCode::from(3);
    }
    if let Err(e) = appaiamento.save(&percorso_appaiamento) {
        annota_motivo(error_file, &format!("appaiamento non scritto: {e}"));
        eprintln!("Appaiamento non scritto: {e}");
        win_setup::disinstalla(&pairing::data_dir());
        return ExitCode::from(3);
    }
    let _ = audit::record(
        &pairing::data_dir().join("audit.log"),
        audit::Event::Paired,
        "-",
        owner_sid,
    );
    // Adesso, non al prossimo riavvio. Chi ha appena chiesto l'installazione
    // interroga l'aiutante nei secondi successivi: un servizio registrato ma
    // spento e', da fuori, indistinguibile da un aiutante che non c'e'.
    //
    // Se non parte NON si annulla niente: e' installato e appaiato
    // correttamente, e partira' al riavvio. Si dice com'e' andata — meglio un
    // «registrato ma non ancora in piedi» che un'installazione dichiarata
    // riuscita e poi inservibile.
    if let Err(e) = win_setup::avvia_servizio() {
        annota_motivo(error_file, &format!("{e}"));
        eprintln!("{e}");
        eprintln!(
            "L'aiutante e' installato e autorizzato: partira' al prossimo \
riavvio del computer."
        );
        return ExitCode::from(4);
    }
    println!("Fatto. Le installazioni successive non chiederanno piu' questo permesso.");
    ExitCode::SUCCESS
}

#[cfg(windows)]
fn disinstalla() -> ExitCode {
    let problemi = win_setup::disinstalla(&pairing::data_dir());
    if problemi.is_empty() {
        println!(
            "Rimosso. Il registro di cio' che e' stato fatto resta in {}.",
            pairing::data_dir().join("audit.log").display()
        );
        return ExitCode::SUCCESS;
    }
    // Si dice tutto cio' che non e' andato: una rimozione parziale taciuta
    // lascerebbe il proprietario convinto di aver tolto un privilegio che
    // invece e' ancora li'.
    eprintln!("Rimozione incompleta:");
    for p in &problemi {
        eprintln!("  - {p}");
    }
    ExitCode::from(3)
}

#[cfg(windows)]
fn servi() -> ExitCode {
    // Prima si prova a presentarsi al gestore dei servizi: e' cosi' che
    // Windows si aspetta di essere trattato quando e' lui a lanciarci, e non
    // farlo significa essere dichiarati caduti dopo trenta secondi (1053) —
    // il difetto per cui questo aiutante non e' mai partito.
    match win_service::esegui_come_servizio() {
        // Il gestore ci ha portati fin dentro il lavoro e ne siamo usciti.
        Ok(true) => ExitCode::SUCCESS,
        // Non ci ha lanciati il gestore: qualcuno sta eseguendo il programma a
        // mano per capirci qualcosa. Si gira il ciclo direttamente, che e'
        // esattamente cio' che serve in quel caso.
        Ok(false) => match win_serve::run() {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("Il servizio si e' fermato: {e}");
                ExitCode::from(3)
            }
        },
        Err(e) => {
            eprintln!("Non riesco a presentarmi al gestore dei servizi: {e}");
            ExitCode::from(3)
        }
    }
}

// ── Fuori Windows: l'aiutante non ha senso, e lo dice ────────────────
//
// Compilarlo su Linux serve a provare la logica, non a farlo girare. Un
// binario che finge di installarsi dove non puo' e' un binario che mente.

#[cfg(not(windows))]
fn installa(
    _owner_sid: &str,
    _public_key_hex: &str,
    _server_key_b64: &str,
    _server_url: &str,
    _error_file: &str,
) -> ExitCode {
    non_su_questa_piattaforma()
}

#[cfg(not(windows))]
fn disinstalla() -> ExitCode {
    non_su_questa_piattaforma()
}

#[cfg(not(windows))]
fn servi() -> ExitCode {
    non_su_questa_piattaforma()
}

#[cfg(not(windows))]
fn non_su_questa_piattaforma() -> ExitCode {
    eprintln!(
        "metnos-helper esiste per Windows: e' li' che il componente Metnos \
gira senza privilegi e ha bisogno di un aiutante."
    );
    eprintln!("Su Linux i privilegi passano da `sudoer` (ADR 0070).");
    ExitCode::from(2)
}
