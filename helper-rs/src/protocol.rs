//! Closed helper vocabulary and validation (ADR 0210 D1).
//!
//! A system-privileged component that executes a command supplied by an
//! unprivileged process is a privilege-escalation interface. The helper
//! therefore accepts no command line. It accepts only typed requests and
//! constructs operations internally from validated values.
//!
//! This module contains pure validation logic and no Windows API calls, so it
//! can be tested on every platform. System-facing adapters live elsewhere and
//! receive only values already validated here.

use serde::{Deserialize, Serialize};

/// Le sorgenti da cui si puo' installare. Enumerazione chiusa: una sorgente
/// nuova e' una decisione, non un valore che arriva dalla rete.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Winget,
}

impl Source {
    /// Il programma che realizza questa sorgente. Nome fisso, non un percorso
    /// ricevuto: un percorso ricevuto sarebbe di nuovo «esegui questo».
    pub fn program(self) -> &'static str {
        match self {
            Source::Winget => "winget.exe",
        }
    }
}

/// Package-management operations. This enumeration stays closed: managed
/// process start uses `ManagedStartRequest`, not another operation here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Operation {
    /// Questo pacchetto e' installato, e in che versione. Non modifica nulla.
    Query,
    Install,
    Uninstall,
    /// Quale versione dell'aiutante c'e'. Non tocca niente, non guarda
    /// nemmeno un pacchetto: e' l'unica voce che non parla di pacchetti.
    ///
    /// Esiste perche' due programmi installati in momenti diversi possono
    /// trovarsi disallineati, e un disallineamento che non si puo' CHIEDERE
    /// si scopre come un guasto: una richiesta che l'altro capo non capisce,
    /// senza modo di dire perche'. Chiedere «chi sei» e' meno potente di
    /// qualunque altra voce — non legge nemmeno il catalogo — e rende la
    /// differenza un fatto invece che un sintomo.
    Version,
}

/// How long a registered package should remain enabled.
///
/// `Session` means this boot only. `Persistent` also creates the fixed
/// helper-owned startup registration described by ADR 0211.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum StartLifetime {
    Session,
    Persistent,
}

impl StartLifetime {
    pub fn as_str(self) -> &'static str {
        match self {
            StartLifetime::Session => "session",
            StartLifetime::Persistent => "persistent",
        }
    }
}

/// La versione del protocollo parlato su questo canale.
///
/// Distinta dalla versione del programma: due build diverse possono parlare
/// la stessa lingua, ed e' la lingua che decide se si capiscono. Cambia solo
/// quando cambia la forma dei messaggi.
pub const PROTOCOL_VERSION: u32 = 4;

/// Una richiesta completa. Ogni campo e' tipizzato: non esiste un campo
/// «argomenti liberi», e non puo' esistere senza cambiare questo file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub operation: Operation,
    pub source: Source,
    pub package_id: String,
    /// Exact package version. For `Version`, this is the client build that
    /// triggered a signed, lazy update check.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    /// Rende la richiesta irripetibile. Senza, una richiesta catturata si
    /// puo' rigiocare, e installare non e' un'operazione che si possa
    /// ripetere senza conseguenze (ADR 0210 D3.4).
    pub idempotency_key: String,
    /// Firma dell'installazione appaiata sopra il corpo canonico.
    pub signature: String,
}

/// A dedicated managed-start request.
///
/// There is deliberately no operation, path, command, argument list, task
/// name, or executable name. The helper resolves the exact package identity
/// from machine-owned registration data (ADR 0211).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedStartRequest {
    pub source: Source,
    pub package_id: String,
    pub lifetime: StartLifetime,
    pub idempotency_key: String,
    pub signature: String,
}

/// Stop exactly one process created by a previous managed-start request.
///
/// A PID alone is not an identity: Windows may reuse it.  The kernel creation
/// time binds the request to the same process object, while `package_id`
/// makes the helper resolve and compare the trusted executable again.  No
/// name, path, command, or argument crosses the channel.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedStopRequest {
    pub source: Source,
    pub package_id: String,
    pub pid: u32,
    pub creation_time: u64,
    pub idempotency_key: String,
    pub signature: String,
}

/// Read-only provider interfaces implemented by the privileged broker.
///
/// The enum is intentionally closed. A signed profile can select an
/// implementation of an existing interface, but cannot provide code, paths,
/// method names, property names, or arguments for the helper to execute.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderInterface {
    HardwareSensorsV1,
}

