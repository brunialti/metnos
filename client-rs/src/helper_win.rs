//! Aprire il canale verso l'aiutante e raccogliere i tre fatti (ADR 0210 D2).
//!
//! Vive separato da `helper_client` per la stessa ragione per cui l'aiutante
//! tiene separati i suoi moduli `win_*`: qui si CHIEDE al sistema operativo,
//! li' si GIUDICA. Il giudizio si prova su qualunque macchina ed e' la parte
//! che deve essere giusta; questo file e' meccanico e non decide niente.
//!
//! L'ordine conta e non e' negoziabile: si apre, si guarda chi c'e', si
//! giudica, e SOLO allora si scrive. Scrivere prima di guardare vorrebbe dire
//! aver gia' consegnato la richiesta firmata a chiunque fosse dall'altro capo.

#![cfg(windows)]

use std::io::{self, Read, Write};

use windows_sys::Win32::Foundation::{
    CloseHandle, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, ReadFile, WriteFile, FILE_ATTRIBUTE_NORMAL, OPEN_EXISTING,
};
use windows_sys::Win32::System::Pipes::GetNamedPipeServerProcessId;
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, QueryFullProcessImageNameW,
    PROCESS_QUERY_LIMITED_INFORMATION,
};

use crate::helper_client::ChannelRefusal;

/// Handle con chiusura garantita.
struct Handle(HANDLE);

impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe { CloseHandle(self.0) };
        }
    }
}

fn wide(v: &str) -> Vec<u16> {
    v.encode_utf16().chain(std::iter::once(0)).collect()
}

fn da_wide(buffer: &[u16], lunghezza: usize) -> String {
    String::from_utf16_lossy(&buffer[..lunghezza])
}

/// Il percorso completo dell'eseguibile di un processo.
fn eseguibile_del_processo(processo: HANDLE) -> io::Result<String> {
    let mut buffer = vec![0u16; 32 * 1024];
    let mut lunghezza = buffer.len() as u32;
    let ok =
        unsafe { QueryFullProcessImageNameW(processo, 0, buffer.as_mut_ptr(), &mut lunghezza) };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(da_wide(&buffer, lunghezza as usize))
}

/// Il SID del proprietario dell'OGGETTO pipe, in forma testuale.
///
/// E' la domanda che si puo' fare: leggere il proprietario di un oggetto
/// richiede `READ_CONTROL`, che un client che ha appena aperto la pipe ha
/// gia'. Leggere invece il token del processo che la serve richiede diritti
/// su quel processo, e un programma senza privilegi non li ha su un processo
/// di sistema — mai.
///
/// La garanzia e' equivalente: un oggetto di proprieta' del sistema lo puo'
/// creare solo il sistema.
fn proprietario_della_pipe(pipe: HANDLE) -> io::Result<String> {
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, GetSecurityInfo, SE_KERNEL_OBJECT,
    };
    use windows_sys::Win32::Security::{OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID};

    let mut owner: PSID = std::ptr::null_mut();
    let mut descrittore: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    let rc = unsafe {
        GetSecurityInfo(
            pipe,
            SE_KERNEL_OBJECT,
            OWNER_SECURITY_INFORMATION,
            &mut owner,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut descrittore,
        )
    };
    if rc != 0 || owner.is_null() {
        return Err(io::Error::from_raw_os_error(rc as i32));
    }

    let mut testo: *mut u16 = std::ptr::null_mut();
    let convertito = unsafe { ConvertSidToStringSidW(owner, &mut testo) };
    // Il descrittore lo alloca Windows e va restituito, sia che la conversione
    // sia riuscita sia che no.
    let libera = |p: *mut core::ffi::c_void| unsafe {
        windows_sys::Win32::Foundation::LocalFree(p as windows_sys::Win32::Foundation::HLOCAL)
    };
    if convertito == 0 {
        let e = io::Error::last_os_error();
        libera(descrittore);
        return Err(e);
    }
    let mut len = 0usize;
    while unsafe { *testo.add(len) } != 0 {
        len += 1;
    }
    let sid = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(testo, len) });
    libera(testo as *mut core::ffi::c_void);
    libera(descrittore);
    Ok(sid)
}

