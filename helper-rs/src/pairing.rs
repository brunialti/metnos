//! Chi puo' chiedere, e con quale prova (ADR 0210 D3 e D4).
//!
//! L'aiutante non si fida della decisione ricevuta: la riverifica al momento
//! di agire. Perche' fra il momento in cui una richiesta e' stata approvata e
//! quello in cui si esegue puo' essere successo di tutto — un riappaiamento,
//! una revoca, una cattura del messaggio.
//!
//! ## Che cosa e' l'appaiamento
//!
//! Al momento del consenso — una volta sola, con una richiesta esplicita
//! all'utente — l'aiutante registra due cose e non le cambia piu':
//!
//! - il **SID del proprietario**: chi puo' parlargli. Un SID, non un gruppo:
//!   chi riesce ad aggiungersi ad «Administrators» non deve ereditare
//!   l'aiutante;
//! - la **chiave pubblica** dell'installazione Metnos che lo ha installato:
//!   con quella verifica ogni richiesta.
//!
//! Sono due controlli distinti e servono a cose diverse. Il SID dice CHI ha
//! aperto la pipe — e' un fatto del sistema operativo, che nessuno puo'
//! dichiarare per conto suo. La firma dice DA DOVE viene la richiesta.
//! Il primo senza il secondo lascerebbe passare qualunque cosa scritta dal
//! processo giusto; il secondo senza il primo lascerebbe passare una
//! richiesta firmata riprodotta da chiunque.

use std::fs;
use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::protocol::{Refusal, WireRequest};

/// Cio' che si e' deciso al momento del consenso, e che non cambia dopo.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Pairing {
    /// Il SID del proprietario, in forma testuale.
    pub owner_sid: String,
    /// La chiave pubblica dell'installazione, in esadecimale.
    pub public_key_hex: String,
    /// La chiave pubblica del SERVER, in base64url.
    ///
    /// Ancora di fiducia distinta da quella qui sopra, con un compito
    /// distinto: `public_key_hex` dice CHI puo' chiedere, questa dice CHE
    /// COSA ci si puo' installare sopra. Tenerle separate significa che
    /// nessuna delle due eredita i poteri dell'altra — chi puo' chiedere
    /// un'installazione non puo' per questo sostituire il programma.
    ///
    /// Vuota su un aiutante installato prima che gli aggiornamenti
    /// esistessero: senza ancora non si aggiorna, il che e' il modo giusto
    /// di sbagliare.
    #[serde(default)]
    pub server_public_key_b64: String,
    /// L'indirizzo del server a cui chiedere se c'e' una versione nuova.
    ///
    /// Fissato al momento del consenso e non piu' cambiato: e' l'unico posto
    /// con cui questo programma parla, e non lo decide una richiesta.
    #[serde(default)]
    pub server_url: String,
    /// Quando e' stato dato il consenso, in secondi dall'epoca.
    ///
    /// Non serve a scadere niente — un consenso dato una volta resta, ed e'
    /// cio' che l'utente ha chiesto. Serve a poterlo raccontare: «hai
    /// autorizzato questo il giorno tale» e' una frase che si deve poter dire.
    pub consented_at: u64,
}

impl Pairing {
    /// Legge l'appaiamento dal disco.
    ///
    /// Un file assente significa che il consenso non e' mai stato dato:
    /// l'aiutante non deve inventarne uno. Un file illeggibile e' peggio di
    /// un file assente e viene trattato come tale — fail-closed.
    pub fn load(path: &Path) -> Option<Self> {
        let testo = fs::read_to_string(path).ok()?;
        let appaiamento: Pairing = serde_json::from_str(&testo).ok()?;
        if appaiamento.owner_sid.is_empty() || appaiamento.public_key_hex.is_empty() {
            return None;
        }
        Some(appaiamento)
    }