impl ProviderInterface {
    pub fn as_str(self) -> &'static str {
        match self {
            ProviderInterface::HardwareSensorsV1 => "hardware_sensors_v1",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HardwareDomain {
    Battery,
    Controller,
    Cpu,
    Gpu,
    Memory,
    Motherboard,
    Network,
    PowerMonitor,
    PowerSupply,
    Storage,
}

impl HardwareDomain {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Battery => "battery",
            Self::Controller => "controller",
            Self::Cpu => "cpu",
            Self::Gpu => "gpu",
            Self::Memory => "memory",
            Self::Motherboard => "motherboard",
            Self::Network => "network",
            Self::PowerMonitor => "power_monitor",
            Self::PowerSupply => "power_supply",
            Self::Storage => "storage",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SensorKind {
    Clock,
    Conductivity,
    Control,
    Current,
    Data,
    Energy,
    Factor,
    Fan,
    Flow,
    Frequency,
    Humidity,
    Level,
    Load,
    Noise,
    Power,
    SmallData,
    Temperature,
    Throughput,
    TimeSpan,
    Timing,
    Voltage,
}

impl SensorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Clock => "clock",
            Self::Conductivity => "conductivity",
            Self::Control => "control",
            Self::Current => "current",
            Self::Data => "data",
            Self::Energy => "energy",
            Self::Factor => "factor",
            Self::Fan => "fan",
            Self::Flow => "flow",
            Self::Frequency => "frequency",
            Self::Humidity => "humidity",
            Self::Level => "level",
            Self::Load => "load",
            Self::Noise => "noise",
            Self::Power => "power",
            Self::SmallData => "small_data",
            Self::Temperature => "temperature",
            Self::Throughput => "throughput",
            Self::TimeSpan => "time_span",
            Self::Timing => "timing",
            Self::Voltage => "voltage",
        }
    }
}

/// One server-authorised, read-only provider request.
///
/// The server grant binds the dependency from the signed executor manifest.
/// The client signature separately proves which paired device requested it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedProviderRequest {
    pub source: Source,
    pub package_id: String,
    pub interface: ProviderInterface,
    pub assembly: String,
    pub entry_type: String,
    pub domains: Vec<HardwareDomain>,
    pub sensor_types: Vec<SensorKind>,
    pub invocation_id: String,
    pub manifest_sha256: String,
    pub dependency_key: String,
    pub grant_signature: String,
    pub idempotency_key: String,
    pub signature: String,
}

/// The closed request shapes accepted on the authenticated channel.
///
/// `untagged` is safe here because both inner structures reject unknown
/// fields and have disjoint required fields (`operation` versus `lifetime`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum WireRequest {
    Package(Request),
    ManagedStart(ManagedStartRequest),
    ManagedStop(ManagedStopRequest),
    ManagedProvider(ManagedProviderRequest),
}

/// The system action selected only after deserialisation and validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    PackageCommand(Vec<String>),
    /// Check the signed update source selected by the installed pairing.
    /// No URL, path, or artifact comes from the request.
    HelperUpdateCheck,
    ManagedStart {
        package_id: String,
        lifetime: StartLifetime,
    },
    ManagedStop {
        package_id: String,
        pid: u32,
        creation_time: u64,
    },
    ManagedProvider {
        package_id: String,
        interface: ProviderInterface,
        assembly: String,
        entry_type: String,
        domains: Vec<HardwareDomain>,
        sensor_types: Vec<SensorKind>,
    },
}

/// Perche' una richiesta non e' accettabile. Ogni variante e' un rifiuto
/// PRIMA di qualunque effetto.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    /// L'identificativo non ha la forma di un identificativo.
    MalformedPackageId,
    /// Una versione che non e' una versione.
    MalformedVersion,
    /// Chiave d'idempotenza assente o non plausibile.
    MalformedIdempotencyKey,
    /// PID e tempo kernel devono identificare un processo reale, non un nome.
    MalformedProcessIdentity,
    /// Chiave gia' consumata: e' un riascolto, non una richiesta nuova.
    ReplayedRequest,
    /// La firma non corrisponde all'installazione appaiata.
    UntrustedSignature,
    /// The server did not authorise this package/interface binding.
    UntrustedGrant,
}

impl Refusal {
    /// Un codice stabile e non tradotto: e' cio' che finisce nel registro e
    /// cio' che si cerca quando un testo non basta.
    pub fn code(&self) -> &'static str {
        match self {
            Refusal::MalformedPackageId => "malformed_package_id",
            Refusal::MalformedVersion => "malformed_version",
            Refusal::MalformedIdempotencyKey => "malformed_idempotency_key",
            Refusal::MalformedProcessIdentity => "malformed_process_identity",
            Refusal::ReplayedRequest => "replayed_request",
            Refusal::UntrustedSignature => "untrusted_signature",
            Refusal::UntrustedGrant => "untrusted_provider_grant",
        }
    }
}

