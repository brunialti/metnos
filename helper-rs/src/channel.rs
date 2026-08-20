//! Il canale locale: come si chiama, e chi puo' parlarci (ADR 0210 D2).
//!
//! Una named pipe, mai una porta di rete — nemmeno su loopback. Una porta
//! aperta e' raggiungibile da qualunque processo della macchina e, con una
//! configurazione sbagliata, da fuori; una pipe ha una lista di controllo
//! d'accesso, che e' esattamente lo strumento per dire «solo questo utente».
//!
//! ## Il nome di una pipe non e' un segreto
//!
//! E' la cosa da capire prima di leggere il resto. Chiunque puo' indovinare o
//! leggere il nome, e chiunque puo' **crearla per primo**: un processo senza
//! privilegi che vince la corsa riceve tutto cio' che il client scrive,
//! comprese le richieste firmate. Percio' l'autenticazione va in ENTRAMBE le
//! direzioni:
//!
//! - l'aiutante verifica che chi lo chiama sia il proprietario che ha dato il
//!   consenso — e per SID, non per appartenenza a un gruppo, perche' un
//!   gruppo lo si puo' allargare;
//! - il client verifica di stare parlando con l'aiutante VERO prima di
//!   scrivere alcunche', e l'aiutante crea la pipe con
//!   `FILE_FLAG_FIRST_PIPE_INSTANCE`, cosi' arrivare secondi e' un errore
//!   invece che una convivenza silenziosa.
//!
//! Questo modulo tiene la parte che si puo' provare su qualunque macchina: la
//! costruzione e la validazione del nome. La parte che apre davvero la pipe
//! vive sotto `#[cfg(windows)]` e riceve da qui un nome gia' verificato.

/// Il prefisso delle pipe locali di Windows. `.` e' la macchina corrente:
/// una pipe su un'altra macchina sarebbe rete travestita da pipe.
const LOCAL_PIPE_PREFIX: &str = r"\\.\pipe\";

/// Radice del nome. Il SID del proprietario la completa, cosi' due utenti
/// della stessa macchina non condividono un canale nemmeno per sbaglio.
const PIPE_ROOT: &str = "metnos-helper";

/// Perche' un nome non e' utilizzabile.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NameError {
    /// Il SID non ha la forma di un SID.
    MalformedSid,
}

/// Vero quando la stringa e' un SID nella forma testuale di Windows:
/// `S-1-5-21-...`. Revisione, autorita' e sottoautorita', tutte numeriche.
///
/// Serve perche' il SID finisce nel NOME di un oggetto di sistema: un valore
/// non verificato potrebbe portarci dentro un separatore e far puntare il
/// nome altrove.
pub fn is_valid_sid(value: &str) -> bool {
    let mut parti = value.split('-');
    if parti.next() != Some("S") {
        return false;
    }
    let numeriche: Vec<&str> = parti.collect();
    // Almeno revisione e autorita'; il resto sono sottoautorita'.
    if numeriche.len() < 2 || numeriche.len() > 16 {
        return false;
    }
    numeriche
        .iter()
        .all(|p| !p.is_empty() && p.len() <= 20 && p.chars().all(|c| c.is_ascii_digit()))
}

/// Il nome della pipe per questo proprietario.
///
/// Il SID viene validato PRIMA di entrare nel nome: e' l'unica parte
/// variabile, e un nome di oggetto di sistema costruito con un valore non
/// verificato e' un modo di farlo puntare dove non si vuole.
pub fn pipe_name_for_owner(owner_sid: &str) -> Result<String, NameError> {
    if !is_valid_sid(owner_sid) {
        return Err(NameError::MalformedSid);
    }
    Ok(format!("{LOCAL_PIPE_PREFIX}{PIPE_ROOT}-{owner_sid}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn un_sid_vero_e_accettato() {
        for sid in [
            "S-1-5-18",
            "S-1-5-21-1004336348-1177238915-682003330-512",
            "S-1-5-32-544",
        ] {
            assert!(is_valid_sid(sid), "rifiutato un SID vero: {sid}");
        }
    }

    #[test]
    fn niente_che_non_sia_un_sid() {
        for cattivo in [
            "",
            "S",
            "S-",
            "S-1",
            "X-1-5-18",
            "s-1-5-18",
            "S-1-5-18-",
            "S-1-5-abc",
            "S-1-5-18 ",
            " S-1-5-18",
            r"S-1-5-18\..\altro",
            "S-1-5-18/altro",
            "S-1-5-18-99999999999999999999999",
        ] {
            assert!(!is_valid_sid(cattivo), "accettato: {cattivo:?}");
        }
    }

    #[test]
    fn il_nome_della_pipe_e_scritto_per_esteso() {
        // Il client costruisce lo stesso nome per conto suo, in un progetto
        // separato che non puo' linkare questo. Se le due formule divergono
        // il client apre un nome che non esiste e riferisce «aiutante
        // assente» proprio mentre l'aiutante c'e': un sintomo che manda a
        // cercare nel posto sbagliato. La stringa e' scritta per esteso qui e
        // li', e un test Python confronta le due.
        assert_eq!(
            pipe_name_for_owner("S-1-5-21-1-2-3-1001").unwrap(),
            r"\\.\pipe\metnos-helper-S-1-5-21-1-2-3-1001"
        );
    }

    #[test]
    fn il_nome_contiene_il_sid_del_proprietario() {
        // Due utenti della stessa macchina non condividono un canale.
        let a = pipe_name_for_owner("S-1-5-21-1-2-3-1001").unwrap();
        let b = pipe_name_for_owner("S-1-5-21-1-2-3-1002").unwrap();
        assert_ne!(a, b);
        assert!(a.contains("S-1-5-21-1-2-3-1001"));
    }

    #[test]
    fn un_sid_malformato_non_entra_nel_nome() {
        // Il caso che conta: il SID e' l'unica parte variabile del nome di un
        // oggetto di sistema. Un valore non verificato potrebbe portarci
        // dentro un separatore e far puntare il nome altrove.
        for cattivo in [r"S-1-5-18\..\..\altro", "S-1-5-18/x", "../evil", ""] {
            assert_eq!(
                pipe_name_for_owner(cattivo),
                Err(NameError::MalformedSid),
                "il valore {cattivo:?} e' entrato nel nome"
            );
        }
    }

    #[test]
    fn il_nome_prodotto_e_sempre_locale() {
        // `\\.\pipe\` e' la macchina corrente. Un prefisso diverso
        // (`\\SERVER\pipe\`) sarebbe rete travestita da canale locale, e il
        // nome lo costruiamo noi proprio per non poterci arrivare.
        let nome = pipe_name_for_owner("S-1-5-21-1-2-3-1001").unwrap();
        assert!(nome.starts_with(r"\\.\pipe\"), "nome non locale: {nome}");
    }
}
