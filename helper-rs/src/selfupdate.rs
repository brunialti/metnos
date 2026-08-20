//! Aggiornarsi da soli, senza chiedere niente a nessuno.
//!
//! Il server pubblica una versione; se e' piu' recente di questa, questo
//! programma si sostituisce. E' la regola che vale per il client, e vale qui
//! per la stessa ragione: un programma che resta indietro in silenzio e' un
//! programma che un giorno non capisce piu' chi gli parla.
//!
//! ## Why the helper pulls instead of accepting a pushed binary
//!
//! The unprivileged client may report that its build is newer, but it cannot
//! provide a URL, path, descriptor, or artifact. The helper reads the server
//! address and trust anchor from its system-owned pairing, fetches the signed
//! release itself, and independently validates every binding below. This
//! keeps the lazy update trigger cheap without allowing the client to choose
//! what a privileged service installs.
//!
//! ## Che cosa si verifica, e in quale ordine
//!
//! 1. la **firma del server** sul descrittore, con la chiave fissata al
//!    momento del consenso: senza, tutto il resto sarebbe fidarsi di un file
//!    scritto da un processo senza privilegi;
//! 2. che il descrittore parli di **questo** componente e di **questo**
//!    sistema: una firma valida per un altro artefatto resta una firma
//!    valida, ed e' esattamente cosi' che si scambia un programma per un
//!    altro;
//! 3. che la versione sia **strettamente piu' recente**: accettare una
//!    versione vecchia ma firmata significherebbe potersi far reinstallare un
//!    difetto gia' corretto;
//! 4. che l'**impronta** del file combaci — e la si calcola su una copia
//!    fatta in una cartella che solo il sistema puo' scrivere, non sul file
//!    lasciato dal client: fra il controllo e l'uso, un file che altri
//!    possono scrivere puo' cambiare.

use std::fs;
use std::io::Read;
use std::path::Path;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Il nome del componente, come lo pubblica il server.
pub const COMPONENT: &str = "helper";

/// Il sistema per cui questo programma e' stato compilato, con lo stesso
/// nome che usa il server per pubblicarlo. Un artefatto per un altro sistema
/// e' firmato altrettanto bene e non deve installarsi lo stesso.
pub const TARGET_TRIPLE: &str = "x86_64-pc-windows-gnu";

/// Il tetto di dimensione dell'artefatto. Non e' una misura di sicurezza —
/// la firma lo e' — ma impedisce che un percorso sbagliato faccia copiare
/// qualcosa di enorme prima di scoprire che non va bene.
pub const MAX_ARTIFACT_BYTES: u64 = 64 * 1024 * 1024;

/// Il descrittore firmato, cosi' come lo scrive il server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Descriptor {
    pub component: String,
    pub version: String,
    pub target: String,
    pub sha256: String,
    pub sig: String,
    /// Dove sta il file, secondo il server. Non entra nella firma: e' un
    /// indirizzo, e cio' che conta e' che il file che arriva abbia
    /// l'impronta firmata — da dove sia passato non cambia niente.
    #[serde(default)]
    pub url_path: String,
}

/// Come e' andata. Ogni voce e' un fatto diverso, e restano diversi: «non
/// c'era niente» e «c'era ed era falso» chiedono due cose diverse a chi legge
/// il registro.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Il server pubblica questa stessa versione, o una piu' vecchia. Il
    /// caso normale.
    NotNewer,
    /// C'era, e non ha superato un controllo. Il codice dice quale.
    Refused(&'static str),
    /// Sostituito. La stringa e' la versione ora installata.
    Applied(String),
}

/// I byte su cui il server ha calcolato la firma.
///
/// Le chiavi in ordine alfabetico e senza spazi: e' la stessa forma che
/// produce il server (`json.dumps(sort_keys=True, separators=(",", ":"))`) e
/// che verifica il client. Tre programmi, tre linguaggi, una sola forma —
/// scritta per esteso in ognuno, perche' nessuno dei tre puo' leggere il
/// codice degli altri.
pub fn canonical_payload(component: &str, sha256: &str, target: &str, version: &str) -> Vec<u8> {
    let mappa: std::collections::BTreeMap<&str, &str> = [
        ("component", component),
        ("sha256", sha256),
        ("target", target),
        ("version", version),
    ]
    .into_iter()
    .collect();
    // BTreeMap serializza in ordine di chiave, e serde_json non mette spazi:
    // le due proprieta' che definiscono la forma canonica.
    serde_json::to_vec(&mappa).unwrap_or_default()
}