/// I caratteri che un identificativo di pacchetto puo' contenere.
///
/// Scritti per esteso invece che con una libreria di espressioni regolari: e'
/// il controllo su cui poggia tutto il resto, e deve essere leggibile senza
/// interpretare una sintassi. Sono le lettere, le cifre e i pochi separatori
/// che gli identificativi veri usano — `Microsoft.VisualStudioCode`,
/// `LibreHardwareMonitor.LibreHardwareMonitor`, `python3-pip`, `libc6:amd64`.
fn is_id_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '+' | ':' | '-' | '@')
}

/// Vero quando la stringa e' un identificativo, non qualcos'altro travestito.
///
/// Il primo carattere deve essere alfanumerico: un identificativo che comincia
/// per `-` arriverebbe al gestore di pacchetti come un'OPZIONE, non come un
/// nome, ed e' il modo piu' semplice di trasformare «installa» in «fai
/// qualcos'altro».
pub fn is_valid_package_id(value: &str) -> bool {
    if value.is_empty() || value.len() > 128 {
        return false;
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(first) if first.is_ascii_alphanumeric() => {}
        _ => return false,
    }
    value.chars().all(is_id_char)
}

/// Una versione: cifre, lettere e separatori. Stessa regola sul primo
/// carattere, e per la stessa ragione.
pub fn is_valid_version(value: &str) -> bool {
    if value.is_empty() || value.len() > 64 {
        return false;
    }
    value.starts_with(|c: char| c.is_ascii_digit())
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '+' | '-'))
}

/// Una chiave d'idempotenza: esadecimale, di lunghezza fissata.
pub fn is_valid_idempotency_key(value: &str) -> bool {
    value.len() == 32 && value.chars().all(|c| c.is_ascii_hexdigit())
}

impl Request {
    /// Il corpo su cui si calcola e si verifica la firma.
    ///
    /// Ordine fisso e separatore che non puo' comparire nei campi: due
    /// richieste diverse non possono produrre lo stesso corpo, e una firma
    /// data per una non vale per l'altra.
    pub fn canonical_body(&self) -> String {
        format!(
            "{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
            match self.operation {
                Operation::Query => "query",
                Operation::Install => "install",
                Operation::Uninstall => "uninstall",
                Operation::Version => "version",
            },
            match self.source {
                Source::Winget => "winget",
            },
            self.package_id,
            self.version.as_deref().unwrap_or(""),
            self.idempotency_key,
        )
    }

    /// Cio' che si puo' verificare senza toccare il sistema: la FORMA.
    ///
    /// La firma e la ripetizione si verificano altrove, perche' richiedono
    /// stato (la chiave appaiata, le chiavi gia' consumate). Qui restano i
    /// controlli che non hanno bisogno di niente, e sono quelli che devono
    /// passare per primi: un valore malformato non deve nemmeno raggiungere
    /// il confronto di una firma.
    pub fn check_shape(&self) -> Result<(), Refusal> {
        if self.operation == Operation::Version {
            // A version request never names a package. Its optional version
            // is the client build that triggered a lazy update check.
            if !self.package_id.is_empty() {
                return Err(Refusal::MalformedPackageId);
            }
        } else if !is_valid_package_id(&self.package_id) {
            return Err(Refusal::MalformedPackageId);
        }
        if let Some(v) = &self.version {
            if !is_valid_version(v) {
                return Err(Refusal::MalformedVersion);
            }
        }
        if !is_valid_idempotency_key(&self.idempotency_key) {
            return Err(Refusal::MalformedIdempotencyKey);
        }
        Ok(())
    }

    /// La riga di comando, costruita QUI dai soli valori validati.
    ///
    /// Nessun pezzo di questa lista viene da chi ha chiamato: i valori
    /// ricevuti compaiono solo come VALORI, mai come opzioni, e le opzioni
    /// sono quelle scritte in questo file. E' l'altra meta' del vocabolario
    /// chiuso: accettare operazioni tipizzate non basterebbe, se una di esse
    /// potesse portare con se' un'opzione arbitraria.
    ///
    /// `--scope machine` e' esplicito: l'aiutante esiste per fare cio' che il
    /// client non puo', e installare per il solo utente corrente il client lo
    /// fa gia' da solo. Chiedere a un componente privilegiato un'operazione
    /// che non richiede privilegi allarga la superficie senza motivo.
    pub fn argv(&self) -> Vec<String> {
        let mut argv: Vec<String> = vec![self.source.program().to_string()];
        match self.operation {
            Operation::Query => {
                argv.push("list".into());
            }
            Operation::Install => {
                argv.push("install".into());
            }
            Operation::Uninstall => {
                argv.push("uninstall".into());
            }
            // Non c'e' niente da eseguire: la risposta e' una proprieta' di
            // questo programma, non di una macchina da interrogare. Il
            // servizio la prende prima di arrivare qui; la lista vuota fa si'
            // che, anche se ci arrivasse, non parta comunque nulla.
            Operation::Version => return Vec::new(),
        }
        argv.push("--id".into());
        argv.push(self.package_id.clone());
        argv.push("--exact".into());
        argv.push("--accept-source-agreements".into());
        argv.push("--disable-interactivity".into());
        if let Some(v) = &self.version {
            argv.push("--version".into());
            argv.push(v.clone());
        }
        if matches!(self.operation, Operation::Install) {
            argv.push("--accept-package-agreements".into());
            argv.push("--silent".into());
            argv.push("--scope".into());
            argv.push("machine".into());
        }
        if matches!(self.operation, Operation::Uninstall) {
            argv.push("--silent".into());
        }
        argv
    }
}

