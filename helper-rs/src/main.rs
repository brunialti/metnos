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
//!
//! Cio' che tocca Windows (la pipe con ACL, la verifica del chiamante) vive
//! separato e riceve da qui soltanto valori gia' verificati.

mod channel;
mod journal;
mod protocol;
#[cfg(windows)]
mod win_pipe;

fn main() {
    eprintln!(
        "metnos-helper {}: componente elevato, non ancora installabile.",
        env!("CARGO_PKG_VERSION")
    );
    eprintln!("Il servizio e il canale locale sono in costruzione (ADR 0210).");
    std::process::exit(2);
}
