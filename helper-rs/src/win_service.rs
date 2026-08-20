//! Presentarsi al gestore dei servizi di Windows.
//!
//! Un programma registrato come servizio non e' un programma normale: il
//! gestore lo lancia e poi **aspetta che sia lui a farsi vivo**. Chi non lo fa
//! viene dichiarato caduto dopo trenta secondi, con l'errore 1053 — «il
//! servizio non ha risposto alla richiesta di avvio nel tempo previsto» — e
//! non parte mai.
//!
//! E' quello che succedeva qui: il ciclo c'era, il resto no. L'aiutante non
//! poteva rispondere a nessuno, e ogni altra cosa costruita sopra — il canale,
//! l'appaiamento, l'installazione — era corretta e inutile. Trovato sulla
//! macchina di Roberto il 19/8/2026, dopo che la catena diagnostica ha
//! finalmente portato il motivo fino alla chat.
//!
//! ## La sequenza, e perche' e' obbligata
//!
//! 1. `StartServiceCtrlDispatcherW` con la tabella nome→`ServiceMain`. Non
//!    torna finche' il servizio non finisce: e' lei a tenere il filo
//!    principale.
//! 2. `RegisterServiceCtrlHandlerW`: da' il riferimento con cui riferire lo
//!    stato, e riceve i comandi (arresto, spegnimento).
//! 3. `SetServiceStatus(SERVICE_RUNNING)` — **questo e' il «eccomi»**.
//! 4. Il ciclo di lavoro.
//! 5. All'arresto, `SERVICE_STOPPED` prima di uscire: altrimenti il gestore lo
//!    considera caduto e applica la politica di riavvio a un servizio che si
//!    stava fermando apposta.
//!
//! ## Fermarsi
//!
//! Il ciclo sta fermo su una pipe ad aspettare un client, e puo' restarci
//! giorni. Interromperlo dall'esterno vorrebbe dire I/O asincrono su tutto il
//! canale. Per un componente che non tiene stato in memoria — il registro
//! delle chiavi consumate si scrive subito, non a fine sessione — la via
//! semplice e corretta e' dichiarare l'arresto e terminare: non c'e' niente da
//! salvare, e il gestore vede un servizio che si e' fermato quando glielo si
//! era chiesto.

use std::sync::atomic::{AtomicUsize, Ordering};

use windows_sys::Win32::Foundation::{ERROR_FAILED_SERVICE_CONTROLLER_CONNECT, NO_ERROR};
use windows_sys::Win32::System::Services::{
    RegisterServiceCtrlHandlerW, SetServiceStatus, StartServiceCtrlDispatcherW,
    SERVICE_ACCEPT_SHUTDOWN, SERVICE_ACCEPT_STOP, SERVICE_CONTROL_SHUTDOWN, SERVICE_CONTROL_STOP,
    SERVICE_RUNNING, SERVICE_START_PENDING, SERVICE_STATUS, SERVICE_STATUS_HANDLE, SERVICE_STOPPED,
    SERVICE_STOP_PENDING, SERVICE_TABLE_ENTRYW, SERVICE_WIN32_OWN_PROCESS,
};

use crate::win_pipe::wide;

/// Il riferimento con cui si riferisce lo stato, condiviso col gestore dei
/// controlli. Un intero perche' deve essere raggiungibile da una funzione di
/// richiamo che non puo' portarsi dietro niente.
static STATO: AtomicUsize = AtomicUsize::new(0);

fn riferisci(stato: u32, codice_uscita: u32) {
    let handle = STATO.load(Ordering::SeqCst) as SERVICE_STATUS_HANDLE;
    if handle.is_null() {
        return;
    }
    let mut s: SERVICE_STATUS = unsafe { std::mem::zeroed() };
    s.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    s.dwCurrentState = stato;
    // Si accettano arresto e spegnimento solo quando si e' in piedi: dichiarare
    // di accettarli mentre si sta partendo invita il gestore a mandarli prima
    // che ci sia qualcuno a riceverli.
    s.dwControlsAccepted = if stato == SERVICE_RUNNING {
        SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
    } else {
        0
    };
    s.dwWin32ExitCode = codice_uscita;
    // Quanto tempo chiediamo prima di essere richiamati: vale solo negli stati
    // «in corso».
    s.dwWaitHint = if stato == SERVICE_START_PENDING {
        10_000
    } else {
        0
    };
    unsafe { SetServiceStatus(handle, &s) };
}

/// I comandi che arrivano dal gestore.
unsafe extern "system" fn gestore_controlli(comando: u32) {
    match comando {
        SERVICE_CONTROL_STOP | SERVICE_CONTROL_SHUTDOWN => {
            // Prima si dichiara che ci si sta fermando, poi si finisce. Uscire
            // senza dirlo farebbe considerare il servizio caduto, e la
            // politica di riavvio lo rimetterebbe in piedi subito dopo averlo
            // fermato apposta.
            riferisci(SERVICE_STOP_PENDING, NO_ERROR);
            riferisci(SERVICE_STOPPED, NO_ERROR);
            std::process::exit(0);
        }
        _ => {}
    }
}

/// Il punto in cui il gestore ci porta.
unsafe extern "system" fn service_main(_argc: u32, _argv: *mut *mut u16) {
    let nome = wide(crate::setup::SERVICE_NAME);
    let handle = RegisterServiceCtrlHandlerW(nome.as_ptr(), Some(gestore_controlli));
    if handle.is_null() {
        // Senza riferimento non si puo' dire niente al gestore: uscire e'
        // l'unica cosa onesta.
        return;
    }
    STATO.store(handle as usize, Ordering::SeqCst);

    riferisci(SERVICE_START_PENDING, NO_ERROR);
    riferisci(SERVICE_RUNNING, NO_ERROR);

    // Da qui in poi e' il lavoro di sempre. Se il ciclo torna con un errore, lo
    // si riferisce: un servizio che sparisce senza dire perche' lascia solo
    // «terminato inaspettatamente» nei registri di Windows.
    let uscita = match crate::win_serve::run() {
        Ok(()) => NO_ERROR,
        Err(e) => e.raw_os_error().unwrap_or(1) as u32,
    };
    riferisci(SERVICE_STOPPED, uscita);
}

/// Avvia il servizio parlando col gestore.
///
/// Ritorna `Ok(false)` quando NON siamo stati lanciati dal gestore: non e' un
/// guasto, e' qualcuno che ha eseguito il programma a mano per capirci
/// qualcosa. Trattarlo come errore renderebbe impossibile provarlo; il
/// chiamante gira il ciclo direttamente.
pub fn esegui_come_servizio() -> std::io::Result<bool> {
    let mut nome = wide(crate::setup::SERVICE_NAME);
    let tabella = [
        SERVICE_TABLE_ENTRYW {
            lpServiceName: nome.as_mut_ptr(),
            lpServiceProc: Some(service_main),
        },
        // La tabella finisce con una riga di zeri: e' cosi' che il gestore sa
        // dove fermarsi.
        SERVICE_TABLE_ENTRYW {
            lpServiceName: std::ptr::null_mut(),
            lpServiceProc: None,
        },
    ];
    let ok = unsafe { StartServiceCtrlDispatcherW(tabella.as_ptr()) };
    if ok != 0 {
        return Ok(true);
    }
    let errore = std::io::Error::last_os_error();
    if errore.raw_os_error() == Some(ERROR_FAILED_SERVICE_CONTROLLER_CONNECT as i32) {
        return Ok(false);
    }
    Err(errore)
}