impl ManagedStartRequest {
    /// The signed body has a fixed domain separator that is not supplied by
    /// the caller. A signature for managed start cannot authorize package
    /// installation, removal, or query.
    pub fn canonical_body(&self) -> String {
        format!(
            "managed-start\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
            match self.source {
                Source::Winget => "winget",
            },
            self.package_id,
            self.lifetime.as_str(),
            self.idempotency_key,
        )
    }

    pub fn check_shape(&self) -> Result<(), Refusal> {
        if !is_valid_package_id(&self.package_id) {
            return Err(Refusal::MalformedPackageId);
        }
        if !is_valid_idempotency_key(&self.idempotency_key) {
            return Err(Refusal::MalformedIdempotencyKey);
        }
        Ok(())
    }
}

impl ManagedStopRequest {
    pub fn canonical_body(&self) -> String {
        format!(
            "managed-stop\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
            match self.source {
                Source::Winget => "winget",
            },
            self.package_id,
            self.pid,
            self.creation_time,
            self.idempotency_key,
        )
    }

    pub fn check_shape(&self) -> Result<(), Refusal> {
        if !is_valid_package_id(&self.package_id) {
            return Err(Refusal::MalformedPackageId);
        }
        if self.pid == 0 || self.creation_time == 0 {
            return Err(Refusal::MalformedProcessIdentity);
        }
        if !is_valid_idempotency_key(&self.idempotency_key) {
            return Err(Refusal::MalformedIdempotencyKey);
        }
        Ok(())
    }
}

fn is_valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_alphabetic()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

fn is_valid_assembly_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.to_ascii_lowercase().ends_with(".dll")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn is_valid_dotnet_type(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 192
        && value.is_ascii()
        && value.split('.').all(is_valid_identifier)
}

