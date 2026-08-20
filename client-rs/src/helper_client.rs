//! Parlare con l'aiutante elevato, verificando CON CHI si sta parlando.
//!
//! E' l'altra meta' dell'autenticazione a due direzioni (ADR 0210 D2), e la
//! meta' che si dimentica.
//!
//! L'aiutante controlla chi lo chiama, e va bene. Ma il nome di una pipe non
//! e' un segreto: un processo senza privilegi che la crea PRIMA tiene il nome,
//! e chi si collega gli consegna le proprie richieste firmate credendo di
//! parlare col servizio di sistema. Il danno non e' l'esecuzione — l'impostore
//! non ha privilegi — e' la raccolta: richieste firmate valide, da rigiocare
//! altrove.
//!
//! Percio' prima di scrivere QUALUNQUE COSA il client si accerta di tre fatti,
//! tutti chiesti al sistema operativo e nessuno dichiarato dal messaggio:
//!
//! 1. dall'altro capo c'e' un processo che gira come `LocalSystem`;
//! 2. il suo eseguibile e' quello installato sotto Program Files;
//! 3. la pipe e' locale.
//!
//! Se uno solo non regge, non si scrive niente e si dice perche'.
//!
//! Il modulo non chiama nessuna API di Windows: raccoglie il GIUDIZIO su tre
//! fatti che qualcun altro ha raccolto. Cosi' si prova su qualunque macchina,
//! ed e' la parte che deve essere giusta.

use anyhow::{anyhow, Result};

/// Il SID del sistema locale. Non e' un valore configurabile: e' la costante
/// con cui Windows identifica se stesso.
const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
/// Il gruppo amministratori predefinito di Windows.
const ADMINISTRATORS_SID: &str = "S-1-5-32-544";

/// I proprietari che bastano a garantire che l'oggetto lo abbia creato
/// qualcuno con privilegi.
///
/// Windows non assegna sempre l'oggetto all'account che lo crea: con
/// l'impostazione predefinita del token, un processo elevato produce oggetti
/// di proprieta' del GRUPPO amministratori. Pretendere esattamente l'account
/// sistema significava rifiutare l'aiutante vero su una macchina normale
/// (misurato: `S-1-5-32-544`, PC di Roberto, 19/8/2026).
///
/// La garanzia non cambia: un utente senza privilegi non puo' creare un
/// oggetto di proprieta' ne' del sistema ne' degli amministratori — per
/// assegnare un proprietario bisogna esserlo, o averne il privilegio. Chi
/// volesse prendere il posto dell'aiutante dovrebbe gia' avere quei
/// privilegi, e a quel punto non avrebbe bisogno di fingersi nessuno.
const PROPRIETARI_PRIVILEGIATI: [&str; 2] = [LOCAL_SYSTEM_SID, ADMINISTRATORS_SID];

/// Le tre operazioni, come le vede il client. Specchio del vocabolario chiuso
/// dell'aiutante: se qui comparisse un quarto verbo, non avrebbe nessuno che
/// lo esegue.
/// La versione del protocollo parlato su questo canale.
///
/// Copia byte-identica della costante dell'aiutante: i due programmi sono
/// separati apposta e nessuno dei due puo' leggere il codice dell'altro. E'
/// la LINGUA, non la build: due versioni diverse dei due programmi si
/// capiscono benissimo se questa combacia, e non si capiscono affatto se non
/// combacia — che e' esattamente la differenza da saper dire.
pub const PROTOCOL_VERSION: u32 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    Query,
    Install,
    Uninstall,
    /// Quale versione dell'aiutante c'e'. Non tocca niente e non nomina
    /// nessun pacchetto: e' l'unica voce che non parla di pacchetti.
    ///
    /// Serve a rendere il disallineamento fra i due programmi un fatto che
    /// si puo' CHIEDERE. Senza, si scopre come un guasto — una richiesta che
    /// l'altro capo non capisce, e nessun modo di dire perche'.
    Version,
}

