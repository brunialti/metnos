"""`install_packages`: due fasi, e la prima non installa (ADR 0209 parte C).

Gli otto controlli obbligatori della spec §8 vivono qui. Il primo — la fase 1
non installa — e' l'invariante di sicurezza dell'intero disegno: se cadesse,
tutto il resto (carta, impronta, consenso) sarebbe decorazione su
un'installazione gia' avvenuta.

Run: `python3 -m pytest tests/runtime/executors/test_install_packages.py -v`
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executors.install_packages import install_packages  # noqa: E402

MANIFEST = ROOT / "executors" / "install_packages" / "manifest.toml"

# Uscita reale di `winget show --id Microsoft.PowerToys` su PC-ROBERTO,
# 17/8/2026. Etichette in italiano di proposito: sono localizzate, e il
# lettore deve funzionare sulla STRUTTURA, non sulle parole.
_WINGET_SHOW = """Trovato PowerToys [Microsoft.PowerToys]
Versione: 0.100.2
Editore: Microsoft Corporation
URL editore: https://github.com/microsoft/PowerToys
Autore: Microsoft Corporation
Licenza: MIT
URL licenza: https://github.com/microsoft/PowerToys/blob/master/LICENSE
Programma di installazione:
  Tipo di programma di installazione: burn
  URL del programma di installazione: https://github.com/microsoft/PowerToys/releases/download/v0.100.2/PowerToysUserSetup-0.100.2-x64.exe
  SHA256 del programma di installazione: 945fdf327e4d38e4ced61b0727b7ab8a1222958782982052989ddf7cb7096f62
  Data di rilascio: 2026-06-26
"""

_APT_URIS = ("'http://it.archive.ubuntu.com/ubuntu/pool/universe/r/rust-ripgrep/"
             "ripgrep_14.1.0-1_amd64.deb' ripgrep_14.1.0-1_amd64.deb 1551362 "
             "SHA512:56b8017d07ab1af9af051d1ee33bad113222a70b12bbcd929113ef"
             "388f39902671b2020f3a1dc7f11f8e9313afa2f3d70b0f8bc648d5942c283"
             "737990d42af47")


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture
def windows(monkeypatch):
    """Contesto Windows con winget, e ogni comando registrato."""
    eseguiti = []

    def finto_run(argv, timeout_s):
        eseguiti.append(argv)
        if "show" in argv:
            return 0, _WINGET_SHOW, ""
        return 0, "", ""

    monkeypatch.setattr(install_packages, "_run", finto_run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})
    return eseguiti


# ── 1. L'invariante: la fase 1 non installa ───────────────────────────
def test_senza_consenso_non_installa_niente(windows) -> None:
    """Nessun comando di installazione parte finche' una persona non ha
    approvato. Verificato sui comandi REALMENTE eseguiti, non sull'esito."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})

    assert res["decision"] == "needs_inputs"
    assert res["installed"] is False
    assert res["ok_count"] == 0
    assert res["results"] == []
    verbi = [a[1] for a in windows if len(a) > 1]
    assert verbi == ["show"], f"la fase 1 ha eseguito {verbi}"
    assert not any("install" in a or "uninstall" in a for a in windows)


def test_la_disinstallazione_non_parte_senza_consenso(windows) -> None:
    """Vale in entrambe le direzioni: rimuovere e' distruttivo quanto
    installare, e chiede lo stesso consenso."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "uninstall": True})
    assert res["decision"] == "needs_inputs"
    assert not any("uninstall" in a for a in windows)


# ── 2. La carta porta indirizzo e impronta ────────────────────────────
def test_la_carta_mostra_indirizzo_e_impronta(windows) -> None:
    """Senza, la persona approva un'ETICHETTA. «Installa PowerToys» e
    «installa QUESTO file, da QUESTO indirizzo, con QUESTA impronta» sono
    due consensi diversi, e solo il secondo si puo' verificare dopo."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    carta = res["needs_inputs"]["description"]

    assert "PowerToysUserSetup-0.100.2-x64.exe" in carta
    assert "945fdf327e4d38e4ced61b0727b7ab8a1222958782982052989ddf7cb7096f62" \
        in carta
    assert "SHA-256" in carta
    assert "0.100.2" in carta


def test_i_dati_della_carta_vengono_dalla_sorgente(windows) -> None:
    """Non sono inventati: si leggono dall'uscita del gestore."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    r = res["resolved"][0]
    assert r["version"] == "0.100.2"
    assert r["publisher"] == "Microsoft Corporation"
    assert r["url"].endswith("PowerToysUserSetup-0.100.2-x64.exe")
    assert r["digest_algo"] == "SHA-256"


def test_le_etichette_localizzate_non_contano(monkeypatch) -> None:
    """Le etichette di winget sono tradotte, i valori no: il lettore usa la
    FORMA del dato (un URL e' un URL, un SHA-256 e' 64 cifre esadecimali)."""
    inglese = (_WINGET_SHOW.replace("Versione:", "Version:")
               .replace("Editore:", "Publisher:")
               .replace("URL del programma di installazione:", "Installer Url:")
               .replace("SHA256 del programma di installazione:",
                        "Installer SHA256:"))
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, inglese, ""))
    r = install_packages._winget_show("Microsoft.PowerToys", "winget")
    assert r["url"].endswith("PowerToysUserSetup-0.100.2-x64.exe")
    assert r["digest"].startswith("945fdf32")


def test_l_algoritmo_dell_impronta_e_dichiarato(monkeypatch) -> None:
    """apt su Ubuntu pubblica SHA-512, winget SHA-256. Un'impronta con
    l'algoritmo sbagliato in etichetta e' peggio di nessuna impronta:
    sembra verificabile e non lo e'."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _APT_URIS, ""))
    r = install_packages._apt_show("ripgrep", "apt-get")
    assert r["digest_algo"] == "SHA512"
    assert r["digest"].startswith("56b8017d")
    assert r["url"].endswith("ripgrep_14.1.0-1_amd64.deb")