/// Vero quando `candidata` e' strettamente piu' recente di `corrente`.
///
/// Confronto numerico per segmenti, non fra stringhe: `0.2.9` e `0.2.10`
/// ordinati come testo darebbero il risultato sbagliato, e il risultato
/// sbagliato qui significa retrocedere a una versione con un difetto gia'
/// corretto.
pub fn version_gt(candidata: &str, corrente: &str) -> bool {
    let pezzi = |v: &str| -> Vec<u64> {
        v.split('.')
            .map(|p| p.trim().parse::<u64>().unwrap_or(0))
            .collect()
    };
    let (a, b) = (pezzi(candidata), pezzi(corrente));
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (
            a.get(i).copied().unwrap_or(0),
            b.get(i).copied().unwrap_or(0),
        );
        if x != y {
            return x > y;
        }
    }
    false
}

/// Il descrittore e' autentico, per questo componente, e piu' recente?
///
/// Nessun effetto: decide soltanto. Cosi' si puo' provare per intero senza
/// toccare un file.
pub fn verify(
    descriptor: &Descriptor,
    server_key_b64: &str,
    own_version: &str,
    own_target: &str,
) -> Result<(), &'static str> {
    if server_key_b64.is_empty() {
        // Un aiutante installato prima che gli aggiornamenti esistessero non
        // ha un'ancora di fiducia. Non si inventa: resta com'e'.
        return Err("no_trust_anchor");
    }
    let chiave = URL_SAFE_NO_PAD
        .decode(server_key_b64)
        .ok()
        .and_then(|b| <[u8; 32]>::try_from(b.as_slice()).ok())
        .and_then(|b| VerifyingKey::from_bytes(&b).ok())
        .ok_or("malformed_trust_anchor")?;
    let firma = URL_SAFE_NO_PAD
        .decode(&descriptor.sig)
        .ok()
        .and_then(|b| <[u8; 64]>::try_from(b.as_slice()).ok())
        .map(|b| Signature::from_bytes(&b))
        .ok_or("malformed_signature")?;

    let corpo = canonical_payload(
        &descriptor.component,
        &descriptor.sha256,
        &descriptor.target,
        &descriptor.version,
    );
    // La firma PRIMA di ogni altra cosa: quello che segue sono affermazioni
    // del descrittore, e finche' la firma non regge sono affermazioni di
    // chiunque abbia potuto scrivere il file.
    chiave
        .verify(&corpo, &firma)
        .map_err(|_| "untrusted_signature")?;

    if descriptor.component != COMPONENT {
        return Err("wrong_component");
    }
    if descriptor.target != own_target {
        return Err("wrong_target");
    }
    if !version_gt(&descriptor.version, own_version) {
        return Err("not_newer");
    }
    Ok(())
}

/// Chiede al server quale versione ci si aspetta.
///
/// Una sola richiesta in uscita, verso l'indirizzo fissato al momento del
/// consenso. Questo programma non ascolta niente e non parla con nessun
/// altro.
pub fn fetch_descriptor(server_url: &str, target: &str) -> Result<Descriptor, &'static str> {
    let url = format!(
        "{}/agent/component/{COMPONENT}/update/{target}",
        server_url.trim_end_matches('/')
    );
    let corpo = ureq::get(&url)
        .call()
        .map_err(|_| "descriptor_unreachable")?
        .body_mut()
        .read_to_string()
        .map_err(|_| "descriptor_unreadable")?;
    serde_json::from_str(&corpo).map_err(|_| "descriptor_malformed")
}

/// Scarica l'artefatto e ne verifica l'impronta.
///
/// Si scrive dove solo il sistema puo' scrivere. L'impronta si controlla
/// PRIMA che il file diventi qualcosa che si esegue: un file scaricato non
/// e' un file fidato, e la firma del descrittore vale solo per il contenuto
/// che quell'impronta descrive.
pub fn fetch_artifact(
    server_url: &str,
    descriptor: &Descriptor,
    destinazione: &Path,
) -> Result<(), &'static str> {
    let url = format!(
        "{}{}",
        server_url.trim_end_matches('/'),
        descriptor.url_path
    );
    let mut byte = Vec::new();
    ureq::get(&url)
        .call()
        .map_err(|_| "artifact_unreachable")?
        .body_mut()
        .as_reader()
        .take(MAX_ARTIFACT_BYTES)
        .read_to_end(&mut byte)
        .map_err(|_| "artifact_unreadable")?;

    let impronta = hex::encode(Sha256::digest(&byte));
    if !impronta.eq_ignore_ascii_case(&descriptor.sha256) {
        return Err("artifact_hash_mismatch");
    }
    fs::write(destinazione, &byte).map_err(|_| "artifact_write_failed")
}

