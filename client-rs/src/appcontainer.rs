//! appcontainer.rs — isolamento FORTE su Windows (Fase 7 / W4).
//!
//! Complementare al Job Object di `sandbox_windows.rs`: quello contiene le
//! RISORSE (memoria, processi, kill d'albero); QUESTO isola il FILESYSTEM e la
//! rete. I due strati COESISTONO (spec §2/§16.2): il processo executor gira
//! DENTRO un AppContainer (fs/rete negati per default) E dentro il Job Object.
//!
//! Traduzione capability→permessi (il buco che W4 chiude): gli hint `fs:*` del
//! manifest diventano ACL `GrantAccess` sul SID del container; `network.*`
//! diventa la capability SID `internetClient`. Senza `network.*` il container
//! nega la rete per costruzione (guadagno gratuito).
//!
//! ONESTA' (§2.8): il chiamante etichetta il result `sandbox:"appcontainer"`
//! SOLO se `run_in_container` ritorna `Ran` (container COSTRUITO e processo
//! avviato al suo interno). Ogni fallimento PRIMA dello spawn ritorna
//! `Unsupported(motivo)` e il chiamante degrada onestamente a job-object con
//! `sandbox_downgrade_reason`. Nessun contenimento dichiarato ma non attivo.
//!
//! GATE: **default ON su Windows** (7/7/2026); opt-OUT via `METNOS_SANDBOX_APPCONTAINER=0`:
//! con gate spento il percorso job-object validato (W3.3) resta INTATTO.
//!
//! NB: modulo con FFI Win32 NON esercitabile fuori da Windows. La logica pura
//! (mappatura capability, quoting, env-block) vive in `sandbox_common.rs` ed e'
//! testata sotto Linux; qui restano solo le chiamate di sistema, da validare
//! sul PC reale (vedi runbook W4).

use std::fs::File;
use std::io::{Read, Write};
use std::os::windows::io::{FromRawHandle, RawHandle};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use anyhow::{bail, Context, Result};

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, LocalFree, SetHandleInformation, BOOL, HANDLE, HLOCAL,
    HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, WAIT_OBJECT_0, WIN32_ERROR,
};
use windows_sys::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSidToSidW, GetNamedSecurityInfoW, SetEntriesInAclW,
    SetNamedSecurityInfoW, ACCESS_MODE, EXPLICIT_ACCESS_W, GRANT_ACCESS, NO_MULTIPLE_TRUSTEE,
    REVOKE_ACCESS, SE_FILE_OBJECT, TRUSTEE_IS_GROUP, TRUSTEE_IS_SID,
};
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile, DeriveAppContainerSidFromAppContainerName,
};
use windows_sys::Win32::Security::{
    ACL, DACL_SECURITY_INFORMATION, FreeSid, PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES,
    SECURITY_CAPABILITIES, SID_AND_ATTRIBUTES, SUB_CONTAINERS_AND_OBJECTS_INHERIT,
};
use windows_sys::Win32::System::JobObjects::{AssignProcessToJobObject, TerminateJobObject};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, InitializeProcThreadAttributeList,
    ResumeThread, TerminateProcess, UpdateProcThreadAttribute, WaitForSingleObject,
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED, EXTENDED_STARTUPINFO_PRESENT, INFINITE, LPPROC_THREAD_ATTRIBUTE_LIST,
    PROCESS_INFORMATION, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, STARTF_USESTDHANDLES,
    STARTUPINFOEXW, STARTUPINFOW,
};

use crate::sandbox_common as common;
use crate::sandbox_common::{AclGrantRecord, AclRegistry, HintGrant};

/// UN profilo per client (spec §2.1): creato idempotente al primo uso, rimosso
/// all'unpair/revoca (W4.4). Il SID e' derivato dal nome, stabile.
const PROFILE_NAME: &str = "Metnos.Executor";
/// Gate W4.1 (default OFF): finche' spento, il percorso job-object resta l'unico.
const GATE_ENV: &str = "METNOS_SANDBOX_APPCONTAINER";
/// SID well-known della capability `internetClient` (WinCapabilityInternetClientSid).
/// Via literal + ConvertStringSidToSidW (advapi32) invece di
/// DeriveCapabilitySidsFromName: evita la dipendenza dall'api-set
/// `api-ms-win-security-base` (import lib assente in mingw) — vedi spec §4.
const INTERNET_CLIENT_SID: &str = "S-1-15-3-1";

// Costanti ABI stabili non necessarie altrove: dichiarate qui (spec §4:
// "dichiarazioni manuali minime preferite al pull di una feature").
const S_OK: i32 = 0;
/// HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS) — profilo gia' presente = deriva.
const HR_ALREADY_EXISTS: i32 = (0x8007_0000u32 | 183u32) as i32;
/// Abilita il figlio a usare la capability SID (SE_GROUP_ENABLED).
const SE_GROUP_ENABLED: u32 = 0x0000_0004;
/// L'environment passato a CreateProcessW e' UTF-16.
const CREATE_UNICODE_ENVIRONMENT: u32 = 0x0000_0400;