def test_un_impronta_assente_viene_dichiarata(monkeypatch) -> None:
    """Il Microsoft Store serve pacchetti che winget non sa firmare.
    «Sconosciuta» e' un fatto che il lettore puo' pesare; il silenzio si
    leggerebbe «controllata, a posto» (§2.8)."""
    senza = _WINGET_SHOW.replace(
        "  SHA256 del programma di installazione: "
        "945fdf327e4d38e4ced61b0727b7ab8a1222958782982052989ddf7cb7096f62\n", "")
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, senza, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    carta = res["needs_inputs"]["description"]
    assert "945fdf" not in carta
    assert "impronta" in carta.lower() or "fingerprint" in carta.lower()


# ── 3. Un'identita' che non si risolve non si installa ────────────────
def test_un_pacchetto_non_risolto_ferma_tutta_la_chiamata(monkeypatch):
    """Installare la meta' che si e' risolta vorrebbe dire fare una cosa
    diversa da quella chiesta."""
    def finto_run(argv, t):
        if "Microsoft.PowerToys" in argv:
            return 0, _WINGET_SHOW, ""
        return 1, "", "nessun pacchetto"

    monkeypatch.setattr(install_packages, "_run", finto_run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})

    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys", "pacchetto-inventato-9f2c"]})
    assert res["ok"] is False
    assert res["error_class"] == "not_found"
    assert "pacchetto-inventato-9f2c" in res["error"]
    assert res.get("decision") != "needs_inputs", "non deve chiedere consenso"


# ── 3.bis Le persone nominano programmi, non identificativi ──────────
# Difetto reale, turno 09f7a5fb (17/8/2026): «installa LibreHardwareMonitor
# su pc-roberto» rispondeva «non trovo questo pacchetto», mentre il catalogo
# ce l'ha come `LibreHardwareMonitor.LibreHardwareMonitor`.

_SEARCH_UNO = (
    "Nome                 Id                                        Versione Origine\n"
    "--------------------------------------------------------------------------------\n"
    "LibreHardwareMonitor LibreHardwareMonitor.LibreHardwareMonitor 0.9.6    winget\n"
)
_SEARCH_MOLTI = (
    "Nome                           Id           Versione Origine\n"
    "-------------------------------------------------------------\n"
    "Python 3.12                    9NCVDN91XZQP Unknown  msstore\n"
    "Python 3.13                    9PNRBTZXMB4Z Unknown  msstore\n"
    "Python Install Manager         9NQ7512CXL7T Unknown  msstore\n"
)


def _finto_winget(monkeypatch, search_out, show_id=None, show_out=None):
    """winget finto ma REALISTICO: `show --id X --exact` risponde solo per
    un identificativo che esiste davvero. Un finto che risponde a qualunque
    id farebbe risolvere anche «python» al primo colpo, e il ramo
    dell'ambiguita' non verrebbe mai esercitato."""
    def run(argv, timeout_s):
        if "search" in argv:
            return 0, search_out, ""
        if "show" in argv and show_id is not None and show_id in argv:
            return 0, show_out or _WINGET_SHOW, ""
        return 1, "", ""
    monkeypatch.setattr(install_packages, "_run", run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})


def test_un_nome_con_un_solo_corrispondente_viene_risolto(monkeypatch):
    """Il caso del difetto: un nome unico si risolve nel suo identificativo,
    e cio' che finisce sulla scheda e' l'identita' vera."""
    _finto_winget(monkeypatch, _SEARCH_UNO,
                  show_id="LibreHardwareMonitor.LibreHardwareMonitor")
    res = install_packages.invoke({"packages": ["LibreHardwareMonitor"]})

    assert res["decision"] == "needs_inputs"
    r = res["resolved"][0]
    assert r["package_id"] == "LibreHardwareMonitor.LibreHardwareMonitor"
    assert r["asked_as"] == "LibreHardwareMonitor", (
        "la scheda ricorda anche come l'utente l'aveva chiamato")


def test_un_nome_ambiguo_viene_elencato_non_scelto(monkeypatch):
    """«python» corrisponde a sei pacchetti: sceglierne uno sarebbe decidere
    al posto di chi ha chiesto. Si elencano e ci si ferma."""
    _finto_winget(monkeypatch, _SEARCH_MOLTI)
    res = install_packages.invoke({"packages": ["python"]})

    assert res["ok"] is False
    assert res["error_code"] == "package_ambiguous"
    assert "9NCVDN91XZQP" in res["error"] and "9PNRBTZXMB4Z" in res["error"]
    assert res.get("decision") != "needs_inputs"


def test_un_nome_ambiguo_non_installa_niente(monkeypatch):
    """L'ambiguita' ferma tutta la chiamata: nessun comando parte."""
    eseguiti = []

    def run(argv, timeout_s):
        eseguiti.append(argv)
        return (0, _SEARCH_MOLTI, "") if "search" in argv else (1, "", "")

    monkeypatch.setattr(install_packages, "_run", run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})
    install_packages.invoke({"packages": ["python"]})
    assert not any("install" in a for a in eseguiti)