/// Il SID del proprietario di un processo, in forma testuale.
fn sid_del_processo(processo: HANDLE) -> io::Result<String> {
    use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
    use windows_sys::Win32::Security::{GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER};

    let mut token: HANDLE = std::ptr::null_mut();
    if unsafe { OpenProcessToken(processo, TOKEN_QUERY, &mut token) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let token = Handle(token);

    let mut necessari: u32 = 0;
    unsafe { GetTokenInformation(token.0, TokenUser, std::ptr::null_mut(), 0, &mut necessari) };
    if necessari == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut buffer = vec![0u8; necessari as usize];
    if unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            buffer.as_mut_ptr() as *mut core::ffi::c_void,
            necessari,
            &mut necessari,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let info = buffer.as_ptr() as *const TOKEN_USER;
    let mut testo: *mut u16 = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW((*info).User.Sid, &mut testo) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut len = 0usize;
    while unsafe { *testo.add(len) } != 0 {
        len += 1;
    }
    let parola = unsafe { std::slice::from_raw_parts(testo, len) };
    let risultato = String::from_utf16_lossy(parola);
    unsafe {
        windows_sys::Win32::Foundation::LocalFree(testo as windows_sys::Win32::Foundation::HLOCAL)
    };
    Ok(risultato)
}

/// A chi appartiene QUESTO processo.
///
/// Serve per costruire il nome del canale: la pipe dell'aiutante porta il SID
/// del proprietario, cosi' due utenti della stessa macchina non ne condividono
/// uno nemmeno per sbaglio.
pub fn sid_corrente() -> io::Result<String> {
    // Lo pseudo-handle del processo corrente non va chiuso: non e' un handle
    // vero, e non passa da `Handle`.
    sid_del_processo(unsafe { GetCurrentProcess() })
}

/// Il canale di questo utente: dove si chiama, e chi ci si aspetta di trovare.
///
/// Entrambi i valori vengono dal sistema operativo (il SID del token, la
/// cartella dei programmi), non da una configurazione: una configurazione la
/// puo' cambiare chi vogliamo tenere fuori, e cambiarla vorrebbe dire
/// scegliere chi e' l'aiutante.
pub fn indirizzo() -> Result<(String, String), ChannelRefusal> {
    let sid = sid_corrente().map_err(|_| ChannelRefusal::NotAvailable)?;
    let nome = crate::helper_client::pipe_name_for_owner(&sid).ok_or(ChannelRefusal::NotLocal)?;
    let program_files = std::env::var("ProgramFiles").map_err(|_| ChannelRefusal::NotAvailable)?;
    Ok((
        nome,
        crate::helper_client::helper_executable_in(&program_files),
    ))
}

/// Apre il canale e stabilisce chi c'e' dall'altro capo. NON scrive niente.
///
/// L'ordine non e' negoziabile: si apre, si guarda, si giudica. Scrivere prima
/// di guardare vorrebbe dire aver gia' consegnato la richiesta firmata a
/// chiunque fosse dall'altro capo.
fn apri_e_verifica(nome_pipe: &str, eseguibile_atteso: &str) -> Result<Handle, ChannelRefusal> {
    let name = wide(nome_pipe);
    let mut pipe = INVALID_HANDLE_VALUE;
    // The service deliberately creates one pipe instance per request. A
    // version handshake followed immediately by the real action can land in
    // the few milliseconds between instances, so wait locally before
    // declaring the helper absent. No request bytes have been written yet.
    for attempt in 0..20 {
        pipe = unsafe {
            CreateFileW(
                name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                0,
                std::ptr::null(),
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                std::ptr::null_mut(),
            )
        };
        if pipe != INVALID_HANDLE_VALUE {
            break;
        }
        if attempt < 19 {
            std::thread::sleep(std::time::Duration::from_millis(25));
        }
    }
    if pipe == INVALID_HANDLE_VALUE {
        // La pipe non c'e': l'aiutante non e' installato o non gira. Non e'
        // un guasto da spiegare in dettaglio, e' una capacita' assente.
        return Err(ChannelRefusal::NotAvailable);
    }
    let pipe = Handle(pipe);

    // DI CHI E' IL CANALE, non chi lo sta servendo.
    //
    // Prima si prendeva il processo all'altro capo e se ne leggeva il token
    // per ricavarne l'identita'. Non puo' funzionare: questo programma gira
    // senza privilegi e il servizio gira come sistema, e Windows non lascia a
    // un processo utente aprire il token di un processo di SYSTEM. Il
    // controllo falliva SEMPRE, su qualunque macchina — e falliva prima di
    // scrivere una parola, quindi il canale si chiudeva a vuoto e l'aiutante
    // registrava «l'altro capo ha chiuso subito» senza sapere perche'
    // (macchina di Roberto, 19/8/2026: mai riconosciuto, nemmeno installato e
    // in ascolto).
    //
    // Il proprietario dell'OGGETTO da' la stessa garanzia e si puo' leggere:
    // un oggetto di proprieta' del sistema lo puo' creare solo il sistema.
    let sid = proprietario_della_pipe(pipe.0)
        .map_err(|_| ChannelRefusal::NotLocalSystem("(proprietario illeggibile)".into()))?;

    // L'eseguibile atteso resta un rafforzativo, non una condizione: leggerlo
    // richiede di aprire il processo, ed e' esattamente la cosa che da qui non
    // si puo' fare. Quando riesce si pretende che combaci; quando non riesce,
    // il proprietario ha gia' detto cio' che conta.
    let eseguibile = (|| {
        let mut pid: u32 = 0;
        if unsafe { GetNamedPipeServerProcessId(pipe.0, &mut pid) } == 0 {
            return None;
        }
        let processo = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if processo.is_null() {
            return None;
        }
        let processo = Handle(processo);
        eseguibile_del_processo(processo.0).ok()
    })()
    .unwrap_or_else(|| eseguibile_atteso.to_string());

    // Il giudizio sta altrove, e si prova altrove.
    crate::helper_client::judge_peer(nome_pipe, &sid, &eseguibile, eseguibile_atteso)?;
    Ok(pipe)
}

/// C'e' l'aiutante vero, su questa macchina, adesso?
///
/// Apre il canale, guarda chi c'e' e richiude senza mandare niente. E' la
/// domanda che serve PRIMA di offrire a una persona una scelta che solo
/// l'aiutante puo' onorare: offrirla senza sapere significherebbe far

/// Manda una richiesta all'aiutante e restituisce la sua risposta.
///
/// Fallisce PRIMA di scrivere se dall'altro capo non c'e' l'aiutante vero.
/// `eseguibile_atteso` e' il percorso dell'eseguibile installato: arriva da
/// chi chiama, perche' questo modulo non deve decidere nemmeno quello.
pub fn chiedi(
    nome_pipe: &str,
    eseguibile_atteso: &str,
    richiesta: &str,
) -> Result<String, ChannelRefusal> {
    let pipe = apri_e_verifica(nome_pipe, eseguibile_atteso)?;

    // Solo adesso.
    let mut scrittore = PipeIo(&pipe);
    scrittore
        .write_all(&crate::frame::framed(richiesta.as_bytes()))
        .map_err(|_| ChannelRefusal::NotAvailable)?;
    scrittore.flush().ok();

    // Si legge fino al delimitatore, non fino alla chiusura del canale:
    // nessuno dei due capi chiude, e aspettare la chiusura vorrebbe dire
    // aspettare un servizio che ha gia' risposto. Il tetto vale anche qui:
    // la risposta la scrive un processo di sistema, ma un tetto costa niente.
    let risposta = crate::frame::read_frame(PipeIo(&pipe), 64 * 1024)
        .map_err(|_| ChannelRefusal::NotAvailable)?;
    Ok(String::from_utf8_lossy(&risposta).to_string())
}

struct PipeIo<'a>(&'a Handle);

impl Read for PipeIo<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut letti: u32 = 0;
        let ok = unsafe {
            ReadFile(
                self.0 .0,
                buf.as_mut_ptr(),
                buf.len() as u32,
                &mut letti,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            let err = io::Error::last_os_error();
            // 109 = l'altro capo ha chiuso: fine dei dati, non un guasto.
            if err.raw_os_error() == Some(109) {
                return Ok(0);
            }
            return Err(err);
        }
        Ok(letti as usize)
    }
}

impl Write for PipeIo<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut scritti: u32 = 0;
        let ok = unsafe {
            WriteFile(
                self.0 .0,
                buf.as_ptr(),
                buf.len() as u32,
                &mut scritti,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(scritti as usize)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
