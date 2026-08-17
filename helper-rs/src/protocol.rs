//! Il vocabolario CHIUSO dell'aiutante, e la sua validazione (ADR 0210 D1).
//!
//! Qui si decide la differenza fra un aiutante e una porta aperta. Un
//! componente con privilegi di sistema che esegue una riga di comando
//! ricevuta da un processo utente e' una scalata di privilegi con
//! un'interfaccia gentile: non importa quanto sia curato il resto.
//!
//! Percio' l'aiutante non accetta comandi. Accetta TRE operazioni tipizzate
//! su un identificativo di pacchetto, e la riga di comando la costruisce lui,
//! da valori che ha validato.
//!
//! Il modulo non usa nessuna API di Windows: e' logica pura, quindi si prova
//! su qualunque macchina. La parte che tocca il sistema vive altrove, e riceve
//! da qui soltanto valori gia' verificati.

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

/// Che cosa si chiede all'aiutante. Tre voci, e nessuna significa «esegui».
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Operation {
    /// Questo pacchetto e' installato, e in che versione. Non modifica nulla.
    Query,
    Install,
    Uninstall,
}

/// Una richiesta completa. Ogni campo e' tipizzato: non esiste un campo
/// «argomenti liberi», e non puo' esistere senza cambiare questo file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Request {
    pub operation: Operation,
    pub source: Source,
    pub package_id: String,
    /// Versione esatta, quando chi chiede ne vuole una precisa.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    /// Rende la richiesta irripetibile. Senza, una richiesta catturata si
    /// puo' rigiocare, e installare non e' un'operazione che si possa
    /// ripetere senza conseguenze (ADR 0210 D3.4).
    pub idempotency_key: String,
    /// Firma dell'installazione appaiata sopra il corpo canonico.
    pub signature: String,
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
    /// Chiave gia' consumata: e' un riascolto, non una richiesta nuova.
    ReplayedRequest,
    /// La firma non corrisponde all'installazione appaiata.
    UntrustedSignature,
}

impl Refusal {
    /// Un codice stabile e non tradotto: e' cio' che finisce nel registro e
    /// cio' che si cerca quando un testo non basta.
    pub fn code(&self) -> &'static str {
        match self {
            Refusal::MalformedPackageId => "malformed_package_id",
            Refusal::MalformedVersion => "malformed_version",
            Refusal::MalformedIdempotencyKey => "malformed_idempotency_key",
            Refusal::ReplayedRequest => "replayed_request",
            Refusal::UntrustedSignature => "untrusted_signature",
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
        if !is_valid_package_id(&self.package_id) {
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
    /// chiuso: accettare tre operazioni non basterebbe, se poi una di quelle
    /// tre potesse portare con se' un'opzione arbitraria.
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
}

impl Response {
    pub fn refused(refusal: Refusal) -> Self {
        Response {
            ok: false,
            error_code: Some(refusal.code().to_string()),
            exit_code: None,
            detail: String::new(),
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
            assert!(is_valid_package_id(id), "rifiutato un identificativo vero: {id}");
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
        for chiave in ["", "corta", &"g".repeat(32), &"a".repeat(31), &"a".repeat(33)] {
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

    // ── Il corpo canonico: una firma non si trasferisce ──
    #[test]
    fn due_richieste_diverse_hanno_corpi_diversi() {
        let a = richiesta("A.Uno");
        let mut b = richiesta("B.Due");
        b.idempotency_key = a.idempotency_key.clone();
        assert_ne!(a.canonical_body(), b.canonical_body());

        let mut c = richiesta("A.Uno");
        c.operation = Operation::Uninstall;
        assert_ne!(a.canonical_body(), c.canonical_body(),
                   "installare e disinstallare non condividono una firma");
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
        // Il campo non esiste nel tipo: una richiesta che lo porta viene
        // accettata ignorandolo, e non puo' raggiungere la riga di comando.
        let json = r#"{"operation":"install","source":"winget",
            "package_id":"X.Y","idempotency_key":"0123456789abcdef0123456789abcdef",
            "signature":"s","command":"cmd.exe /c calc","args":["--force"]}"#;
        let r: Request = serde_json::from_str(json).expect("deve deserializzare");
        let argv = r.argv();
        assert!(!argv.iter().any(|a| a.contains("cmd.exe")));
        assert!(!argv.contains(&"--force".to_string()));
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