/// Closed lifetime values for the dedicated managed-start request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StartLifetime {
    Session,
    Persistent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderInterface {
    HardwareSensorsV1,
}

impl ProviderInterface {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "hardware_sensors_v1" => Some(Self::HardwareSensorsV1),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::HardwareSensorsV1 => "hardware_sensors_v1",
        }
    }
}

impl StartLifetime {
    pub fn as_str(self) -> &'static str {
        match self {
            StartLifetime::Session => "session",
            StartLifetime::Persistent => "persistent",
        }
    }
}

impl Operation {
    fn as_str(self) -> &'static str {
        match self {
            Operation::Query => "query",
            Operation::Install => "install",
            Operation::Uninstall => "uninstall",
            Operation::Version => "version",
        }
    }
}

/// Il corpo su cui si calcola la firma.
///
/// DEVE coincidere con `protocol::Request::canonical_body` dell'aiutante,
/// campo per campo e separatore per separatore. Non e' una duplicazione
/// evitabile: i due programmi sono binari separati per scelta, e condividere
/// una libreria significherebbe che l'aiutante linka codice del client — cioe'
/// esattamente cio' che la separazione esiste per impedire.
///
/// Il vincolo e' presidiato da un test che confronta le due stringhe.
pub fn canonical_body(
    operation: Operation,
    source: &str,
    package_id: &str,
    version: Option<&str>,
    idempotency_key: &str,
) -> String {
    format!(
        "{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
        operation.as_str(),
        source,
        package_id,
        version.unwrap_or(""),
        idempotency_key,
    )
}

/// Signed body for managed start.
///
/// This mirrors `ManagedStartRequest::canonical_body` in the helper. The
/// fixed domain separator prevents a signature from authorising a package
/// operation, and no caller-controlled path or command exists in the shape.
pub fn canonical_start_body(
    source: &str,
    package_id: &str,
    lifetime: StartLifetime,
    idempotency_key: &str,
) -> String {
    format!(
        "managed-start\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
        source,
        package_id,
        lifetime.as_str(),
        idempotency_key,
    )
}

pub fn canonical_provider_grant_body(
    invocation_id: &str,
    manifest_sha256: &str,
    dependency_key: &str,
    source: &str,
    package_id: &str,
    interface: ProviderInterface,
    assembly: &str,
    entry_type: &str,
    domains: &[String],
    sensor_types: &[String],
) -> String {
    format!(
        "managed-provider-grant\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
        invocation_id,
        manifest_sha256,
        dependency_key,
        source,
        package_id,
        interface.as_str(),
        assembly,
        entry_type,
        domains.join(","),
        sensor_types.join(","),
    )
}

pub fn canonical_provider_body(
    grant_body: &str,
    grant_signature: &str,
    idempotency_key: &str,
) -> String {
    format!(
        "managed-provider\u{1f}{}\u{1f}{}\u{1f}{}",
        grant_body, grant_signature, idempotency_key,
    )
}

/// Il prefisso delle pipe locali. `.` e' la macchina corrente: una pipe su
/// un'altra macchina sarebbe rete travestita da pipe.
const LOCAL_PIPE_PREFIX: &str = r"\\.\pipe\";

/// La radice del nome della pipe. SPECCHIO di `channel::PIPE_ROOT`
/// dell'aiutante: stessa ragione della duplicazione di `canonical_body`, e
/// stesso presidio (il test che confronta i due sorgenti).
const PIPE_ROOT: &str = "metnos-helper";

/// Il nome dell'eseguibile installato. SPECCHIO di `win_setup`.
const HELPER_EXECUTABLE: &str = "metnos-helper.exe";