fn is_valid_invocation_id(value: &str) -> bool {
    value.len() == 28
        && value.starts_with("inv-")
        && value[4..].bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_valid_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_valid_b64url_signature(value: &str) -> bool {
    value.len() == 86
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn closed_selectors<T: Copy>(values: &[T], render: fn(T) -> &'static str) -> bool {
    if values.is_empty() || values.len() > 16 {
        return false;
    }
    let names = values.iter().copied().map(render).collect::<Vec<_>>();
    names.windows(2).all(|pair| pair[0] < pair[1])
}

impl ManagedProviderRequest {
    /// Server-signed authority. This deliberately excludes the client
    /// idempotency key and client signature.
    pub fn canonical_grant_body(&self) -> String {
        let domains = self
            .domains
            .iter()
            .map(|value| value.as_str())
            .collect::<Vec<_>>()
            .join(",");
        let sensor_types = self
            .sensor_types
            .iter()
            .map(|value| value.as_str())
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "managed-provider-grant\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1f}{}",
            self.invocation_id,
            self.manifest_sha256,
            self.dependency_key,
            match self.source {
                Source::Winget => "winget",
            },
            self.package_id,
            self.interface.as_str(),
            self.assembly,
            self.entry_type,
            domains,
            sensor_types,
        )
    }

    /// Client-signed request. Including the server signature prevents a grant
    /// from being replaced while keeping the paired client's signature.
    pub fn canonical_body(&self) -> String {
        format!(
            "managed-provider\u{1f}{}\u{1f}{}\u{1f}{}",
            self.canonical_grant_body(),
            self.grant_signature,
            self.idempotency_key,
        )
    }

    pub fn check_shape(&self) -> Result<(), Refusal> {
        if !is_valid_package_id(&self.package_id) {
            return Err(Refusal::MalformedPackageId);
        }
        if !is_valid_invocation_id(&self.invocation_id)
            || !is_valid_sha256(&self.manifest_sha256)
            || !is_valid_identifier(&self.dependency_key)
            || !is_valid_assembly_name(&self.assembly)
            || !is_valid_dotnet_type(&self.entry_type)
            || !is_valid_b64url_signature(&self.grant_signature)
            || !closed_selectors(&self.domains, HardwareDomain::as_str)
            || !closed_selectors(&self.sensor_types, SensorKind::as_str)
        {
            return Err(Refusal::UntrustedGrant);
        }
        if !is_valid_idempotency_key(&self.idempotency_key) {
            return Err(Refusal::MalformedIdempotencyKey);
        }
        Ok(())
    }
}

impl WireRequest {
    pub fn package_id(&self) -> &str {
        match self {
            WireRequest::Package(request) => &request.package_id,
            WireRequest::ManagedStart(request) => &request.package_id,
            WireRequest::ManagedStop(request) => &request.package_id,
            WireRequest::ManagedProvider(request) => &request.package_id,
        }
    }

    pub fn idempotency_key(&self) -> &str {
        match self {
            WireRequest::Package(request) => &request.idempotency_key,
            WireRequest::ManagedStart(request) => &request.idempotency_key,
            WireRequest::ManagedStop(request) => &request.idempotency_key,
            WireRequest::ManagedProvider(request) => &request.idempotency_key,
        }
    }

    pub fn signature(&self) -> &str {
        match self {
            WireRequest::Package(request) => &request.signature,
            WireRequest::ManagedStart(request) => &request.signature,
            WireRequest::ManagedStop(request) => &request.signature,
            WireRequest::ManagedProvider(request) => &request.signature,
        }
    }

    pub fn canonical_body(&self) -> String {
        match self {
            WireRequest::Package(request) => request.canonical_body(),
            WireRequest::ManagedStart(request) => request.canonical_body(),
            WireRequest::ManagedStop(request) => request.canonical_body(),
            WireRequest::ManagedProvider(request) => request.canonical_body(),
        }
    }

    pub fn check_shape(&self) -> Result<(), Refusal> {
        match self {
            WireRequest::Package(request) => request.check_shape(),
            WireRequest::ManagedStart(request) => request.check_shape(),
            WireRequest::ManagedStop(request) => request.check_shape(),
            WireRequest::ManagedProvider(request) => request.check_shape(),
        }
    }

    pub fn is_version_query(&self) -> bool {
        matches!(self, WireRequest::Package(request)
                 if request.operation == Operation::Version)
    }

    pub fn action(&self) -> Option<Action> {
        match self {
            WireRequest::Package(request)
                if request.operation == Operation::Version && request.version.is_some() =>
            {
                Some(Action::HelperUpdateCheck)
            }
            WireRequest::Package(request) if request.operation == Operation::Version => None,
            WireRequest::Package(request) => Some(Action::PackageCommand(request.argv())),
            WireRequest::ManagedStart(request) => Some(Action::ManagedStart {
                package_id: request.package_id.clone(),
                lifetime: request.lifetime,
            }),
            WireRequest::ManagedStop(request) => Some(Action::ManagedStop {
                package_id: request.package_id.clone(),
                pid: request.pid,
                creation_time: request.creation_time,
            }),
            WireRequest::ManagedProvider(request) => Some(Action::ManagedProvider {
                package_id: request.package_id.clone(),
                interface: request.interface,
                assembly: request.assembly.clone(),
                entry_type: request.entry_type.clone(),
                domains: request.domains.clone(),
                sensor_types: request.sensor_types.clone(),
            }),
        }
    }
}

/// L'esito di un'operazione, come torna al chiamante.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub ok: bool,
    /// Codice stabile e non tradotto quando qualcosa non e' andato.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    /// Il codice d'uscita del gestore di pacchetti, quando c'e' stato.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    /// Le ultime righe utili dell'uscita: il verdetto lo scrivono in fondo.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub detail: String,
    /// Typed provider data. Package operations never populate this field.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload: Option<serde_json::Value>,
    /// La versione dell'aiutante che ha risposto, sempre.
    ///
    /// Su OGNI risposta, non solo su quella che la chiede: chi riceve un
    /// esito strano deve poter vedere con chi ha parlato senza dover fare
    /// una seconda domanda — e un rifiuto e' proprio il momento in cui la
    /// seconda domanda potrebbe non arrivare mai.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub helper_version: String,
    /// La lingua parlata, per distinguere «non ci capiamo» da «non funziona».
    #[serde(default)]
    pub protocol_version: u32,
}

/// La versione di QUESTO programma, come la dichiara al mondo.
pub fn helper_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

impl Response {
    /// Una risposta vuota, gia' firmata con chi la scrive.
    ///
    /// Ogni risposta esce da qui. Non e' una comodita': i campi di versione
    /// servono proprio quando qualcosa e' andato storto, cioe' nei rami che
    /// si scrivono di fretta, ed e' li' che ci si dimentica di riempirli.
    /// Cosi' non c'e' un ramo che possa dimenticarsene.
    pub fn stamped() -> Self {
        Response {
            ok: false,
            error_code: None,
            exit_code: None,
            detail: String::new(),
            payload: None,
            helper_version: helper_version().to_string(),
            protocol_version: PROTOCOL_VERSION,
        }
    }

