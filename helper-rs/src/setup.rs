//! Installarsi e togliersi di mezzo (ADR 0210 D4 e D6).
//!
//! Due comandi, e sono l'unico momento in cui l'aiutante tocca il sistema
//! fuori dalle sue tre operazioni.
//!
//! ## L'installazione chiede una volta sola
//!
//! Windows mostra la richiesta di amministratore quando l'installatore parte,
//! e da quel momento l'aiutante resta: le installazioni successive non
//! chiedono piu' niente. E' la cosa che l'utente ha domandato esplicitamente
//! — «i privilegi vanno chiesti una volta sola e mantenuti» — ed e' anche il
//! motivo per cui la richiesta deve dire con chiarezza che cosa si sta
//! concedendo: un consenso dato una volta e per sempre va capito una volta e
//! per sempre.
//!
//! L'aiutante NON si installa da solo durante un'installazione di pacchetto,
//! e nemmeno insieme al client: sarebbe far entrare il componente piu'
//! privilegiato come effetto collaterale di qualcos'altro.
//!
//! ## La rimozione non passa da Metnos
//!
//! Compare fra i programmi installati di Windows e si toglie da li'. Se per
//! disinstallarlo servisse Metnos, il proprietario della macchina dipenderebbe
//! da noi per riprendersi un privilegio che ha concesso lui: e' il contrario
//! di come deve funzionare.

use std::path::{Path, PathBuf};

use crate::pairing::Pairing;

/// Il nome del servizio. Fisso: non un valore che arriva da fuori.
pub const SERVICE_NAME: &str = "MetnosHelper";

/// La chiave sotto cui Windows elenca i programmi installati.
pub const ARP_KEY: &str = r"HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\MetnosHelper";

/// Che cosa la persona sta concedendo. Compare nella richiesta di consenso.
///
/// Scritto per essere capito, non per essere formalmente completo: chi legge
/// deve poter decidere, e un testo che nessuno finisce di leggere e' un
/// consenso che nessuno ha dato davvero.
pub fn consent_text(owner_name: &str) -> String {
    format!(
        "Stai per dare a Metnos il permesso di installare programmi su questo \
computer per TUTTI gli utenti.\n\n\
Che cosa viene concesso: un componente di Metnos che sa fare tre cose e \
nient'altro — dire se un programma e' installato, installarlo, rimuoverlo. \
Non accetta comandi, non parla con la rete, e obbedisce soltanto a «{owner_name}».\n\n\
Quando: una volta sola. Da adesso in poi le installazioni non chiederanno \
piu' questo permesso.\n\n\
Come toglierlo: da Impostazioni > App, come qualunque altro programma. Non \
serve Metnos, e non serve chiedere niente a nessuno."
    )
}

/// Gli argomenti per registrare il servizio.
///
/// Costruiti QUI da valori noti: il percorso dell'eseguibile viene dal sistema
/// operativo, il nome e' una costante. Nessun pezzo arriva da una richiesta.
///
/// `start= auto` perche' un aiutante che va avviato a mano non e' un aiutante:
/// la prima installazione dopo un riavvio fallirebbe senza che nessuno capisca
/// perche'.
pub fn service_create_argv(exe: &Path) -> Vec<String> {
    vec![
        "sc.exe".into(),
        "create".into(),
        SERVICE_NAME.into(),
        // Chiave e valore sono due argomenti SEPARATI, e il segno di uguale
        // sta attaccato alla chiave. Non e' un vezzo: `sc.exe` si legge la
        // riga di comando per conto suo, e un `binPath= valore` passato come
        // un'unica parola gli arriva richiuso fra virgolette e non lo capisce
        // — risponde con la sua schermata d'aiuto e non crea niente.
        //
        // Misurato sul PC il 19/8/2026: la forma unita fallisce con «campo
        // start= non valido», quella separata crea il servizio. L'aiutante
        // finiva in Program Files e poi si fermava li', senza servizio e
        // senza appaiamento, con un codice d'uscita che nessuno leggeva.
        "binPath=".into(),
        // Le virgolette interne restano: un percorso con spazi (Program
        // Files) senza di esse verrebbe letto come piu' argomenti.
        format!("\"{}\" service", exe.display()),
        "start=".into(),
        "auto".into(),
        "obj=".into(),
        "LocalSystem".into(),
        "DisplayName=".into(),
        "Metnos helper".into(),
    ]
}