    /// Scrive l'appaiamento. Si chiama UNA volta, al consenso.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let testo = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        fs::write(path, testo)
    }

    /// La chiave pubblica come struttura utilizzabile, se e' valida.
    fn verifying_key(&self) -> Option<VerifyingKey> {
        let bytes = hex::decode(&self.public_key_hex).ok()?;
        let array: [u8; 32] = bytes.try_into().ok()?;
        VerifyingKey::from_bytes(&array).ok()
    }

    /// Vero quando questo e' il proprietario che ha dato il consenso.
    ///
    /// Confronto esatto sul SID. Non «appartiene al gruppo», non «assomiglia
    /// a»: e' quella persona o non lo e'.
    pub fn is_owner(&self, caller_sid: &str) -> bool {
        !caller_sid.is_empty() && caller_sid == self.owner_sid
    }

    /// Vero quando la richiesta e' firmata dall'installazione appaiata.
    pub fn verify_signature(&self, request: &WireRequest) -> bool {
        let Some(key) = self.verifying_key() else {
            return false;
        };
        let Ok(bytes) = hex::decode(request.signature()) else {
            return false;
        };
        let Ok(array) = <[u8; 64]>::try_from(bytes.as_slice()) else {
            return false;
        };
        key.verify(
            request.canonical_body().as_bytes(),
            &Signature::from_bytes(&array),
        )
        .is_ok()
    }

    /// Verify the server authority carried only by managed-provider reads.
    /// Other request kinds have their existing authorization path unchanged.
    pub fn verify_provider_grant(&self, request: &WireRequest) -> bool {
        let WireRequest::ManagedProvider(provider) = request else {
            return true;
        };
        let Ok(public_bytes) = URL_SAFE_NO_PAD.decode(&self.server_public_key_b64) else {
            return false;
        };
        let Ok(public_array) = <[u8; 32]>::try_from(public_bytes.as_slice()) else {
            return false;
        };
        let Ok(public_key) = VerifyingKey::from_bytes(&public_array) else {
            return false;
        };
        let Ok(signature_bytes) = URL_SAFE_NO_PAD.decode(&provider.grant_signature) else {
            return false;
        };
        let Ok(signature_array) = <[u8; 64]>::try_from(signature_bytes.as_slice()) else {
            return false;
        };
        public_key
            .verify(
                provider.canonical_grant_body().as_bytes(),
                &Signature::from_bytes(&signature_array),
            )
            .is_ok()
    }
}

/// Dove vivono i dati dell'aiutante.
///
/// Sotto ProgramData, non sotto la cartella di un utente: l'aiutante gira
/// come servizio di sistema e i suoi dati non appartengono a una sessione.
pub fn data_dir() -> PathBuf {
    if let Ok(program_data) = std::env::var("ProgramData") {
        return PathBuf::from(program_data).join("Metnos").join("helper");
    }
    // Fuori Windows serve solo alle prove: nessun percorso di sistema.
    std::env::temp_dir().join("metnos-helper")
}

pub fn pairing_path() -> PathBuf {
    data_dir().join("pairing.json")
}

pub fn journal_path() -> PathBuf {
    data_dir().join("consumed.log")
}

/// Dove si scarica il programma nuovo prima di metterlo al suo posto.
/// Qui scrive solo il sistema.
pub fn download_path() -> PathBuf {
    data_dir().join("metnos-helper.new")
}