def test_l_identificativo_esatto_non_passa_dalla_ricerca(monkeypatch):
    """La ricerca per nome e' un ripiego: quando l'identificativo esiste non
    si va oltre."""
    eseguiti = []

    def run(argv, timeout_s):
        eseguiti.append(argv)
        return (0, _WINGET_SHOW, "") if "show" in argv else (1, "", "")

    monkeypatch.setattr(install_packages, "_run", run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    assert res["decision"] == "needs_inputs"
    assert not any("search" in a for a in eseguiti)
    assert "asked_as" not in res["resolved"][0]


# ── 3.ter Cio' che si installa INSIEME e' cio' che si approva ────────
# Difetto reale, turni 234daad8 e b7d63070 (17/8/2026): la scheda diceva
# «1 programma» mentre winget ne avrebbe installati due, e l'installazione
# falliva proprio sulla dipendenza.

_SHOW_CON_DIPENDENZA = _WINGET_SHOW + """  Distribuzione offline supportata: true
  Dipendenze: 
    - Dipendenze dei pacchetti: 
        namazso.PawnIO
"""


def test_le_dipendenze_finiscono_sulla_scheda(monkeypatch):
    """Chi approva stava autorizzando anche un secondo componente senza
    averlo letto: e' esattamente cio' che la scheda esiste per evitare."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _SHOW_CON_DIPENDENZA, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC", "elevated": True})

    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    assert res["resolved"][0]["dependencies"] == ["namazso.PawnIO"]
    assert "namazso.PawnIO" in res["needs_inputs"]["description"]


def test_un_pacchetto_senza_dipendenze_non_ne_inventa(windows):
    """Zero falsi positivi: le parole chiave del pacchetto e le note di
    versione non devono diventare dipendenze."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    assert res["resolved"][0]["dependencies"] == []


def test_le_dipendenze_si_leggono_per_struttura_non_per_etichetta(monkeypatch):
    """Le intestazioni di winget sono tradotte: cercare «Dipendenze»
    funzionerebbe solo in italiano. L'ancora e' l'impronta, riconoscibile
    dalla forma."""
    inglese = (_SHOW_CON_DIPENDENZA
               .replace("Dipendenze:", "Dependencies:")
               .replace("Dipendenze dei pacchetti:", "Package Dependencies:")
               .replace("SHA256 del programma di installazione:",
                        "Installer SHA256:"))
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, inglese, ""))
    r = install_packages._winget_show("X.Y", "winget")
    assert r["dependencies"] == ["namazso.PawnIO"]


def test_senza_impronta_non_si_indovinano_dipendenze(monkeypatch):
    """L'ancora e' l'impronta: senza, ogni riga dopo sarebbe un candidato, e
    si finirebbe per leggere le parole chiave come dipendenze."""
    senza = _SHOW_CON_DIPENDENZA.replace(
        "945fdf327e4d38e4ced61b0727b7ab8a1222958782982052989ddf7cb7096f62", "")
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, senza, ""))
    r = install_packages._winget_show("X.Y", "winget")
    assert r["dependencies"] == []


def test_senza_privilegi_una_dipendenza_viene_annunciata(monkeypatch):
    """Va detto PRIMA: scoprirlo dopo aver confermato e' scoprirlo tardi."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _SHOW_CON_DIPENDENZA, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC", "elevated": False})

    testo = install_packages.invoke(
        {"packages": ["X.Y"]})["needs_inputs"]["description"].lower()
    assert "richiede altri" in testo or "requires others" in testo


# ── 4. Nessun carattere jolly ─────────────────────────────────────────
@pytest.mark.parametrize("jolly", [
    "micro*", "*", "?", "Microsoft.*", "a?b", "*.exe",
])
def test_un_carattere_jolly_e_rifiutato(jolly, windows) -> None:
    """«Installa qualunque cosa corrisponda» non e' una cosa che una
    persona possa approvare. Il rifiuto arriva PRIMA di qualunque comando."""
    res = install_packages.invoke({"packages": [jolly]})
    assert res["ok"] is False
    assert res["error_code"] == "wildcard_not_allowed"
    assert windows == [], "nessun comando deve partire"


@pytest.mark.parametrize("cattivo", [
    "/etc/passwd", "../x", "-rf", "--force", "a;rm -rf /", "a$(id)",
    "a|b", "a b", "x" * 200,
])
def test_un_identificativo_malformato_e_rifiutato(cattivo, windows) -> None:
    res = install_packages.invoke({"packages": [cattivo]})
    assert res["ok"] is False
    assert windows == []


def test_niente_di_scritto_dall_utente_diventa_un_opzione(windows) -> None:
    """La riga di comando la costruisce l'executor dai soli argomenti
    tipizzati: un valore rifiutato non arriva mai al gestore."""
    install_packages.invoke({"packages": ["--force", "-rf"]})
    assert windows == []


# ── 5-6. Autorita' e undo: dichiarati nel manifest ────────────────────
def test_la_regola_dei_permessi_non_sta_nell_executor() -> None:
    """ADR 0209 D4: chi puo' installare e dove lo decide il choke-point.
    Un executor che decide chi puo' chiamarlo si aggira chiamandolo in un
    altro modo."""
    codice = (ROOT / "executors" / "install_packages"
              / "install_packages.py").read_text(encoding="utf-8")
    for vietato in ("role ==", 'role=="', "is_admin", "== \"admin\"",
                    "owner_user_id =="):
        assert vietato not in codice, f"decisione di autorita' nel codice: {vietato}"


def test_dichiara_di_non_essere_annullabile() -> None:
    """ADR 0209 D3: l'ambiente cambia in modo che nessuno sa ripercorrere
    all'indietro. La disinstallazione e' un'altra operazione, non un undo."""
    m = _manifest()
    assert m["revertible"] is False
    assert m["critical"] is True
    assert "reverse_pattern" not in m


def test_dichiara_l_autorita_che_esercita() -> None:
    m = _manifest()
    nomi = [c["name"] for c in m["capabilities"]]
    assert nomi == ["system:admin", "code:exec"]
    assert set(m["platforms"]) == {"windows", "linux"}