/// Esito dell'esecuzione nel container.
pub enum Outcome {
    /// Container costruito e processo avviato al suo interno: risultato ONESTO
    /// a livello "appcontainer" (anche se l'executor poi fallisce o va in timeout).
    Ran { stdout: String, stderr: String, timed_out: bool },
    /// Container NON costruibile su questo device (edizione, policy, FS non-NTFS,
    /// gate): il chiamante degrada a job-object dichiarando `motivo`.
    Unsupported(String),
}

/// Parametri per l'esecuzione nel container. Tutti dati POSSEDUTI (Send): la
/// funzione gira in `spawn_blocking` e non deve trasportare handle non-Send.
pub struct ContainerParams {
    pub python: PathBuf,
    pub entry: PathBuf,
    pub shim_dir: PathBuf,
    pub exec_dir: PathBuf,
    /// Dir di lavoro/TEMP per-invocazione (gia' su TEMP/TMP in `env_pairs`),
    /// creata e rimossa dal chiamante; qui riceve un ACL di SCRITTURA.
    pub scratch_dir: PathBuf,
    pub env_pairs: Vec<(String, String)>,
    pub args_json: String,
    pub deadline_ms: u64,
    /// Concessioni fs derivate dalle capability (radici gia' de-globbate).
    pub grants: Vec<HintGrant>,
    pub want_net: bool,
}

/// Gate W4 — **default ON su Windows** dal 7/7/2026 (fase 7 chiusa).
/// Opt-OUT esplicito via `METNOS_SANDBOX_APPCONTAINER=0|false|off` (fallback al
/// solo Job Object). Abilitato in prod dopo validazione sul PC reale: happy-path
/// (`sandbox=appcontainer`, ACL per-invocazione su Documents) + registry-drop
/// all'unpair (0 concessioni orfane); il rollback ACL su fallimento container e'
/// coperto dalla stessa pulizia unpair validata.
pub fn gate_on() -> bool {
    !matches!(std::env::var(GATE_ENV).ok().as_deref(), Some("0") | Some("false") | Some("off"))
}

/// Livello riportato a freddo per l'heartbeat: "appcontainer" solo se il gate e'
/// attivo E il profilo si costruisce davvero (probe cacheata una volta per
/// processo — l'env non cambia in un daemon di lunga durata). Onesto: se la
/// creazione del profilo fallisce, l'heartbeat NON millanta "appcontainer".
pub fn probe_supported() -> bool {
    if !gate_on() {
        return false;
    }
    static SUPPORTED: OnceLock<bool> = OnceLock::new();
    *SUPPORTED.get_or_init(|| match ensure_profile_sid() {
        Ok(_sid) => true, // _sid droppa qui → FreeSid
        Err(e) => {
            tracing::warn!("AppContainer non disponibile ({e:#}): heartbeat riporta job-object");
            false
        }
    })
}

// --- guardie RAII ------------------------------------------------------------

/// Come liberare un SID: dipende da chi lo ha allocato.
enum SidFree {
    /// SID del profilo AppContainer (CreateAppContainerProfile / Derive...).
    FreeSid,
    /// SID da ConvertStringSidToSidW (LocalAlloc).
    LocalFree,
}

struct OwnedSid {
    psid: PSID,
    free: SidFree,
}

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if self.psid.is_null() {
            return;
        }
        unsafe {
            match self.free {
                SidFree::FreeSid => {
                    FreeSid(self.psid);
                }
                SidFree::LocalFree => {
                    LocalFree(self.psid as HLOCAL);
                }
            }
        }
    }
}

/// Handle Win32 con CloseHandle alla Drop; `release` lo estrae senza chiuderlo
/// (per cederlo a un `File` o al figlio).
struct Handle(HANDLE);

impl Handle {
    fn release(self) -> HANDLE {
        let h = self.0;
        std::mem::forget(self);
        h
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe { CloseHandle(self.0) };
        }
    }
}

/// Puntatore LocalAlloc (security descriptor, ACL da SetEntriesInAcl): LocalFree.
struct LocalPtr(*mut core::ffi::c_void);

impl Drop for LocalPtr {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { LocalFree(self.0 as HLOCAL) };
        }
    }
}

/// Attribute list con DeleteProcThreadAttributeList alla Drop. Il buffer di
/// backing e' tenuto vivo a parte dal chiamante.
struct AttrList(LPPROC_THREAD_ATTRIBUTE_LIST);

impl Drop for AttrList {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { DeleteProcThreadAttributeList(self.0) };
        }
    }
}

