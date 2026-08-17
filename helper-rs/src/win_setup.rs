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
    arp_entries, files_to_remove, install_dir, service_create_argv, service_delete_argv, ARP_KEY,
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
    if codice != 0 {
        return Err(io::Error::other(format!(
            "registrazione del servizio fallita (rc={codice}): {}",
            uscita.trim()
        )));
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
                problemi.push(format!("servizio non rimosso (rc={codice}): {}", uscita.trim()));
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
