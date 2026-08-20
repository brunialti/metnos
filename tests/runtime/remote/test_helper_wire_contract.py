"""Il corpo della firma deve combaciare fra client e aiutante (17/8/2026).

Sono due programmi separati per scelta di sicurezza: l'aiutante gira con i
privilegi di sistema e non deve linkare codice del client. Il prezzo di quella
separazione e' che il formato su cui si calcola la firma e' scritto DUE volte,
in due linguaggi che non si parlano.

Se una delle due stringhe cambia, la firma smette di verificare e ogni
installazione fallisce con «firma non attendibile» — un sintomo che non
somiglia per niente alla causa. Questa guardia lo dice subito, e in italiano.

Le due stringhe attese vivono nei rispettivi test Rust, scritte per esteso.
Qui si confrontano fra loro: e' l'unico posto che vede entrambi i progetti.

Run: `python3 -m pytest tests/runtime/remote/test_helper_wire_contract.py -v`
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AIUTANTE = ROOT / "helper-rs" / "src" / "protocol.rs"
CLIENT = ROOT / "client-rs" / "src" / "helper_client.rs"
CANALE = ROOT / "helper-rs" / "src" / "channel.rs"
INSTALLAZIONE = ROOT / "helper-rs" / "src" / "win_setup.rs"
ATTIVAZIONE = ROOT / "helper-rs" / "src" / "win_activation.rs"
PROVIDER_WINDOWS = ROOT / "helper-rs" / "src" / "win_provider.rs"
DELIMITAZIONE = (ROOT / "helper-rs" / "src" / "frame.rs",
                 ROOT / "client-rs" / "src" / "frame.rs")

# Il corpo canonico di una richiesta d'esempio. I campi sono separati da un
# carattere di controllo che non puo' comparire in un identificativo: nel
# sorgente Rust e' scritto come sequenza di escape, ed e' cosi' che lo
# cerchiamo — leggendo il testo del file, non eseguendolo.
_ATTESA = re.compile(r'"(install\\u\{1f\}winget\\u\{1f\}[^"]+)"')
_START_ATTESO = re.compile(
    r'"(managed-start\\u\{1f\}winget\\u\{1f\}LibreHardwareMonitor'
    r'\.LibreHardwareMonitor\\u\{1f\}session\\u\{1f\}[^"]+)"')
_PROVIDER_GRANT_TEMPLATE = re.compile(
    r'"(managed-provider-grant(?:\\u\{1f\}\{\}){10})"')
_PROVIDER_REQUEST_TEMPLATE = re.compile(
    r'"(managed-provider(?:\\u\{1f\}\{\}){3})"')


def _corpo_atteso(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    trovate = _ATTESA.findall(testo)
    assert trovate, f"nessun corpo canonico atteso in {percorso.name}"
    assert len(trovate) == 1, (
        f"{percorso.name} dichiara {len(trovate)} corpi attesi: "
        "il contratto deve avere una sola fonte per progetto")
    return trovate[0]


def test_i_due_progetti_esistono():
    """Una guardia che non trova i file passerebbe senza verificare niente."""
    assert AIUTANTE.is_file(), AIUTANTE
    assert CLIENT.is_file(), CLIENT


def test_il_corpo_della_firma_combacia():
    """Il vincolo: due programmi separati, un solo formato.

    Se questo test diventa rosso, NON allineare la stringa a caso: decidere
    quale dei due e' cambiato per errore, perche' una firma calcolata su un
    formato e verificata su un altro non e' un dettaglio di scrittura."""
    assert _corpo_atteso(AIUTANTE) == _corpo_atteso(CLIENT)


def test_i_campi_sono_separati_da_un_carattere_impossibile_nei_valori():
    """Senza un separatore impossibile nei campi, due richieste diverse
    potrebbero produrre lo stesso corpo spostando un confine — e una firma
    varrebbe per un'operazione che nessuno ha approvato."""
    corpo = _corpo_atteso(AIUTANTE)
    assert corpo.count("u{1f}") == 4, "servono quattro separatori, cinque campi"


