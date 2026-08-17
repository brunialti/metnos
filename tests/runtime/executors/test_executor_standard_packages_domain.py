"""Dominio `packages`: «e' installato?» sul server o su un device pairato.

Riscritto il 17/8/2026 (ADR 0209, parte B). Prima l'executor guardava il
PATH: rispondeva a «c'e' un eseguibile che si chiama cosi'», che e' una
domanda diversa da quella che l'utente fa. Ora interroga il gestore dei
pacchetti della macchina e tiene il PATH come ultima verifica.

Il vecchio file di test descriveva il vecchio contratto: e' cambiato per
decisione, non per errore (§7.1, niente compatibilita' all'indietro in dev).
Cio' che il vecchio proteggeva e vale ancora — un dettaglio interno non
trapela nel messaggio all'utente — e' conservato in fondo.

Run: `python3 -m pytest tests/runtime/executors/test_executor_standard_packages_domain.py -v`
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

from executors.find_packages import find_packages  # noqa: E402


MANIFEST = ROOT / "executors" / "find_packages" / "manifest.toml"


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


# ── Autorita' dichiarata ──────────────────────────────────────────────
def test_dichiara_lettura_piu_esecuzione_di_un_programma() -> None:
    """`system:read` e' il privilegio, `code:exec` e' il MECCANISMO.

    Interrogare winget o dpkg vuol dire eseguire un programma: tacerlo
    sarebbe dichiarare meno di quello che si fa. E' la stessa coppia gia'
    dichiarata da `get_processes` per ps/tasklist."""
    manifest = _manifest()
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert set(manifest["platforms"]) == {"linux", "windows"}
    nomi = [c["name"] for c in manifest["capabilities"]]
    assert nomi == ["system:read", "code:exec"]
    assert "winget" in manifest["capabilities"][1]["hint"]
    assert "schema_inline" in manifest["output"]


def test_resta_read_only_e_raggiunge_il_device() -> None:
    """«E' installato X sul mio PC» va chiesto a QUEL PC. E il sandbox
    dichiarato e' job-object, non appcontainer: con `code:exec` il client
    declassa, e il manifest non deve promettere un contenimento che non
    otterra' (§2.8)."""
    manifest = _manifest()
    assert manifest["placement"] == {
        "scope": "any", "device_ok": True, "min_sandbox": "job-object",
    }
    assert manifest["revertible"] is False


# ── Il contratto: lista dentro, lista fuori ───────────────────────────
def test_una_lista_produce_una_riga_per_elemento() -> None:
    res = find_packages.invoke({"packages": ["bash", "sh"]})
    assert res["ok"] is True
    assert [e["package_id"] for e in res["entries"]] == ["bash", "sh"]


def test_un_solo_elemento_puo_arrivare_come_stringa() -> None:
    """§2.4: un argomento plurale accetta anche un elemento."""
    res = find_packages.invoke({"packages": "bash"})
    assert res["ok"] is True
    assert len(res["entries"]) == 1


def test_un_pacchetto_assente_e_una_risposta_non_un_errore() -> None:
    """La differenza che conta: «non installato» e' un dato, non un
    fallimento. Un pacchetto che la macchina non ha non deve far cadere
    gli altri elementi della stessa chiamata (§2.1)."""
    res = find_packages.invoke({
        "packages": ["bash", "metnos-package-that-does-not-exist-7f09"]})
    assert res["ok"] is True
    assert res["fail_count"] == 0
    per_id = {e["package_id"]: e for e in res["entries"]}
    assert per_id["bash"]["installed"] is True
    assert per_id["metnos-package-that-does-not-exist-7f09"]["installed"] \
        is False


def test_ok_count_conta_gli_interrogati_non_i_trovati() -> None:
    """§2.8: `ok_count` dice quanti elementi sono stati davvero elaborati.
    Un pacchetto risultato assente e' stato comunque interrogato."""
    res = find_packages.invoke({
        "packages": ["metnos-assente-a", "metnos-assente-b"]})
    assert res["ok_count"] == 2
    assert all(e["installed"] is False for e in res["entries"])


def test_ogni_riga_dichiara_da_dove_viene_la_risposta() -> None:
    """Senza `source` il lettore non distingue il registro dei pacchetti da
    un colpo di fortuna nel PATH, e «installato: si'» porterebbe una
    fiducia che non si e' guadagnato."""
    res = find_packages.invoke({"packages": ["bash"]})
    assert res["entries"][0]["source"] in {
        "dpkg", "rpm", "pacman", "winget", "path"}
    assert res["source_primary"]


def test_il_path_e_l_ultima_verifica_non_l_unica(tmp_path, monkeypatch):
    """Un programma installato fuori dal gestore dei pacchetti esiste
    comunque: il PATH lo prende, e lo dichiara come tale."""
    finto = tmp_path / "metnos-fuori-dal-registro"
    finto.write_text("#!/bin/sh\nexit 0\n")
    finto.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")

    res = find_packages.invoke({"packages": ["metnos-fuori-dal-registro"]})
    voce = res["entries"][0]
    assert voce["installed"] is True
    assert voce["source"] == "path"
    assert voce["path"]