/// Rollback degli ACE concessi in QUESTA costruzione (§2.8): i grant si
/// applicano PRIMA di CreateProcessW; se il container non parte (bail su un
/// qualsiasi passo 4-11 → degrado a job-object), gli ACE resterebbero orfani sul
/// SID del container — dir utente accessibili a un'identita' che non gira. La
/// guardia li revoca alla Drop A MENO CHE sia disarmata: la si disarma appena
/// CreateProcessW riesce, oltre quel punto i grant servono al processo avviato.
/// Il registro NON si tocca: grant_dir lo ri-applica a ogni invocazione
/// (idempotente) e l'unpair revoca un ACE gia' assente come no-op.
struct GrantGuard {
    sid: PSID,
    paths: Vec<PathBuf>,
    armed: bool,
}

impl GrantGuard {
    fn new(sid: PSID) -> Self {
        Self { sid, paths: Vec::new(), armed: true }
    }
    fn track(&mut self, root: &Path) {
        self.paths.push(root.to_path_buf());
    }
    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for GrantGuard {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        for root in &self.paths {
            if let Err(e) = apply_acl(self.sid, root, 0, REVOKE_ACCESS) {
                tracing::warn!(path = %root.display(), "rollback ACE (container non avviato) fallito: {e:#}");
            }
        }
    }
}

/// Wrapper per spostare un HANDLE grezzo in un thread lettore (i raw pointer non
/// sono Send). Sicuro: il thread e' l'unico proprietario finche' vive.
struct SendHandle(HANDLE);
unsafe impl Send for SendHandle {}

impl SendHandle {
    /// Consuma il wrapper e restituisce l'handle. Chiamarlo NELLA closure forza
    /// la cattura dell'INTERO `SendHandle` (che e' Send), non del solo campo
    /// `*mut` (non-Send: la cattura disgiunta di edition 2021 prenderebbe quello).
    fn take(self) -> HANDLE {
        self.0
    }
}

// --- helper errori -----------------------------------------------------------

fn last_error() -> u32 {
    unsafe { GetLastError() }
}

fn check_bool(op: &str, ok: BOOL) -> Result<()> {
    if ok == 0 {
        bail!("{op} fallita (GetLastError={})", last_error());
    }
    Ok(())
}

fn check_win32(op: &str, code: WIN32_ERROR) -> Result<()> {
    if code != 0 {
        bail!("{op} → WIN32_ERROR {code}");
    }
    Ok(())
}

// --- profilo + SID -----------------------------------------------------------

/// Crea (idempotente) il profilo del container e ritorna il suo SID.
fn ensure_profile_sid() -> Result<OwnedSid> {
    let name = common::to_wide_null(PROFILE_NAME);
    let display = common::to_wide_null("Metnos Executor");
    let desc = common::to_wide_null("Sandbox executor remoti Metnos");
    let mut psid: PSID = std::ptr::null_mut();

    // Capability del profilo = nessuna: le capability effettive (internetClient)
    // si iniettano al momento dello spawn via SECURITY_CAPABILITIES.
    let hr = unsafe {
        CreateAppContainerProfile(
            name.as_ptr(),
            display.as_ptr(),
            desc.as_ptr(),
            std::ptr::null(),
            0,
            &mut psid,
        )
    };
    if hr == S_OK {
        return Ok(OwnedSid { psid, free: SidFree::FreeSid });
    }
    if hr == HR_ALREADY_EXISTS {
        // Profilo gia' registrato: deriva il SID dal nome (stabile).
        let mut psid2: PSID = std::ptr::null_mut();
        let hr2 = unsafe { DeriveAppContainerSidFromAppContainerName(name.as_ptr(), &mut psid2) };
        if hr2 == S_OK {
            return Ok(OwnedSid { psid: psid2, free: SidFree::FreeSid });
        }
        bail!("DeriveAppContainerSidFromAppContainerName HRESULT {hr2:#010x}");
    }
    bail!("CreateAppContainerProfile HRESULT {hr:#010x}");
}

/// SID della capability `internetClient` (rete uscente).
fn internet_client_sid() -> Result<OwnedSid> {
    let s = common::to_wide_null(INTERNET_CLIENT_SID);
    let mut psid: PSID = std::ptr::null_mut();
    check_bool("ConvertStringSidToSidW(internetClient)", unsafe {
        ConvertStringSidToSidW(s.as_ptr(), &mut psid)
    })?;
    Ok(OwnedSid { psid, free: SidFree::LocalFree })
}

/// Deriva il SID del container SENZA (ri)crearne il profilo. Il SID e'
/// deterministico dal nome, quindi funziona anche dopo `DeleteAppContainerProfile`
/// (serve alla pulizia per revocare gli ACE anche a profilo gia' rimosso).
fn derive_sid() -> Result<OwnedSid> {
    let name = common::to_wide_null(PROFILE_NAME);
    let mut psid: PSID = std::ptr::null_mut();
    let hr = unsafe { DeriveAppContainerSidFromAppContainerName(name.as_ptr(), &mut psid) };
    if hr == S_OK {
        Ok(OwnedSid { psid, free: SidFree::FreeSid })
    } else {
        bail!("DeriveAppContainerSidFromAppContainerName HRESULT {hr:#010x}");
    }
}