def test_il_corpo_managed_start_combacia_senza_aggiungere_un_verbo():
    """The dedicated request is signed identically at both ends while the
    package-operation enumeration remains unchanged."""
    bodies = []
    for path in (AIUTANTE, CLIENT):
        found = _START_ATTESO.findall(path.read_text(encoding="utf-8"))
        assert len(found) == 1, (path, found)
        bodies.append(found[0])
    assert bodies[0] == bodies[1]
    assert bodies[0].count("u{1f}") == 4


@pytest.mark.parametrize(
    "pattern", (_PROVIDER_GRANT_TEMPLATE, _PROVIDER_REQUEST_TEMPLATE))
def test_i_corpi_del_provider_combacciano_e_non_duplicano_campi(pattern):
    """Server grant and client request use one minimal byte contract."""
    bodies = []
    for path in (AIUTANTE, CLIENT):
        found = pattern.findall(path.read_text(encoding="utf-8"))
        assert len(found) == 1, (path, found)
        bodies.append(found[0])
    assert bodies[0] == bodies[1]


def test_il_provider_accetta_un_profilo_dati_non_codice_libero():
    """The privileged provider accepts a signed standard profile only."""
    for path in (AIUTANTE, CLIENT):
        assert "hardware_sensors_v1" in path.read_text(encoding="utf-8")
    helper_shape = re.search(
        r"pub struct ManagedProviderRequest \{(.*?)\n\}",
        AIUTANTE.read_text(encoding="utf-8"),
        re.S,
    )
    assert helper_shape
    fields = set(re.findall(r"pub (\w+):", helper_shape.group(1)))
    assert fields == {
        "source", "package_id", "interface", "invocation_id",
        "manifest_sha256", "dependency_key", "grant_signature",
        "assembly", "entry_type", "domains", "sensor_types",
        "idempotency_key", "signature",
    }
    assert fields.isdisjoint({"path", "command", "args", "method", "type_name"})


def test_hardware_provider_initialises_once_and_keeps_partial_sensor_data():
    """Initialisation is single-shot and traversal preserves healthy rows."""
    source = PROVIDER_WINDOWS.read_text(encoding="utf-8")
    assert source.count("$computer.Open()") == 1
    assert "$attempt" not in source
    assert "$script:probeFailed = $true" in source
    assert "if ($rows.Count -eq 0 -and $script:probeFailed) { exit 22 }" in source
    for code in (
        "provider_assembly_load_failed",
        "provider_open_failed",
        "provider_read_failed",
        "provider_instance_failed",
        "provider_configuration_failed",
    ):
        assert code in source


def test_provider_loads_a_validated_winget_asset_without_removing_its_web_mark():
    """A downloaded DLL can retain Mark-of-the-Web after WinGet extracts it.

    The helper may bypass that loader check only after Rust has resolved the
    signed assembly name to a canonical direct child of the registered package
    root.  It must not mutate the package with Unblock-File or an ADS write.
    """
    source = PROVIDER_WINDOWS.read_text(encoding="utf-8")
    assert "Assembly]::UnsafeLoadFrom" in source
    assert "Assembly]::LoadFrom" not in source
    assert "Unblock-File" not in source
    assert "Zone.Identifier" not in source
    assert "canonical_direct_child(package_id, assembly)" in source


def test_provider_respells_only_the_validated_local_path_for_dotnet():
    """Rust canonical paths are verbatim paths, which .NET Framework rejects."""
    provider = PROVIDER_WINDOWS.read_text(encoding="utf-8")
    activation = ATTIVAZIONE.read_text(encoding="utf-8")
    pure_rules = (ROOT / "helper-rs" / "src" / "activation.rs").read_text(
        encoding="utf-8")
    assert "canonical_direct_child(package_id, assembly)" in provider
    assert "windows_local_interop_path(&path)" in provider
    assert provider.index("canonical_direct_child(package_id, assembly)") < \
        provider.index("windows_local_interop_path(&path)")
    assert r'strip_prefix(r"\\?\")' in pure_rules
    assert "bytes[1] != b':'" in pure_rules
    assert "HKEY_LOCAL_MACHINE" in activation


