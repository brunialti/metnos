//! L'aggancio a Windows: copiare, registrare, comparire, togliersi.
//!
//! Applica cio' che `setup` ha deciso. Qui non si prende nessuna decisione:
//! percorsi, argomenti e voci arrivano gia' costruiti da valori noti, e questo
//! modulo si limita a chiamare il sistema operativo.
//!
//! La separazione non e' estetica. `setup` si prova su qualunque macchina, e
//! infatti e' provato; questo file no, perche' tocca il registro di sistema e
//! il gestore dei servizi. Tenere le decisioni fuori di qui significa che la
//! parte non provabile e' la piu' piccola e la piu' stupida possibile.

#![cfg(windows)]

use std::io;
use std::path::Path;
use std::process::Command;

use crate::setup::{
    arp_entries, files_to_remove, install_dir, service_config_argv, service_create_argv,
    service_delete_argv, service_recovery_argv, ARP_KEY, SERVICE_EXISTS,
};

/// Esegue un comando di sistema e restituisce l'esito.
///
/// `argv` arriva da `setup`, costruito da costanti e da percorsi che il
/// sistema operativo ci ha dato. Nessun pezzo viene da una richiesta: questa
/// funzione non ha modo di eseguire qualcosa che non sia stato deciso nel
/// codice.
fn esegui(argv: &[String]) -> io::Result<(i32, String)> {
    let (programma, resto) = argv
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "comando vuoto"))?;
    let esito = Command::new(programma).args(resto).output()?;
    let testo = String::from_utf8_lossy(&esito.stdout).to_string()
        + &String::from_utf8_lossy(&esito.stderr);
    Ok((esito.status.code().unwrap_or(-1), testo))
}