/// Forma stringa (`S-1-15-...`) di un SID, per registrarlo nel registro ACL.
fn sid_to_string(sid: PSID) -> Result<String> {
    let mut pstr: *mut u16 = std::ptr::null_mut();
    check_bool("ConvertSidToStringSidW", unsafe {
        ConvertSidToStringSidW(sid, &mut pstr)
    })?;
    if pstr.is_null() {
        bail!("ConvertSidToStringSidW ha restituito null");
    }
    let s = unsafe { wide_ptr_to_string(pstr) };
    unsafe { LocalFree(pstr as HLOCAL) };
    Ok(s)
}

/// Legge una stringa UTF-16 NUL-terminata da un puntatore grezzo.
unsafe fn wide_ptr_to_string(p: *const u16) -> String {
    let mut len = 0usize;
    while *p.add(len) != 0 {
        len += 1;
    }
    String::from_utf16_lossy(std::slice::from_raw_parts(p, len))
}

/// Rimuove il profilo AppContainer (W4.4). NON tocca gli ACL sulle dir utente:
/// quella pulizia la fa `cleanup_all_grants` (che chiama anche questa). Il SID
/// resta derivabile dal nome anche dopo, quindi l'ordine revoca→delete e' sicuro.
pub fn delete_profile() -> Result<()> {
    let name = common::to_wide_null(PROFILE_NAME);
    let hr = unsafe { DeleteAppContainerProfile(name.as_ptr()) };
    if hr == S_OK {
        Ok(())
    } else {
        bail!("DeleteAppContainerProfile HRESULT {hr:#010x}");
    }
}

/// Esito della pulizia W4.4 (per il report all'utente/chiamante).
pub struct CleanupReport {
    /// Concessioni registrate trovate.
    pub total: usize,
    /// ACE revocati con successo.
    pub revoked: usize,
    /// Voci NON revocate (mantenute nel registro per un retry, §2.8).
    pub failed: usize,
    /// Voci scartate perche' il path e' sparito (l'ACE e' morto con la dir):
    /// niente da revocare, non un fallimento retry-abile.
    pub dropped: usize,
    /// Profilo AppContainer rimosso.
    pub profile_removed: bool,
}

/// Pulizia all'unpair/revoca (W4.4): revoca TUTTI gli ACE registrati dalle dir,
/// rimuove il profilo, svuota il registro. ONESTO (§2.8): le voci che NON si
/// riescono a revocare restano nel registro (retry-abili), non vengono
/// silenziosamente scartate. Il SID e' derivato dal nome, quindi la revoca
/// funziona anche se il profilo non esiste piu'.
pub fn cleanup_all_grants() -> Result<CleanupReport> {
    let path = registry_path()?;
    let records = AclRegistry::load(&path).take_all();
    let total = records.len();

    // SID del container (derivato, non ri-crea il profilo). Se non derivabile,
    // non possiamo revocare nulla: manteniamo tutte le voci.
    let sid = if records.is_empty() { None } else { derive_sid().ok() };

    let mut revoked = 0usize;
    let mut dropped = 0usize;
    let mut remaining: Vec<AclGrantRecord> = Vec::new();
    for rec in records {
        // Path sparito → l'ACE e' morto con la dir: niente da revocare, la voce
        // va SCARTATA (non tenuta per un retry che non potra' mai riuscire).
        if !Path::new(&rec.path).exists() {
            tracing::debug!(path = %rec.path, "path assente all'unpair: voce ACL scartata");
            dropped += 1;
            continue;
        }
        match &sid {
            Some(s) => match apply_acl(s.psid, Path::new(&rec.path), 0, REVOKE_ACCESS) {
                Ok(()) => revoked += 1,
                Err(e) => {
                    tracing::warn!(path = %rec.path, "REVOKE ACL fallito, mantengo la voce: {e:#}");
                    remaining.push(rec);
                }
            },
            None => remaining.push(rec),
        }
    }
    let failed = remaining.len();

    // Rimuovi il profilo (il SID resta derivabile per eventuali retry).
    let profile_removed = match delete_profile() {
        Ok(()) => true,
        Err(e) => {
            tracing::warn!("DeleteAppContainerProfile: {e:#}");
            false
        }
    };

    // Persisti SOLO le voci non revocate; se tutto e' andato, rimuovi il file.
    if remaining.is_empty() {
        let _ = std::fs::remove_file(&path);
    } else {
        let reg = AclRegistry { grants: remaining };
        if let Err(e) = reg.save(&path) {
            tracing::warn!("registro ACL residuo non salvato ({e})");
        }
    }
    // Azzera anche la cache in-processo, se inizializzata.
    if let Some(m) = ACL_REGISTRY.get() {
        if let Ok(mut g) = m.lock() {
            *g = AclRegistry::default();
        }
    }

    Ok(CleanupReport { total, revoked, failed, dropped, profile_removed })
}