/// Vero quando la stringa e' un SID nella forma testuale di Windows.
///
/// Il SID arriva dal sistema operativo, quindi e' valido per costruzione. Si
/// controlla lo stesso perche' finisce dentro il NOME di un oggetto di
/// sistema, e un nome costruito con un valore non verificato e' un modo di
/// farlo puntare altrove. Specchio di `channel::is_valid_sid`.
pub fn is_valid_sid(value: &str) -> bool {
    let mut parti = value.split('-');
    if parti.next() != Some("S") {
        return false;
    }
    let numeriche: Vec<&str> = parti.collect();
    if numeriche.len() < 2 || numeriche.len() > 16 {
        return false;
    }
    numeriche
        .iter()
        .all(|p| !p.is_empty() && p.len() <= 20 && p.chars().all(|c| c.is_ascii_digit()))
}

/// Il nome della pipe di questo proprietario. SPECCHIO di
/// `channel::pipe_name_for_owner`: se le due formule divergessero, il client
/// aprirebbe un nome che non esiste e leggerebbe «aiutante assente» quando
/// l'aiutante c'e'.
pub fn pipe_name_for_owner(owner_sid: &str) -> Option<String> {
    if !is_valid_sid(owner_sid) {
        return None;
    }
    Some(format!("{LOCAL_PIPE_PREFIX}{PIPE_ROOT}-{owner_sid}"))
}

/// Dove deve stare l'aiutante perche' sia l'aiutante. SPECCHIO di
/// `setup::install_dir` + `win_setup::installa_eseguibile`.
///
/// Non e' una preferenza configurabile: e' la cartella che un utente senza
/// privilegi non puo' riscrivere. Prenderla da una configurazione
/// significherebbe che chi puo' modificare quella configurazione decide chi
/// e' l'aiutante.
pub fn helper_executable_in(program_files: &str) -> String {
    format!(
        "{}\\Metnos\\{}",
        program_files.trim_end_matches('\\'),
        HELPER_EXECUTABLE
    )
}

/// Perche' non si e' potuto parlare con l'aiutante.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChannelRefusal {
    /// Il nome non punta alla macchina corrente.
    NotLocal,
    /// Dall'altro capo non c'e' un processo di sistema.
    NotLocalSystem(String),
    /// L'eseguibile non e' quello installato.
    UnexpectedExecutable(String),
    /// La pipe non c'e': l'aiutante non e' installato o non gira.
    NotAvailable,
}

impl ChannelRefusal {
    pub fn code(&self) -> &'static str {
        match self {
            ChannelRefusal::NotLocal => "pipe_not_local",
            ChannelRefusal::NotLocalSystem(_) => "peer_not_local_system",
            ChannelRefusal::UnexpectedExecutable(_) => "peer_unexpected_executable",
            ChannelRefusal::NotAvailable => "helper_not_available",
        }
    }

    /// Che cosa dire a una persona.
    pub fn message(&self) -> String {
        match self {
            ChannelRefusal::NotAvailable => {
                "Il componente amministrativo di Metnos non e' installato su questo \
computer, oppure non e' in esecuzione."
                    .into()
            }
            ChannelRefusal::NotLocal => {
                "Il canale verso il componente amministrativo non e' locale: non ci \
parlo."
                    .into()
            }
            ChannelRefusal::NotLocalSystem(chi) => format!(
                "Dall'altro capo del canale non c'e' il servizio di sistema ma «{chi}». \
Non mando niente: qualcuno potrebbe aver preso il posto del componente."
            ),
            ChannelRefusal::UnexpectedExecutable(percorso) => format!(
                "Il programma all'altro capo del canale non e' quello installato \
(«{percorso}»). Non mando niente."
            ),
        }
    }
}