# ── Argomenti sbagliati ───────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    [],                       # non e' nemmeno un oggetto
    {},                       # manca l'argomento
    {"packages": []},         # lista vuota
    {"packages": ""},         # stringa vuota
    {"packages": None},
])
def test_una_chiamata_senza_pacchetti_e_un_errore_tipizzato(args) -> None:
    res = find_packages.invoke(args)
    assert res["ok"] is False
    assert res["error_class"]
    assert res["error_code"]


@pytest.mark.parametrize("args", [{}, {"packages": []}, {"packages": ""}])
def test_un_argomento_mancante_e_rimediabile_non_terminale(args) -> None:
    """La classe non e' decorazione: il motore ripara `missing_input`
    richiedendo l'argomento al modello, e la query il programma lo nomina.
    Marcarlo `invalid_input` chiudeva un turno recuperabile — difetto reale
    su «ho outlook sul pc?» (17/8/2026): lo strumento era quello giusto,
    l'argomento era vuoto, e nessuno lo richiedeva."""
    from engine.types import ERROR_CLASSES
    res = find_packages.invoke(args)
    assert res["error_class"] == "missing_input"
    assert res["error_class"] in ERROR_CLASSES


def test_un_argomento_malformato_resta_terminale() -> None:
    """L'opposto: richiedere di nuovo un valore inutilizzabile ripeterebbe
    solo lo stesso rifiuto. `invalid_input` dice «cosi' non si puo' usare»."""
    res = find_packages.invoke({"packages": ["/etc/passwd"]})
    assert res["error_class"] == "invalid_input"


def test_l_oggetto_non_dict_resta_invalido() -> None:
    res = find_packages.invoke([])
    assert res["error_class"] == "invalid_input"


@pytest.mark.parametrize("cattivo", [
    "/usr/bin/python3",       # un percorso, non un identificativo
    "../etc/passwd",
    "-rf",                    # sarebbe letto come opzione dal gestore
    "--force",
    "pacchetto con spazi",
    "a;rm -rf /",
    "a$(id)",
    "a|b",
    "a\nb",
    "x" * 200,                # oltre il limite
])
def test_un_identificativo_malformato_non_affonda_gli_altri(cattivo) -> None:
    """Un elemento sbagliato e' un problema di QUELL'elemento. Gli altri
    ricevono comunque la loro risposta, e il fallimento e' tipizzato."""
    res = find_packages.invoke({"packages": ["bash", cattivo]})
    assert res["ok"] is False
    assert res["ok_count"] == 1, "l'elemento buono va comunque interrogato"
    assert res["fail_count"] == 1
    assert res["partial"] is True
    assert res["failed"][0]["error_code"] == "package_id_invalid"


def test_un_identificativo_malformato_non_arriva_al_gestore(monkeypatch):
    """Il controllo sta PRIMA dell'esecuzione: un valore rifiutato non
    diventa mai un argomento di un processo."""
    visti = []
    monkeypatch.setattr(find_packages, "_run",
                        lambda argv, **kw: (visti.append(argv), (1, ""))[1])
    find_packages.invoke({"packages": ["--force", "/etc/shadow", "a;b"]})
    assert visti == []


# ── Le persone nominano programmi, non identificativi ─────────────────
# Difetto reale, turno eb4f0cc9 del 17/8/2026: «outlook e' installato su
# pc-roberto?» rispondeva NO su una macchina dove Outlook c'e'. La macchina
# lo archivia sotto l'id `9NRX63209R7B`, e nessuno digita quello.

_WINGET_REALE = (
    "Nome                Id           Versione        Origine\n"
    "---------------------------------------------------------\n"
    "Outlook for Windows 9NRX63209R7B 1.2025.1209.500 msstore\n"
)
_WINGET_VUOTO = (
    "Non è stato trovato alcun pacchetto installato "
    "corrispondente ai criteri di input.\n"
)


def test_le_colonne_winget_si_leggono_dalla_geometria() -> None:
    """La riga non si puo' spezzare sui gruppi di spazi: winget riempie le
    colonne a larghezza fissa, quindi un valore che riempie la sua colonna
    lascia UN SOLO spazio prima della successiva. Erano quattro colonne, e
    la lettura per gruppi di spazi ne vedeva una."""
    righe = find_packages._winget_rows(_WINGET_REALE, "outlook")
    assert righe == [("Outlook for Windows", "9NRX63209R7B",
                      "1.2025.1209.500")]


def test_le_intestazioni_localizzate_non_contano() -> None:
    """Si usano le POSIZIONI delle colonne, non i loro nomi: «Nome» in
    italiano e «Name» in inglese stanno nello stesso posto."""
    inglese = _WINGET_REALE.replace(
        "Nome                Id           Versione        Origine",
        "Name                Id           Version         Source ")
    assert find_packages._winget_rows(inglese, "outlook")[0][1] \
        == "9NRX63209R7B"


