//! sandbox_common.rs — logica PURA condivisa fra i due sandbox (linux/windows).
//!
//! Qui vive tutto cio' che NON tocca API di sistema: derivazione hint→radice
//! (§9/§16.2), mappatura capability→permessi e i piccoli encoder Win32 (quoting
//! della command line, blocco environment UTF-16). Vantaggio: sono funzioni
//! deterministiche testabili su QUALUNQUE piattaforma (i test girano nella CI
//! Linux), mentre `appcontainer.rs` resta il solo modulo con FFI non
//! esercitabile fuori da Windows. Confine §7.9: codice deterministico, zero LLM.

// Gli encoder Win32 (quoting, env-block) e la mappatura capability→ACL sono
// consumati SOLO dal path Windows (`appcontainer.rs`, `#[cfg(windows)]`) e dai
// test. In un `cargo build` host non-Windows resterebbero non richiamati: qui
// li marchiamo dead-code-ammessi SOLO fuori Windows — sul target reale il lint
// resta attivo (nulla e' nascosto dove il codice davvero gira).
#![cfg_attr(not(windows), allow(dead_code))]

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::executors::Capability;

// Diritti d'accesso Win32 standard (valori stabili dell'ABI, identici a
// windows_sys::Win32::Foundation::GENERIC_* / Storage::FileSystem::DELETE).
// Ridichiarati qui come semplici u32 cosi' questo modulo resta puro (niente
// dipendenza da windows-sys, compila e si testa su Linux). §2.1 spec W4:
// dichiarazioni manuali minime preferite al pull di feature per una costante.
const GENERIC_READ: u32 = 0x8000_0000;
const GENERIC_WRITE: u32 = 0x4000_0000;
const GENERIC_EXECUTE: u32 = 0x2000_0000;
const DELETE: u32 = 0x0001_0000;

/// Maschera ACL per una radice in sola lettura (fs:read) — lettura + traversal
/// di directory (EXECUTE su una dir = attraversala).
pub const ACCESS_READ: u32 = GENERIC_READ | GENERIC_EXECUTE;
/// Maschera ACL per una radice scrivibile (fs:write) — lettura, scrittura,
/// traversal e cancellazione (tabella di verita' §2.2).
pub const ACCESS_WRITE: u32 = GENERIC_READ | GENERIC_WRITE | GENERIC_EXECUTE | DELETE;

/// Radice non-glob di un hint del manifest (`~/notes/**` → `~/notes`), con `~`
/// espanso alla home dell'utente del device. Punto CONDIVISO (W4.2: "stessa
/// derivazione hint→root di sandbox_linux"): su Linux diventa un bind bwrap, su
/// Windows la directory su cui si concede l'ACL al SID del container.
///
/// `dirs::home_dir()` risolve `C:\Users\<user>` su Windows e `/home/<user>` su
/// Linux: la stessa funzione serve entrambi. Radici relative senza `~/` o `/`
/// iniziale → `None` (non ancorabili). `*` da solo → `None` (troppo largo per
/// un permesso mirato: un hint "tutto" non e' un isolamento).
pub fn glob_root(hint: &str) -> Option<PathBuf> {
    if hint == "*" {
        return None;
    }
    let mut h = hint.to_string();
    for sep in ["/**", "/*", "**"] {
        if let Some(idx) = h.find(sep) {
            h.truncate(idx);
            break;
        }
    }
    if let Some(rest) = h.strip_prefix("~/") {
        return dirs::home_dir().map(|home| home.join(rest));
    }
    if h.starts_with('/') {
        // Anchora solo se ASSOLUTO PER LA PIATTAFORMA: su Linux "/tmp" e'
        // assoluto → bind bwrap; su Windows "/tmp" NON lo e' (manca drive/UNC),
        // e' un hint POSIX del manifest che NON deve produrre un ACL spurio su
        // "\tmp" (§7.3: un default Linux non trapela in un grant Windows).
        let pb = PathBuf::from(&h);
        return pb.is_absolute().then_some(pb);
    }
    None
}

/// Una concessione fs derivata dalle capability: radice + se scrivibile.
#[derive(Debug, Clone, PartialEq)]
pub struct HintGrant {
    pub root: PathBuf,
    pub write: bool,
}