/// Verifica che il processo dall'altro capo sia davvero l'aiutante.
///
/// Prende i tre fatti gia' raccolti dal sistema operativo, cosi' la decisione
/// si prova senza aprire una pipe vera. La raccolta e' altrove; qui c'e' il
/// giudizio, che e' la parte che deve essere giusta.
pub fn judge_peer(
    pipe_name: &str,
    peer_sid: &str,
    peer_executable: &str,
    expected_executable: &str,
) -> Result<(), ChannelRefusal> {
    if !pipe_name.starts_with(r"\\.\pipe\") || pipe_name.len() <= r"\\.\pipe\".len() {
        return Err(ChannelRefusal::NotLocal);
    }
    if !PROPRIETARI_PRIVILEGIATI.contains(&peer_sid) {
        return Err(ChannelRefusal::NotLocalSystem(peer_sid.to_string()));
    }
    // Confronto senza distinzione fra maiuscole e minuscole: i percorsi di
    // Windows non la fanno, e un confronto sensibile rifiuterebbe l'aiutante
    // vero solo perche' il sistema ha scritto «C:\PROGRAM FILES».
    if !peer_executable.eq_ignore_ascii_case(expected_executable) {
        return Err(ChannelRefusal::UnexpectedExecutable(
            peer_executable.to_string(),
        ));
    }
    Ok(())
}

/// Una chiave d'idempotenza nuova: 32 cifre esadecimali.
///
/// Casuale, non un contatore. Un contatore ripartirebbe da capo dopo una
/// reinstallazione del client, e le richieste nuove sembrerebbero vecchie.
pub fn new_idempotency_key() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Il messaggio da mandare, firmato.
pub fn build_request(
    identity: &crate::identity::Identity,
    operation: Operation,
    package_id: &str,
    version: Option<&str>,
) -> Result<String> {
    use ed25519_dalek::Signer;
    let key = new_idempotency_key();
    let body = canonical_body(operation, "winget", package_id, version, &key);
    let firma = identity.signing.sign(body.as_bytes());
    let corpo = serde_json::json!({
        "operation": operation.as_str(),
        "source": "winget",
        "package_id": package_id,
        "version": version,
        "idempotency_key": key,
        "signature": firma.to_bytes().iter().map(|b| format!("{b:02x}")).collect::<String>(),
    });
    serde_json::to_string(&corpo).map_err(|e| anyhow!("richiesta non serializzabile: {e}"))
}

/// Build the separate managed-start request.
pub fn build_start_request(
    identity: &crate::identity::Identity,
    package_id: &str,
    lifetime: StartLifetime,
) -> Result<String> {
    use ed25519_dalek::Signer;
    let key = new_idempotency_key();
    let body = canonical_start_body("winget", package_id, lifetime, &key);
    let signature = identity.signing.sign(body.as_bytes());
    let request = serde_json::json!({
        "source": "winget",
        "package_id": package_id,
        "lifetime": lifetime.as_str(),
        "idempotency_key": key,
        "signature": signature.to_bytes().iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>(),
    });
    serde_json::to_string(&request)
        .map_err(|error| anyhow!("managed-start request is not serializable: {error}"))
}

/// Build one provider request from the server-signed grant carried by the
/// already verified invocation. No caller-selected path or code is accepted.
pub fn build_provider_request(
    identity: &crate::identity::Identity,
    grant: &crate::wire::ManagedProviderGrant,
) -> Result<String> {
    use ed25519_dalek::Signer;
    let provider = ProviderInterface::parse(&grant.interface)
        .ok_or_else(|| anyhow!("managed provider interface is unknown"))?;
    let key = new_idempotency_key();
    let grant_body = canonical_provider_grant_body(
        &grant.invocation_id,
        &grant.manifest_sha256,
        &grant.dependency_key,
        &grant.source,
        &grant.package_id,
        provider,
        &grant.assembly,
        &grant.entry_type,
        &grant.domains,
        &grant.sensor_types,
    );
    let body = canonical_provider_body(&grant_body, &grant.server_sig, &key);
    let signature = identity.signing.sign(body.as_bytes());
    let request = serde_json::json!({
        "source": grant.source,
        "package_id": grant.package_id,
        "interface": provider.as_str(),
        "assembly": grant.assembly,
        "entry_type": grant.entry_type,
        "domains": grant.domains,
        "sensor_types": grant.sensor_types,
        "invocation_id": grant.invocation_id,
        "manifest_sha256": grant.manifest_sha256,
        "dependency_key": grant.dependency_key,
        "grant_signature": grant.server_sig,
        "idempotency_key": key,
        "signature": signature.to_bytes().iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>(),
    });
    serde_json::to_string(&request)
        .map_err(|error| anyhow!("managed-provider request is not serializable: {error}"))
}