/// Gli argomenti che dicono a Windows di rimettere in piedi il servizio.
///
/// Serve all'aggiornamento: dopo essersi sostituito, il programma esce, e
/// deve tornare su da solo — girando il binario nuovo. Senza questa
/// politica un aggiornamento spegnerebbe l'aiutante fino al riavvio della
/// macchina, che e' un modo di aggiornare peggiore del non aggiornare.
///
/// `reset= 0` perche' il contatore dei guasti non deve azzerarsi: se il
/// programma nuovo non sta in piedi, i tentativi devono restare tre e poi
/// smettere, invece di ripartire all'infinito.
pub fn service_recovery_argv() -> Vec<String> {
    vec![
        "sc.exe".into(),
        "failure".into(),
        SERVICE_NAME.into(),
        // Chiave e valore separati, come sopra e per la stessa ragione.
        "reset=".into(),
        "0".into(),
        "actions=".into(),
        "restart/5000/restart/15000/restart/60000".into(),
    ]
}

/// Gli argomenti per correggere un servizio che c'e' gia'.
///
/// Installare deve poter funzionare anche sopra un'installazione rimasta a
/// meta'. Succede: il servizio viene registrato e un passo successivo
/// fallisce, e da quel momento la macchina resta in un vicolo cieco —
/// l'aiutante non risponde (senza consenso il servizio non si avvia), quindi
/// Metnos propone di installarlo, e l'installazione sbatte contro il servizio
/// di prima con «servizio specificato gia' esistente» (1073). Successo il
/// 19/8/2026 su PC-ROBERTO.
///
/// `config` invece di `delete`+`create`: si corregge cio' che c'e' — il
/// percorso del binario puo' essere cambiato — senza smontare e rimontare un
/// servizio che potrebbe essere in uso.
pub fn service_config_argv(exe: &Path) -> Vec<String> {
    vec![
        "sc.exe".into(),
        "config".into(),
        SERVICE_NAME.into(),
        // Chiave e valore separati, come in `service_create_argv`.
        "binPath=".into(),
        format!("\"{}\" service", exe.display()),
        "start=".into(),
        "auto".into(),
        "obj=".into(),
        "LocalSystem".into(),
    ]
}

/// Gli argomenti per fermare il servizio.
///
/// Serve PRIMA di sostituire l'eseguibile: un servizio in esecuzione tiene
/// aperto il proprio file, e Windows rifiuta di sovrascriverlo («il file e'
/// utilizzato da un altro processo», errore 32). Installare sopra
/// un'installazione viva e' il caso normale, non l'eccezione.
pub fn service_stop_argv() -> Vec<String> {
    vec!["sc.exe".into(), "stop".into(), SERVICE_NAME.into()]
}

/// Gli argomenti per avviare il servizio adesso.
///
/// `start= auto` dice a Windows di avviarlo al PROSSIMO riavvio, non adesso.
/// Senza questo, un'installazione perfettamente riuscita lasciava l'aiutante
/// spento: la richiesta che l'aveva fatto installare falliva subito dopo con
/// «l'aiutante non risponde», e la macchina restava cosi' fino a un riavvio
/// che nessuno aveva motivo di fare. Trovato il 19/8/2026.
pub fn service_start_argv() -> Vec<String> {
    vec!["sc.exe".into(), "start".into(), SERVICE_NAME.into()]
}

/// Gli argomenti per chiedere com'e' messo il servizio.
pub fn service_query_argv() -> Vec<String> {
    vec!["sc.exe".into(), "query".into(), SERVICE_NAME.into()]
}

/// Il codice con cui Windows dice «quel servizio c'e' gia'».
pub const SERVICE_EXISTS: i32 = 1073;

/// Gli argomenti per togliere il servizio.
pub fn service_delete_argv() -> Vec<Vec<String>> {
    vec![
        vec!["sc.exe".into(), "stop".into(), SERVICE_NAME.into()],
        vec!["sc.exe".into(), "delete".into(), SERVICE_NAME.into()],
    ]
}