/// Copia l'eseguibile nella cartella d'installazione.
///
/// Sotto Program Files, che un utente senza privilegi non puo' riscrivere.
/// La copia avviene PRIMA di registrare il servizio: registrare un servizio
/// che punta a un file non ancora presente lo lascerebbe rotto al primo avvio.
/// Ferma il servizio, se c'e' e sta girando, e aspetta che abbia mollato il
/// file.
///
/// Un servizio in esecuzione tiene aperto il proprio eseguibile: senza questo,
/// installare sopra un'installazione viva fallisce con «il file e' utilizzato
/// da un altro processo» (errore 32, macchina di Roberto, 19/8/2026). Non e'
/// un errore se il servizio non c'e' o e' gia' fermo: e' il caso normale
/// della prima installazione.
pub fn ferma_servizio_se_gira() {
    if esegui(&crate::setup::service_stop_argv()).is_err() {
        return;
    }
    // Fermarsi non e' istantaneo: `sc stop` chiede, non impone. Si aspetta
    // che il file sia davvero libero, fino a cinque secondi.
    for _ in 0..10 {
        if let Ok((_, stato)) = esegui(&crate::setup::service_query_argv()) {
            if stato.contains("STOPPED") || stato.contains("ARRESTATO") {
                return;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

pub fn installa_eseguibile() -> io::Result<std::path::PathBuf> {
    let sorgente = std::env::current_exe()?;
    let cartella = install_dir();
    std::fs::create_dir_all(&cartella)?;
    let destinazione = cartella.join("metnos-helper.exe");
    // Copiare su se stessi fallirebbe: succede se qualcuno lancia
    // l'installazione dall'eseguibile gia' installato.
    if sorgente != destinazione {
        std::fs::copy(&sorgente, &destinazione)?;
    }
    Ok(destinazione)
}

/// Registra il servizio, avviato da solo come sistema.
pub fn registra_servizio(exe: &Path) -> io::Result<()> {
    let (codice, uscita) = esegui(&service_create_argv(exe))?;
    if codice == SERVICE_EXISTS {
        // Il servizio c'e' gia': resto di un'installazione rimasta a meta'.
        // Non e' un motivo per fermarsi — anzi, fermarsi qui e' proprio cio'
        // che lasciava la macchina in un vicolo cieco, con un servizio che
        // non parte e un'installazione che non puo' ripararlo. Si corregge
        // dove punta e si prosegue.
        let (rc, out) = esegui(&service_config_argv(exe))?;
        if rc != 0 {
            return Err(io::Error::other(format!(
                "il servizio esisteva gia' e non si e' potuto correggere \
(rc={rc}): {}",
                out.trim()
            )));
        }
    } else if codice != 0 {
        return Err(io::Error::other(format!(
            "registrazione del servizio fallita (rc={codice}): {}",
            uscita.trim()
        )));
    }
    // La politica di riavvio serve all'aggiornamento: dopo essersi
    // sostituito il programma esce, e deve tornare su col binario nuovo.
    // Non e' un motivo per fermare l'installazione se non riesce: si
    // otterrebbe un aiutante che non c'e' invece di uno che non si aggiorna
    // da solo.
    match esegui(&service_recovery_argv()) {
        Ok((0, _)) => {}
        Ok((codice, uscita)) => eprintln!(
            "Avvertenza: politica di riavvio non impostata (rc={codice}): {}. \
L'aiutante funziona, ma un aggiornamento richiedera' un riavvio del computer.",
            uscita.trim()
        ),
        Err(e) => eprintln!("Avvertenza: politica di riavvio non impostata: {e}"),
    }
    Ok(())
}

/// Scrive le voci che fanno comparire l'aiutante fra i programmi installati.
///
/// Se questa parte fallisce, l'installazione NON prosegue: un componente
/// privilegiato presente e invisibile e' peggio di uno assente, perche' il
/// proprietario non ha modo di sapere che c'e' ne' di toglierlo.
pub fn registra_fra_i_programmi(exe: &Path, versione: &str) -> io::Result<()> {
    for (nome, valore) in arp_entries(exe, versione) {
        let argv: Vec<String> = vec![
            "reg.exe".into(),
            "add".into(),
            ARP_KEY.into(),
            "/v".into(),
            nome.clone(),
            "/t".into(),
            "REG_SZ".into(),
            "/d".into(),
            valore,
            "/f".into(),
        ];
        let (codice, uscita) = esegui(&argv)?;
        if codice != 0 {
            return Err(io::Error::other(format!(
                "voce «{nome}» non scritta (rc={codice}): {}",
                uscita.trim()
            )));
        }
    }
    Ok(())
}

/// Toglie tutto, nell'ordine in cui va tolto.
///
/// Prima si ferma e si cancella il servizio, poi si cancellano i dati: un
/// servizio ancora vivo con i dati spariti sotto risponderebbe «non appaiato»
/// a ogni richiesta invece di non esserci.
///
/// Ogni passo prosegue anche se il precedente fallisce, e si riporta il primo
/// errore: una disinstallazione che si ferma a meta' lascia il proprietario con
/// un componente privilegiato che non sa piu' come togliere.
pub fn disinstalla(data_dir: &Path) -> Vec<String> {
    let mut problemi = Vec::new();

    for argv in service_delete_argv() {
        match esegui(&argv) {
            // `stop` su un servizio gia' fermo non e' un problema.
            Ok((codice, uscita)) if codice != 0 && argv[1] == "delete" => {
                problemi.push(format!(
                    "servizio non rimosso (rc={codice}): {}",
                    uscita.trim()
                ));
            }
            Err(e) => problemi.push(format!("comando {} fallito: {e}", argv[1])),
            _ => {}
        }
    }

    for percorso in files_to_remove(data_dir) {
        if percorso.exists() {
            if let Err(e) = std::fs::remove_file(&percorso) {
                problemi.push(format!("{} non cancellato: {e}", percorso.display()));
            }
        }
    }

    // Il registro degli eventi NON si cancella: e' la traccia di cio' che e'
    // stato fatto mentre l'aiutante c'era, e chi disinstalla non deve poter
    // far sparire le proprie tracce.

    let argv: Vec<String> = vec![
        "reg.exe".into(),
        "delete".into(),
        ARP_KEY.into(),
        "/f".into(),
    ];
    if let Ok((codice, uscita)) = esegui(&argv) {
        if codice != 0 {
            problemi.push(format!(
                "voce fra i programmi non rimossa (rc={codice}): {}",
                uscita.trim()
            ));
        }
    }

    problemi
}

/// Mette il file nuovo al posto dell'eseguibile in esecuzione.
///
/// Su Windows un eseguibile in esecuzione non si puo' sovrascrivere, ma si
/// puo' RINOMINARE: e' il trucco su cui poggia ogni aggiornamento di un
/// programma vivo, ed e' lo stesso che usa il client.
///
/// Se qualcosa va storto a meta', il vecchio torna al suo posto. Un servizio
/// di sistema senza eseguibile non riparte, e non riparte in un modo che
/// nessuno sa spiegare guardando i registri.
pub fn sostituisci_eseguibile(nuovo: &Path) -> Result<(), &'static str> {
    let corrente = std::env::current_exe().map_err(|_| "current_exe_unknown")?;
    let vecchio = corrente.with_extension("old");
    let _ = std::fs::remove_file(&vecchio);
    std::fs::rename(&corrente, &vecchio).map_err(|_| "rename_failed")?;
    if std::fs::copy(nuovo, &corrente).is_err() {
        // Torna indietro: meglio la versione di prima che nessuna.
        let _ = std::fs::rename(&vecchio, &corrente);
        return Err("copy_failed");
    }
    Ok(())
}

/// Avvia il servizio e aspetta che sia davvero in piedi.
///
/// Non basta chiederne l'avvio: `sc start` torna subito, col servizio ancora
/// in partenza. Chi ha appena installato l'aiutante lo interroga nei secondi
/// successivi, e trovarlo non ancora pronto e' indistinguibile, da fuori, dal
/// non averlo affatto. Si aspetta che risponda «RUNNING», con un tetto: se
/// non parte entro il tetto, lo si dice invece di dichiarare un'installazione
/// riuscita che non serve a niente.
pub fn avvia_servizio() -> io::Result<()> {
    let (codice, uscita) = esegui(&crate::setup::service_start_argv())?;
    // 1056 = «e' gia' in esecuzione»: non e' un errore, e' cio' che volevamo.
    const GIA_IN_ESECUZIONE: i32 = 1056;
    if codice != 0 && codice != GIA_IN_ESECUZIONE {
        return Err(io::Error::other(format!(
            "avvio del servizio fallito (rc={codice}): {}",
            uscita.trim()
        )));
    }
    // Fino a dieci secondi, guardando ogni mezzo.
    for _ in 0..20 {
        if let Ok((0, stato)) = esegui(&crate::setup::service_query_argv()) {
            if stato.contains("RUNNING") || stato.contains("IN ESECUZIONE") {
                return Ok(());
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    Err(io::Error::other(
        "il servizio e' stato registrato ma non e' partito entro dieci secondi",
    ))
}