#[cfg(test)]
mod tests {

    #[test]
    fn un_canale_di_proprieta_degli_amministratori_e_autentico() {
        // Windows non assegna sempre l'oggetto all'account che lo crea: con
        // l'impostazione predefinita del token, un processo elevato produce
        // oggetti di proprieta' del GRUPPO amministratori. Pretendere
        // esattamente l'account sistema rifiutava l'aiutante vero su una
        // macchina normale — misurato sul PC di Roberto il 19/8/2026, ed e'
        // il motivo per cui non e' mai stato riconosciuto.
        for proprietario in ["S-1-5-18", "S-1-5-32-544"] {
            assert_eq!(
                judge_peer(
                    r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001",
                    proprietario,
                    "C:\\x\\h.exe",
                    "C:\\x\\h.exe"
                ),
                Ok(()),
                "rifiutato un proprietario privilegiato: {proprietario}"
            );
        }
    }

    #[test]
    fn un_canale_di_un_utente_qualunque_non_passa() {
        // Il rovescio, ed e' cio' che il controllo esiste per fare: chi non ha
        // privilegi non puo' possedere quell'oggetto, quindi se lo possiede
        // qualcun altro non e' l'aiutante.
        let esito = judge_peer(
            r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001",
            "S-1-5-21-1-2-3-1001",
            "C:\\x\\h.exe",
            "C:\\x\\h.exe",
        );
        assert!(
            matches!(esito, Err(ChannelRefusal::NotLocalSystem(_))),
            "accettato un canale di un utente senza privilegi"
        );
    }

    use super::*;

    const ESEGUIBILE: &str = r"C:\Program Files\Metnos\metnos-helper.exe";
    const PIPE: &str = r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001";

    // ── Il giudizio su chi c'e' dall'altro capo ──
    #[test]
    fn laiutante_vero_passa() {
        assert_eq!(judge_peer(PIPE, "S-1-5-18", ESEGUIBILE, ESEGUIBILE), Ok(()));
    }

    #[test]
    fn un_processo_utente_che_ha_preso_il_nome_non_passa() {
        // E' il caso che questa verifica esiste per chiudere: chi crea la
        // pipe per primo tiene il nome e raccoglie le richieste firmate.
        let esito = judge_peer(PIPE, "S-1-5-21-1-2-3-1001", ESEGUIBILE, ESEGUIBILE);
        assert!(matches!(esito, Err(ChannelRefusal::NotLocalSystem(_))));
    }

    #[test]
    fn un_altro_programma_di_sistema_non_passa() {
        // Girare come sistema non basta: deve essere QUEL programma.
        let esito = judge_peer(PIPE, "S-1-5-18", r"C:\Windows\System32\cmd.exe", ESEGUIBILE);
        assert!(matches!(
            esito,
            Err(ChannelRefusal::UnexpectedExecutable(_))
        ));
    }

    #[test]
    fn le_maiuscole_del_percorso_non_contano() {
        // I percorsi di Windows non distinguono: un confronto sensibile
        // rifiuterebbe l'aiutante vero perche' il sistema ha scritto in
        // maiuscolo.
        assert_eq!(
            judge_peer(
                PIPE,
                "S-1-5-18",
                r"C:\PROGRAM FILES\METNOS\METNOS-HELPER.EXE",
                ESEGUIBILE
            ),
            Ok(())
        );
    }

