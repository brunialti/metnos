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

use std::io::{self, Write};

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

    avvia_guardia_aggiornamenti(appaiamento.clone());

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
                error_code: Some("malformed_request".into()),
                ..Response::stamped()
            }
        }
    };

    let testo = serde_json::to_vec(&risposta)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    scrivi(pipe, &testo)
}

/// Legge una richiesta, fino al tetto e non oltre.
///
/// Si legge fino al delimitatore e non fino a fine-flusso: il canale e'
/// bidirezionale, nessuno dei due capi lo chiude, e aspettare la chiusura
/// vorrebbe dire aspettare un client che a sua volta aspetta la risposta.
fn leggi_richiesta(pipe: &win_pipe::Handle) -> io::Result<Vec<u8>> {
    crate::frame::read_frame(pipe.reader(), MAX_REQUEST_BYTES)
}

fn scrivi(pipe: &win_pipe::Handle, dati: &[u8]) -> io::Result<()> {
    let mut scrittore = pipe.writer();
    scrittore.write_all(&crate::frame::framed(dati))?;
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

/// Ogni tanto, chiede al server se c'e' una versione piu' recente.
///
/// Un filo a parte perche' il ciclo principale sta fermo ad aspettare un
/// client, e potrebbe aspettare per giorni: legare l'aggiornamento alle
/// richieste vorrebbe dire che un aiutante poco usato non si aggiorna mai —
/// cioe' proprio quello che resta indietro senza che nessuno se ne accorga.
///
/// Chiede al server per conto suo, come fa il client: una sola regola per
/// tutti i pezzi, e nessun pezzo che dipende da un altro per restare al
/// passo.
fn avvia_guardia_aggiornamenti(appaiamento: pairing::Pairing) {
    // Un controllo all'avvio e poi ogni dieci minuti. All'avvio perche' un
    // aggiornamento lasciato mentre il servizio era fermo deve valere lo
    // stesso.
    const INTERVALLO: std::time::Duration = std::time::Duration::from_secs(600);
    std::thread::spawn(move || loop {
        applica_aggiornamento(&appaiamento);
        std::thread::sleep(INTERVALLO);
    });
}

fn applica_aggiornamento(appaiamento: &pairing::Pairing) {
    use crate::selfupdate::Outcome;
    let registro = pairing::data_dir().join("audit.log");
    let esito = crate::selfupdate::check_and_apply(
        &appaiamento.server_url,
        &appaiamento.server_public_key_b64,
        env!("CARGO_PKG_VERSION"),
        crate::selfupdate::TARGET_TRIPLE,
        &pairing::download_path(),
        crate::selfupdate::fetch_descriptor,
        crate::selfupdate::fetch_artifact,
        crate::win_setup::sostituisci_eseguibile,
    );
    match esito {
        // Il caso normale: non si registra niente, o il registro diventerebbe
        // una riga ogni dieci minuti e nessuno lo leggerebbe piu'.
        Outcome::NotNewer => {}
        Outcome::Refused(motivo) => {
            let _ = crate::audit::record(
                &registro, crate::audit::Event::Refused, "-",
                &format!("update_refused: {motivo}"));
        }
        Outcome::Applied(versione) => {
            let _ = crate::audit::record(
                &registro, crate::audit::Event::Executed, "-",
                &format!("updated_to {versione}"));
            // Il programma nuovo e' al suo posto, ma in memoria gira ancora
            // il vecchio. Si esce con un codice diverso da zero perche'
            // Windows lo rimetta in piedi (politica impostata
            // all'installazione): uscire con zero sarebbe una fine regolare,
            // e nessuno riavvierebbe niente.
            std::process::exit(3);
        }
    }
}