def test_provider_host_has_no_program_specific_branch():
    """A future compatible package changes profile data, not helper code."""
    source = PROVIDER_WINDOWS.read_text(encoding="utf-8")
    assert "LibreHardwareMonitor" not in source
    assert "package_id ==" not in source
    assert "match package_id" not in source
    assert "METNOS_PROVIDER_ASSEMBLY" in source
    assert "METNOS_PROVIDER_ENTRY_TYPE" in source
    for fixed_property in (
            "IsCpuEnabled", "IsGpuEnabled", "IsStorageEnabled",
            "IsMotherboardEnabled"):
        assert fixed_property in source
    assert "foreach ($domain in $requestedDomains)" in source
    assert "METNOS_PROVIDER_DOMAINS" in source
    assert "METNOS_PROVIDER_SENSOR_TYPES" in source
    assert "$attempt" not in source


def _operazioni_dichiarate(percorso: Path) -> tuple:
    """I nomi delle operazioni, presi dalla DICHIARAZIONE dell'enumerazione.

    Non da una ricerca su tutto il file: `protocol.rs` contiene un test che
    manda `"exec"` per dimostrare che viene rifiutato, e una ricerca ingenua
    lo scambierebbe per un quarto verbo. Un test che sbaglia bersaglio e'
    peggio di nessun test, perche' fa perdere tempo su un problema che non
    c'e'.
    """
    testo = percorso.read_text(encoding="utf-8")
    corpo = re.search(r"enum Operation \{(.*?)\n\}", testo, re.S)
    assert corpo, f"{percorso.name}: nessuna enumerazione Operation"
    return tuple(re.findall(r"^\s{4}(\w+),", corpo.group(1), re.M))


def test_i_due_progetti_conoscono_le_stesse_operazioni():
    """Un verbo in piu' da una parte sola sarebbe un'operazione che nessuno
    esegue, o peggio una che nessuno si aspetta.

    L'elenco e' scritto per esteso di proposito: e' il vocabolario CHIUSO del
    componente piu' privilegiato che Metnos installa, e allungarlo deve
    costare una modifica deliberata a questa riga. `Version` e' entrata il
    18/8/2026 e non tocca niente: serve a poter CHIEDERE se i due programmi
    sono allineati, invece di scoprirlo come un guasto.
    """
    assert _operazioni_dichiarate(AIUTANTE) == (
        "Query", "Install", "Uninstall", "Version")
    assert _operazioni_dichiarate(CLIENT) == _operazioni_dichiarate(AIUTANTE)


def test_nessuna_operazione_significa_esegui():
    """Il confine dell'intero disegno: nessuna operazione e' «esegui
    questo». Tre riguardano un pacchetto, la quarta dice solo chi si e'."""
    for percorso in (AIUTANTE, CLIENT):
        for vietata in ("Exec", "Run", "Shell", "Command"):
            assert vietata not in _operazioni_dichiarate(percorso), (
                f"{percorso.name} dichiara l'operazione «{vietata}»")


def test_managed_start_uses_the_official_winget_portable_target_value():
    """The helper resolves the registered portable target without guessing.

    WinGet writes ``PortableTargetFullPath`` for a portable package. The
    shorter look-alike name is not a registered value and would make every
    legitimate start fail closed as if its executable were missing.
    """
    source = ATTIVAZIONE.read_text(encoding="utf-8")
    queried_values = re.findall(r'string_value\(key\.0, "([^"]+)"\)', source)
    assert "PortableTargetFullPath" in queried_values
    assert "TargetFullPath" not in queried_values