/// Guarda se c'e' una versione piu' recente, e nel caso si aggiorna.
///
/// L'ordine e' quello che conta, ed e' fissato qui una volta sola: prima si
/// verifica la FIRMA del descrittore, poi si scarica, poi si controlla
/// l'impronta, e solo alla fine si sostituisce. Ogni passo si fida soltanto
/// del precedente.
///
/// `replace` riceve il file gia' verificato. E' un parametro perche'
/// sostituire un eseguibile e' l'unica parte che dipende dal sistema
/// operativo: cosi' la sequenza dei controlli si prova per intero su
/// qualunque macchina.
pub fn check_and_apply(
    server_url: &str,
    server_key_b64: &str,
    own_version: &str,
    own_target: &str,
    scaricato: &Path,
    fetch: impl FnOnce(&str, &str) -> Result<Descriptor, &'static str>,
    download: impl FnOnce(&str, &Descriptor, &Path) -> Result<(), &'static str>,
    replace: impl FnOnce(&Path) -> Result<(), &'static str>,
) -> Outcome {
    if server_url.is_empty() {
        return Outcome::Refused("no_server_url");
    }
    let descrittore = match fetch(server_url, own_target) {
        Ok(d) => d,
        Err(motivo) => return Outcome::Refused(motivo),
    };
    // LA FIRMA PRIMA DI TUTTO. Niente che venga dal descrittore puo'
    // influenzare il comportamento finche' la firma non regge: quello che
    // c'e' scritto dentro, impronta compresa, sono affermazioni di chiunque
    // abbia potuto rispondere alla richiesta.
    //
    // Qui c'era il difetto: la scorciatoia sull'impronta stava PRIMA di
    // questa riga. Chi sta in mezzo alla rete — e il canale in chiaro e'
    // ammesso di proposito, perche' a proteggere e' la firma — poteva
    // rispondere con un descrittore non firmato che dichiarava l'impronta
    // del binario gia' installato: si tornava «sei aggiornato» senza mai
    // verificare niente, e l'aiutante restava fermo alla sua versione per
    // sempre. Non un'installazione falsa — quella la firma la impedisce —
    // ma un aggiornamento impedito IN SILENZIO, che e' peggio di uno
    // impedito rumorosamente (trovato dalla revisione, 19/8/2026).
    if let Err(motivo) = verify(&descrittore, server_key_b64, own_version, own_target) {
        return if motivo == "not_newer" {
            Outcome::NotNewer
        } else {
            Outcome::Refused(motivo)
        };
    }

    // Sono GIA' questo binario? Ora la domanda si fa su un descrittore
    // VERIFICATO, e non e' piu' un confine di fiducia: serve solo a non
    // riscaricarsi addosso lo stesso file quando il numero pubblicato e
    // quello compilato non coincidono — succede al primo rilascio, o se
    // qualcuno dimentica di allinearli. L'impronta dice la verita' dove il
    // numero mentirebbe.
    if let Ok(mio) = std::env::current_exe().and_then(fs::read) {
        if hex::encode(Sha256::digest(&mio)).eq_ignore_ascii_case(&descrittore.sha256) {
            return Outcome::NotNewer;
        }
    }

    let esito = (|| -> Result<String, &'static str> {
        download(server_url, &descrittore, scaricato)?;
        replace(scaricato)?;
        Ok(descrittore.version.clone())
    })();
    let _ = fs::remove_file(scaricato);
    match esito {
        Ok(versione) => Outcome::Applied(versione),
        Err("not_newer") => Outcome::NotNewer,
        Err(motivo) => Outcome::Refused(motivo),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    const TARGET: &str = "x86_64-pc-windows-gnu";

    fn firmato(chiave: &SigningKey, versione: &str, sha: &str, componente: &str) -> Descriptor {
        let corpo = canonical_payload(componente, sha, TARGET, versione);
        Descriptor {
            component: componente.into(),
            version: versione.into(),
            target: TARGET.into(),
            sha256: sha.into(),
            sig: URL_SAFE_NO_PAD.encode(chiave.sign(&corpo).to_bytes()),
            url_path: "/agent/client/x/y/metnos-helper.exe".into(),
        }
    }

    fn chiavi() -> (SigningKey, String) {
        let k = SigningKey::from_bytes(&[7u8; 32]);
        let pub_b64 = URL_SAFE_NO_PAD.encode(k.verifying_key().to_bytes());
        (k, pub_b64)
    }

    #[test]
    fn la_forma_canonica_e_scritta_per_esteso() {
        // Tre programmi in tre linguaggi devono produrre questi byte. Se
        // cambia, le firme del server smettono di verificare qui e
        // l'aggiornamento si ferma senza che nessuno sappia perche'.
        assert_eq!(
            String::from_utf8(canonical_payload("helper", "abc", "t", "1.2.3")).unwrap(),
            r#"{"component":"helper","sha256":"abc","target":"t","version":"1.2.3"}"#
        );
    }

    #[test]
    fn un_descrittore_autentico_e_piu_recente_passa() {
        let (k, pubk) = chiavi();
        let d = firmato(&k, "0.3.0", &"a".repeat(64), COMPONENT);
        assert_eq!(verify(&d, &pubk, "0.2.26", TARGET), Ok(()));
    }

    #[test]
    fn una_firma_di_un_altro_non_passa() {
        let (_, pubk) = chiavi();
        let altra = SigningKey::from_bytes(&[9u8; 32]);
        let d = firmato(&altra, "0.3.0", &"a".repeat(64), COMPONENT);
        assert_eq!(
            verify(&d, &pubk, "0.2.26", TARGET),
            Err("untrusted_signature")
        );
    }

    #[test]
    fn un_descrittore_alterato_dopo_la_firma_non_passa() {
        // Il caso che conta: la firma c'e' ed e' del server, ma il contenuto
        // non e' piu' quello firmato.
        let (k, pubk) = chiavi();
        let mut d = firmato(&k, "0.3.0", &"a".repeat(64), COMPONENT);
        d.sha256 = "b".repeat(64);
        assert_eq!(
            verify(&d, &pubk, "0.2.26", TARGET),
            Err("untrusted_signature")
        );
    }

    #[test]
    fn il_descrittore_di_un_altro_componente_non_passa() {
        // Firma valida, artefatto sbagliato: e' cosi' che si fa installare a
        // un programma il binario di un altro.
        let (k, pubk) = chiavi();
        let d = firmato(&k, "0.3.0", &"a".repeat(64), "client");
        assert_eq!(verify(&d, &pubk, "0.2.26", TARGET), Err("wrong_component"));
    }

    #[test]
    fn una_versione_vecchia_ma_firmata_non_passa() {
        // Reinstallare all'indietro significherebbe potersi far rimettere un
        // difetto gia' corretto.
        let (k, pubk) = chiavi();
        for vecchia in ["0.2.25", "0.2.26", "0.1.0"] {
            let d = firmato(&k, vecchia, &"a".repeat(64), COMPONENT);
            assert_eq!(
                verify(&d, &pubk, "0.2.26", TARGET),
                Err("not_newer"),
                "accettata {vecchia}"
            );
        }
    }

    #[test]
    fn senza_ancora_di_fiducia_non_si_aggiorna() {
        let (k, _) = chiavi();
        let d = firmato(&k, "9.9.9", &"a".repeat(64), COMPONENT);
        assert_eq!(verify(&d, "", "0.2.26", TARGET), Err("no_trust_anchor"));
    }

    #[test]
    fn le_versioni_si_confrontano_per_numero_non_per_testo() {
        assert!(version_gt("0.2.10", "0.2.9"), "0.2.10 deve battere 0.2.9");
        assert!(version_gt("0.3.0", "0.2.99"));
        assert!(version_gt("1.0.0", "0.99.99"));
        assert!(!version_gt("0.2.9", "0.2.10"));
        assert!(!version_gt("0.2.26", "0.2.26"));
    }
}