    pub fn refused(refusal: Refusal) -> Self {
        Response {
            error_code: Some(refusal.code().to_string()),
            ..Response::stamped()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn richiesta(id: &str) -> Request {
        Request {
            operation: Operation::Install,
            source: Source::Winget,
            package_id: id.to_string(),
            version: None,
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        }
    }

    // ── Identificativi: la forma e' l'unica difesa che viene prima di tutto ──
    #[test]
    fn identificativi_veri_sono_accettati() {
        for id in [
            "Microsoft.VisualStudioCode",
            "LibreHardwareMonitor.LibreHardwareMonitor",
            "python3-pip",
            "libc6:amd64",
            "7zip.7zip",
            "9NRX63209R7B",
        ] {
            assert!(
                is_valid_package_id(id),
                "rifiutato un identificativo vero: {id}"
            );
        }
    }

    #[test]
    fn un_identificativo_che_comincia_per_meno_e_unopzione() {
        // Il caso che conta: arriverebbe al gestore come opzione, non come
        // nome, e trasformerebbe «installa» in «fai qualcos'altro».
        for id in ["-rf", "--force", "--scope", "-"] {
            assert!(!is_valid_package_id(id), "accettata un'opzione: {id}");
        }
    }

    #[test]
    fn niente_che_non_sia_un_identificativo() {
        for id in [
            "",
            " ",
            "a b",
            "a;rm -rf /",
            "a$(id)",
            "a|b",
            "a&b",
            "a>b",
            "a\nb",
            "a\tb",
            "C:\\Windows\\System32\\cmd.exe",
            "/etc/passwd",
            "../..",
            "a\"b",
            "a'b",
            "a`b",
            "a%PATH%",
            "pacchetto€",
        ] {
            assert!(!is_valid_package_id(id), "accettato: {id:?}");
        }
    }

    #[test]
    fn un_identificativo_lunghissimo_e_rifiutato() {
        assert!(!is_valid_package_id(&"a".repeat(129)));
        assert!(is_valid_package_id(&"a".repeat(128)));
    }

    // ── La riga di comando: nessun pezzo viene da chi chiama ──
    #[test]
    fn la_riga_di_comando_non_porta_opzioni_del_chiamante() {
        let argv = richiesta("Microsoft.PowerToys").argv();
        // L'identificativo compare UNA volta, come valore, subito dopo --id.
        let posizione = argv.iter().position(|a| a == "--id").expect("manca --id");
        assert_eq!(argv[posizione + 1], "Microsoft.PowerToys");
        // Nessun altro elemento e' l'identificativo: non e' finito altrove.
        assert_eq!(
            argv.iter().filter(|a| *a == "Microsoft.PowerToys").count(),
            1
        );
        // Il programma e' quello fisso della sorgente, non un percorso.
        assert_eq!(argv[0], "winget.exe");
    }

    #[test]
    fn le_tre_operazioni_producono_tre_verbi_distinti() {
        let mut verbi = vec![];
        for op in [Operation::Query, Operation::Install, Operation::Uninstall] {
            let mut r = richiesta("X.Y");
            r.operation = op;
            verbi.push(r.argv()[1].clone());
        }
        assert_eq!(verbi, vec!["list", "install", "uninstall"]);
    }

    #[test]
    fn solo_linstallazione_chiede_lambito_macchina() {
        // L'aiutante esiste per fare cio' che il client non puo'. Chiedergli
        // un'operazione che non richiede privilegi allargherebbe la
        // superficie senza motivo.
        let mut r = richiesta("X.Y");
        assert!(r.argv().contains(&"machine".to_string()));
        r.operation = Operation::Query;
        assert!(!r.argv().contains(&"machine".to_string()));
        r.operation = Operation::Uninstall;
        assert!(!r.argv().contains(&"machine".to_string()));
    }

    #[test]
    fn una_consultazione_non_porta_silent_ne_accettazioni() {
        let mut r = richiesta("X.Y");
        r.operation = Operation::Query;
        let argv = r.argv();
        assert!(!argv.contains(&"--silent".to_string()));
        assert!(!argv.contains(&"--accept-package-agreements".to_string()));
    }

    // ── La forma si controlla prima di tutto ──
    #[test]
    fn la_forma_si_verifica_prima_della_firma() {
        let mut r = richiesta("--force");
        r.signature = "qualunque".into();
        assert_eq!(r.check_shape(), Err(Refusal::MalformedPackageId));
    }

    #[test]
    fn una_chiave_didempotenza_deve_essere_plausibile() {
        for chiave in [
            "",
            "corta",
            &"g".repeat(32),
            &"a".repeat(31),
            &"a".repeat(33),
        ] {
            let mut r = richiesta("X.Y");
            r.idempotency_key = chiave.to_string();
            assert_eq!(r.check_shape(), Err(Refusal::MalformedIdempotencyKey));
        }
    }

    #[test]
    fn una_versione_malformata_e_rifiutata() {
        for v in ["", "ultima", "-1", "1.0; rm", "1.0 2.0"] {
            let mut r = richiesta("X.Y");
            r.version = Some(v.to_string());
            assert_eq!(r.check_shape(), Err(Refusal::MalformedVersion));
        }
        let mut r = richiesta("X.Y");
        r.version = Some("0.100.2".into());
        assert!(r.check_shape().is_ok());
    }

    #[test]
    fn version_query_may_request_one_signed_lazy_update_check() {
        let mut request = richiesta("X.Y");
        request.operation = Operation::Version;
        request.package_id.clear();
        request.version = Some("0.2.44".into());
        let wire = WireRequest::Package(request);

        assert!(wire.check_shape().is_ok());
        assert_eq!(wire.action(), Some(Action::HelperUpdateCheck));
    }

    // ── Il corpo canonico: una firma non si trasferisce ──
    #[test]
    fn due_richieste_diverse_hanno_corpi_diversi() {
        let a = richiesta("A.Uno");
        let mut b = richiesta("B.Due");
        b.idempotency_key = a.idempotency_key.clone();
        assert_ne!(a.canonical_body(), b.canonical_body());

        let mut c = richiesta("A.Uno");
        c.operation = Operation::Uninstall;
        assert_ne!(
            a.canonical_body(),
            c.canonical_body(),
            "installare e disinstallare non condividono una firma"
        );
    }

    #[test]
    fn i_campi_non_si_possono_confondere_fra_loro() {
        // Senza un separatore che non puo' comparire nei campi, due richieste
        // diverse potrebbero produrre lo stesso corpo spostando un confine.
        let mut a = richiesta("A.UnoB");
        a.version = Some("2.0".into());
        let mut b = richiesta("A.Uno");
        b.version = Some("B2.0".into());
        assert_ne!(a.canonical_body(), b.canonical_body());
    }

    #[test]
    fn il_corpo_canonico_e_stabile() {
        // Se cambia, ogni firma esistente smette di valere: e' un cambio di
        // contratto, non un dettaglio.
        let r = richiesta("Microsoft.PowerToys");
        assert_eq!(
            r.canonical_body(),
            "install\u{1f}winget\u{1f}Microsoft.PowerToys\u{1f}\u{1f}0123456789abcdef0123456789abcdef"
        );
    }

    // ── Il protocollo non ha una via d'uscita verso «esegui questo» ──
    #[test]
    fn una_richiesta_con_un_campo_comando_non_si_deserializza() {
        // Unknown fields are rejected rather than ignored. This makes a
        // command-like extension fail closed at the wire boundary.
        let json = r#"{"operation":"install","source":"winget",
            "package_id":"X.Y","idempotency_key":"0123456789abcdef0123456789abcdef",
            "signature":"s","command":"cmd.exe /c calc","args":["--force"]}"#;
        assert!(serde_json::from_str::<Request>(json).is_err());
    }

    #[test]
    fn managed_start_has_no_operation_or_executable_fields() {
        let json = r#"{"source":"winget","package_id":"X.Y",
            "lifetime":"session",
            "idempotency_key":"0123456789abcdef0123456789abcdef",
            "signature":"s"}"#;
        let request: WireRequest = serde_json::from_str(json).unwrap();
        assert!(matches!(request, WireRequest::ManagedStart(_)));

        for extra in [
            r#", "operation":"run""#,
            r#", "path":"C:\\Windows\\System32\\cmd.exe""#,
            r#", "args":["/c", "calc"]"#,
            r#", "task_name":"chosen-by-caller""#,
        ] {
            let malformed = json.replacen("}", &format!("{extra}}}"), 1);
            assert!(
                serde_json::from_str::<WireRequest>(&malformed).is_err(),
                "accepted caller-controlled field: {extra}"
            );
        }
    }