def test_la_delimitazione_dei_messaggi_e_la_stessa_parola_per_parola():
    """Dove finisce un messaggio lo devono sapere allo stesso modo.

    Il canale e' bidirezionale e nessuno dei due capi lo chiude: se uno dei
    due leggesse fino a fine-flusso, aspetterebbe l'altro che sta aspettando
    lui. Il sintomo sarebbe un blocco senza messaggi — il piu' difficile da
    ricondurre alla causa. Qui i due file si confrontano per intero, byte per
    byte: una differenza di un carattere basta a produrlo.
    """
    aiutante, client = DELIMITAZIONE
    assert aiutante.is_file(), aiutante
    assert client.is_file(), client
    assert aiutante.read_bytes() == client.read_bytes(), (
        "helper-rs/src/frame.rs e client-rs/src/frame.rs sono diversi: "
        "vanno tenuti identici (copiare l'uno sull'altro)")


def test_la_delimitazione_non_e_il_fine_flusso():
    """Una guardia sul MODO, non solo sul contenuto.

    Rendere di nuovo identici i due file dopo averli rotti entrambi allo
    stesso modo passerebbe il confronto qui sopra. Questa dice che il
    meccanismo e' un delimitatore esplicito, che e' la ragione per cui i due
    file esistono.
    """
    testo = DELIMITAZIONE[0].read_text(encoding="utf-8")
    assert "pub const TERMINATOR" in testo
    assert "read_to_end" not in testo.split("mod tests")[0], (
        "leggere fino a fine-flusso e' esattamente il blocco che questo "
        "modulo esiste per evitare")


# ── Dove ci si trova: il nome del canale e il programma installato ────────
#
# Il client deve ARRIVARE all'aiutante prima ancora di poterlo giudicare. Il
# nome della pipe e il percorso dell'eseguibile sono scritti due volte, come
# il corpo della firma, e per la stessa ragione.

_NOME_PIPE = re.compile(r'r"(\\\\\.\\pipe\\metnos-helper-[^"]+)"')