impl HintGrant {
    /// Maschera ACL Win32 corrispondente (read-only vs read/write/delete).
    pub fn access_mask(&self) -> u32 {
        if self.write { ACCESS_WRITE } else { ACCESS_READ }
    }
}

/// Un ACL ereditabile sulla home intera o sulla radice di un volume e' troppo
/// ampio sia come autorita' sia come costo: Windows puo' propagare l'ACE su un
/// albero enorme prima ancora dello spawn (quindi fuori dalla deadline del
/// processo executor). Il chiamante Windows usa questo segnale per degradare
/// onestamente al Job Object; directory figlie specifiche restano idonee
/// all'AppContainer.
pub fn is_broad_acl_root(root: &Path, home: Option<&Path>) -> bool {
    home.is_some_and(|value| root == value) || root.parent().is_none()
}

/// Prima parola-chiave di un nome capability, con separatore `:` O `.` (il
/// vocabolario reale li mescola: `fs:read`, `network.read`, `net:read`,
/// `network:http`). Serve a classificare la famiglia senza una lista chiusa
/// hardcodata (§7.3).
fn cap_kind(name: &str) -> &str {
    let end = name.find([':', '.']).unwrap_or(name.len());
    &name[..end]
}

/// True se la capability chiede accesso di rete. Regola generale (non lista):
/// la famiglia e' esattamente `net` o `network` (copre `network.read`,
/// `network.write`, `network:http`, `net:read`, `net:google_vision`).
/// `exec_subprocess`/`exec_net` NON concedono rete (§2.2: exec = nessun
/// permesso specifico).
fn cap_is_network(name: &str) -> bool {
    matches!(cap_kind(name), "net" | "network")
}

/// True se la capability implica lo spawn di un SOTTOPROCESSO DI SISTEMA (tool
/// nativi come `tasklist`/`ps`/`pip`/`tesseract`, query WMI/RPC). Gemello di
/// `cap_is_network`: match sulle FAMIGLIE exec del vocabolario — `code:exec`
/// (famiglia `code`) piu' le storiche `exec_subprocess`/`exec_net`. Regola
/// capability-driven (§7.3), NON una lista di executor.
///
/// Motivo W4 (bug scoperto abilitando l'AppContainer in prod): il token
/// ristretto del container nega a questi tool l'accesso ai servizi di sistema
/// (get_processes → `tasklist rc=1: password non corretta`) → sotto AppContainer
/// fallirebbero. Il chiamante Windows declassa ONESTAMENTE al Job Object (§2.8),
/// che li contiene comunque (albero+memoria+conteggio processi).
fn cap_is_system_exec(name: &str) -> bool {
    matches!(cap_kind(name), "code" | "exec_subprocess" | "exec_net")
}

/// Traduce le capability del manifest in (concessioni fs, vuole-rete).
/// SOLO la famiglia `fs` diventa ACL su disco; `metnos`/`index`/`mail`/`time`
/// sono capability logiche mediate dal server, non toccano il filesystem del
/// device (come su Linux: solo `fs` → bind). Le radici NON sono controllate per
/// esistenza qui: lo skip-se-inesistente avviene al momento dell'applicazione
/// ACL (§4: "radici inesistenti → skip onesto, non crearle").
pub fn hint_grants(caps: &[Capability]) -> (Vec<HintGrant>, bool) {
    let mut grants = Vec::new();
    let mut want_net = false;
    for cap in caps {
        if cap_is_network(&cap.name) {
            want_net = true;
            continue;
        }
        if cap_kind(&cap.name) == "fs" {
            let write = cap.name.split(':').nth(1) == Some("write");
            for hint in &cap.hint {
                if let Some(root) = glob_root(hint) {
                    grants.push(HintGrant { root, write });
                }
            }
        }
    }
    (grants, want_net)
}

/// Accesso fs massimo richiesto dalle capability: `Some(true)` se l'executor
/// dichiara una `fs:write`, `Some(false)` se ha solo `fs:*` in lettura, `None`
/// se non tocca il filesystem. Serve a decidere la maschera dei grant DERIVATI
/// dagli arg (sotto): un executor senza capability fs non ottiene ACL sui path
/// che gli passiamo (non deve toccarli).
pub fn caps_fs_access(caps: &[Capability]) -> Option<bool> {
    let mut any_fs = false;
    let mut any_write = false;
    for cap in caps {
        if cap_kind(&cap.name) == "fs" {
            any_fs = true;
            if cap.name.split(':').nth(1) == Some("write") {
                any_write = true;
            }
        }
    }
    if any_fs { Some(any_write) } else { None }
}