/// La decisione completa su una richiesta: si esegue, o non si esegue e
/// perche'.
///
/// L'ordine dei controlli non e' casuale. Prima la forma, che non costa
/// niente e non richiede stato; poi il chiamante, che e' un fatto del sistema
/// operativo; poi la firma, che e' un calcolo; infine la ripetizione, che
/// richiede di leggere il registro. Un valore malformato non deve nemmeno
/// arrivare a un confronto di firma.
pub fn authorize(
    pairing: &Pairing,
    caller_sid: &str,
    request: &WireRequest,
    already_used: impl Fn(&str) -> bool,
) -> Result<(), Refusal> {
    request.check_shape()?;
    if !pairing.is_owner(caller_sid) {
        return Err(Refusal::UntrustedSignature);
    }
    if !pairing.verify_signature(request) {
        return Err(Refusal::UntrustedSignature);
    }
    if !pairing.verify_provider_grant(request) {
        return Err(Refusal::UntrustedGrant);
    }
    if already_used(request.idempotency_key()) {
        return Err(Refusal::ReplayedRequest);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{
        HardwareDomain, ManagedProviderRequest, Operation, ProviderInterface, Request, SensorKind,
        Source, WireRequest,
    };
    use ed25519_dalek::{Signer, SigningKey};

    fn coppia() -> (SigningKey, Pairing) {
        // Chiave deterministica: una prova non deve dipendere dal caso.
        let signing = SigningKey::from_bytes(&[7u8; 32]);
        let pairing = Pairing {
            owner_sid: "S-1-5-21-1-2-3-1001".into(),
            public_key_hex: hex::encode(signing.verifying_key().to_bytes()),
            server_public_key_b64: String::new(),
            server_url: String::new(),
            consented_at: 1_786_000_000,
        };
        (signing, pairing)
    }

    fn firmata(signing: &SigningKey, id: &str) -> Request {
        let mut r = Request {
            operation: Operation::Install,
            source: Source::Winget,
            package_id: id.into(),
            version: None,
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        };
        r.signature = hex::encode(signing.sign(r.canonical_body().as_bytes()).to_bytes());
        r
    }

    fn mai_usata(_: &str) -> bool {
        false
    }

    fn autorizza(
        pairing: &Pairing,
        caller_sid: &str,
        request: &Request,
        already_used: impl Fn(&str) -> bool,
    ) -> Result<(), Refusal> {
        authorize(
            pairing,
            caller_sid,
            &WireRequest::Package(request.clone()),
            already_used,
        )
    }

    // ── Il caso buono ──
    #[test]
    fn il_proprietario_con_una_firma_valida_passa() {
        let (s, p) = coppia();
        let r = firmata(&s, "Microsoft.PowerToys");
        assert_eq!(autorizza(&p, &p.owner_sid, &r, mai_usata), Ok(()));
    }

    // ── I due controlli servono a cose diverse ──
    #[test]
    fn un_altro_utente_non_passa_nemmeno_con_una_firma_valida() {
        // La firma dice DA DOVE viene la richiesta, non CHI la manda: senza
        // il controllo sul chiamante, una richiesta firmata riprodotta da
        // chiunque sarebbe accettata.
        let (s, p) = coppia();
        let r = firmata(&s, "Microsoft.PowerToys");
        assert_eq!(
            autorizza(&p, "S-1-5-21-1-2-3-1002", &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    #[test]
    fn il_proprietario_senza_firma_valida_non_passa() {
        // Il controllo opposto: senza la firma, qualunque cosa scritta dal
        // processo giusto verrebbe eseguita.
        let (_s, p) = coppia();
        let altra = SigningKey::from_bytes(&[9u8; 32]);
        let r = firmata(&altra, "Microsoft.PowerToys");
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    #[test]
    fn un_chiamante_senza_identita_non_passa() {
        let (s, p) = coppia();
        let r = firmata(&s, "X.Y");
        assert_eq!(
            autorizza(&p, "", &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    // ── Una firma non si trasferisce ──
    #[test]
    fn una_firma_non_vale_per_unaltra_richiesta() {
        let (s, p) = coppia();
        let mut r = firmata(&s, "Microsoft.PowerToys");
        // Stessa firma, pacchetto cambiato: e' il caso che conta.
        r.package_id = "Qualcos.Altro".into();
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    #[test]
    fn una_firma_per_installare_non_vale_per_rimuovere() {
        let (s, p) = coppia();
        let mut r = firmata(&s, "X.Y");
        r.operation = Operation::Uninstall;
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    #[test]
    fn una_firma_non_vale_per_unaltra_versione() {
        let (s, p) = coppia();
        let mut r = firmata(&s, "X.Y");
        r.version = Some("9.9".into());
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, mai_usata),
            Err(Refusal::UntrustedSignature)
        );
    }

    // ── Una richiesta non si ripete ──
    #[test]
    fn una_richiesta_gia_usata_non_si_ripete() {
        let (s, p) = coppia();
        let r = firmata(&s, "X.Y");
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, |_| true),
            Err(Refusal::ReplayedRequest)
        );
    }

    // ── L'ordine dei controlli ──
    #[test]
    fn la_forma_si_verifica_prima_di_tutto() {
        // Un valore malformato non deve nemmeno arrivare al confronto di una
        // firma: si rifiuta per cio' che e', non per cio' che non prova.
        let (s, p) = coppia();
        let mut r = firmata(&s, "X.Y");
        r.package_id = "--force".into();
        assert_eq!(
            autorizza(&p, &p.owner_sid, &r, mai_usata),
            Err(Refusal::MalformedPackageId)
        );
    }

    // ── Firme che non sono firme ──
    #[test]
    fn una_firma_malformata_non_passa() {
        let (s, p) = coppia();
        for cattiva in ["", "non-esadecimale", "aabb", &"ab".repeat(100)] {
            let mut r = firmata(&s, "X.Y");
            r.signature = cattiva.to_string();
            assert_eq!(
                autorizza(&p, &p.owner_sid, &r, mai_usata),
                Err(Refusal::UntrustedSignature),
                "accettata la firma {cattiva:?}"
            );
        }
    }

    #[test]
    fn una_chiave_pubblica_malformata_non_autorizza_niente() {
        let (s, mut p) = coppia();
        let r = firmata(&s, "X.Y");
        for cattiva in ["", "zz", &"aa".repeat(31), &"aa".repeat(33)] {
            p.public_key_hex = cattiva.to_string();
            assert_eq!(
                autorizza(&p, &p.owner_sid, &r, mai_usata),
                Err(Refusal::UntrustedSignature)
            );
        }
    }

    #[test]
    fn provider_requires_both_client_signature_and_server_grant() {
        use base64::Engine as _;

        let (client, mut pairing) = coppia();
        let server = SigningKey::from_bytes(&[8u8; 32]);
        pairing.server_public_key_b64 = URL_SAFE_NO_PAD.encode(server.verifying_key().to_bytes());
        let mut provider = ManagedProviderRequest {
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
            grant_signature: String::new(),
            idempotency_key: "0123456789abcdef0123456789abcdef".into(),
            signature: String::new(),
        };
        provider.grant_signature = URL_SAFE_NO_PAD.encode(
            server
                .sign(provider.canonical_grant_body().as_bytes())
                .to_bytes(),
        );
        provider.signature =
            hex::encode(client.sign(provider.canonical_body().as_bytes()).to_bytes());
        let wire = WireRequest::ManagedProvider(provider.clone());
        assert_eq!(
            authorize(&pairing, &pairing.owner_sid, &wire, mai_usata),
            Ok(())
        );

        provider.package_id = "Other.Package".into();
        provider.signature =
            hex::encode(client.sign(provider.canonical_body().as_bytes()).to_bytes());
        let tampered = WireRequest::ManagedProvider(provider);
        assert_eq!(
            authorize(&pairing, &pairing.owner_sid, &tampered, mai_usata),
            Err(Refusal::UntrustedGrant),
        );
    }

    // ── L'appaiamento sul disco ──
    #[test]
    fn un_appaiamento_assente_non_si_inventa() {
        let p = std::env::temp_dir().join("metnos-pairing-mai-esistito.json");
        let _ = fs::remove_file(&p);
        assert!(Pairing::load(&p).is_none());
    }

    #[test]
    fn un_appaiamento_illeggibile_vale_come_assente() {
        // Fail-closed: un file rotto non deve produrre un consenso a caso.
        let p =
            std::env::temp_dir().join(format!("metnos-pairing-rotto-{}.json", std::process::id()));
        fs::write(&p, "{ non sono json").unwrap();
        assert!(Pairing::load(&p).is_none());
        fs::write(
            &p,
            r#"{"owner_sid":"","public_key_hex":"aa","consented_at":1}"#,
        )
        .unwrap();
        assert!(Pairing::load(&p).is_none());
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn un_appaiamento_scritto_si_rilegge_uguale() {
        let (_s, originale) = coppia();
        let p = std::env::temp_dir().join(format!("metnos-pairing-{}.json", std::process::id()));
        originale.save(&p).unwrap();
        let riletto = Pairing::load(&p).expect("deve rileggersi");
        assert_eq!(riletto.owner_sid, originale.owner_sid);
        assert_eq!(riletto.public_key_hex, originale.public_key_hex);
        assert_eq!(riletto.consented_at, originale.consented_at);
        let _ = fs::remove_file(&p);
    }
}