def _nome_pipe_atteso(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    trovati = sorted(set(_NOME_PIPE.findall(testo)))
    assert trovati, f"nessun nome di pipe scritto per esteso in {percorso.name}"
    assert len(trovati) == 1, (
        f"{percorso.name} dichiara {len(trovati)} nomi attesi: "
        "il contratto deve avere una sola fonte per progetto")
    return trovati[0]


def test_il_nome_del_canale_combacia():
    """Il client apre il nome che l'aiutante crea, o non lo trova.

    Una divergenza qui non somiglia a un errore di scrittura: il client
    riferisce «l'aiutante non e' installato» mentre l'aiutante e' installato,
    in ascolto, e sta aspettando. Chi indaga va a guardare il servizio, che
    sta benissimo.
    """
    assert _nome_pipe_atteso(CANALE) == _nome_pipe_atteso(CLIENT)


def test_il_nome_del_canale_porta_il_sid_di_chi_lo_possiede():
    """Senza il SID nel nome, due utenti della stessa macchina si
    troverebbero sullo stesso canale — e il consenso di uno varrebbe per
    l'altro."""
    assert re.search(r"-S-\d+(-\d+)+$", _nome_pipe_atteso(CANALE))


def test_i_due_progetti_nominano_lo_stesso_programma_installato():
    """Il client rifiuta chi non e' l'eseguibile installato: deve sapere
    quale, e saperlo allo stesso modo di chi lo installa.

    Piu' leggera delle altre di proposito. Una divergenza qui si annuncia da
    sola — «il programma all'altro capo non e' quello installato», col
    percorso trovato scritto dentro — mentre le altre due si presentano
    travestite da qualcos'altro.
    """
    installa = INSTALLAZIONE.read_text(encoding="utf-8")
    decide = (ROOT / "helper-rs" / "src" / "setup.rs").read_text(encoding="utf-8")
    cerca = CLIENT.read_text(encoding="utf-8")
    for pezzo, dove in (('"metnos-helper.exe"', installa), ('"Metnos"', decide)):
        assert pezzo in dove, f"l'installazione non nomina piu' {pezzo}"
    assert '"metnos-helper.exe"' in cerca, "il client non nomina l'eseguibile atteso"
    assert "Metnos" in cerca, "il client non nomina la cartella d'installazione"


def test_la_chiave_che_il_client_consegna_e_quella_che_l_aiutante_accetta():
    """La forma della chiave pubblica e' un contratto fra due programmi.

    L'aiutante conserva la chiave come 64 cifre esadecimali e rifiuta tutto
    il resto. Il client gliela passa al momento dell'installazione. Una
    divergenza qui non si vede subito: si vede DOPO che la persona ha gia'
    risposto alla finestra di conferma di Windows, che e' il momento peggiore
    per scoprire un errore di formato — e succede una volta sola, quindi
    nessuno la incontra due volte abbastanza da capirla.

    Trovato il 18/8/2026: il client passava base64, l'aiutante voleva
    esadecimale.
    """
    decide = (ROOT / "helper-rs" / "src" / "setup.rs").read_text(encoding="utf-8")
    # La regola dell'aiutante, letta da dove vive.
    assert "public_key_hex.len() != 64" in decide, (
        "l'aiutante non chiede piu' 64 cifre: il contratto e' cambiato")
    assert "is_ascii_hexdigit" in decide

    # Il client deve produrre ESATTAMENTE quella forma, e da un posto solo.
    identita = (ROOT / "client-rs" / "src" / "identity.rs").read_text(encoding="utf-8")
    assert "pub fn fingerprint(&self) -> String" in identita
    # Una seconda rappresentazione della NOSTRA chiave e' cio' che ha causato
    # lo scambio: se torna, questa prova deve accorgersene. La chiave del
    # SERVER e' un'altra cosa e vive davvero in base64 (`verify_b64`): il
    # controllo guarda i produttori, non ogni occorrenza della parola.
    assert "fn public_key_b64" not in identita, (
        "e' tornata una seconda forma della chiave pubblica del device")

    principale = (ROOT / "client-rs" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "&id.fingerprint()" in principale, (
        "il client non consegna piu' la chiave in esadecimale")

    # E il controllo preventivo, che rifiuta la forma sbagliata PRIMA della
    # finestra di Windows invece che dopo.
    setup = (ROOT / "client-rs" / "src" / "helper_setup.rs").read_text(encoding="utf-8")
    assert "public_key_hex.len() != 64" in setup


def test_i_due_progetti_parlano_la_stessa_versione_di_protocollo():
    """La lingua del canale e' scritta in due file che non si vedono.

    E' il numero che decide se i due programmi si capiscono. Se divergesse,
    ogni scambio verrebbe dichiarato disallineato — o peggio, allineato
    quando non lo e' — e la diagnosi manderebbe a riallineare due programmi
    che erano gia' allineati.
    """
    import re as _re
    numeri = {}
    for nome, percorso in (
        ("aiutante", ROOT / "helper-rs" / "src" / "protocol.rs"),
        ("client", ROOT / "client-rs" / "src" / "helper_client.rs"),
    ):
        testo = percorso.read_text(encoding="utf-8")
        trovato = _re.search(r"pub const PROTOCOL_VERSION: u32 = (\d+);", testo)
        assert trovato, f"{nome} non dichiara piu' la versione di protocollo"
        numeri[nome] = trovato.group(1)
    assert numeri["aiutante"] == numeri["client"], (
        f"la lingua del canale e' divergente: {numeri}")


def test_la_forma_firmata_dell_aggiornamento_e_la_stessa_in_tre_linguaggi():
    """Il server firma, il client verifica, l'aiutante riverifica.

    Tre programmi in tre linguaggi devono produrre gli STESSI byte, o la
    firma non regge da nessuna parte. Qui si confronta quello che il server
    produce davvero con la stringa scritta per esteso nella prova
    dell'aiutante: non due sorgenti fra loro, ma il fatto contro la
    dichiarazione.
    """
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "runtime"))
    import invocations

    prodotta = invocations.canonical_bytes(
        {"component": "helper", "sha256": "abc", "target": "t",
         "version": "1.2.3"}).decode("utf-8")
    assert prodotta == (
        '{"component":"helper","sha256":"abc","target":"t","version":"1.2.3"}')

    # E la stessa stringa deve essere quella che l'aiutante si aspetta.
    aiutante = (ROOT / "helper-rs" / "src" / "selfupdate.rs").read_text(encoding="utf-8")
    assert prodotta in aiutante, (
        "la forma canonica dell'aiutante non e' quella che il server produce: "
        "le firme degli aggiornamenti non verificherebbero")


