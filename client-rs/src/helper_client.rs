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

/// Le tre operazioni, come le vede il client. Specchio del vocabolario chiuso
/// dell'aiutante: se qui comparisse un quarto verbo, non avrebbe nessuno che
/// lo esegue.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    Query,
    Install,
    Uninstall,
}

impl Operation {
    fn as_str(self) -> &'static str {
        match self {
            Operation::Query => "query",
            Operation::Install => "install",
            Operation::Uninstall => "uninstall",
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
parlo.".into()
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
    if peer_sid != LOCAL_SYSTEM_SID {
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

#[cfg(test)]
mod tests {
    use super::*;

    const ESEGUIBILE: &str = r"C:\Program Files\Metnos\metnos-helper.exe";
    const PIPE: &str = r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001";

    // ── Il giudizio su chi c'e' dall'altro capo ──
    #[test]
    fn laiutante_vero_passa() {
        assert_eq!(
            judge_peer(PIPE, "S-1-5-18", ESEGUIBILE, ESEGUIBILE),
            Ok(())
        );
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
        assert!(matches!(esito, Err(ChannelRefusal::UnexpectedExecutable(_))));
    }

    #[test]
    fn le_maiuscole_del_percorso_non_contano() {
        // I percorsi di Windows non distinguono: un confronto sensibile
        // rifiuterebbe l'aiutante vero perche' il sistema ha scritto in
        // maiuscolo.
        assert_eq!(
            judge_peer(PIPE, "S-1-5-18", r"C:\PROGRAM FILES\METNOS\METNOS-HELPER.EXE", ESEGUIBILE),
            Ok(())
        );
    }

    #[test]
    fn una_pipe_non_locale_non_passa() {
        for remoto in [
            r"\\SERVER\pipe\metnos-helper",
            r"\\192.168.1.9\pipe\metnos-helper",
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
