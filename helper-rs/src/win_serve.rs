//! Il ciclo del servizio: sta in ascolto, e per ogni chiamante decide una volta.
//!
//! Struttura volutamente noiosa. Una richiesta per connessione, nessuna coda,
//! nessuna concorrenza: due richieste che si sovrappongono su un componente
//! che modifica il sistema sono due modi di lasciarlo a meta', e il guadagno
//! sarebbe nullo perche' un'installazione dura secondi e non arrivano a
//! raffica.
//!
//! Il ciclo non decide niente: legge, passa a `service::handle`, risponde.
//! Tutto cio' che conta — chi puo', con quale prova, che cosa si esegue — sta
//! nei moduli che si provano su qualunque macchina.

#![cfg(windows)]

use std::io::{self, Read, Write};

use crate::pairing;
use crate::protocol::{Request, Response};
use crate::service;
use crate::win_pipe;

/// Quanto puo' essere grande una richiesta.
///
/// Un tetto esiste perche' senza, chi puo' aprire la pipe puo' far crescere
/// la memoria del processo di sistema scrivendo e basta. Una richiesta vera
/// sta in poche centinaia di byte.
const MAX_REQUEST_BYTES: usize = 8 * 1024;

/// Il ciclo, finche' il servizio vive.
///
/// Ogni giro ricrea la pipe. Costa poco e chiude un caso: una connessione
/// caduta a meta' non lascia il canale in uno stato che il chiamante
/// successivo eredita.
pub fn run() -> io::Result<()> {
    let appaiamento = pairing::Pairing::load(&pairing::pairing_path()).ok_or_else(|| {
        // Un servizio che gira senza consenso registrato non deve mettersi in
        // ascolto: non avrebbe nessuno da servire, e una pipe aperta senza
        // proprietario e' solo superficie.
        io::Error::other("nessun appaiamento: il servizio non si avvia")
    })?;

    let nome = channel_name(&appaiamento.owner_sid)?;
    let registro = pairing::data_dir().join("audit.log");

    loop {
        let pipe = win_pipe::create_owner_only_pipe(&nome, &appaiamento.owner_sid)?;
        if let Err(e) = win_pipe::wait_for_client(&pipe) {
            let _ = crate::audit::record(
                &registro,
                crate::audit::Event::Refused,
                "-",
                &format!("connect_failed: {e}"),
            );
            continue;
        }
        // Un errore su una connessione non ferma il servizio: il chiamante
        // successivo non c'entra niente con quello che ha sbagliato prima.
        if let Err(e) = servi_una_richiesta(&pipe, &registro) {
            let _ = crate::audit::record(
                &registro,
                crate::audit::Event::Refused,
                "-",
                &format!("connection_error: {e}"),
            );
        }
    }
}

fn channel_name(owner_sid: &str) -> io::Result<String> {
    crate::channel::pipe_name_for_owner(owner_sid)
        .map_err(|_| io::Error::other("SID del proprietario non valido"))
}

/// Una connessione, una richiesta, una risposta.
fn servi_una_richiesta(pipe: &win_pipe::Handle, registro: &std::path::Path) -> io::Result<()> {
    // Chi ha chiamato lo dice il sistema operativo, non il messaggio. Si
    // chiede PRIMA di leggere: se non e' il proprietario, il contenuto non
    // interessa.
    let chiamante = win_pipe::caller_sid(pipe)?;

    let corpo = leggi_richiesta(pipe)?;
    let risposta = match serde_json::from_slice::<Request>(&corpo) {
        Ok(richiesta) => service::handle(
            &richiesta,
            &chiamante,
            &pairing::pairing_path(),
            &pairing::journal_path(),
            registro,
            esegui_comando,
        ),
        Err(_) => {
            // Un corpo che non e' una richiesta non e' un caso da spiegare in
            // dettaglio: dirlo con precisione aiuterebbe chi sta provando
            // forme diverse.
            let _ = crate::audit::record(
                registro,
                crate::audit::Event::Refused,
                "-",
                "malformed_request",
            );
            Response {
                ok: false,
                error_code: Some("malformed_request".into()),
                exit_code: None,
                detail: String::new(),
            }
        }
    };

    let testo = serde_json::to_vec(&risposta)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    scrivi(pipe, &testo)
}

/// Legge fino al tetto, e non oltre.
fn leggi_richiesta(pipe: &win_pipe::Handle) -> io::Result<Vec<u8>> {
    let lettore = pipe.reader();
    let mut buffer = Vec::new();
    // `take` sul lettore, non un `by_ref`: qui il tipo implementa sia `Read`
    // sia `Write`, e `by_ref` sarebbe ambiguo fra i due.
    Read::take(lettore, MAX_REQUEST_BYTES as u64).read_to_end(&mut buffer)?;
    Ok(buffer)
}

fn scrivi(pipe: &win_pipe::Handle, dati: &[u8]) -> io::Result<()> {
    let mut scrittore = pipe.writer();
    scrittore.write_all(dati)?;
    scrittore.flush()
}

/// Lancia il gestore di pacchetti con la riga costruita dal protocollo.
///
/// `argv` non e' influenzabile da chi chiama: viene da `Request::argv`, che
/// costruisce ogni pezzo da valori validati. Qui si lancia e basta.
fn esegui_comando(argv: &[String]) -> service::Outcome {
    let Some((programma, resto)) = argv.split_first() else {
        return (None, String::new());
    };
    match std::process::Command::new(programma)
        .args(resto)
        // Nessuno guarda questo terminale: un gestore che si ferma a chiedere
        // resterebbe li' per sempre.
        .stdin(std::process::Stdio::null())
        .output()
    {
        Ok(esito) => {
            let testo = String::from_utf8_lossy(&esito.stdout).to_string()
                + &String::from_utf8_lossy(&esito.stderr);
            (esito.status.code(), testo)
        }
        Err(e) => (None, format!("spawn_failed: {e}")),
    }
}