// --- ACL ---------------------------------------------------------------------

/// Percorso del registro persistito delle concessioni ACL (W4.4), sotto la
/// stessa data_dir del resto dello stato (`config::Paths`, fonte unica §7.11).
fn registry_path() -> Result<PathBuf> {
    let paths = crate::config::Paths::resolve().context("risoluzione paths per registro ACL")?;
    Ok(paths.data_dir.join("acl_grants.json"))
}

/// Registro caricato una volta per processo e scritto in write-through: fa da
/// guardia d'idempotenza (niente ACE duplicati fra riavvii) e da traccia per la
/// rimozione all'unpair (W4.4).
static ACL_REGISTRY: OnceLock<Mutex<AclRegistry>> = OnceLock::new();

fn registry() -> &'static Mutex<AclRegistry> {
    ACL_REGISTRY.get_or_init(|| {
        let reg = registry_path().map(|p| AclRegistry::load(&p)).unwrap_or_default();
        Mutex::new(reg)
    })
}

/// Concede `mask` al SID del container sulla directory `root`, ereditata a
/// sotto-oggetti. Idempotente e PERSISTITA: se la coppia (root, sid) e' gia' nel
/// registro NON ri-aggiunge l'ACE (chiude il rischio ACE-duplicati che avevamo
/// segnalato); ogni nuova concessione e' registrata su disco per la revoca
/// all'unpair (W4.4).
fn grant_dir(sid: PSID, sid_str: &str, root: &Path, mask: u32) -> Result<()> {
    let path_str = root.display().to_string();
    let reg = registry();
    let mut guard = reg
        .lock()
        .map_err(|_| anyhow::anyhow!("registro ACL avvelenato (lock)"))?;
    // Applica SEMPRE l'ACE: il registro NON e' una cache di "gia' fatto". Una
    // dir gia' concessa puo' essere stata RICREATA fra due invocazioni (es.
    // `ensure_shim` rigenera lo shim con remove_dir_all + rename → l'ACE
    // on-disk sparisce con la vecchia inode mentre la voce di registro resta;
    // il container non legge piu' lo shim → ModuleNotFoundError al primo import).
    // `apply_acl` e' idempotente (SetEntriesInAclW fonde l'ACE del SID, niente
    // duplicato), quindi ri-applicare a ogni invocazione e' sicuro. Il registro
    // serve SOLO a sapere cosa REVOCARE all'unpair. Sotto lock: serializza i
    // grant concorrenti nello stesso processo.
    apply_acl(sid, root, mask, GRANT_ACCESS)?;
    // Persisti solo su voce NUOVA (record idempotente su (path,sid)): evita una
    // scrittura del registro a ogni invocazione per grant gia' noti.
    let added = guard.record(AclGrantRecord {
        path: path_str,
        sid: sid_str.to_string(),
        access_mask: mask,
        granted_at: common::now_epoch_secs(),
    });
    if added {
        match registry_path() {
            Ok(rp) => {
                if let Err(e) = guard.save(&rp) {
                    tracing::warn!(
                        "registro ACL non salvato ({e}): la rimozione all'unpair \
                         potrebbe perdere questa voce"
                    );
                }
            }
            Err(e) => tracing::warn!("path registro ACL non risolto ({e:#}): voce non persistita"),
        }
    }
    Ok(())
}

/// Applica una EXPLICIT_ACCESS al SID sulla directory: `GRANT_ACCESS` (con
/// `mask`) per concedere, `REVOKE_ACCESS` (mask ignorata) per togliere OGNI ACE
/// del SID (usato dalla pulizia all'unpair, W4.4).
fn apply_acl(sid: PSID, root: &Path, mask: u32, mode: ACCESS_MODE) -> Result<()> {
    let name = common::to_wide_null(&root.display().to_string());

    // 1. DACL corrente del file object.
    let mut pdacl: *mut ACL = std::ptr::null_mut();
    let mut psd: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    check_win32("GetNamedSecurityInfoW", unsafe {
        GetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut pdacl,
            std::ptr::null_mut(),
            &mut psd,
        )
    })?;
    let _sd = LocalPtr(psd); // free del security descriptor alla Drop

    // 2. una EXPLICIT_ACCESS che concede `mask` al SID del container.
    let mut ea: EXPLICIT_ACCESS_W = unsafe { std::mem::zeroed() };
    ea.grfAccessPermissions = mask;
    ea.grfAccessMode = mode;
    ea.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
    ea.Trustee.MultipleTrusteeOperation = NO_MULTIPLE_TRUSTEE;
    ea.Trustee.TrusteeForm = TRUSTEE_IS_SID;
    ea.Trustee.TrusteeType = TRUSTEE_IS_GROUP;
    // TRUSTEE_IS_SID: ptstrName porta il PSID (cast documentato).
    ea.Trustee.ptstrName = sid as *mut u16;

    // 3. fondi la nuova ACE nella DACL esistente.
    let mut new_acl: *mut ACL = std::ptr::null_mut();
    check_win32("SetEntriesInAclW", unsafe {
        SetEntriesInAclW(1, &ea, pdacl, &mut new_acl)
    })?;
    let _na = LocalPtr(new_acl as *mut core::ffi::c_void);

    // 4. riscrivi la DACL sul file object.
    check_win32("SetNamedSecurityInfoW", unsafe {
        SetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(), // psidowner (PSID, non toccato)
            std::ptr::null_mut(), // psidgroup (PSID, non toccato)
            new_acl,              // pdacl (*const ACL: *mut→*const per coercion)
            std::ptr::null(),     // psacl (*const ACL)
        )
    })?;
    Ok(())
}

