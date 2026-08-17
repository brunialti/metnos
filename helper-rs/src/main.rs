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
//! - `pairing` — chi puo' chiedere, e con quale prova.
//! - `audit` — il registro proprio, separato da quello del client.
//! - `service` — il ciclo: autorizza, consuma, esegue, registra.
//! - `setup` — che cosa vuol dire installarsi e togliersi.
//! - `cli` — i tre verbi della riga di comando.
//!
//! I moduli `win_*` sono l'unica parte che non si prova su un'altra macchina.
//! Ricevono valori gia' decisi e si limitano a chiamare Windows: la parte non
//! provabile e' cosi' la piu' piccola e la piu' stupida possibile.

mod audit;
mod channel;
mod cli;
mod journal;
mod pairing;
mod protocol;
mod service;
mod setup;
#[cfg(windows)]
mod win_pipe;
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
        } => installa(&owner_sid, &public_key_hex),
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
            println!("  chiave       : {}…", &p.public_key_hex[..16.min(p.public_key_hex.len())]);
            println!("  consenso dato: {}", p.consented_at);
            println!("  registro     : {}", pairing::data_dir().join("audit.log").display());
            ExitCode::SUCCESS
        }
        None => {
            println!("Non installato: nessun consenso registrato su questa macchina.");
            ExitCode::from(1)
        }
    }
}

#[cfg(windows)]
fn installa(owner_sid: &str, public_key_hex: &str) -> ExitCode {
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
        &percorso_appaiamento,
        adesso,
    ) {
        Ok(p) => p,
        Err(e) => {
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

    let eseguibile = match win_setup::installa_eseguibile() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Copia dell'eseguibile fallita: {e}");
            return ExitCode::from(3);
        }
    };
    if let Err(e) = win_setup::registra_servizio(&eseguibile) {
        eprintln!("{e}");
        return ExitCode::from(3);
    }
    // Un componente privilegiato presente e INVISIBILE e' peggio di uno
    // assente: il proprietario non saprebbe che c'e' ne' come toglierlo.
    if let Err(e) = win_setup::registra_fra_i_programmi(&eseguibile, env!("CARGO_PKG_VERSION")) {
        eprintln!("{e}");
        eprintln!("Annullo: un aiutante che non compare fra i programmi non si puo' togliere.");
        win_setup::disinstalla(&pairing::data_dir());
        return ExitCode::from(3);
    }
    if let Err(e) = appaiamento.save(&percorso_appaiamento) {
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
    println!("Fatto. Le installazioni successive non chiederanno piu' questo permesso.");
    ExitCode::SUCCESS
}

#[cfg(windows)]
fn disinstalla() -> ExitCode {
    let problemi = win_setup::disinstalla(&pairing::data_dir());
    if problemi.is_empty() {
        println!("Rimosso. Il registro di cio' che e' stato fatto resta in {}.",
                 pairing::data_dir().join("audit.log").display());
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
    eprintln!("Il ciclo del servizio non e' ancora implementato (ADR 0210).");
    ExitCode::from(2)
}

// ── Fuori Windows: l'aiutante non ha senso, e lo dice ────────────────
//
// Compilarlo su Linux serve a provare la logica, non a farlo girare. Un
// binario che finge di installarsi dove non puo' e' un binario che mente.

#[cfg(not(windows))]
fn installa(_owner_sid: &str, _public_key_hex: &str) -> ExitCode {
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