def test_il_consenso_lo_inietta_il_runtime() -> None:
    """Il planner non scrive mai il token: e' `runtime_resolved`."""
    m = _manifest()
    token = m["args"]["properties"]["actor_consent_token"]
    assert token["runtime_resolved"] is True
    assert "actor_consent_token" not in (m["args"].get("required") or [])


def test_il_consenso_passa_dal_cancello_canonico(windows) -> None:
    """`gate_dispatch` esegue il ramo SOLO su una scelta dichiarata.

    La prima stesura usava `resume_executor_with_values`, che serve a
    disambiguare fra candidati e invoca SEMPRE: un «No» avrebbe eseguito lo
    stesso. Difetto trovato dal vivo sul turno dffe878b (17/8/2026)."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    oc = res["needs_inputs"]["on_complete"]

    assert oc["type"] == "gate_dispatch", (
        "un consenso non passa dal meccanismo di disambiguazione")
    assert "reject" not in oc["branches"], "un rifiuto non esegue niente"
    for ramo in oc["branches"].values():
        assert ramo["tool"] == "install_packages"
        assert ramo["args"]["packages"] == ["Microsoft.PowerToys"]


def test_la_portata_si_sceglie_a_bottoni(windows) -> None:
    """Piu' sicuro che farla scrivere: una scelta premuta non si puo'
    fraintendere, una frase si' (Roberto, 17/8/2026). Chi conferma non deve
    ricordare nessuna formula."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    scelte = res["needs_inputs"]["dialog"][0]["schema"]["choices"]
    valori = [c["value"] for c in scelte]

    assert valori == ["machine", "user", "reject"]
    assert all(c["label"] for c in scelte), "un bottone senza scritta"
    rami = res["needs_inputs"]["on_complete"]["branches"]
    assert rami["machine"]["args"]["scope"] == "machine"
    assert rami["user"]["args"]["scope"] == "user"


def test_senza_privilegi_si_offre_la_strada_che_metnos_puo_percorrere(monkeypatch) -> None:
    """«Per tutti gli utenti» senza privilegi non e' un no: e' un'altra strada.

    L'aiutante elevato lo porta Metnos, sul canale firmato e con UNA conferma
    di Windows. Il bottone resta distinto da «per tutti» semplice perche'
    promette una cosa diversa — e scoprire la finestra di Windows dopo aver
    premuto sarebbe scoprirla troppo tardi.
    """
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _WINGET_SHOW, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": False,
        "helper": False})

    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    valori = [c["value"]
              for c in res["needs_inputs"]["dialog"][0]["schema"]["choices"]]
    assert valori == ["user", "machine_setup", "reject"]
    rami = res["needs_inputs"]["on_complete"]["branches"]
    # La portata «macchina» esiste solo sul ramo che porta anche l'aiutante:
    # un ramo «machine» nudo fallirebbe, ed e' quello che non deve esserci.
    assert "machine" not in rami
    assert rami["machine_setup"]["args"]["scope"] == "machine"
    assert rami["machine_setup"]["args"]["install_helper"] is True


def test_dove_l_aiutante_non_puo_arrivare_l_assenza_resta_spiegata(monkeypatch) -> None:
    """Fuori da Windows non c'e' nessun aiutante da portare.

    Il bottone non deve comparire: manderebbe una persona ad aspettare una
    finestra che non arrivera' mai. Al suo posto resta la spiegazione — chi
    amministra la macchina puo' farlo, Metnos no.
    """
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _WINGET_SHOW, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "linux", "winget": "", "apt": "/usr/bin/apt-get",
        "manager": "apt", "machine": "server-di-prova", "elevated": True,
        "helper": False})

    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    valori = [c["value"]
              for c in res["needs_inputs"]["dialog"][0]["schema"]["choices"]]
    assert "machine_setup" not in valori


def test_una_rimozione_non_ha_portata(windows) -> None:
    """Si toglie cio' che c'e': chiedere «per tutti o solo per te» quando si
    disinstalla sarebbe una domanda senza significato."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "uninstall": True})
    valori = [c["value"]
              for c in res["needs_inputs"]["dialog"][0]["schema"]["choices"]]
    assert valori == ["approve", "reject"]


def test_ogni_ramo_porta_il_proprio_consenso(windows) -> None:
    """Senza token la ripresa ricadeva in fase 1 e mostrava di nuovo la
    scheda: l'installazione non partiva mai. E' cio' che ha visto Roberto."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    rami = res["needs_inputs"]["on_complete"]["branches"]
    for nome, ramo in rami.items():
        assert ramo["args"].get("actor_consent_token"), f"ramo {nome} senza consenso"
    assert (rami["machine"]["args"]["actor_consent_token"]
            != rami["user"]["args"]["actor_consent_token"]), (
        "due portate diverse non condividono un consenso")


def test_il_consenso_e_legato_a_quella_scheda(windows) -> None:
    """Un consenso dato per un'installazione non vale per una rimozione,
    ne' per un altro pacchetto: l'impronta comprende cio' che si e' letto."""
    def token(pkgs, uninstall=False, ramo="machine"):
        r = install_packages.invoke({"packages": pkgs, "uninstall": uninstall})
        rami = r["needs_inputs"]["on_complete"]["branches"]
        chiave = "approve" if uninstall else ramo
        return rami[chiave]["args"]["actor_consent_token"]

    base = token(["Microsoft.PowerToys"])
    assert base != token(["Microsoft.PowerToys"], uninstall=True)
    assert base != token(["Microsoft.PowerToys"], ramo="user")
    assert base == token(["Microsoft.PowerToys"]), "stessa scheda, stesso token"