// --- spawn -------------------------------------------------------------------

/// Coppia di pipe anonime; entrambi gli estremi ereditabili (poi il chiamante
/// rende NON ereditabile l'estremo che tiene il padre).
fn make_pipe() -> Result<(Handle, Handle)> {
    let mut sa: SECURITY_ATTRIBUTES = unsafe { std::mem::zeroed() };
    sa.nLength = std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32;
    sa.bInheritHandle = 1;
    let mut rd: HANDLE = std::ptr::null_mut();
    let mut wr: HANDLE = std::ptr::null_mut();
    check_bool("CreatePipe", unsafe { CreatePipe(&mut rd, &mut wr, &sa, 0) })?;
    Ok((Handle(rd), Handle(wr)))
}

fn set_no_inherit(h: HANDLE) -> Result<()> {
    check_bool("SetHandleInformation", unsafe {
        SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)
    })
}

/// Esegue l'executor nel container. Ogni errore PRIMA che CreateProcessW
/// riesca → `Unsupported` (degrado onesto). Dopo lo spawn, sempre `Ran`.
pub fn run_in_container(p: ContainerParams) -> Outcome {
    match try_run(p) {
        Ok(out) => out,
        Err(e) => Outcome::Unsupported(format!("{e:#}")),
    }
}

fn try_run(p: ContainerParams) -> Result<Outcome> {
    // 1. SID del profilo + sua forma stringa (chiave del registro ACL). Se non
    //    serializzabile, un segnaposto: la revoca all'unpair usa comunque il SID
    //    derivato dal nome, non questa stringa.
    let sid = ensure_profile_sid().context("profilo AppContainer")?;
    let sid_str = sid_to_string(sid.psid).unwrap_or_else(|e| {
        tracing::warn!("SID container non serializzabile ({e:#}): registro ACL usera' un segnaposto");
        "unknown".to_string()
    });

    // Rollback degli ACE se il container non parte oltre questo punto.
    let mut grant_guard = GrantGuard::new(sid.psid);

    // 2. concessioni IMPLICITE (indispensabili: senza, il container e'
    //    inutilizzabile → e' un caso di degrado onesto, non un container zoppo).
    //    Runtime python + shim + codice executor in lettura/esecuzione.
    if let Some(py_root) = p.python.parent() {
        grant_dir(sid.psid, &sid_str, py_root, common::ACCESS_READ)
            .context("ACL lettura runtime python")?;
        grant_guard.track(py_root);
    }
    grant_dir(sid.psid, &sid_str, &p.shim_dir, common::ACCESS_READ).context("ACL lettura shim")?;
    grant_guard.track(&p.shim_dir);
    grant_dir(sid.psid, &sid_str, &p.exec_dir, common::ACCESS_READ).context("ACL lettura executor")?;
    grant_guard.track(&p.exec_dir);
    // Scratch/TEMP in scrittura.
    grant_dir(sid.psid, &sid_str, &p.scratch_dir, common::ACCESS_WRITE)
        .context("ACL scrittura scratch")?;
    grant_guard.track(&p.scratch_dir);

    // 3. concessioni da capability (best-effort: radice inesistente = skip
    //    onesto; un ACL fallito su una radice utente NON e' un degrado — sara'
    //    l'executor a fallire onestamente su quel path).
    for g in &p.grants {
        if !g.root.exists() {
            tracing::debug!(root = %g.root.display(), "hint radice inesistente: skip");
            continue;
        }
        if let Err(e) = grant_dir(sid.psid, &sid_str, &g.root, g.access_mask()) {
            tracing::warn!(root = %g.root.display(), "ACL capability fallito: {e:#}");
        } else {
            grant_guard.track(&g.root);
        }
    }

    // 4. capability SID di rete (solo se richiesta).
    let net_sid = if p.want_net {
        Some(internet_client_sid().context("SID internetClient")?)
    } else {
        None
    };
    let mut cap_attrs: Vec<SID_AND_ATTRIBUTES> = Vec::new();
    if let Some(ns) = &net_sid {
        cap_attrs.push(SID_AND_ATTRIBUTES { Sid: ns.psid, Attributes: SE_GROUP_ENABLED });
    }

    // 5. SECURITY_CAPABILITIES: identita' del container + capability abilitate.
    let mut sec_caps = SECURITY_CAPABILITIES {
        AppContainerSid: sid.psid,
        Capabilities: if cap_attrs.is_empty() {
            std::ptr::null_mut()
        } else {
            cap_attrs.as_mut_ptr()
        },
        CapabilityCount: cap_attrs.len() as u32,
        Reserved: 0,
    };

    // 6. attribute list con l'attributo SECURITY_CAPABILITIES.
    let mut size: usize = 0;
    // Primo giro: ritorna FALSE e riempie `size` (atteso). Ignoriamo l'esito.
    unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut size) };
    if size == 0 {
        bail!("InitializeProcThreadAttributeList non ha restituito una dimensione");
    }
    let mut attr_buf: Vec<u8> = vec![0u8; size];
    let attr_ptr = attr_buf.as_mut_ptr() as LPPROC_THREAD_ATTRIBUTE_LIST;
    check_bool("InitializeProcThreadAttributeList", unsafe {
        InitializeProcThreadAttributeList(attr_ptr, 1, 0, &mut size)
    })?;
    let attr_list = AttrList(attr_ptr); // DeleteProcThreadAttributeList alla Drop
    check_bool("UpdateProcThreadAttribute", unsafe {
        UpdateProcThreadAttribute(
            attr_list.0,
            0,
            PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
            &mut sec_caps as *mut _ as *const core::ffi::c_void,
            std::mem::size_of::<SECURITY_CAPABILITIES>(),
            std::ptr::null_mut(),
            std::ptr::null(),
        )
    })?;

    // 7. pipe stdin/stdout/stderr; il padre tiene un estremo non ereditabile.
    let (stdin_rd, stdin_wr) = make_pipe().context("pipe stdin")?;
    set_no_inherit(stdin_wr.0)?;
    let (stdout_rd, stdout_wr) = make_pipe().context("pipe stdout")?;
    set_no_inherit(stdout_rd.0)?;
    let (stderr_rd, stderr_wr) = make_pipe().context("pipe stderr")?;
    set_no_inherit(stderr_rd.0)?;

    // 8. Job Object (strato risorse ORTOGONALE, coesiste col container).
    let job = crate::sandbox_windows::create_job().context("job object")?;

    // 9. STARTUPINFOEXW con handle standard + attribute list.
    let mut si: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
    si.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    si.StartupInfo.hStdInput = stdin_rd.0;
    si.StartupInfo.hStdOutput = stdout_wr.0;
    si.StartupInfo.hStdError = stderr_wr.0;
    si.lpAttributeList = attr_list.0;

    // 10. command line + environment + cwd in UTF-16 (buffer vivi fino a dopo
    //     CreateProcessW).
    let python_str = p.python.display().to_string();
    let entry_str = p.entry.display().to_string();
    let cmdline = common::build_command_line(&[python_str.clone(), entry_str]);
    let app_w = common::to_wide_null(&python_str);
    let mut cmdline_w = common::to_wide_null(&cmdline);
    let env_w = common::env_block_utf16(&p.env_pairs);
    let cwd_w = common::to_wide_null(&p.scratch_dir.display().to_string());

    // Diagnostica W4 (err 203): l'AppContainer richiede LOCALAPPDATA nel blocco
    // env per montare lo storage redirette. Logghiamo le CHIAVI presenti (no
    // valori: niente PII) per distinguere «env incompleto» da «attribute-list»
    // se CreateProcessW fallisce ancora ERROR_ENVVAR_NOT_FOUND.
    let has = |k: &str| p.env_pairs.iter().any(|(n, _)| n.eq_ignore_ascii_case(k));
    tracing::info!(
        pairs = p.env_pairs.len(),
        localappdata = has("LOCALAPPDATA"),
        appdata = has("APPDATA"),
        "env pronto per CreateProcessW (appcontainer)"
    );

    // 11. spawn CREATE_SUSPENDED (assegna al job PRIMA di risvegliarlo) +
    //     EXTENDED (attribute list) + UNICODE env + niente finestra.
    let mut pi: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    let flags = EXTENDED_STARTUPINFO_PRESENT
        | CREATE_SUSPENDED
        | CREATE_NO_WINDOW
        | CREATE_UNICODE_ENVIRONMENT;
    let ok = unsafe {
        CreateProcessW(
            app_w.as_ptr(),
            cmdline_w.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1, // bInheritHandles: solo i 3 estremi-figlio sono ereditabili
            flags,
            env_w.as_ptr() as *const core::ffi::c_void,
            cwd_w.as_ptr(),
            &si as *const STARTUPINFOEXW as *const STARTUPINFOW,
            &mut pi,
        )
    };
    check_bool("CreateProcessW (appcontainer)", ok)?;
    // Processo avviato: i grant servono ORA al figlio → niente rollback.
    grant_guard.disarm();

    // --- oltre questa linea: processo AVVIATO nel container → sempre Ran ---
    // Chiudi gli estremi-figlio nel padre (necessario per l'EOF ai lettori).
    drop(stdin_rd);
    drop(stdout_wr);
    drop(stderr_wr);

    let (stdout, stderr, timed_out) = run_child(
        job.raw(),
        pi,
        stdin_wr.release(),
        stdout_rd.release(),
        stderr_rd.release(),
        p.args_json,
        p.deadline_ms,
    );
    Ok(Outcome::Ran { stdout, stderr, timed_out })
    // `job`, `attr_list`, `sid`, `net_sid`, i buffer wide droppano qui.
}

