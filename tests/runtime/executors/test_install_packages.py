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


def test_senza_privilegi_non_si_offre_cio_che_fallirebbe(monkeypatch) -> None:
    """Offrire «per tutti gli utenti» dove non si puo' vorrebbe dire far
    scegliere qualcosa che fallira'. L'assenza e' spiegata sulla scheda,
    non silenziosa."""
    monkeypatch.setattr(install_packages, "_run",
                        lambda argv, t: (0, _WINGET_SHOW, ""))
    monkeypatch.setattr(install_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "apt": "",
        "manager": "winget", "machine": "PC-DI-PROVA", "elevated": False})

    res = install_packages.invoke({"packages": ["Microsoft.PowerToys"]})
    valori = [c["value"]
              for c in res["needs_inputs"]["dialog"][0]["schema"]["choices"]]
    assert valori == ["user", "reject"]
    assert "machine" not in res["needs_inputs"]["on_complete"]["branches"]
    testo = res["needs_inputs"]["description"].lower()
    assert "amministratore" in testo or "administrator" in testo


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