def test_ogni_pezzo_si_aggiorna_da_se():
    """Una regola sola per tutti i pezzi, e nessuno che dipenda da un altro.

    Il client chiede al server per se'; l'aiutante chiede al server per se'.
    Se fosse il client a portare l'aggiornamento all'aiutante, un client
    fermo o vecchio lascerebbe l'aiutante indietro per sempre e in silenzio —
    proprio il guasto che questo meccanismo esiste per evitare.

    E c'e' un confine che non si puo' attraversare: il client gira senza
    privilegi, l'aiutante come sistema. Un programma senza privilegi che
    sostituisce un binario di sistema sarebbe la chiave della macchina.
    """
    def codice(percorso):
        """Il sorgente senza i commenti: si guarda cosa fa, non cosa spiega."""
        testo = percorso.read_text(encoding="utf-8")
        return "\n".join(r for r in testo.splitlines()
                         if not r.lstrip().startswith(("//", "///", "//!")))

    client = codice(ROOT / "client-rs" / "src" / "helper_setup.rs")
    # Il client porta l'aiutante la prima volta, e li' finisce il suo ruolo.
    assert "pub fn install_elevated" in client
    for vietato in ("stage_for_helper", "sostituisci_eseguibile", "Program Files"):
        assert vietato not in client, (
            f"il client tocca «{vietato}»: aggiornare l'aiutante non e' compito suo")

    # L'aiutante chiede al server per conto proprio, e sostituisce se stesso.
    aggiorna = codice(ROOT / "helper-rs" / "src" / "selfupdate.rs")
    assert "pub fn fetch_descriptor" in aggiorna
    assert "pub fn check_and_apply" in aggiorna
    assert "pub fn sostituisci_eseguibile" in codice(
        ROOT / "helper-rs" / "src" / "win_setup.rs")