def test_il_giro_completo_installa_solo_alla_fase_due(windows) -> None:
    """La prova che unisce le due meta': fase 1 interroga e basta, e gli
    argomenti che il cancello consegna fanno davvero partire l'operazione."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    assert [a[1] for a in windows] == ["show"]

    windows.clear()
    finale = install_packages.invoke(
        dict(res["needs_inputs"]["on_complete"]["branches"]["user"]["args"]))
    assert finale["ok"] is True
    assert finale["installed"] is True
    assert [a[1] for a in windows] == ["install"]


# ── Fase 2: col consenso, esegue ──────────────────────────────────────
def test_col_consenso_esegue_e_lo_dichiara(windows) -> None:
    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"],
        "actor_consent_token": "token-dal-runtime"})

    assert res["ok"] is True
    assert res["installed"] is True
    assert res["ok_count"] == 1
    assert res["results"][0]["action"] == "install"
    argv = windows[0]
    assert "install" in argv and "--exact" in argv
    assert "--silent" in argv, "nessuna finestra: non c'e' nessuno a guardarla"


def test_la_direzione_arriva_dagli_argomenti(windows) -> None:
    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"], "uninstall": True,
        "actor_consent_token": "token"})
    assert res["results"][0]["action"] == "uninstall"
    assert res["installed"] is False, "una rimozione non ha installato niente"
    assert "uninstall" in windows[0]


def test_la_diagnosi_viene_dal_fondo_non_dall_insegna(monkeypatch):
    """La prima riga di winget e' l'insegna del pacchetto trovato; mostrarla
    come errore dice l'opposto di cio' che e' successo — sembra un successo.
    Il verdetto sta in fondo (turno e563a5ab, 17/8/2026)."""
    uscita = ("Trovato LibreHardwareMonitor [LibreHardwareMonitor] Versione 0.9.6\n"
              "Download in corso...\n"
              "Il programma di installazione ha riportato un errore.\n")
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (42, uscita, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC", "elevated": True})

    res = install_packages.invoke({"packages": ["X"],
                                   "actor_consent_token": "tok"})
    errore = res["failed"][0]["error"]
    assert "errore" in errore.lower()
    assert "Trovato LibreHardwareMonitor" not in errore, (
        "l'insegna del pacchetto non e' una diagnosi")
    assert "rc=42" in errore, "il codice di uscita e' l'unico dato non tradotto"


def test_un_fallimento_del_gestore_non_diventa_un_successo(monkeypatch):
    """§2.8: `ok_count` conta cio' che e' stato REALMENTE applicato."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (1, "", "installer failed rc=1"))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": True})
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "actor_consent_token": "token"})
    assert res["ok"] is False
    assert res["ok_count"] == 0
    assert res["installed"] is False
    assert res["failed"][0]["error_code"] == "package_operation_failed"


# ── 8. Nessun gestore: la strada, non un errore generico ──────────────
def test_senza_gestore_dice_che_cosa_manca(monkeypatch) -> None:
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "linux", "winget": "", "apt": "", "manager": ""})
    res = install_packages.invoke({"packages": ["ripgrep"]})
    assert res["ok"] is False
    assert res["error_class"] == "capability_missing"
    assert res["error_code"] == "no_package_manager"
    assert "winget" in res["error"] or "apt" in res["error"]


# ── Argomenti ─────────────────────────────────────────────────────────
def test_una_lista_vuota_e_rimediabile() -> None:
    res = install_packages.invoke({"packages": []})
    assert res["error_class"] == "missing_input"


def test_troppi_pacchetti_in_una_volta(windows) -> None:
    """Una scheda che non si legge fino in fondo viene approvata senza
    leggerla, ed e' peggio che farla in due volte."""
    res = install_packages.invoke({"packages": [f"p{i}" for i in range(11)]})
    assert res["ok"] is False
    assert res["error_code"] == "too_many_packages"
    assert windows == []


def test_uno_scope_sconosciuto_ricade_sul_piu_stretto(windows) -> None:
    install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                             "scope": "qualcosa-di-inventato",
                             "actor_consent_token": "token"})
    assert "machine" in windows[0]


# ── L'aiutante elevato di Windows (ADR 0210 D) ────────────────────────
#
# Il client Metnos gira senza privilegi per scelta. «Per tutti gli utenti»
# non e' quindi qualcosa che questo processo possa fare: o lo fa l'aiutante,
# o non e' una scelta da offrire. Queste prove tengono ferme le due meta' —
# che cosa si offre, e dove va davvero a finire.

@pytest.fixture
def windows_senza_privilegi(monkeypatch):
    """Windows con winget, senza elevazione e senza aiutante: il caso di
    partenza, che e' anche quello di tutti i PC su cui l'aiutante non e'
    stato installato."""
    eseguiti = []

    def finto_run(argv, timeout_s):
        eseguiti.append(argv)
        if "show" in argv:
            return 0, _WINGET_SHOW, ""
        return 0, "", ""

    monkeypatch.setattr(install_packages, "_run", finto_run)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA",
        "elevated": False, "helper": False})
    return eseguiti


def _con_aiutante(monkeypatch, risposta, eseguiti=None):
    """Un aiutante presente che risponde `risposta` a ogni operazione.

    Registra le chiamate ricevute, cosi' si puo' verificare non solo l'esito
    ma DOVE e' andata a finire l'operazione — che e' la meta' che conta.
    """
    chiamate = eseguiti if eseguiti is not None else []

    def finta_chiamata(*argv, timeout):
        chiamate.append(argv)
        return risposta

    monkeypatch.setattr(install_packages, "_helper_call", finta_chiamata)
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA",
        "elevated": False, "helper": True})
    return chiamate


