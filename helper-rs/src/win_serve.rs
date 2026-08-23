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
use crate::protocol::{Action, Response, WireRequest};
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
        if let Err(e) = servi_una_richiesta(&pipe, &registro, &appaiamento) {
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
fn servi_una_richiesta(
    pipe: &win_pipe::Handle,
    registro: &std::path::Path,
    appaiamento: &pairing::Pairing,
) -> io::Result<()> {
    // Chi ha chiamato lo dice il sistema operativo, non il messaggio. Si
    // chiede PRIMA di leggere: se non e' il proprietario, il contenuto non
    // interessa.
    let chiamante = win_pipe::caller_sid(pipe)?;

    let corpo = leggi_richiesta(pipe)?;
    let risposta = match serde_json::from_slice::<WireRequest>(&corpo) {
        Ok(richiesta) => service::handle(
            &richiesta,
            &chiamante,
            &pairing::pairing_path(),
            &pairing::journal_path(),
            registro,
            |action| execute_action(action, appaiamento),
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

    let testo =
        serde_json::to_vec(&risposta).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
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
/// Dove sta davvero `winget` per un processo di sistema.
///
/// `winget` non e' un programma nel percorso di ricerca: e' un alias
/// d'esecuzione installato PER UTENTE sotto `WindowsApps`. Un servizio che
/// gira come sistema non ce l'ha, e lanciarlo per nome fallisce con «programma
/// non trovato» — che sembra un guasto della macchina e invece e' un guasto di
/// prospettiva (macchina di Roberto, 19/8/2026: l'aiutante funzionava, ma non
/// trovava il gestore).
///
/// Il programma vero vive nella cartella del pacchetto DesktopAppInstaller,
/// leggibile da chiunque. Fra piu' versioni si prende l'ultima in ordine di
/// nome, che e' l'ordine delle versioni.
fn percorso_del_gestore(nome: &str) -> String {
    if !nome.eq_ignore_ascii_case("winget.exe") {
        return nome.to_string();
    }
    let radice = std::env::var("ProgramFiles")
        .map(|p| std::path::PathBuf::from(p).join("WindowsApps"))
        .unwrap_or_else(|_| std::path::PathBuf::from(r"C:\Program Files\WindowsApps"));
    let Ok(voci) = std::fs::read_dir(&radice) else {
        return nome.to_string();
    };
    let mut candidati: Vec<std::path::PathBuf> = voci
        .filter_map(|v| v.ok())
        .map(|v| v.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| {
                    n.starts_with("Microsoft.DesktopAppInstaller_")
                        && n.ends_with("__8wekyb3d8bbwe")
                })
                .unwrap_or(false)
        })
        .map(|p| p.join("winget.exe"))
        .filter(|p| p.is_file())
        .collect();
    candidati.sort();
    match candidati.pop() {
        Some(p) => p.display().to_string(),
        // Non trovato: si prova comunque per nome. Su una macchina dove
        // l'alias c'e' funziona, e il messaggio d'errore resta quello vero.
        None => nome.to_string(),
    }
}

fn execute_action(action: &Action, appaiamento: &pairing::Pairing) -> service::Outcome {
    match action {
        Action::PackageCommand(argv) => execute_package_command(argv),
        Action::HelperUpdateCheck => applica_aggiornamento(appaiamento),
        Action::ManagedStart {
            package_id,
            lifetime,
        } => crate::win_activation::start(package_id, *lifetime),
        Action::ManagedStop {
            package_id,
            pid,
            creation_time,
        } => crate::win_activation::stop(package_id, *pid, *creation_time),
        Action::ManagedProvider {
            package_id,
            interface,
            assembly,
            entry_type,
            domains,
            sensor_types,
        } => crate::win_provider::read(
            package_id,
            *interface,
            assembly,
            entry_type,
            domains,
            sensor_types,
        ),
    }
}

fn execute_package_command(argv: &[String]) -> service::Outcome {
    let Some((programma, resto)) = argv.split_first() else {
        return service::Outcome::failure("package_operation_failed", "empty package action");
    };
    let programma = percorso_del_gestore(programma);
    match std::process::Command::new(&programma)
        .args(resto)
        // Nessuno guarda questo terminale: un gestore che si ferma a chiedere
        // resterebbe li' per sempre.
        .stdin(std::process::Stdio::null())
        .output()
    {
        Ok(esito) => {
            let testo = String::from_utf8_lossy(&esito.stdout).to_string()
                + &String::from_utf8_lossy(&esito.stderr);
            (esito.status.code(), testo).into()
        }
        Err(e) => (None, format!("spawn_failed: {e}")).into(),
    }
}

/// Check the signed release only when an aligned client asks to use the
/// helper. A local version handshake happens on every use; network and
/// download work happens only when the installed helper is behind.
fn applica_aggiornamento(appaiamento: &pairing::Pairing) -> service::Outcome {
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
        Outcome::NotNewer => service::Outcome::success("helper_current"),
        Outcome::Refused(motivo) => {
            let _ = crate::audit::record(
                &registro,
                crate::audit::Event::Refused,
                "-",
                &format!("update_refused: {motivo}"),
            );
            service::Outcome::failure("helper_update_refused", motivo)
        }
        Outcome::Applied(versione) => {
            let _ = crate::audit::record(
                &registro,
                crate::audit::Event::Executed,
                "-",
                &format!("updated_to {versione}"),
            );
            // Il programma nuovo e' al suo posto, ma in memoria gira ancora
            // il vecchio. Si esce con un codice diverso da zero perche'
            // Windows lo rimetta in piedi (politica impostata
            // all'installazione): uscire con zero sarebbe una fine regolare,
            // e nessuno riavvierebbe niente.
            std::process::exit(3);
        }
    }
}