def test_l_aiutante_parla_il_protocollo_dei_servizi():
    """Un programma registrato come servizio deve presentarsi al gestore.

    Il gestore lo lancia e aspetta che sia LUI a farsi vivo: chi non lo fa
    viene dichiarato caduto dopo trenta secondi (errore 1053) e non parte
    mai. E' il difetto per cui l'aiutante non ha mai potuto rispondere a
    nessuno — trovato sulla macchina vera il 19/8/2026, dopo che tutto il
    resto era gia' corretto e inutile.

    Le tre chiamate sono la sequenza obbligata: chi tiene il filo, chi riceve
    i comandi, e il «eccomi» senza il quale non esiste servizio.
    """
    servizio = ROOT / "helper-rs" / "src" / "win_service.rs"
    assert servizio.is_file(), "il modulo del protocollo dei servizi non c'e' piu'"
    testo = servizio.read_text(encoding="utf-8")
    for chiamata in ("StartServiceCtrlDispatcherW",
                     "RegisterServiceCtrlHandlerW",
                     "SetServiceStatus"):
        assert chiamata in testo, f"manca {chiamata}: il servizio non partira'"
    # Lo stato «in esecuzione» e' il «eccomi»; quello «fermato» evita che un
    # arresto voluto venga scambiato per una caduta e faccia scattare la
    # politica di riavvio.
    assert "SERVICE_RUNNING" in testo
    assert "SERVICE_STOPPED" in testo

    # E il comando `serve` deve passare di li', non girare il ciclo a mano.
    principale = (ROOT / "helper-rs" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "win_service::esegui_come_servizio()" in principale, (
        "`serve` non passa piu' dal gestore dei servizi")


def test_una_richiesta_apre_una_sola_connessione():
    """Il canale serve un client alla volta: le connessioni si contano.

    Il comando `check` ne apriva DUE: una per guardare chi c'era dall'altro
    capo, chiusa subito senza mandare niente, e una per la richiesta vera. Ma
    l'aiutante crea un'istanza della pipe alla volta: la sonda se la bruciava
    e la richiesta vera arrivava nel buco. Il client riferiva «l'aiutante non
    risponde» mentre l'aiutante era li', in ascolto, e nel suo registro
    restavano due righe «messaggio senza delimitatore: l'altro capo ha chiuso
    subito» (macchina di Roberto, 19/8/2026).

    Il controllo su CHI c'e' non si e' perso: `chiedi` giudica l'altro capo
    prima di scrivere una sola parola. Era la sonda a essere di troppo.
    """
    win = (ROOT / "client-rs" / "src" / "helper_win.rs").read_text(encoding="utf-8")
    assert "pub fn chiedi" in win
    assert "judge_peer" in win, "il giudizio su chi risponde e' sparito"
    assert "pub fn presente" not in win, (
        "e' tornata la sonda che apre una connessione a vuoto")

    principale = (ROOT / "client-rs" / "src" / "main.rs").read_text(encoding="utf-8")
    codice = "\n".join(r for r in principale.splitlines()
                       if not r.lstrip().startswith("//"))
    assert "helper_win::presente" not in codice, (
        "qualcuno apre di nuovo una connessione prima della richiesta")


def test_chi_c_e_dall_altro_capo_si_chiede_all_oggetto_non_al_processo():
    """Il controllo dev'essere una domanda che si PUO' fare.

    Prima il client apriva il processo che serve la pipe e ne leggeva il token
    per ricavarne l'identita'. Non puo' funzionare: il client gira senza
    privilegi e il servizio gira come sistema, e Windows non lascia a un
    processo utente aprire il token di un processo di SYSTEM. Il controllo
    falliva SEMPRE, su qualunque macchina — e falliva prima di scrivere una
    parola, quindi la connessione si chiudeva a vuoto e l'aiutante registrava
    «l'altro capo ha chiuso subito» senza che nessuno sapesse perche'
    (19/8/2026: mai riconosciuto, nemmeno installato, avviato e in ascolto).

    Il proprietario dell'OGGETTO da' la stessa garanzia — un oggetto di
    proprieta' del sistema lo puo' creare solo il sistema — e si puo' leggere
    con i diritti che un client ha gia'.
    """
    win = (ROOT / "client-rs" / "src" / "helper_win.rs").read_text(encoding="utf-8")
    assert "fn proprietario_della_pipe" in win
    assert "OWNER_SECURITY_INFORMATION" in win

    codice = "\n".join(r for r in win.splitlines()
                       if not r.lstrip().startswith("//"))
    # L'identita' NON deve piu' venire dal token del processo.
    i_prop = codice.find("proprietario_della_pipe(pipe.0)")
    assert i_prop != -1, "il giudizio non usa piu' il proprietario dell'oggetto"
    assert "sid_del_processo(processo.0)" not in codice, (
        "e' tornata la lettura del token del processo, che da qui non si puo' fare")