/// Le voci che fanno comparire l'aiutante fra i programmi installati.
///
/// Senza `UninstallString` il programma appare ma non si disinstalla, che e'
/// peggio di non apparire: sembra rimovibile e non lo e'.
pub fn arp_entries(exe: &Path, version: &str) -> Vec<(String, String)> {
    vec![
        ("DisplayName".into(), "Metnos helper".into()),
        ("DisplayVersion".into(), version.into()),
        ("Publisher".into(), "Metnos".into()),
        (
            "UninstallString".into(),
            format!("\"{}\" uninstall", exe.display()),
        ),
        ("NoModify".into(), "1".into()),
        ("NoRepair".into(), "1".into()),
    ]
}

/// Dove si installa l'eseguibile.
///
/// Sotto Program Files: e' la cartella che un utente senza privilegi non puo'
/// riscrivere. Installarlo altrove significherebbe che chiunque puo'
/// sostituire il binario che gira come sistema, e a quel punto tutto il resto
/// di questo progetto non conta piu' niente.
pub fn install_dir() -> PathBuf {
    if let Ok(program_files) = std::env::var("ProgramFiles") {
        return PathBuf::from(program_files).join("Metnos");
    }
    std::env::temp_dir().join("metnos-install")
}

/// Che cosa deve essere vero perche' l'installazione abbia senso.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SetupRefusal {
    /// Il SID del proprietario non e' un SID.
    MalformedOwnerSid,
    /// La chiave pubblica non e' una chiave.
    MalformedPublicKey,
    /// La chiave del server non e' una chiave.
    MalformedServerKey,
    /// L'indirizzo del server non e' un indirizzo cifrato.
    MalformedServerUrl,
    /// C'e' gia' un appaiamento: installare di nuovo cambierebbe il
    /// proprietario in silenzio.
    AlreadyPaired,
}

impl SetupRefusal {
    pub fn code(&self) -> &'static str {
        match self {
            SetupRefusal::MalformedOwnerSid => "malformed_owner_sid",
            SetupRefusal::MalformedPublicKey => "malformed_public_key",
            SetupRefusal::MalformedServerKey => "malformed_server_key",
            SetupRefusal::MalformedServerUrl => "malformed_server_url",
            SetupRefusal::AlreadyPaired => "already_paired",
        }
    }
}

/// Prepara l'appaiamento, o dice perche' non si puo'.
///
/// Un aiutante gia' appaiato NON viene riappaiato in silenzio: cambiare
/// proprietario e' cambiare chi comanda un componente privilegiato, e non e'
/// una cosa che debba poter succedere rilanciando un installatore. Si
/// disinstalla e si reinstalla, cosi' il passaggio e' un atto esplicito.
pub fn prepare_pairing(
    owner_sid: &str,
    public_key_hex: &str,
    server_key_b64: &str,
    server_url: &str,
    pairing_path: &Path,
    now: u64,
) -> Result<Pairing, SetupRefusal> {
    if !crate::channel::is_valid_sid(owner_sid) {
        return Err(SetupRefusal::MalformedOwnerSid);
    }
    if public_key_hex.len() != 64 || !public_key_hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(SetupRefusal::MalformedPublicKey);
    }
    // Una chiave Ed25519 in base64url senza riempimento: 43 caratteri, e
    // niente che non appartenga a quell'alfabeto. Si valida qui perche' da
    // qui in poi e' cio' che decide se un aggiornamento e' autentico.
    // Un indirizzo, e nient'altro: e' l'unico posto con cui questo programma
    // parlera'.
    //
    // In chiaro va bene, e non e' una concessione: a proteggere un
    // aggiornamento e' la FIRMA del server, non il canale. Il server di casa
    // sta sulla rete locale in chiaro, ed e' il caso normale — pretendere un
    // canale cifrato qui non aggiungerebbe una difesa, toglierebbe la
    // possibilita' di installare.
    //
    // Cosa resta scoperto, detto chiaro: chi ascolta la rete vede quale
    // versione gira. Chi la controlla puo' impedire un aggiornamento, non
    // provocarne uno falso — la firma e il divieto di tornare indietro
    // bastano a quello.
    if !(server_url.starts_with("http://") || server_url.starts_with("https://"))
        || server_url.len() > 300
        || server_url.bytes().any(|b| b <= b' ' || b == b'"')
    {
        return Err(SetupRefusal::MalformedServerUrl);
    }
    if server_key_b64.len() != 43
        || !server_key_b64
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(SetupRefusal::MalformedServerKey);
    }
    let chiave = public_key_hex.to_ascii_lowercase();
    if let Some(esistente) = Pairing::load(pairing_path) {
        // Reinstallare la STESSA installazione sopra se stessa e' come si
        // aggiorna: i due programmi viaggiano insieme e devono restare
        // allineati, e chiedere di nuovo il consenso a ogni versione
        // significherebbe che l'utente lo concede molte volte invece di una
        // — l'opposto di cio' che gli e' stato promesso.
        //
        // Il consenso originale sopravvive con la sua data: e' quello che
        // l'utente ha dato, e non lo si riscrive perche' e' passato del
        // tempo.
        if esistente.owner_sid == owner_sid && esistente.public_key_hex == chiave {
            // La chiave del server puo' essere cambiata (o mancare del tutto,
            // su un aiutante installato prima che gli aggiornamenti
            // esistessero): la si aggiorna, perche' viene dalla stessa
            // installazione che aveva gia' il consenso.
            return Ok(Pairing {
                server_public_key_b64: server_key_b64.to_string(),
                server_url: server_url.to_string(),
                ..esistente
            });
        }
        // Un proprietario diverso, o una chiave diversa, non e' un
        // aggiornamento: e' qualcun altro che prova a subentrare. Il consenso
        // si toglie disinstallando, non sovrascrivendo.
        return Err(SetupRefusal::AlreadyPaired);
    }
    Ok(Pairing {
        owner_sid: owner_sid.to_string(),
        public_key_hex: chiave,
        server_public_key_b64: server_key_b64.to_string(),
        server_url: server_url.to_string(),
        consented_at: now,
    })
}

