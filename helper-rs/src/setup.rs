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
pub const ARP_KEY: &str =
    r"HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\MetnosHelper";

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
        // Le virgolette servono: un percorso con spazi (Program Files) senza
        // di esse verrebbe letto come piu' argomenti.
        format!("binPath= \"{}\" service", exe.display()),
        "start= auto".into(),
        "obj= LocalSystem".into(),
        format!("DisplayName= Metnos helper"),
    ]
}

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
    /// C'e' gia' un appaiamento: installare di nuovo cambierebbe il
    /// proprietario in silenzio.
    AlreadyPaired,
}

impl SetupRefusal {
    pub fn code(&self) -> &'static str {
        match self {
            SetupRefusal::MalformedOwnerSid => "malformed_owner_sid",
            SetupRefusal::MalformedPublicKey => "malformed_public_key",
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
    pairing_path: &Path,
    now: u64,
) -> Result<Pairing, SetupRefusal> {
    if !crate::channel::is_valid_sid(owner_sid) {
        return Err(SetupRefusal::MalformedOwnerSid);
    }
    if public_key_hex.len() != 64 || !public_key_hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(SetupRefusal::MalformedPublicKey);
    }
    if Pairing::load(pairing_path).is_some() {
        return Err(SetupRefusal::AlreadyPaired);
    }
    Ok(Pairing {
        owner_sid: owner_sid.to_string(),
        public_key_hex: public_key_hex.to_ascii_lowercase(),
        consented_at: now,
    })
}

/// I file che la rimozione deve cancellare.
///
/// L'appaiamento se ne va con l'aiutante: lasciarlo significherebbe che una
/// reinstallazione riprende un consenso che il proprietario aveva revocato
/// togliendo il programma.
pub fn files_to_remove(data_dir: &Path) -> Vec<PathBuf> {
    vec![
        data_dir.join("pairing.json"),
        data_dir.join("consumed.log"),
    ]
}

/// Il registro NON si cancella con la disinstallazione.
///
/// E' la traccia di cio' che e' stato fatto sulla macchina mentre l'aiutante
/// c'era. Cancellarla insieme al programma darebbe a chiunque possa
/// disinstallare anche il potere di far sparire le proprie tracce, e un
/// registro che si puo' cancellare non e' un registro.
pub fn audit_survives_removal() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporanea(nome: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "metnos-setup-{nome}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    const CHIAVE: &str = "aa00bb11cc22dd33ee44ff5566778899aabbccddeeff00112233445566778899";
    const SID: &str = "S-1-5-21-1-2-3-1001";

    // ── L'appaiamento al consenso ──
    #[test]
    fn un_appaiamento_valido_si_prepara() {
        let d = temporanea("valido");
        let p = prepare_pairing(SID, CHIAVE, &d.join("pairing.json"), 1_786_000_000).unwrap();
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
        prepare_pairing(SID, CHIAVE, &percorso, 1)
            .unwrap()
            .save(&percorso)
            .unwrap();

        assert_eq!(
            prepare_pairing("S-1-5-21-9-9-9-9999", CHIAVE, &percorso, 2),
            Err(SetupRefusal::AlreadyPaired)
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn un_sid_malformato_non_diventa_proprietario() {
        let d = temporanea("sid-rotto");
        for cattivo in ["", "amministratore", "S-1-5-abc", r"S-1-5-18\..\x"] {
            assert_eq!(
                prepare_pairing(cattivo, CHIAVE, &d.join("pairing.json"), 1),
                Err(SetupRefusal::MalformedOwnerSid)
            );
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn una_chiave_malformata_non_diventa_lautorita() {
        let d = temporanea("chiave-rotta");
        for cattiva in ["", "zz", &"aa".repeat(31), &"aa".repeat(33), &"g".repeat(64)] {
            assert_eq!(
                prepare_pairing(SID, cattiva, &d.join("pairing.json"), 1),
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
        // argomenti, e il servizio punterebbe a «C:\Program».
        let argv = service_create_argv(Path::new(r"C:\Program Files\Metnos\helper.exe"));
        let bin = argv.iter().find(|a| a.starts_with("binPath=")).unwrap();
        assert!(bin.contains(r#""C:\Program Files\Metnos\helper.exe""#));
    }

    #[test]
    fn il_servizio_parte_da_solo() {
        // Un aiutante da avviare a mano fallirebbe alla prima installazione
        // dopo un riavvio, senza che nessuno capisca perche'.
        let argv = service_create_argv(Path::new(r"C:\x\helper.exe"));
        assert!(argv.iter().any(|a| a.contains("start= auto")));
        assert!(argv.iter().any(|a| a.contains("LocalSystem")));
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
        assert!(audit_survives_removal());
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