def test_senza_aiutante_per_tutti_gli_utenti_non_si_offre(
        windows_senza_privilegi) -> None:
    """Offrire una portata che fallira' e' far scegliere nel vuoto."""
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    rami = res["needs_inputs"]["on_complete"]["branches"]
    assert "machine" not in rami
    assert "user" in rami


def test_con_l_aiutante_per_tutti_gli_utenti_torna_a_essere_una_scelta(
        monkeypatch) -> None:
    """L'aiutante e' l'unica strada verso quella portata su un client senza
    privilegi: dove c'e', la scelta va offerta."""
    _con_aiutante(monkeypatch, {"ok": True})
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (0, _WINGET_SHOW, ""))
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    rami = res["needs_inputs"]["on_complete"]["branches"]
    assert "machine" in rami
    assert rami["machine"]["args"]["scope"] == "machine"


def test_l_installazione_per_tutti_va_all_aiutante_non_a_winget(
        monkeypatch) -> None:
    """Il fatto che conta: la portata macchina NON viene tentata dal
    processo senza privilegi. Verificato su dove e' andata la chiamata."""
    chiamate = _con_aiutante(monkeypatch, {"ok": True})
    eseguiti = []
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (eseguiti.append(argv), (0, "", ""))[1])

    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "scope": "machine",
                                   "actor_consent_token": "token"})
    assert res["ok"] is True
    assert res["ok_count"] == 1
    assert res["results"][0]["via"] == "helper"
    assert chiamate == [("install", "--package-id", "Microsoft.PowerToys")]
    assert eseguiti == [], "winget e' stato lanciato lo stesso"


def test_la_portata_utente_resta_a_winget(monkeypatch) -> None:
    """L'aiutante non e' una scorciatoia per tutto: cio' che il processo puo'
    fare da solo continua a farlo da solo, senza privilegi."""
    chiamate = _con_aiutante(monkeypatch, {"ok": True})
    eseguiti = []
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (eseguiti.append(argv), (0, "", ""))[1])

    install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                             "scope": "user", "actor_consent_token": "token"})
    assert chiamate == []
    assert eseguiti and "user" in eseguiti[0]


def test_un_rifiuto_dell_aiutante_non_diventa_un_successo(monkeypatch) -> None:
    """§2.8: cio' che l'aiutante rifiuta non e' installato, e il risultato lo
    dice — col codice che l'aiutante ha usato, non con uno inventato qui."""
    _con_aiutante(monkeypatch, {"ok": False, "error_code": "replayed_request",
                                "exit_code": None})
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "scope": "machine",
                                   "actor_consent_token": "token"})
    assert res["ok"] is False
    assert res["ok_count"] == 0
    assert res["installed"] is False
    assert res["error_code"] == "replayed_request"


def test_un_aiutante_irraggiungibile_e_una_capacita_assente(monkeypatch) -> None:
    """Non risponde e' diverso da ha detto no: il primo e' una capacita' che
    manca, e chi legge deve poterli distinguere."""
    _con_aiutante(monkeypatch, None)
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "scope": "machine",
                                   "actor_consent_token": "token"})
    assert res["ok"] is False
    assert res["error_class"] == "capability_missing"
    assert res["error_code"] == "helper_unreachable"


def test_una_rimozione_fallita_ritenta_con_l_aiutante(monkeypatch) -> None:
    """Togliere cio' che e' stato installato per tutti richiede la stessa
    portata che e' servita a metterlo: il tentativo senza privilegi ha gia'
    fallito, quindi non si sta indovinando niente."""
    chiamate = _con_aiutante(monkeypatch, {"ok": True})
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (1, "", "accesso negato"))
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "uninstall": True,
                                   "actor_consent_token": "token"})
    assert res["ok"] is True
    assert res["results"][0]["via"] == "helper"
    assert chiamate == [("uninstall", "--package-id", "Microsoft.PowerToys")]


def test_una_rimozione_riuscita_non_disturba_l_aiutante(monkeypatch) -> None:
    """Il ritentativo nasce da un fallimento vero, non dalla presenza
    dell'aiutante: dove la strada senza privilegi basta, si ferma li'."""
    chiamate = _con_aiutante(monkeypatch, {"ok": True})
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (0, "", ""))
    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"],
                                   "uninstall": True,
                                   "actor_consent_token": "token"})
    assert res["ok"] is True
    assert chiamate == []


def test_fuori_windows_nessun_aiutante_viene_cercato(monkeypatch) -> None:
    """Su Linux non c'e' nessun aiutante elevato, e cercarlo lancerebbe un
    processo per niente a ogni chiamata."""
    monkeypatch.setattr(install_packages.sys, "platform", "linux")
    monkeypatch.setenv("METNOS_CLIENT_EXE", "/non/deve/essere/lanciato")
    lanciati = []
    monkeypatch.setattr(install_packages.subprocess, "run",
                        lambda *a, **k: lanciati.append(a))
    assert install_packages._helper_call("check", timeout=1) is None
    assert lanciati == []


def test_senza_il_percorso_del_client_non_si_chiama_nessuno(monkeypatch) -> None:
    """L'executor puo' girare anche dove il client non l'ha lanciato: senza
    quel percorso non c'e' niente da interrogare, e non e' un errore."""
    monkeypatch.setattr(install_packages.sys, "platform", "win32")
    monkeypatch.delenv("METNOS_CLIENT_EXE", raising=False)
    assert install_packages._helper_call("check", timeout=1) is None