    #[test]
    fn una_pipe_non_locale_non_passa() {
        for remoto in [
            r"\\SERVER\pipe\metnos-helper",
            r"\\192.0.2.0\pipe\metnos-helper",
            r"\\.\pipe\",
            "metnos-helper",
        ] {
            assert_eq!(
                judge_peer(remoto, "S-1-5-18", ESEGUIBILE, ESEGUIBILE),
                Err(ChannelRefusal::NotLocal),
                "accettata come locale: {remoto}"
            );
        }
    }

    #[test]
    fn il_controllo_sulla_localita_viene_per_primo() {
        // Un canale che punta a un'altra macchina non merita nemmeno che si
        // guardi chi c'e' dall'altro capo.
        assert_eq!(
            judge_peer(r"\\SERVER\pipe\x", "chiunque", "qualunque", ESEGUIBILE),
            Err(ChannelRefusal::NotLocal)
        );
    }

    #[test]
    fn ogni_rifiuto_ha_un_codice_e_una_spiegazione() {
        for r in [
            ChannelRefusal::NotLocal,
            ChannelRefusal::NotAvailable,
            ChannelRefusal::NotLocalSystem("S-1-5-21-1".into()),
            ChannelRefusal::UnexpectedExecutable(r"C:\x.exe".into()),
        ] {
            assert!(!r.code().is_empty());
            assert!(r.message().len() > 20, "{}", r.code());
        }
    }