/// True se UNA QUALUNQUE capability dell'executor implica lo spawn di un
/// sottoprocesso di sistema (`cap_is_system_exec`): l'AppContainer va SALTATO e
/// il contenimento declassato al Job Object (§2.8). Consumato da
/// `sandbox_windows::run_sandboxed` prima di costruire il container (W4).
pub fn needs_system_exec(caps: &[Capability]) -> bool {
    caps.iter().any(|c| cap_is_system_exec(&c.name))
}

/// True se `s` e' un path ANCORABILE (assoluto): POSIX `/…`, home `~/…`/`~\…`,
/// drive Windows `C:\…`/`C:/…`, o UNC `\\server\…`. Un path relativo non e'
/// ancorabile a una radice certa → escluso (niente grant su cwd arbitraria).
fn is_abs_pathish(s: &str) -> bool {
    let b = s.as_bytes();
    s.starts_with('/')
        || s.starts_with("~/")
        || s.starts_with("~\\")
        || s == "~"
        || s.starts_with("\\\\")
        || (b.len() >= 3
            && b[0].is_ascii_alphabetic()
            && b[1] == b':'
            && (b[2] == b'\\' || b[2] == b'/'))
}

/// Estrae i path-target CONCRETI dagli argomenti dell'invocazione (JSON): sono
/// il vero bersaglio del comando (`path`, `files[].path`, `paths[]`, …), non
/// gli hint illustrativi del manifest. Cosi' la sandbox forte concede al SID
/// del container esattamente le directory che il comando tocca (Documents,
/// Downloads, …), non solo gli scope-esempio.
///
/// Regola generale (§7.3, nessuna lista di chiavi hardcodata): si scandiscono
/// TUTTI i valori-stringa del JSON e si tengono quelli ANCORABILI (`is_abs_pathish`).
/// Un `path_template` (contiene `{campo}`) e' troncato al prefisso statico prima
/// della prima graffa: la dir-antenata e' comune a tutte le entry.
/// La risoluzione a directory-esistente e i grant li fa il chiamante Windows
/// (tocca il filesystem); qui resta pura e testabile ovunque.
pub fn extract_path_args(args_json: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let parsed: serde_json::Value = match serde_json::from_str(args_json) {
        Ok(v) => v,
        Err(_) => return out,
    };
    fn walk(v: &serde_json::Value, out: &mut Vec<String>) {
        match v {
            serde_json::Value::String(s) => {
                // Template con campi {…}: tieni solo il prefisso statico.
                let candidate = match s.find('{') {
                    Some(i) => &s[..i],
                    None => s.as_str(),
                };
                if is_abs_pathish(candidate) && !out.iter().any(|e| e == candidate) {
                    out.push(candidate.to_string());
                }
            }
            serde_json::Value::Array(a) => a.iter().for_each(|x| walk(x, out)),
            serde_json::Value::Object(m) => m.values().for_each(|x| walk(x, out)),
            _ => {}
        }
    }
    walk(&parsed, &mut out);
    out
}

/// True quando un argomento semanticamente di percorso e' relativo e quindi il
/// client non puo' ancorarlo PRIMA di avviare l'executor. La risoluzione reale
/// puo' dipendere dallo shim (`Documenti/...` -> workspace Metnos): concedere
/// un ACL basandosi sugli hint illustrativi del manifest sarebbe ambiguo e puo'
/// propagare su una directory utente molto ampia. Il chiamante Windows degrada
/// onestamente al Job Object, che mantiene contenimento di processo/risorse.
pub fn has_unanchored_path_args(args_json: &str) -> bool {
    let parsed: serde_json::Value = match serde_json::from_str(args_json) {
        Ok(v) => v,
        Err(_) => return false,
    };

    fn path_key(key: &str) -> bool {
        let key = key.to_ascii_lowercase().replace('-', "_");
        matches!(key.as_str(),
                 "path" | "paths" | "base_path" | "root" | "dir" |
                 "dirs" | "directory" | "directories" | "src" | "dst")
            || key.ends_with("_path")
            || key.ends_with("_paths")
            || key.ends_with("_dir")
            || key.ends_with("_directory")
    }

    fn walk(value: &serde_json::Value, is_path_value: bool) -> bool {
        match value {
            serde_json::Value::String(raw) if is_path_value => {
                let candidate = raw.split('{').next().unwrap_or(raw).trim();
                !candidate.is_empty()
                    && !candidate.contains("://")
                    && !is_abs_pathish(candidate)
            }
            serde_json::Value::Array(values) =>
                values.iter().any(|value| walk(value, is_path_value)),
            // Su un oggetto annidato il nome della chiave figlia e'
            // autoritativo: `files:[{path,content}]` non trasforma `content`
            // in un path soltanto perche' vive dentro la collezione `files`.
            serde_json::Value::Object(values) => values.iter().any(
                |(key, value)| walk(value, path_key(key))),
            _ => false,
        }
    }

    walk(&parsed, false)
}