#[cfg(test)]
mod tests_applicazione {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    const TARGET: &str = "x86_64-pc-windows-gnu";

    fn chiavi() -> (SigningKey, String) {
        let k = SigningKey::from_bytes(&[11u8; 32]);
        let pubb = URL_SAFE_NO_PAD.encode(k.verifying_key().to_bytes());
        (k, pubb)
    }

    fn descrittore(
        chiave: &SigningKey,
        versione: &str,
        contenuto: &[u8],
        componente: &str,
    ) -> Descriptor {
        let sha = hex::encode(Sha256::digest(contenuto));
        let corpo = canonical_payload(componente, &sha, TARGET, versione);
        Descriptor {
            component: componente.into(),
            version: versione.into(),
            target: TARGET.into(),
            sha256: sha,
            sig: URL_SAFE_NO_PAD.encode(chiave.sign(&corpo).to_bytes()),
            url_path: "/agent/client/x/y/metnos-helper.exe".into(),
        }
    }

    /// Fa girare il flusso senza rete: la sequenza dei controlli e' la stessa,
    /// ed e' quella che si vuole provare.
    fn prova(
        d: Descriptor,
        contenuto: &'static [u8],
        chiave_fidata: &str,
    ) -> (Outcome, Option<Vec<u8>>) {
        let scaricato =
            std::env::temp_dir().join(format!("metnos-su-{}-{:p}.new", std::process::id(), &d));
        let sostituito = std::cell::RefCell::new(None);
        let esito = check_and_apply(
            "https://server.esempio",
            chiave_fidata,
            "0.2.26",
            TARGET,
            &scaricato,
            |_, _| Ok(d.clone()),
            |_, desc, dove| {
                // Come farebbe la vera: scrive e controlla l'impronta.
                let impronta = hex::encode(Sha256::digest(contenuto));
                if !impronta.eq_ignore_ascii_case(&desc.sha256) {
                    return Err("artifact_hash_mismatch");
                }
                fs::write(dove, contenuto).map_err(|_| "artifact_write_failed")
            },
            |percorso| {
                *sostituito.borrow_mut() = Some(fs::read(percorso).unwrap());
                Ok(())
            },
        );
        let _ = fs::remove_file(&scaricato);
        (esito, sostituito.into_inner())
    }