    #[test]
    fn managed_start_signature_is_domain_separated_and_binds_lifetime() {
        let request = ManagedStartRequest {
            source: Source::Winget,
            package_id: "LibreHardwareMonitor.LibreHardwareMonitor".into(),
            lifetime: StartLifetime::Session,
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        };
        assert_eq!(
            request.canonical_body(),
            "managed-start\u{1f}winget\u{1f}LibreHardwareMonitor.LibreHardwareMonitor\u{1f}session\u{1f}0123456789abcdef0123456789abcdef"
        );
        let mut persistent = request.clone();
        persistent.lifetime = StartLifetime::Persistent;
        assert_ne!(request.canonical_body(), persistent.canonical_body());
        assert_ne!(
            request.canonical_body(),
            richiesta("LibreHardwareMonitor.LibreHardwareMonitor").canonical_body()
        );
    }

    #[test]
    fn managed_stop_binds_package_pid_and_kernel_creation_time() {
        let request = ManagedStopRequest {
            source: Source::Winget,
            package_id: "LibreHardwareMonitor.LibreHardwareMonitor".into(),
            pid: 4242,
            creation_time: 133_700_000_000_000_000,
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        };
        assert_eq!(
            request.canonical_body(),
            "managed-stop\u{1f}winget\u{1f}LibreHardwareMonitor.LibreHardwareMonitor\u{1f}4242\u{1f}133700000000000000\u{1f}0123456789abcdef0123456789abcdef"
        );
        assert!(request.check_shape().is_ok());
        let mut reused_pid = request.clone();
        reused_pid.creation_time += 1;
        assert_ne!(request.canonical_body(), reused_pid.canonical_body());
        let mut invalid = request;
        invalid.pid = 0;
        assert_eq!(
            invalid.check_shape(),
            Err(Refusal::MalformedProcessIdentity)
        );
    }