/// Quoting di un argomento per la command line Win32 (`CreateProcessW` riceve
/// UNA stringa, non un argv). Algoritmo canonico Microsoft: raddoppia i
/// backslash solo quando precedono una virgoletta (o la virgoletta di
/// chiusura). Necessario perche' i path Windows contengono `\` e spazi.
pub fn quote_win_arg(arg: &str) -> String {
    let needs_quote = arg.is_empty()
        || arg.chars().any(|c| c == ' ' || c == '\t' || c == '"');
    if !needs_quote {
        return arg.to_string();
    }
    let chars: Vec<char> = arg.chars().collect();
    let mut s = String::from("\"");
    let mut i = 0;
    while i < chars.len() {
        let mut backslashes = 0;
        while i < chars.len() && chars[i] == '\\' {
            backslashes += 1;
            i += 1;
        }
        if i == chars.len() {
            // backslash finali prima della virgoletta di chiusura: raddoppia.
            for _ in 0..backslashes * 2 {
                s.push('\\');
            }
        } else if chars[i] == '"' {
            for _ in 0..backslashes * 2 + 1 {
                s.push('\\');
            }
            s.push('"');
            i += 1;
        } else {
            for _ in 0..backslashes {
                s.push('\\');
            }
            s.push(chars[i]);
            i += 1;
        }
    }
    s.push('"');
    s
}

/// Command line completa (`argv[0]` + argomenti) come UNA stringa quotata,
/// pronta per il buffer mutabile passato a `CreateProcessW`.
pub fn build_command_line(parts: &[String]) -> String {
    parts.iter().map(|p| quote_win_arg(p)).collect::<Vec<_>>().join(" ")
}

/// Blocco environment UTF-16 per `CreateProcessW` (con
/// `CREATE_UNICODE_ENVIRONMENT`): sequenza di `CHIAVE=VALORE\0`, chiusa da un
/// `\0` finale. Un environment vuoto e' `\0\0`.
pub fn env_block_utf16(pairs: &[(String, String)]) -> Vec<u16> {
    let mut block: Vec<u16> = Vec::new();
    for (k, v) in pairs {
        block.extend(k.encode_utf16());
        block.push(u16::from(b'='));
        block.extend(v.encode_utf16());
        block.push(0);
    }
    block.push(0);
    if pairs.is_empty() {
        // Blocco degenere: due null (contratto Win32 per env vuoto).
        block.push(0);
    }
    block
}

/// Stringa UTF-16 NUL-terminata (PCWSTR/PWSTR) da un &str.
pub fn to_wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

// --- Registro persistito delle concessioni ACL (W4.4) ------------------------

/// Secondi dall'epoch UNIX (nessuna dipendenza da un crate di date, §7.2).
pub fn now_epoch_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Una concessione ACL registrata: cosa e' stato concesso a chi e quando, per
/// poterlo RIMUOVERE all'unpair (W4.4). Persistita in `acl_grants.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AclGrantRecord {
    /// Directory su cui e' stato aggiunto l'ACE.
    pub path: String,
    /// SID del container (forma stringa, es. `S-1-15-2-...`).
    pub sid: String,
    /// Maschera d'accesso concessa (diagnostica; la revoca toglie ogni ACE del SID).
    pub access_mask: u32,
    pub granted_at: u64,
}

