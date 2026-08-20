//! La pipe vera su Windows: chi la crea, chi puo' aprirla, chi ha chiamato.
//!
//! Vive separato dal resto perche' e' l'unica parte che non si puo' provare
//! su un'altra macchina. Riceve da `channel` un nome gia' verificato e da
//! `protocol` valori gia' validati: qui non si decide niente, si applica.
//!
//! Tre proprieta', e ognuna chiude un modo diverso di aggirare l'aiutante.
//!
//! **La lista d'accesso nomina un SID, non un gruppo.** Un gruppo lo si puo'
//! allargare — chi riesce ad aggiungersi ad «Administrators» eredita il
//! canale. Un SID e' quella persona e basta.
//!
//! **La prima istanza, o niente.** `FILE_FLAG_FIRST_PIPE_INSTANCE` fa
//! fallire la creazione se la pipe esiste gia'. Senza, un processo senza
//! privilegi che arriva per primo tiene il nome, e l'aiutante gli si affianca
//! in silenzio: il client parlerebbe con l'impostore.
//!
//! **Chi chiama si guarda, non si crede.** `GetNamedPipeClientProcessId` piu'
//! il token del processo danno il SID vero di chi ha aperto la pipe, che e'
//! un fatto del sistema operativo e non un campo del messaggio.

#![cfg(windows)]

use std::io;

use windows_sys::Win32::Foundation::{
    CloseHandle, LocalFree, ERROR_SUCCESS, HANDLE, HLOCAL, INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSidToSidW, SetEntriesInAclW, EXPLICIT_ACCESS_W,
    NO_MULTIPLE_TRUSTEE, SET_ACCESS, TRUSTEE_IS_SID, TRUSTEE_IS_USER, TRUSTEE_W,
};
use windows_sys::Win32::Security::{
    GetTokenInformation, InitializeSecurityDescriptor, SetSecurityDescriptorDacl, TokenUser, ACL,
    PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES, SECURITY_DESCRIPTOR, TOKEN_QUERY, TOKEN_USER,
};
// `PIPE_ACCESS_DUPLEX` vive fra gli attributi di file, non fra le costanti
// delle pipe: e' un flag di apertura, e Windows lo classifica li'.
use windows_sys::Win32::Storage::FileSystem::{ReadFile, WriteFile, PIPE_ACCESS_DUPLEX};
use windows_sys::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, GetNamedPipeClientProcessId, PIPE_READMODE_BYTE,
    PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
};
use windows_sys::Win32::System::Threading::{
    OpenProcess, OpenProcessToken, PROCESS_QUERY_LIMITED_INFORMATION,
};

/// La versione della struttura del descrittore di sicurezza. Costante di
/// Windows, non un numero scelto qui.
const SECURITY_DESCRIPTOR_REVISION: u32 = 1;

/// Fa fallire la creazione se la pipe esiste gia'. E' la difesa contro chi
/// arriva per primo e tiene il nome.
const FILE_FLAG_FIRST_PIPE_INSTANCE: u32 = 0x0008_0000;

/// Handle con chiusura garantita.
pub struct Handle(HANDLE);

impl Handle {
    /// Legge dalla pipe.
    ///
    /// `ReadFile`/`WriteFile` invece di trasformare l'handle in un `File`:
    /// un `File` lo chiuderebbe alla Drop, e la chiusura appartiene gia' a
    /// questo tipo. Due proprietari dello stesso handle sono una chiusura
    /// doppia, che su Windows puo' colpire un handle nel frattempo riusato
    /// da qualcun altro.
    pub fn reader(&self) -> PipeIo<'_> {
        PipeIo(self)
    }

    pub fn writer(&self) -> PipeIo<'_> {
        PipeIo(self)
    }
}

/// Lettura e scrittura su una pipe che resta di chi l'ha creata.
pub struct PipeIo<'a>(&'a Handle);

impl std::io::Read for PipeIo<'_> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
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
            let err = std::io::Error::last_os_error();
            // 109 = la pipe e' stata chiusa dall'altro capo: e' la fine dei
            // dati, non un guasto.
            if err.raw_os_error() == Some(109) {
                return Ok(0);
            }
            return Err(err);
        }
        Ok(letti as usize)
    }
}

impl std::io::Write for PipeIo<'_> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
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
            return Err(std::io::Error::last_os_error());
        }
        Ok(scritti as usize)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe { CloseHandle(self.0) };
        }
    }
}

/// Puntatore da `LocalAlloc` (SID convertiti, ACL costruite).
struct LocalPtr(*mut core::ffi::c_void);

impl Drop for LocalPtr {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0 as HLOCAL) };
        }
    }
}

pub(crate) fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Il SID testuale del proprietario, come struttura di Windows.
fn sid_from_string(sid: &str) -> io::Result<LocalPtr> {
    let mut psid: PSID = std::ptr::null_mut();
    let ok = unsafe { ConvertStringSidToSidW(wide(sid).as_ptr(), &mut psid) };
    if ok == 0 || psid.is_null() {
        return Err(io::Error::last_os_error());
    }
    Ok(LocalPtr(psid))
}

