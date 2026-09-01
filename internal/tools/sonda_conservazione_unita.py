"""Probe the four named states of the replaced-unit preservation.

The passage renames the previous system unit aside and lets the signed
fragment take its name. That single step touches the file of a running
service, and the design admits only four states on resume. Unlike every
other durable step of this unit, it declares no interruption point, so the
convergence suite never reaches it: this probe walks the states directly.

Exit codes: 0 the four states behave and the tampered ones are refused,
1 one does not, 2 the probe could not run and says why.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

if os.name == "nt":
    print("questa sonda misura una scena POSIX e non gira su Windows")
    raise SystemExit(2)

try:
    import executor_birth_legacy_neutralizer as NEUTRALIZZATORE
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura non disponibile: {errore}")
    raise SystemExit(2)

VECCHIO = b"[Unit]\nDescription=unita' precedente\n"
NUOVO = b"[Unit]\nDescription=frammento firmato\n"
NOME = "metnos-http.service"


def _scena(base: Path) -> Path:
    cartella = Path(tempfile.mkdtemp(dir=base))
    percorso = cartella / NOME
    percorso.write_bytes(VECCHIO)
    percorso.chmod(0o644)
    return percorso


def _conserva(percorso: Path):
    return NEUTRALIZZATORE._preserve_replaced_unit_v1(
        "legacy-service-http-system", percorso, NUOVO,
    )


def principale() -> int:
    base = Path(tempfile.mkdtemp(prefix="sonda-conservazione-"))
    guasti: list[str] = []
    try:
        percorso = _scena(base)
        conservato = percorso.with_name(percorso.name + "")
        nome, gia = _conserva(percorso)
        preservato = percorso.parent / nome
        resti = sorted(item.name for item in percorso.parent.iterdir())
        print(f"1 stato iniziale        conserva={nome} gia_fatto={gia}")
        print(f"                        resta: {resti}")
        if gia or not preservato.is_file() or preservato.read_bytes() != VECCHIO:
            guasti.append("lo stato iniziale non conserva i byte precedenti")
        if percorso.exists():
            guasti.append("il nome finale resta occupato dopo la conservazione")

        nome2, gia2 = _conserva(percorso)
        print(f"2 ripetuta a meta'      conserva={nome2} gia_fatto={gia2}")
        if not gia2 or nome2 != nome:
            guasti.append("la ripetizione a meta' non e' idempotente")

        percorso.write_bytes(NUOVO)
        percorso.chmod(0o644)
        nome3, gia3 = _conserva(percorso)
        print(f"3 col frammento nuovo   conserva={nome3} gia_fatto={gia3}")
        if not gia3 or preservato.read_bytes() != VECCHIO:
            guasti.append("il frammento nuovo non chiude il passo, o tocca lo storico")

        percorso.write_bytes(b"[Unit]\nDescription=intruso\n")
        percorso.chmod(0o644)
        esito = _misura_rifiuto("4 un intruso al nome  ", percorso)
        if esito is None:
            guasti.append("un file estraneo al nome finale viene accettato")

        # The historical name must not be adoptable by tampering either.
        # The current name must hold the exact replacement, otherwise the
        # refusal would come from the replacement check and this case would
        # measure nothing.
        secondo = _scena(base)
        _conserva(secondo)
        secondo.write_bytes(NUOVO)
        secondo.chmod(0o644)
        assert _conserva(secondo)[1] is True, "la scena 5 non parte da uno stato chiuso"
        storico = secondo.parent / (
            secondo.name + NEUTRALIZZATORE.PRESERVED_EXTENSION_V1
        )
        storico.write_bytes(b"storico manomesso\n")
        esito = _misura_rifiuto("5 storico manomesso   ", secondo)
        if esito is None:
            guasti.append("uno storico manomesso viene accettato")

        terzo = _scena(base)
        collegamento = terzo.parent / "collegamento.service"
        collegamento.symlink_to(terzo)
        esito = _misura_rifiuto("6 un collegamento     ", collegamento)
        if esito is None:
            guasti.append("un collegamento al posto del file viene accettato")

        print()
        if guasti:
            for guasto in guasti:
                print("ESITO:", guasto)
            return 1
        print("ESITO: i quattro stati si comportano e i tre manomessi sono")
        print("rifiutati. La conservazione e' riprendibile, ma nessuna")
        print("giuntura d'interruzione la dichiara: la suite di convergenza")
        print("non la raggiunge, e questa sonda e' l'unica che la guarda.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _misura_rifiuto(etichetta: str, percorso: Path):
    try:
        esito = _conserva(percorso)
    except Exception as errore:  # noqa: BLE001
        codice = getattr(errore, "code", type(errore).__name__)
        print(f"{etichetta}  rifiutato: {codice}")
        return codice
    print(f"{etichetta}  ACCETTATO: {esito}")
    return None


if __name__ == "__main__":
    raise SystemExit(principale())