    #[test]
    fn una_versione_piu_recente_e_autentica_si_installa() {
        let (k, pubb) = chiavi();
        let d = descrittore(&k, "0.3.0", b"il programma nuovo", COMPONENT);
        let (esito, sostituito) = prova(d, b"il programma nuovo", &pubb);
        assert_eq!(esito, Outcome::Applied("0.3.0".into()));
        assert_eq!(sostituito.as_deref(), Some(&b"il programma nuovo"[..]));
    }

    #[test]
    fn la_stessa_versione_non_e_un_errore_ed_e_il_caso_normale() {
        let (k, pubb) = chiavi();
        let d = descrittore(&k, "0.2.26", b"identico", COMPONENT);
        let (esito, sostituito) = prova(d, b"identico", &pubb);
        assert_eq!(esito, Outcome::NotNewer);
        assert!(sostituito.is_none());
    }

    #[test]
    fn un_file_diverso_da_quello_firmato_non_si_installa() {
        // Il descrittore e' autentico, ma quello che arriva e' un altro file.
        // Senza il controllo dell'impronta la firma del server garantirebbe
        // un programma che il server non ha mai visto.
        let (k, pubb) = chiavi();
        let d = descrittore(&k, "0.3.0", b"quello firmato", COMPONENT);
        let (esito, sostituito) = prova(d, b"tutt'altro programma", &pubb);
        assert_eq!(esito, Outcome::Refused("artifact_hash_mismatch"));
        assert!(
            sostituito.is_none(),
            "ha sostituito con un file non firmato"
        );
    }

    #[test]
    fn un_descrittore_firmato_da_un_altro_non_si_installa() {
        let (k, _) = chiavi();
        let altra = URL_SAFE_NO_PAD.encode(
            SigningKey::from_bytes(&[3u8; 32])
                .verifying_key()
                .to_bytes(),
        );
        let d = descrittore(&k, "0.3.0", b"programma", COMPONENT);
        let (esito, sostituito) = prova(d, b"programma", &altra);
        assert_eq!(esito, Outcome::Refused("untrusted_signature"));
        assert!(sostituito.is_none());
    }