def test_la_risposta_e_l_ultima_riga_non_la_prima(monkeypatch) -> None:
    """Il client scrive anche il proprio registro su stdout: una riga di log
    davanti alla risposta non deve diventare «risposta malformata»."""
    monkeypatch.setattr(install_packages.sys, "platform", "win32")
    monkeypatch.setenv("METNOS_CLIENT_EXE", "metnos-client.exe")

    class Esito:
        stdout = ('2026-08-18T10:00:00Z INFO metnos_client: avvio\n'
                  '{"ok": true, "executable": "C:\\\\x\\\\metnos-helper.exe"}\n')

    monkeypatch.setattr(install_packages.subprocess, "run",
                        lambda *a, **k: Esito())
    assert install_packages._helper_call("check", timeout=1)["ok"] is True


def test_un_client_che_non_parte_non_e_un_aiutante_presente(monkeypatch) -> None:
    """Fail-closed: se non si riesce nemmeno a chiedere, la risposta e' no."""
    monkeypatch.setattr(install_packages.sys, "platform", "win32")
    monkeypatch.setenv("METNOS_CLIENT_EXE", "metnos-client.exe")

    def esplode(*a, **k):
        raise OSError("non eseguibile")

    monkeypatch.setattr(install_packages.subprocess, "run", esplode)
    assert install_packages._helper_call("check", timeout=1) is None
    assert install_packages._helper_present() is False


def test_dove_nessuna_portata_regge_non_si_fa_approvare_niente(monkeypatch) -> None:
    """Un gestore che scrive solo cartelle di sistema non ha una portata «solo
    per me» piu' piccola: senza privilegi non fa niente.

    Far leggere una scheda con indirizzo e impronta per poi fallire sarebbe
    far approvare qualcosa che non poteva avvenire. Si dice subito, e non si
    risolve nemmeno il catalogo."""
    eseguiti = []
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (eseguiti.append(argv),
                                                 (0, _APT_URIS, ""))[1])
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "linux", "winget": "", "apt": "/usr/bin/apt-get",
        "manager": "apt", "machine": "server", "elevated": False,
        "helper": False})
    res = install_packages.invoke({"packages": ["ripgrep"]})

    assert res["ok"] is False
    assert res["error_class"] == "capability_missing"
    assert res["error_code"] == "needs_administrator"
    assert "server" in res["error"]
    assert eseguiti == [], "ha interrogato il catalogo per niente"


def test_con_i_privilegi_apt_torna_a_essere_installabile(monkeypatch) -> None:
    """Lo stesso gestore, con root, ha la sua unica portata e la usa."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (0, _APT_URIS, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "linux", "winget": "", "apt": "/usr/bin/apt-get",
        "manager": "apt", "machine": "server", "elevated": True,
        "helper": False})
    rami = install_packages.invoke(
        {"packages": ["ripgrep"]})["needs_inputs"]["on_complete"]["branches"]
    assert set(rami) == {"machine"}, rami


def test_apt_non_offre_mai_solo_per_me(monkeypatch) -> None:
    """Il confine e' una proprieta' del GESTORE, non dei privilegi: apt scrive
    in cartelle di sistema anche quando li ha tutti."""
    for elevato in (True, False):
        ctx = {"os": "linux", "winget": "", "apt": "/usr/bin/apt-get",
               "manager": "apt", "machine": "server", "elevated": elevato,
               "helper": False}
        assert "user" not in install_packages._scopes_available(ctx)


def test_la_nota_non_promette_un_aiutante_che_non_esiste(monkeypatch) -> None:
    """Su Windows l'aiutante elevato restituisce la portata «per tutti», e la
    nota dice come averlo (ADR 0210 D8: altrove non c'e' nessun aiutante da
    installare)."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (0, _WINGET_SHOW, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC", "elevated": False,
        "helper": False})
    testo = install_packages.invoke(
        {"packages": ["Microsoft.PowerToys"]})["needs_inputs"]["description"]
    assert install_packages._msg("MSG_PACKAGES_NO_ELEVATION_NOTE") in testo
    assert install_packages._msg("MSG_PACKAGES_NO_ELEVATION_ADMIN") not in testo


def test_la_versione_apt_non_e_un_pezzo_di_url(monkeypatch) -> None:
    """`+` viaggia nell'URL come `%2b`: mostrata cosi' sulla scheda, la
    versione non e' quella che la persona trova scritta altrove."""
    # Uscita reale di `apt-get download --print-uris cowsay` su .33,
    # 18/8/2026: la versione porta un `+`, e l'URL lo scrive `%2b`.
    riga = ("'http://it.archive.ubuntu.com/ubuntu/pool/universe/c/cowsay/"
            "cowsay_3.03%2bdfsg2-8_all.deb' cowsay_3.03+dfsg2-8_all.deb 18572 "
            "SHA512:" + "8e" * 64)
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, timeout_s: (0, riga, ""))
    dati = install_packages._apt_show("cowsay", "/usr/bin/apt-get")
    assert dati["version"] == "3.03+dfsg2-8", dati["version"]


# ── L'aiutante elevato lo porta Metnos, non un amministratore ─────────
def _contesto_senza_aiutante():
    return {"os": "windows", "winget": "winget.exe", "apt": "",
            "manager": "winget", "machine": "PC-DI-PROVA", "elevated": False,
            "helper": False}


def _consenso_per_tutti(pacchetto="Microsoft.PowerToys"):
    """Il token della scheda «per tutti gli utenti», come lo produce il ramo."""
    risolto = [{"package_id": pacchetto}]
    return install_packages._consent_token(risolto, False, "machine")