    #[test]
    fn managed_start_rejects_unknown_lifetime() {
        let json = r#"{"source":"winget","package_id":"X.Y",
            "lifetime":"forever",
            "idempotency_key":"0123456789abcdef0123456789abcdef",
            "signature":"s"}"#;
        assert!(serde_json::from_str::<WireRequest>(json).is_err());
    }

    #[test]
    fn managed_provider_has_no_path_code_or_free_arguments() {
        let json = format!(
            r#"{{"source":"winget","package_id":"Vendor.Sensor","interface":"hardware_sensors_v1","assembly":"Vendor.SensorLib.dll","entry_type":"Vendor.Sensor.Computer","domains":["cpu"],"sensor_types":["temperature"],"invocation_id":"inv-0123456789abcdef01234567","manifest_sha256":"{}","dependency_key":"hardware_sensor_provider","grant_signature":"{}","idempotency_key":"0123456789abcdef0123456789abcdef","signature":"s"}}"#,
            "a".repeat(64),
            "A".repeat(86),
        );
        let request: WireRequest = serde_json::from_str(&json).unwrap();
        assert!(matches!(request, WireRequest::ManagedProvider(_)));
        assert!(request.check_shape().is_ok());
        for field in ["path", "command", "args", "method"] {
            let extra = json.replacen("}", &format!(r#", "{field}":"x"}}"#), 1);
            assert!(serde_json::from_str::<WireRequest>(&extra).is_err());
        }
    }

    #[test]
    fn managed_provider_signature_bodies_are_exact_and_minimal() {
        let request = ManagedProviderRequest {
            source: Source::Winget,
            package_id: "Vendor.Sensor".into(),
            interface: ProviderInterface::HardwareSensorsV1,
            assembly: "Vendor.SensorLib.dll".into(),
            entry_type: "Vendor.Sensor.Computer".into(),
            domains: vec![HardwareDomain::Cpu],
            sensor_types: vec![SensorKind::Temperature],
            invocation_id: "inv-0123456789abcdef01234567".into(),
            manifest_sha256: "a".repeat(64),
            dependency_key: "hardware_sensor_provider".into(),
            grant_signature: "A".repeat(86),
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        };
        let grant = format!(
            "managed-provider-grant\u{1f}inv-0123456789abcdef01234567\u{1f}{}\u{1f}hardware_sensor_provider\u{1f}winget\u{1f}Vendor.Sensor\u{1f}hardware_sensors_v1\u{1f}Vendor.SensorLib.dll\u{1f}Vendor.Sensor.Computer\u{1f}cpu\u{1f}temperature",
            "a".repeat(64),
        );
        assert_eq!(request.canonical_grant_body(), grant);
        assert_eq!(
            request.canonical_body(),
            format!(
                "managed-provider\u{1f}{grant}\u{1f}{}\u{1f}0123456789abcdef0123456789abcdef",
                "A".repeat(86),
            ),
        );
    }

    #[test]
    fn unoperazione_sconosciuta_non_si_deserializza() {
        let json = r#"{"operation":"exec","source":"winget","package_id":"X.Y",
            "idempotency_key":"0123456789abcdef0123456789abcdef","signature":"s"}"#;
        assert!(serde_json::from_str::<Request>(json).is_err());
    }

    #[test]
    fn una_sorgente_sconosciuta_non_si_deserializza() {
        let json = r#"{"operation":"install","source":"powershell","package_id":"X.Y",
            "idempotency_key":"0123456789abcdef0123456789abcdef","signature":"s"}"#;
        assert!(serde_json::from_str::<Request>(json).is_err());
    }
}