    #[test]
    fn senza_indirizzo_del_server_non_si_aggiorna() {
        // Un aiutante installato prima che gli aggiornamenti esistessero non
        // sa a chi chiedere. Resta com'e' invece di indovinare.
        let esito = check_and_apply(
            "",
            "chiave",
            "0.2.26",
            TARGET,
            Path::new("/non/serve"),
            |_, _| panic!("non deve nemmeno chiedere"),
            |_, _, _| panic!("non deve scaricare"),
            |_| panic!("non deve sostituire"),
        );
        assert_eq!(esito, Outcome::Refused("no_server_url"));
    }
}

#[cfg(test)]
mod tests_idempotenza {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    #[test]
    fn lo_stesso_binario_non_si_reinstalla_all_infinito() {
        // Il caso che ci sarebbe costato caro: il numero pubblicato e quello
        // compilato non coincidono (primo rilascio, o numeri non allineati).
        // Il confronto fra NUMERI direbbe «sei indietro» per sempre; quello
        // fra IMPRONTE dice la verita': sono gia' io.
        //
        // Il descrittore e' FIRMATO per davvero. Prima non lo era, e la prova
        // passava lo stesso: era il difetto a farla passare — la scorciatoia
        // sull'impronta stava prima della verifica della firma, quindi una
        // firma vuota non veniva mai guardata. Adesso la scorciatoia sta
        // dopo, e questa prova verifica cio' che dice di verificare.
        let mio = std::env::current_exe().and_then(fs::read).unwrap();
        let mia_impronta = hex::encode(Sha256::digest(&mio));
        let chiave = SigningKey::from_bytes(&[13u8; 32]);
        let pubblica = URL_SAFE_NO_PAD.encode(chiave.verifying_key().to_bytes());

        let corpo = canonical_payload(COMPONENT, &mia_impronta, TARGET_TRIPLE, "9.9.9");
        let descrittore = Descriptor {
            component: COMPONENT.into(),
            version: "9.9.9".into(),
            target: TARGET_TRIPLE.into(),
            sha256: mia_impronta,
            sig: URL_SAFE_NO_PAD.encode(chiave.sign(&corpo).to_bytes()),
            url_path: "/x".into(),
        };

        let esito = check_and_apply(
            "https://server.esempio",
            &pubblica,
            // Numero volutamente indietro: se contasse il numero, si
            // aggiornerebbe.
            "0.1.0",
            TARGET_TRIPLE,
            Path::new("/non/serve"),
            move |_, _| Ok(descrittore.clone()),
            |_, _, _| panic!("non deve scaricare: e' gia' questo binario"),
            |_| panic!("non deve sostituire: e' gia' questo binario"),
        );
        assert_eq!(esito, Outcome::NotNewer);
    }

    #[test]
    fn un_descrittore_non_firmato_non_passa_nemmeno_dalla_scorciatoia() {
        // Il difetto trovato dalla revisione, fissato come prova. Chi sta in
        // mezzo alla rete conosce l'impronta del binario installato — basta
        // scaricarlo dal mirror — e puo' rispondere con un descrittore che la
        // dichiara. Se la scorciatoia stesse prima della firma, si
        // risponderebbe «sei aggiornato» senza verificare niente, e
        // l'aggiornamento resterebbe bloccato IN SILENZIO: nessuna riga nel
        // registro, perche' «non c'e' niente di nuovo» e' il caso normale.
        let mio = std::env::current_exe().and_then(fs::read).unwrap();
        let mia_impronta = hex::encode(Sha256::digest(&mio));
        let vera = SigningKey::from_bytes(&[13u8; 32]);
        let pubblica = URL_SAFE_NO_PAD.encode(vera.verifying_key().to_bytes());

        let falso = Descriptor {
            component: COMPONENT.into(),
            version: "9.9.9".into(),
            target: TARGET_TRIPLE.into(),
            sha256: mia_impronta, // l'impronta giusta...
            sig: String::new(),   // ...ma nessuna firma
            url_path: "/x".into(),
        };
        let esito = check_and_apply(
            "https://server.esempio",
            &pubblica,
            "0.1.0",
            TARGET_TRIPLE,
            Path::new("/non/serve"),
            move |_, _| Ok(falso.clone()),
            |_, _, _| panic!("non deve scaricare"),
            |_| panic!("non deve sostituire"),
        );
        assert_eq!(
            esito,
            Outcome::Refused("malformed_signature"),
            "un descrittore non firmato ha superato la scorciatoia"
        );
    }
}