def test_un_no_alla_finestra_di_windows_non_installa_niente(monkeypatch) -> None:
    """Rifiutare la conferma di Windows e' una risposta, non un guasto.

    E soprattutto: NIENTE deve essere installato dopo un no. Il messaggio
    dice anche la strada che resta aperta — solo per te, che non chiede
    nulla — perche' un vicolo cieco non e' una risposta utile.
    """
    eseguiti = []

    def finto_run(argv, t):
        eseguiti.append(argv)
        return (0, _WINGET_SHOW, "") if "show" in argv else (0, "", "")

    monkeypatch.setattr(install_packages, "_run", finto_run)
    monkeypatch.setattr(install_packages, "_context", _contesto_senza_aiutante)
    monkeypatch.setattr(install_packages, "_setup_helper",
                        lambda: {"ok": False, "error_code": "consent_refused"})

    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"], "scope": "machine",
        "install_helper": True,
        "actor_consent_token": _consenso_per_tutti()})

    assert res["ok"] is False
    assert res["error_code"] == "consent_refused"
    assert not any("install" in a for a in eseguiti), \
        f"ha installato dopo un rifiuto: {eseguiti}"


def test_se_il_client_non_risponde_non_si_installa_alla_cieca(monkeypatch) -> None:
    """«Non riesco a parlare col client» e' diverso da «hai detto no», e
    diverso da «e' andata». Sono tre esiti, e restano tre."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _WINGET_SHOW, ""))
    monkeypatch.setattr(install_packages, "_context", _contesto_senza_aiutante)
    monkeypatch.setattr(install_packages, "_setup_helper", lambda: None)

    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"], "scope": "machine",
        "install_helper": True,
        "actor_consent_token": _consenso_per_tutti()})
    assert res["ok"] is False
    assert res["error_code"] == "helper_unreachable"


def test_quando_l_aiutante_arriva_l_installazione_prosegue(monkeypatch) -> None:
    """Il caso felice: portato l'aiutante, la macchina e' un'altra e la
    portata «per tutti» adesso regge davvero."""
    eseguiti = []

    def finto_run(argv, t):
        eseguiti.append(argv)
        return (0, _WINGET_SHOW, "") if "show" in argv else (0, "", "")

    stato = {"aiutante": False}

    def contesto():
        c = _contesto_senza_aiutante()
        c["helper"] = stato["aiutante"]
        return c

    def porta_aiutante():
        stato["aiutante"] = True
        return {"ok": True, "installed": True, "version": "0.2.26"}

    monkeypatch.setattr(install_packages, "_run", finto_run)
    monkeypatch.setattr(install_packages, "_context", contesto)
    monkeypatch.setattr(install_packages, "_setup_helper", porta_aiutante)
    # Con l'aiutante presente l'operazione passa di li', non da winget locale.
    monkeypatch.setattr(install_packages, "_apply_via_helper",
                        lambda pid, uninstall: {"package_id": pid, "ok": True,
                                                "action": "install",
                                                "via": "helper"})

    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"], "scope": "machine",
        "install_helper": True,
        "actor_consent_token": _consenso_per_tutti()})

    assert res["ok"] is True
    assert res["ok_count"] == 1
    assert res["results"][0]["via"] == "helper"


def test_un_aiutante_che_parla_unaltra_lingua_non_conta_come_presente(monkeypatch):
    """Due programmi installati in momenti diversi si disallineano.

    Un aiutante che risponde ma non parla la stessa lingua non puo' eseguire
    la richiesta: contarlo come presente vorrebbe dire offrire «per tutti gli
    utenti» e poi fallire su un messaggio che l'altro capo non capisce — un
    guasto che non somiglia alla causa.
    """
    monkeypatch.setattr(install_packages, "_helper_call",
                        lambda *a, **k: {"ok": True, "aligned": False,
                                         "helper_version": "0.0.9"})
    assert install_packages._helper_present() is False


def test_un_aiutante_allineato_conta_come_presente(monkeypatch) -> None:
    monkeypatch.setattr(install_packages, "_helper_call",
                        lambda *a, **k: {"ok": True, "aligned": True,
                                         "helper_version": "0.2.26"})
    assert install_packages._helper_present() is True


def test_una_risposta_senza_verdetto_non_e_una_prova_di_allineamento(monkeypatch):
    """Un client vecchio non sa dire se i due si capiscono. L'assenza di una
    risposta non e' una risposta affermativa."""
    monkeypatch.setattr(install_packages, "_helper_call",
                        lambda *a, **k: {"ok": True})
    assert install_packages._helper_present() is False


def test_nella_rimozione_il_verdetto_dell_aiutante_arriva_all_utente(monkeypatch):
    """Chi legge deve sapere QUALE dei due tentativi e' fallito, e perche'.

    La rimozione prova prima senza privilegi; se fallisce e l'aiutante c'e',
    riprova da li'. Tenendo il verdetto dell'aiutante solo quando era
    positivo, un «non riesco a parlare col client» o un «hai detto no»
    venivano sostituiti, all'uscita, dall'errore del PRIMO tentativo: la
    persona leggeva «accesso negato» e non sapeva nemmeno che la seconda
    strada fosse stata tentata (§2.8).
    """
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _WINGET_SHOW, "") if "show" in argv
                        else (1, "", "accesso negato"))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": False,
        "helper": True})
    monkeypatch.setattr(
        install_packages, "_apply_via_helper",
        lambda pid, uninstall: {"package_id": pid, "ok": False,
                                "error": "il componente non risponde",
                                "error_class": "capability_missing",
                                "error_code": "helper_unreachable"})

    res = install_packages.invoke({
        "packages": ["Microsoft.PowerToys"], "uninstall": True,
        "actor_consent_token": install_packages._consent_token(
            [{"package_id": "Microsoft.PowerToys"}], True, "machine")})

    assert res["ok"] is False
    assert res["error_code"] == "helper_unreachable", \
        f"e' tornato l'errore del primo tentativo: {res.get('error')}"
    assert "accesso negato" not in str(res.get("error") or "")