/// Crea la pipe, aperta al SOLO proprietario.
///
/// La lista d'accesso contiene una voce sola. Non e' minimalismo: ogni voce
/// in piu' e' un altro modo di arrivare a un componente che ha i privilegi di
/// sistema, e non ce n'e' nessuno che serva.
///
/// `PIPE_REJECT_REMOTE_CLIENTS` chiude l'accesso da un'altra macchina anche
/// nel caso in cui la configurazione di rete lo consentisse: la pipe e'
/// locale per contratto, non per circostanza.
pub fn create_owner_only_pipe(name: &str, owner_sid: &str) -> io::Result<Handle> {
    let sid = sid_from_string(owner_sid)?;

    let mut accesso: EXPLICIT_ACCESS_W = unsafe { std::mem::zeroed() };
    accesso.grfAccessPermissions = 0x0012_019F; // lettura+scrittura sulla pipe
    accesso.grfAccessMode = SET_ACCESS;
    accesso.grfInheritance = 0;
    accesso.Trustee = TRUSTEE_W {
        pMultipleTrustee: std::ptr::null_mut(),
        MultipleTrusteeOperation: NO_MULTIPLE_TRUSTEE,
        TrusteeForm: TRUSTEE_IS_SID,
        TrusteeType: TRUSTEE_IS_USER,
        ptstrName: sid.0 as *mut u16,
    };

    let mut acl: *mut ACL = std::ptr::null_mut();
    let rc = unsafe { SetEntriesInAclW(1, &accesso, std::ptr::null_mut(), &mut acl) };
    if rc != ERROR_SUCCESS {
        return Err(io::Error::from_raw_os_error(rc as i32));
    }
    let _acl_guard = LocalPtr(acl as *mut core::ffi::c_void);

    let mut descrittore: SECURITY_DESCRIPTOR = unsafe { std::mem::zeroed() };
    let pdesc: PSECURITY_DESCRIPTOR = &mut descrittore as *mut _ as PSECURITY_DESCRIPTOR;
    if unsafe { InitializeSecurityDescriptor(pdesc, SECURITY_DESCRIPTOR_REVISION) } == 0 {
        return Err(io::Error::last_os_error());
    }
    // Il terzo argomento a 0 e' «questa lista, non quella di default»: una
    // lista assente significherebbe accesso a tutti, che e' l'opposto.
    if unsafe { SetSecurityDescriptorDacl(pdesc, 1, acl, 0) } == 0 {
        return Err(io::Error::last_os_error());
    }

    let attributi = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: pdesc,
        bInheritHandle: 0,
    };

    let handle = unsafe {
        CreateNamedPipeW(
            wide(name).as_ptr(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1, // una istanza: un proprietario, una conversazione per volta
            64 * 1024,
            64 * 1024,
            0,
            &attributi,
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        // Fra gli errori possibili c'e' «esiste gia'»: qualcuno ha preso il
        // nome per primo. Non si prosegue affiancandosi.
        return Err(io::Error::last_os_error());
    }
    Ok(Handle(handle))
}

/// Attende un chiamante.
pub fn wait_for_client(pipe: &Handle) -> io::Result<()> {
    if unsafe { ConnectNamedPipe(pipe.0, std::ptr::null_mut()) } == 0 {
        let err = io::Error::last_os_error();
        // 535 = il chiamante era gia' collegato quando abbiamo atteso: e' una
        // connessione valida, non un errore.
        if err.raw_os_error() != Some(535) {
            return Err(err);
        }
    }
    Ok(())
}

/// Il SID di CHI ha aperto la pipe, chiesto al sistema operativo.
///
/// Non e' un campo del messaggio: un messaggio lo scrive chi lo manda. Questo
/// e' il proprietario del processo all'altro capo, e nessuno puo' dichiararlo
/// per conto suo.
pub fn caller_sid(pipe: &Handle) -> io::Result<String> {
    let mut pid: u32 = 0;
    if unsafe { GetNamedPipeClientProcessId(pipe.0, &mut pid) } == 0 {
        return Err(io::Error::last_os_error());
    }
    // Il diritto minimo che serve a leggere il token: chiedere di piu'
    // fallirebbe su processi che non possiamo aprire, senza guadagno.
    let processo = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if processo.is_null() {
        return Err(io::Error::last_os_error());
    }
    let processo = Handle(processo);

    let mut token: HANDLE = std::ptr::null_mut();
    if unsafe { OpenProcessToken(processo.0, TOKEN_QUERY, &mut token) } == 0 {
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
    let psid = unsafe { (*info).User.Sid };
    let mut testo: *mut u16 = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(psid, &mut testo) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let _guardia = LocalPtr(testo as *mut core::ffi::c_void);
    let mut len = 0usize;
    while unsafe { *testo.add(len) } != 0 {
        len += 1;
    }
    let parola = unsafe { std::slice::from_raw_parts(testo, len) };
    Ok(String::from_utf16_lossy(parola))
}