/// I file che la rimozione deve cancellare.
///
/// L'appaiamento se ne va con l'aiutante: lasciarlo significherebbe che una
/// reinstallazione riprende un consenso che il proprietario aveva revocato
/// togliendo il programma.
/// Il registro (`audit.log`) NON e' in questa lista, e non e' una svista:
/// e' la traccia di cio' che e' stato fatto sulla macchina mentre l'aiutante
/// c'era. Cancellarla insieme al programma darebbe a chiunque possa
/// disinstallare anche il potere di far sparire le proprie tracce, e un
/// registro che si puo' cancellare non e' un registro.
pub fn files_to_remove(data_dir: &Path) -> Vec<PathBuf> {
    vec![data_dir.join("pairing.json"), data_dir.join("consumed.log")]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporanea(nome: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("metnos-setup-{nome}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    const CHIAVE: &str = "aa00bb11cc22dd33ee44ff5566778899aabbccddeeff00112233445566778899";
    const URL_SERVER: &str = "https://metnos.esempio";
    const CHIAVE_SERVER: &str = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8";
    const SID: &str = "S-1-5-21-1-2-3-1001";

    // ── L'appaiamento al consenso ──
    #[test]
    fn un_appaiamento_valido_si_prepara() {
        let d = temporanea("valido");
        let p = prepare_pairing(
            SID,
            CHIAVE,
            CHIAVE_SERVER,
            URL_SERVER,
            &d.join("pairing.json"),
            1_786_000_000,
        )
        .unwrap();
        assert_eq!(p.owner_sid, SID);
        assert_eq!(p.consented_at, 1_786_000_000);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn un_secondo_appaiamento_non_cambia_il_proprietario_in_silenzio() {
        // Cambiare proprietario e' cambiare chi comanda un componente
        // privilegiato: non deve poter succedere rilanciando un installatore.
        let d = temporanea("gia-appaiato");
        let percorso = d.join("pairing.json");
        prepare_pairing(SID, CHIAVE, CHIAVE_SERVER, URL_SERVER, &percorso, 1)
            .unwrap()
            .save(&percorso)
            .unwrap();

        assert_eq!(
            prepare_pairing(
                "S-1-5-21-9-9-9-9999",
                CHIAVE,
                CHIAVE_SERVER,
                URL_SERVER,
                &percorso,
                2
            ),
            Err(SetupRefusal::AlreadyPaired)
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn un_server_in_chiaro_sulla_rete_di_casa_va_bene() {
        // Il caso reale: il server di Metnos sta sulla rete locale in
        // chiaro. A proteggere un aggiornamento e' la firma, non il canale;
        // pretendere un canale cifrato renderebbe impossibile installare
        // senza aggiungere una difesa.
        let d = temporanea("server-in-chiaro");
        assert!(prepare_pairing(
            SID,
            CHIAVE,
            CHIAVE_SERVER,
            "http://192.0.2.10:8765",
            &d.join("pairing.json"),
            1
        )
        .is_ok());
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn cio_che_non_e_un_indirizzo_non_diventa_un_indirizzo() {
        let d = temporanea("server-non-indirizzo");
        for cattivo in [
            "",
            "192.0.2.10",
            "file:///etc/passwd",
            "http://a b",
            "http://a\"b",
        ] {
            assert_eq!(
                prepare_pairing(
                    SID,
                    CHIAVE,
                    CHIAVE_SERVER,
                    cattivo,
                    &d.join("pairing.json"),
                    1
                ),
                Err(SetupRefusal::MalformedServerUrl),
                "accettato {cattivo:?}"
            );
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn reinstallare_la_stessa_installazione_e_un_aggiornamento() {
        // I due programmi viaggiano insieme e vanno tenuti allineati. Se
        // aggiornare richiedesse un nuovo consenso, l'utente lo darebbe a
        // ogni versione: gli e' stato promesso UNA volta sola.
        let d = temporanea("aggiornamento");
        let percorso = d.join("pairing.json");
        let primo =
            prepare_pairing(SID, CHIAVE, CHIAVE_SERVER, URL_SERVER, &percorso, 1_000).unwrap();
        primo.save(&percorso).unwrap();

        let secondo =
            prepare_pairing(SID, CHIAVE, CHIAVE_SERVER, URL_SERVER, &percorso, 9_999).unwrap();
        // La data del consenso e' quella originale: e' quando l'utente ha
        // detto di si', e non lo si riscrive perche' e' passato del tempo.
        assert_eq!(secondo.consented_at, 1_000);
        assert_eq!(secondo.owner_sid, SID);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn unaltra_installazione_non_subentra_aggiornando() {
        // Il rovescio, ed e' il motivo per cui il controllo non e' solo sul
        // SID: stesso utente, ma un'altra installazione Metnos. Aggiornare
        // non e' una porta per subentrare a un consenso dato a qualcun altro.
        let d = temporanea("subentro");
        let percorso = d.join("pairing.json");
        prepare_pairing(SID, CHIAVE, CHIAVE_SERVER, URL_SERVER, &percorso, 1_000)
            .unwrap()
            .save(&percorso)
            .unwrap();

        let altra_chiave = "b".repeat(64);
        assert_eq!(
            prepare_pairing(
                SID,
                &altra_chiave,
                CHIAVE_SERVER,
                URL_SERVER,
                &percorso,
                2_000
            ),
            Err(SetupRefusal::AlreadyPaired)
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn un_sid_malformato_non_diventa_proprietario() {
        let d = temporanea("sid-rotto");
        for cattivo in ["", "amministratore", "S-1-5-abc", r"S-1-5-18\..\x"] {
            assert_eq!(
                prepare_pairing(
                    cattivo,
                    CHIAVE,
                    CHIAVE_SERVER,
                    URL_SERVER,
                    &d.join("pairing.json"),
                    1
                ),
                Err(SetupRefusal::MalformedOwnerSid)
            );
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn una_chiave_malformata_non_diventa_lautorita() {
        let d = temporanea("chiave-rotta");
        for cattiva in [
            "",
            "zz",
            &"aa".repeat(31),
            &"aa".repeat(33),
            &"g".repeat(64),
        ] {
            assert_eq!(
                prepare_pairing(
                    SID,
                    cattiva,
                    CHIAVE_SERVER,
                    URL_SERVER,
                    &d.join("pairing.json"),
                    1
                ),
                Err(SetupRefusal::MalformedPublicKey)
            );
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    // ── Il testo del consenso ──
    #[test]
    fn il_consenso_dice_le_quattro_cose_che_servono_a_decidere() {
        let testo = consent_text("Roberto");
        // Che cosa concedo, a chi, per quanto, e come lo tolgo.
        assert!(testo.contains("TUTTI gli utenti"));
        assert!(testo.contains("Roberto"));
        assert!(testo.contains("una volta sola"));
        assert!(testo.to_lowercase().contains("impostazioni"));
        // E il confine: non e' un permesso generico.
        assert!(testo.contains("tre cose"));
        assert!(testo.contains("Non accetta comandi"));
    }

    // ── La registrazione del servizio ──
    #[test]
    fn il_percorso_del_servizio_regge_gli_spazi() {
        // «C:\Program Files\Metnos\...» senza virgolette diventerebbe due
        // argomenti, e il servizio punterebbe a «C:\Program». Il valore e'
        // l'argomento SUBITO DOPO `binPath=`, non attaccato ad esso.
        let argv = service_create_argv(Path::new(r"C:\Program Files\Metnos\helper.exe"));
        let i = argv
            .iter()
            .position(|a| a == "binPath=")
            .expect("manca binPath=");
        assert!(
            argv[i + 1].contains(r#""C:\Program Files\Metnos\helper.exe""#),
            "valore inatteso: {}",
            argv[i + 1]
        );
    }

    #[test]
    fn il_servizio_parte_da_solo() {
        // Un aiutante da avviare a mano fallirebbe alla prima installazione
        // dopo un riavvio, senza che nessuno capisca perche'.
        let argv = service_create_argv(Path::new(r"C:\x\helper.exe"));
        let i = argv
            .iter()
            .position(|a| a == "start=")
            .expect("manca start=");
        assert_eq!(argv[i + 1], "auto");
        assert!(argv.iter().any(|a| a == "LocalSystem"));
    }

    // ── La rimozione ──
    #[test]
    fn laiutante_compare_fra_i_programmi_installati_con_il_modo_di_toglierlo() {
        // Apparire senza potersi disinstallare e' peggio che non apparire:
        // sembra rimovibile e non lo e'.
        let voci = arp_entries(Path::new(r"C:\x\helper.exe"), "0.1.0");
        let mappa: std::collections::HashMap<_, _> = voci.into_iter().collect();
        assert!(mappa.contains_key("DisplayName"));
        let disinstalla = mappa.get("UninstallString").unwrap();
        assert!(disinstalla.contains("uninstall"));
        assert!(disinstalla.contains(r#""C:\x\helper.exe""#));
    }

    #[test]
    fn la_rimozione_porta_via_il_consenso() {
        // Lasciarlo significherebbe che una reinstallazione riprende un
        // consenso che il proprietario aveva revocato togliendo il programma.
        let da_togliere = files_to_remove(Path::new("/dati"));
        let nomi: Vec<String> = da_togliere
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().to_string())
            .collect();
        assert!(nomi.contains(&"pairing.json".to_string()));
        assert!(nomi.contains(&"consumed.log".to_string()));
    }

    #[test]
    fn la_rimozione_non_porta_via_il_registro() {
        // Chi puo' disinstallare non deve poter far sparire le proprie
        // tracce: un registro cancellabile non e' un registro.
        let da_togliere = files_to_remove(Path::new("/dati"));
        assert!(!da_togliere.iter().any(|p| p.ends_with("audit.log")));
    }

    #[test]
    fn la_rimozione_ferma_il_servizio_prima_di_cancellarlo() {
        let passi = service_delete_argv();
        assert_eq!(passi[0][1], "stop");
        assert_eq!(passi[1][1], "delete");
    }

    // ── Dove si installa ──
    #[test]
    fn si_installa_dove_un_utente_normale_non_puo_riscrivere() {
        // Se chiunque potesse sostituire il binario che gira come sistema,
        // tutto il resto di questo progetto non conterebbe niente.
        //
        // Il confronto e' sui COMPONENTI, non sulla stringa: il separatore
        // dipende dalla piattaforma su cui gira la prova, e su Linux
        // `PathBuf::join` scrive `/` anche partendo da un percorso Windows.
        // Verificare la stringa misurerebbe il sistema del collaudo invece
        // del comportamento.
        std::env::set_var("ProgramFiles", r"C:\Program Files");
        let dir = install_dir();
        std::env::remove_var("ProgramFiles");

        let testo = dir.to_string_lossy().replace('\\', "/");
        assert!(testo.starts_with("C:/Program Files"), "{testo}");
        assert!(testo.ends_with("/Metnos"), "{testo}");
    }

    #[test]
    fn senza_program_files_non_si_installa_in_una_cartella_di_sistema() {
        // Fuori Windows serve solo alle prove: nessun percorso privilegiato
        // inventato per far girare qualcosa.
        std::env::remove_var("ProgramFiles");
        let dir = install_dir();
        let testo = dir.to_string_lossy().replace('\\', "/");
        assert!(!testo.starts_with("/usr"), "{testo}");
        assert!(!testo.starts_with("/opt"), "{testo}");
    }
}

#[cfg(test)]
mod tests_argomenti_sc {
    use super::*;

    /// Come `std::process::Command` rende una lista di argomenti sulla riga di
    /// comando di Windows: quota chi contiene spazi o virgolette, e le
    /// virgolette interne le fa precedere da una barra rovescia.
    fn riga_di_comando(argv: &[String]) -> String {
        argv.iter()
            .map(|a| {
                if a.contains(' ') || a.contains('"') {
                    format!("\"{}\"", a.replace('"', "\\\""))
                } else {
                    a.clone()
                }
            })
            .collect::<Vec<_>>()
            .join(" ")
    }

    #[test]
    fn chiave_e_valore_sono_due_argomenti_separati() {
        // `sc.exe` si legge la riga di comando per conto suo: un
        // `start= auto` passato come UNA parola gli arriva richiuso fra
        // virgolette e risponde «campo start= non valido». Misurato sul PC il
        // 19/8/2026; l'aiutante finiva in Program Files e si fermava li'.
        let argv = service_create_argv(Path::new(r"C:\Program Files\Metnos\metnos-helper.exe"));
        for chiave in ["binPath=", "start=", "obj=", "DisplayName="] {
            assert!(
                argv.iter().any(|a| a == chiave),
                "«{chiave}» non e' un argomento a se': {argv:?}"
            );
            assert!(
                !argv
                    .iter()
                    .any(|a| a.starts_with(chiave) && a.len() > chiave.len()),
                "«{chiave}» ha il valore attaccato: {argv:?}"
            );
        }
    }

    #[test]
    fn il_percorso_con_spazi_resta_una_cosa_sola() {
        // «Program Files» ha uno spazio: senza virgolette interne il servizio
        // verrebbe registrato su un percorso troncato.
        let argv = service_create_argv(Path::new(r"C:\Program Files\Metnos\metnos-helper.exe"));
        let riga = riga_di_comando(&argv);
        assert!(
            riga.contains(r#"binPath= "\"C:\Program Files\Metnos\metnos-helper.exe\" service""#),
            "riga di comando inattesa: {riga}"
        );
    }

    #[test]
    fn anche_la_politica_di_riavvio_separa_chiave_e_valore() {
        let argv = service_recovery_argv();
        for chiave in ["reset=", "actions="] {
            assert!(
                argv.iter().any(|a| a == chiave),
                "«{chiave}» attaccata: {argv:?}"
            );
        }
    }
}

#[cfg(test)]
mod tests_ciclo_di_vita_servizio {
    use super::*;

    #[test]
    fn si_avvia_adesso_non_al_prossimo_riavvio() {
        // `start= auto` dice a Windows «al prossimo avvio», e chi ha appena
        // installato l'aiutante lo interroga fra due secondi: un servizio
        // registrato e spento e', da fuori, un aiutante che non c'e'.
        let argv = service_start_argv();
        assert_eq!(argv, vec!["sc.exe", "start", SERVICE_NAME]);
    }

    #[test]
    fn si_puo_correggere_un_servizio_gia_esistente() {
        // Installare sopra un'installazione rimasta a meta' deve funzionare.
        // Senza, la macchina resta in un vicolo cieco: il servizio c'e' ma non
        // parte (manca il consenso), quindi l'aiutante non risponde, quindi si
        // propone di installarlo, e l'installazione sbatte contro il servizio
        // di prima. Successo il 19/8/2026, errore 1073.
        let argv = service_config_argv(Path::new(r"C:\Program Files\Metnos\helper.exe"));
        assert_eq!(argv[1], "config");
        let i = argv
            .iter()
            .position(|a| a == "binPath=")
            .expect("manca binPath=");
        assert!(argv[i + 1].contains(r#""C:\Program Files\Metnos\helper.exe""#));
        assert_eq!(SERVICE_EXISTS, 1073);
    }
}