/// Dopo lo spawn: assegna al job, risveglia, alimenta stdin, drena
/// stdout/stderr in parallelo (anti-deadlock del buffer pipe), attende con
/// deadline, al timeout termina l'albero via job. Non ritorna mai errore
/// (il processo e' gia' partito nel container: l'esito e' onesto per costruzione).
fn run_child(
    job: HANDLE,
    pi: PROCESS_INFORMATION,
    stdin_wr: HANDLE,
    stdout_rd: HANDLE,
    stderr_rd: HANDLE,
    args_json: String,
    deadline_ms: u64,
) -> (String, String, bool) {
    // Assegna PRIMA del resume: il figlio non gira mai fuori dal job.
    // Fail-closed: se l'assegnazione fallisce NON risvegliamo il processo. La
    // vecchia sequenza lo avviava comunque fuori dal job; al timeout
    // TerminateJobObject non lo raggiungeva e i reader delle pipe restavano in
    // join per sempre. TerminateProcess sul figlio ancora sospeso garantisce
    // EOF e il ritorno del worker.
    let assigned = unsafe { AssignProcessToJobObject(job, pi.hProcess) } != 0;
    let resumed = if assigned {
        (unsafe { ResumeThread(pi.hThread) }) != u32::MAX
    } else {
        false
    };
    let startup_failed = !assigned || !resumed;
    if !assigned {
        tracing::warn!(
            "AssignProcessToJobObject fallita (GetLastError={}): processo sospeso terminato",
            last_error()
        );
    } else if !resumed {
        tracing::warn!(
            "ResumeThread fallita (GetLastError={}): job e processo terminati",
            last_error()
        );
    }
    if startup_failed {
        unsafe {
            TerminateJobObject(job, 126);
            TerminateProcess(pi.hProcess, 126);
        }
    }

    // stdin: scrivi gli args e chiudi (il File chiude l'handle alla Drop).
    {
        let mut f = unsafe { File::from_raw_handle(stdin_wr as RawHandle) };
        let _ = f.write_all(args_json.as_bytes());
    }

    // stdout/stderr: due thread lettori concorrenti (il figlio potrebbe
    // riempire un buffer pipe mentre l'altro e' fermo → deadlock se seriale).
    let out_h = SendHandle(stdout_rd);
    let t_out = std::thread::spawn(move || read_all(out_h.take()));
    let err_h = SendHandle(stderr_rd);
    let t_err = std::thread::spawn(move || read_all(err_h.take()));

    let wait_ms = if startup_failed {
        5_000
    } else if deadline_ms == 0 {
        INFINITE
    } else {
        deadline_ms.min(u32::MAX as u64) as u32
    };
    let waited = unsafe { WaitForSingleObject(pi.hProcess, wait_ms) };
    // Solo WAIT_OBJECT_0 significa processo concluso. WAIT_FAILED e qualunque
    // esito inatteso non devono cadere nel join delle pipe con il figlio vivo.
    let timed_out = startup_failed || waited != WAIT_OBJECT_0;
    if timed_out {
        // Gemello del SIGKILL-al-gruppo: uccide l'albero; gli estremi-scrittura
        // del figlio si chiudono → i lettori ricevono EOF e i thread terminano.
        unsafe {
            TerminateJobObject(job, 137);
            // Rete diretta se il job non contiene il processo o la sua
            // terminazione fallisce. Sul processo gia' morto e' un no-op.
            TerminateProcess(pi.hProcess, 137);
            WaitForSingleObject(pi.hProcess, 5_000);
        }
    }

    let stdout = t_out.join().unwrap_or_default();
    let stderr = t_err.join().unwrap_or_default();
    unsafe {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    (
        String::from_utf8_lossy(&stdout).into_owned(),
        String::from_utf8_lossy(&stderr).into_owned(),
        timed_out,
    )
}

/// Legge un estremo-lettura di pipe fino a EOF; l'handle e' chiuso dal `File`
/// alla Drop.
fn read_all(handle: HANDLE) -> Vec<u8> {
    let mut f = unsafe { File::from_raw_handle(handle as RawHandle) };
    let mut buf = Vec::new();
    let _ = f.read_to_end(&mut buf);
    buf
}