    // ── L'indirizzo dell'aiutante ──
    #[test]
    fn il_nome_della_pipe_porta_il_sid_del_proprietario() {
        // Deve combaciare parola per parola con quello che l'aiutante crea:
        // un nome diverso non e' un errore visibile, e' un «aiutante assente»
        // quando l'aiutante c'e'.
        assert_eq!(
            pipe_name_for_owner("S-1-5-21-1-2-3-1001").as_deref(),
            Some(r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001")
        );
    }

    #[test]
    fn due_utenti_non_condividono_un_canale() {
        assert_ne!(
            pipe_name_for_owner("S-1-5-21-1-2-3-1001"),
            pipe_name_for_owner("S-1-5-21-1-2-3-1002")
        );
    }

    #[test]
    fn un_sid_che_non_e_un_sid_non_diventa_un_nome() {
        // Il valore finisce dentro il nome di un oggetto di sistema: uno
        // qualunque potrebbe portarci dentro un separatore.
        for cattivo in [
            "",
            "S",
            "S-1",
            "X-1-5-18",
            "s-1-5-18",
            r"S-1-5-18\..\altro",
            "S-1-5-18-",
            "S-1-5-1a",
        ] {
            assert_eq!(pipe_name_for_owner(cattivo), None, "accettato: {cattivo:?}");
        }
    }

    #[test]
    fn un_nome_costruito_qui_passa_il_giudizio_sulla_localita() {
        // Le due meta' — costruzione e verifica — devono essere d'accordo.
        let nome = pipe_name_for_owner("S-1-5-21-1-2-3-1001").unwrap();
        assert_eq!(
            judge_peer(&nome, "S-1-5-18", ESEGUIBILE, ESEGUIBILE),
            Ok(())
        );
    }

    #[test]
    fn laiutante_atteso_sta_sotto_program_files() {
        assert_eq!(
            helper_executable_in(r"C:\Program Files"),
            r"C:\Program Files\Metnos\metnos-helper.exe"
        );
        // Una barra di troppo in coda non deve produrre un percorso diverso:
        // il confronto con l'eseguibile vero e' testuale.
        assert_eq!(
            helper_executable_in(r"C:\Program Files\"),
            r"C:\Program Files\Metnos\metnos-helper.exe"
        );
    }

    #[test]
    fn il_percorso_atteso_e_quello_che_il_giudizio_accetta() {
        let atteso = helper_executable_in(r"C:\Program Files");
        let nome = pipe_name_for_owner("S-1-5-21-1-2-3-1001").unwrap();
        assert_eq!(judge_peer(&nome, "S-1-5-18", &atteso, &atteso), Ok(()));
    }

    // ── Il corpo canonico deve combaciare con quello dell'aiutante ──
    #[test]
    fn il_corpo_canonico_e_quello_che_laiutante_si_aspetta() {
        // I due programmi sono binari separati per scelta: condividere una
        // libreria significherebbe che l'aiutante linka codice del client,
        // cioe' cio' che la separazione esiste per impedire. Il vincolo si
        // presidia qui, con la stringa attesa scritta per esteso.
        let corpo = canonical_body(
            Operation::Install,
            "winget",
            "Microsoft.PowerToys",
            None,
            "0123456789abcdef0123456789abcdef",
        );
        assert_eq!(
            corpo,
            "install\u{1f}winget\u{1f}Microsoft.PowerToys\u{1f}\u{1f}0123456789abcdef0123456789abcdef"
        );
    }

    #[test]
    fn managed_start_body_matches_the_helper_contract() {
        let body = canonical_start_body(
            "winget",
            "LibreHardwareMonitor.LibreHardwareMonitor",
            StartLifetime::Session,
            "0123456789abcdef0123456789abcdef",
        );
        assert_eq!(
            body,
            "managed-start\u{1f}winget\u{1f}LibreHardwareMonitor.LibreHardwareMonitor\u{1f}session\u{1f}0123456789abcdef0123456789abcdef"
        );
        assert_ne!(
            body,
            canonical_body(
                Operation::Install,
                "winget",
                "LibreHardwareMonitor.LibreHardwareMonitor",
                None,
                "0123456789abcdef0123456789abcdef",
            )
        );
    }

    #[test]
    fn managed_provider_bodies_match_the_helper_contract() {
        let grant = canonical_provider_grant_body(
            "inv-0123456789abcdef01234567",
            &"a".repeat(64),
            "hardware_sensor_provider",
            "winget",
            "Vendor.Sensor",
            ProviderInterface::HardwareSensorsV1,
            "Vendor.SensorLib.dll",
            "Vendor.Sensor.Computer",
            &["cpu".to_string()],
            &["temperature".to_string()],
        );
        assert_eq!(
            grant,
            format!(
                "managed-provider-grant\u{1f}inv-0123456789abcdef01234567\u{1f}{}\u{1f}hardware_sensor_provider\u{1f}winget\u{1f}Vendor.Sensor\u{1f}hardware_sensors_v1\u{1f}Vendor.SensorLib.dll\u{1f}Vendor.Sensor.Computer\u{1f}cpu\u{1f}temperature",
                "a".repeat(64),
            ),
        );
        let body =
            canonical_provider_body(&grant, &"A".repeat(86), "0123456789abcdef0123456789abcdef");
        assert_eq!(
            body,
            format!(
                "managed-provider\u{1f}{grant}\u{1f}{}\u{1f}0123456789abcdef0123456789abcdef",
                "A".repeat(86),
            ),
        );
    }

    #[test]
    fn due_richieste_diverse_hanno_corpi_diversi() {
        let a = canonical_body(Operation::Install, "winget", "A.Uno", None, "k");
        let b = canonical_body(Operation::Uninstall, "winget", "A.Uno", None, "k");
        assert_ne!(a, b, "installare e rimuovere non condividono una firma");
    }

    // ── Le chiavi d'idempotenza ──
    #[test]
    fn una_chiave_nuova_e_diversa_ogni_volta() {
        let mut viste = std::collections::HashSet::new();
        for _ in 0..500 {
            let k = new_idempotency_key();
            assert_eq!(k.len(), 32);
            assert!(k.chars().all(|c| c.is_ascii_hexdigit()));
            assert!(viste.insert(k), "chiave ripetuta");
        }
    }
}