/// Registro persistito delle concessioni ACL. Fa da GUARDIA d'idempotenza
/// (niente ACE duplicati fra riavvii) e da traccia per la rimozione all'unpair.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct AclRegistry {
    pub grants: Vec<AclGrantRecord>,
}

impl AclRegistry {
    /// Carica dal disco; file assente o corrotto → registro vuoto (tollerante:
    /// un registro illeggibile non deve bloccare l'esecuzione).
    pub fn load(path: &Path) -> Self {
        std::fs::read(path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default()
    }

    /// Scrive atomicamente (tmp + rename), creando la dir se manca.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let bytes = serde_json::to_vec_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, bytes)?;
        std::fs::rename(&tmp, path)
    }

    /// True se esiste gia' una concessione per (path, sid).
    pub fn contains(&self, path: &str, sid: &str) -> bool {
        self.grants.iter().any(|g| g.path == path && g.sid == sid)
    }

    /// Registra una concessione se assente (idempotente su (path,sid)). Ritorna
    /// true se aggiunta, false se gia' presente.
    pub fn record(&mut self, rec: AclGrantRecord) -> bool {
        if self.contains(&rec.path, &rec.sid) {
            return false;
        }
        self.grants.push(rec);
        true
    }

    /// Estrae tutte le concessioni svuotando il registro (per la pulizia).
    pub fn take_all(&mut self) -> Vec<AclGrantRecord> {
        std::mem::take(&mut self.grants)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executors::Capability;

    fn cap(name: &str, hints: &[&str]) -> Capability {
        Capability {
            name: name.to_string(),
            hint: hints.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn glob_root_strips_wildcards_and_expands_home() {
        let home = dirs::home_dir().expect("home");
        assert_eq!(glob_root("~/notes/**"), Some(home.join("notes")));
        assert_eq!(glob_root("~/Documents/*"), Some(home.join("Documents")));
        assert_eq!(glob_root("/tmp/**"), Some(PathBuf::from("/tmp")));
        assert_eq!(glob_root("*"), None, "wildcard universale non ancorabile");
        assert_eq!(glob_root("relativo/x"), None, "senza ancora ~ o / = None");
        // Su questa piattaforma (CI Linux) "/tmp" E' assoluto → ancorato. Su
        // Windows lo stesso hint NON e' assoluto e glob_root ritorna None (niente
        // grant spurio su \tmp): la logica e' `pb.is_absolute()`, delegata a std.
        assert!(PathBuf::from("/tmp").is_absolute(), "precondizione del test (Linux)");
        assert_eq!(glob_root("/var/data/**"), Some(PathBuf::from("/var/data")));
    }

    #[test]
    fn cap_kind_handles_both_separators() {
        assert_eq!(cap_kind("fs:read"), "fs");
        assert_eq!(cap_kind("network.read"), "network");
        assert_eq!(cap_kind("net:google_vision"), "net");
        assert_eq!(cap_kind("exec_subprocess"), "exec_subprocess");
    }

    #[test]
    fn network_detection_matches_real_vocab() {
        for n in ["network.read", "network.write", "network:http", "net:read"] {
            assert!(cap_is_network(n), "{n} deve valere rete");
        }
        for n in ["fs:read", "fs:write", "exec_subprocess", "exec_net", "metnos:read"] {
            assert!(!cap_is_network(n), "{n} NON deve valere rete");
        }
    }

    #[test]
    fn system_exec_detection_matches_real_vocab() {
        // Le 3 famiglie exec del vocabolario reale (code:exec, exec_subprocess,
        // exec_net): get_processes/find_packages/read_files_ocr e find/read_contacts.
        for n in ["code:exec", "exec_subprocess", "exec_net"] {
            assert!(cap_is_system_exec(n), "{n} deve valere system-exec");
        }
        // Tutte le altre famiglie: AppContainer-izzabili → NON system-exec.
        for n in [
            "fs:read", "fs:write", "net:read", "network.read", "network:http",
            "metnos:read", "metnos:write", "index.read", "mail:send", "time:read",
        ] {
            assert!(!cap_is_system_exec(n), "{n} NON deve valere system-exec");
        }
    }

    #[test]
    fn needs_system_exec_any_cap_triggers() {
        // get_processes reale: solo code:exec.
        assert!(needs_system_exec(&[cap("code:exec", &["ps", "/proc"])]));
        // find_contacts reale: exec_subprocess + exec_net.
        assert!(needs_system_exec(&[cap("exec_subprocess", &[]), cap("exec_net", &[])]));
        // Mista: basta UNA capability exec a saltare l'AppContainer.
        assert!(needs_system_exec(&[cap("fs:write", &["~/x/**"]), cap("code:exec", &[])]));
        // Solo fs/net → AppContainer-izzabile.
        assert!(!needs_system_exec(&[cap("fs:read", &["~/x/**"]), cap("network.read", &[])]));
        assert!(!needs_system_exec(&[]), "nessuna capability → non system-exec");
    }

    #[test]
    fn hint_grants_maps_fs_and_net() {
        let home = dirs::home_dir().expect("home");
        let caps = vec![
            cap("fs:read", &["~/notes/**", "/tmp/**"]),
            cap("fs:write", &["~/Documents/**"]),
            cap("network.read", &["https://*"]),
            cap("metnos:read", &[]), // logica: nessun ACL su disco
        ];
        let (grants, want_net) = hint_grants(&caps);
        assert!(want_net, "network.read deve chiedere rete");
        assert_eq!(
            grants,
            vec![
                HintGrant { root: home.join("notes"), write: false },
                HintGrant { root: PathBuf::from("/tmp"), write: false },
                HintGrant { root: home.join("Documents"), write: true },
            ]
        );
        assert_eq!(grants[2].access_mask(), ACCESS_WRITE);
        assert_eq!(grants[0].access_mask(), ACCESS_READ);
    }

    #[test]
    fn hint_grants_no_net_when_absent() {
        let (_g, want_net) = hint_grants(&[cap("fs:read", &["~/x/**"])]);
        assert!(!want_net);
    }

    #[test]
    fn relative_path_args_are_unanchored_but_content_is_not() {
        assert!(has_unanchored_path_args(
            r#"{"base_path":"Documenti/Progetto Atlas","patterns":["*.pdf"]}"#));
        assert!(has_unanchored_path_args(
            r#"{"files":[{"path":"report.xlsx","content":"hello"}]}"#));
        assert!(!has_unanchored_path_args(
            r#"{"files":[{"path":"C:\\Atlas\\report.xlsx","content":"relative/text"}]}"#));
        assert!(!has_unanchored_path_args(
            r#"{"path":"C:\\Atlas","file_type":"pdf","source_name":"a.pdf"}"#));
    }

    #[test]
    fn broad_acl_root_rejects_home_and_volume_not_children() {
        let home = PathBuf::from("/home/user");
        assert!(is_broad_acl_root(&home, Some(&home)));
        assert!(is_broad_acl_root(Path::new("/"), Some(&home)));
        assert!(!is_broad_acl_root(
            Path::new("/home/user/Documents/Atlas"), Some(&home)));
    }

    #[test]
    fn caps_fs_access_reports_max_mode() {
        assert_eq!(caps_fs_access(&[cap("fs:read", &[])]), Some(false));
        assert_eq!(
            caps_fs_access(&[cap("fs:read", &[]), cap("fs:write", &[])]),
            Some(true),
            "una sola fs:write basta a promuovere a write"
        );
        assert_eq!(caps_fs_access(&[cap("network.read", &[])]), None,
                   "nessuna capability fs → nessun grant da arg");
        assert_eq!(caps_fs_access(&[]), None);
    }

    #[test]
    fn extract_path_args_keeps_only_anchorable_targets() {
        // Forma scalare + lista + valore non-path: solo i path ancorabili.
        let j = r#"{"path":"C:\\Users\\rober\\Documents\\nota.txt",
                    "content":"ciao non-path",
                    "paths":["/tmp/a.txt","D:/dati/b.csv","relativo/c"],
                    "count": 3}"#;
        let got = extract_path_args(j);
        assert!(got.contains(&r"C:\Users\rober\Documents\nota.txt".to_string()));
        assert!(got.contains(&"/tmp/a.txt".to_string()));
        assert!(got.contains(&"D:/dati/b.csv".to_string()));
        assert!(!got.iter().any(|s| s.contains("ciao")), "il contenuto non e' un target");
        assert!(!got.iter().any(|s| s == "relativo/c"), "relativo non ancorabile");
    }

    #[test]
    fn extract_path_args_truncates_template_at_first_brace() {
        let j = r#"{"path_template":"/opt/metnos/issues/issue_{number}.json",
                   "home":"~/notes/scratch/x.txt"}"#;
        let got = extract_path_args(j);
        assert!(got.contains(&"/opt/metnos/issues/issue_".to_string()),
                "prefisso statico prima della graffa");
        assert!(got.contains(&"~/notes/scratch/x.txt".to_string()));
    }

    #[test]
    fn extract_path_args_dedups_and_survives_bad_json() {
        let j = r#"{"a":"/tmp/x","b":"/tmp/x"}"#;
        assert_eq!(extract_path_args(j), vec!["/tmp/x".to_string()]);
        assert_eq!(extract_path_args("non-json"), Vec::<String>::new());
    }

    #[test]
    fn quote_win_arg_rules() {
        assert_eq!(quote_win_arg("semplice"), "semplice");
        assert_eq!(quote_win_arg("con spazio"), "\"con spazio\"");
        assert_eq!(
            quote_win_arg(r"C:\Program Files\python.exe"),
            "\"C:\\Program Files\\python.exe\""
        );
        // virgoletta interna: backslash-escape.
        assert_eq!(quote_win_arg(r#"a"b"#), r#""a\"b""#);
        // backslash finale dentro un arg quotato: raddoppiato prima di ".
        assert_eq!(quote_win_arg(r"dir\ x\"), "\"dir\\ x\\\\\"");
        assert_eq!(quote_win_arg(""), "\"\"", "arg vuoto = coppia di virgolette");
    }

    #[test]
    fn build_command_line_quotes_only_when_needed() {
        // argv[0] senza spazi → NON quotato; l'arg con spazio → quotato.
        let cl = build_command_line(&[
            r"C:\py\python.exe".to_string(),
            r"C:\code\exec entry.py".to_string(),
        ]);
        assert_eq!(cl, "C:\\py\\python.exe \"C:\\code\\exec entry.py\"");
    }

    #[test]
    fn env_block_utf16_double_null_terminated() {
        let b = env_block_utf16(&[("A".into(), "1".into())]);
        assert_eq!(b, vec![b'A' as u16, b'=' as u16, b'1' as u16, 0, 0]);
        let empty = env_block_utf16(&[]);
        assert_eq!(empty, vec![0, 0], "env vuoto = due null");
    }

    #[test]
    fn to_wide_null_terminates() {
        let w = to_wide_null("ab");
        assert_eq!(w, vec![b'a' as u16, b'b' as u16, 0]);
    }

    fn rec(path: &str, sid: &str) -> AclGrantRecord {
        AclGrantRecord {
            path: path.to_string(),
            sid: sid.to_string(),
            access_mask: ACCESS_WRITE,
            granted_at: 1_700_000_000,
        }
    }

    #[test]
    fn acl_registry_record_is_idempotent() {
        let mut r = AclRegistry::default();
        assert!(r.record(rec("/a", "S-1")), "prima aggiunta");
        assert!(!r.record(rec("/a", "S-1")), "duplicato (path,sid) NON riaggiunto");
        assert!(r.record(rec("/a", "S-2")), "sid diverso = altra voce");
        assert!(r.record(rec("/b", "S-1")), "path diverso = altra voce");
        assert_eq!(r.grants.len(), 3);
        assert!(r.contains("/a", "S-1"));
        assert!(!r.contains("/a", "S-9"));
    }

    #[test]
    fn acl_registry_roundtrip_and_drain() {
        let path = std::env::temp_dir()
            .join(format!("metnos-acl-test-{}.json", std::process::id()));
        let _ = std::fs::remove_file(&path);

        let mut r = AclRegistry::default();
        r.record(rec("/x", "S-1"));
        r.record(rec("/y", "S-1"));
        r.save(&path).expect("save");

        let loaded = AclRegistry::load(&path);
        assert_eq!(loaded.grants, r.grants, "round-trip disco preserva le voci");

        let mut loaded = loaded;
        let drained = loaded.take_all();
        assert_eq!(drained.len(), 2);
        assert!(loaded.grants.is_empty(), "take_all svuota il registro");

        // File assente → registro vuoto (tollerante).
        let _ = std::fs::remove_file(&path);
        assert!(AclRegistry::load(&path).grants.is_empty());
    }
}