def test_la_riga_di_niente_trovato_non_diventa_un_risultato() -> None:
    """Il messaggio «non trovato» e' localizzato e non ha intestazione
    sopra: non deve mai essere letto come una riga di tabella."""
    assert find_packages._winget_rows(_WINGET_VUOTO, "outlook") == []
    assert find_packages._winget_rows("", "outlook") == []


def test_un_nome_umano_viene_risolto_quando_l_id_non_esiste(monkeypatch):
    """Il rimedio al difetto: l'identificativo esatto si prova per primo;
    se manca, la STESSA parola si prova come nome. La risposta dice come e'
    stata trovata e restituisce l'identita' vera della macchina."""
    chiamate = []

    def finto_run(argv, **kw):
        chiamate.append(argv)
        if "--exact" in argv:
            return 1, _WINGET_VUOTO       # nessun pacchetto con QUELL'id
        return 0, _WINGET_REALE            # ma uno con quel NOME si'

    monkeypatch.setattr(find_packages, "_run", finto_run)
    monkeypatch.setattr(find_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "manager": "",
        "manager_path": "", "primary_source": "winget"})

    res = find_packages.invoke({"packages": ["outlook"]})
    voce = res["entries"][0]

    assert voce["installed"] is True
    assert voce["match"] == "name"
    assert voce["resolved_id"] == "9NRX63209R7B"
    assert voce["version"] == "1.2025.1209.500"
    assert voce["package_id"] == "outlook", "resta quello che ha chiesto l'utente"
    assert any("--exact" in c for c in chiamate), "l'id esatto si prova PRIMA"


def test_l_identificativo_esatto_ha_la_precedenza(monkeypatch):
    """La ricerca per nome e' un ripiego, non la regola: quando l'id esiste
    non si va oltre, e nessuna seconda chiamata parte."""
    chiamate = []

    def finto_run(argv, **kw):
        chiamate.append(argv)
        return 0, _WINGET_REALE

    monkeypatch.setattr(find_packages, "_run", finto_run)
    monkeypatch.setattr(find_packages, "_context", lambda: {
        "os": "windows", "winget": "winget.exe", "manager": "",
        "manager_path": "", "primary_source": "winget"})

    voce = find_packages.invoke({"packages": ["outlook"]})["entries"][0]
    assert voce["match"] == "exact"
    assert "resolved_id" not in voce, "un id esatto non ha bisogno di essere risolto"
    assert len(chiamate) == 1


def test_su_linux_il_nome_parziale_trova_il_pacchetto() -> None:
    """Stesso rimedio, stessa forma: «python» deve trovare `python3`."""
    voce = find_packages.invoke({"packages": ["python"]})["entries"][0]
    assert voce["installed"] is True
    assert voce["match"] == "name"
    assert voce["resolved_id"].startswith("python")


def test_ogni_riga_dice_come_e_stata_trovata() -> None:
    """`match` distingue un colpo esatto da una risoluzione per nome da un
    ritrovamento nel PATH: sono tre gradi di certezza diversi, e chi legge
    deve poterli distinguere (§2.8)."""
    res = find_packages.invoke({"packages": ["bash", "metnos-assente-9f2c"]})
    per_id = {e["package_id"]: e for e in res["entries"]}
    assert per_id["bash"]["match"] in {"exact", "name", "path"}
    assert per_id["metnos-assente-9f2c"]["match"] == "none"


# ── Invariante conservata dal test precedente ─────────────────────────
def test_un_dettaglio_interno_non_trapela_nel_messaggio(monkeypatch) -> None:
    """Una ricerca nel PATH puo' fallire per un mount rotto o un permesso
    cambiato, e il testo di quell'errore porta dettagli di filesystem che
    non hanno niente a che fare con l'utente. Il turno non deve cadere e il
    dettaglio non deve comparire."""
    def esplode(_name):
        raise OSError("dettaglio privato del PATH")

    monkeypatch.setattr(find_packages.shutil, "which", esplode)
    res = find_packages.invoke({"packages": ["bash"]})

    assert res["ok"] is True, "una ricerca fallita non fa cadere il turno"
    assert res["entries"][0]["installed"] is False
    assert "dettaglio privato" not in str(res)


def test_un_gestore_che_va_in_timeout_non_blocca_il_turno(monkeypatch):
    """Se il gestore dei pacchetti non risponde, l'elemento risulta non
    trovato: meglio una risposta prudente che un turno appeso."""
    import subprocess as _sp

    def scade(*a, **kw):
        raise _sp.TimeoutExpired(cmd="winget", timeout=1)

    monkeypatch.setattr(find_packages.subprocess, "run", scade)
    monkeypatch.setattr(find_packages, "_which", lambda n: "")
    res = find_packages.invoke({"packages": ["bash"]})
    assert res["ok"] is True
    assert res["entries"][0]["installed"] is False
