"""Il Validator non boccia la forma canonica del piping (6/8/2026).

`requires_one_of` esprime «almeno uno fra X, Y, Z». Un consumatore in pipeline
NON porta ancora nessuno dei tre: porta `from_step`, che diventa `entries` — o
l'arg-lista naturale del consumatore — solo all'invoke. Il ramo dei `required`
lo sapeva gia'; quello dei `requires_one_of` no, e bocciava §4.1 su nove gruppi
del catalogo (read_files, delete_files, get_urls, read_files_doc,
read_files_spreadsheet, get_images_google_photos, write_images_google_photos,
find_images_indices, find_persons_indices).

Il costo non era solo un controllo inutile: ogni bocciatura fa ri-proporre il
piano all'LLM, e il piano rifatto puo' essere PEGGIORE. Misurato il 6/8 su
«trova i file .md nella cartella X e leggili»: il piano grezzo era
find_files + read_files, il Validator lo bocciava, il re-propose sceglieva
find_files_hash (il cercatore di DUPLICATI) e il turno finiva «Nessun risultato
trovato» con entrambi gli step ok=True.

Il confine resta: un gruppo di soli scalari NON e' soddisfatto dal piping — il
contesto scalare ha il suo controllo e il suo messaggio (§2.8).
"""
from __future__ import annotations

from engine.types import Framework, StepSpec
from engine.validator import Validator


class _Exec:
    def __init__(self, name: str, args_schema: dict) -> None:
        self.name = name
        self.args_schema = args_schema


_LETTORE = {
    "type": "object",
    "required": [],
    "requires_one_of": [["path", "paths", "entries", "name"]],
    "properties": {
        "path": {"type": "string"},
        "paths": {"type": "array"},
        "entries": {"type": "array"},
        "name": {"type": "string"},
        "from_step": {"type": "integer"},
    },
}

_SOLO_SCALARI = {
    "type": "object",
    "required": [],
    "requires_one_of": [["account", "folder"]],
    "properties": {
        "account": {"type": "string"},
        "folder": {"type": "string"},
        "from_step": {"type": "integer"},
    },
}


def _catalogo(schema: dict = _LETTORE) -> list:
    return [
        _Exec("find_files", {"type": "object", "required": ["base_path"],
                             "properties": {"base_path": {"type": "string"},
                                            "pattern": {"type": "string"}}}),
        _Exec("read_files", schema),
    ]


def _piano(args_lettore: dict) -> Framework:
    return Framework(steps=[
        StepSpec(tool="find_files", args={"base_path": "/x", "pattern": "*.md"}),
        StepSpec(tool="read_files", args=dict(args_lettore)),
        StepSpec(tool="final_answer", args={}),
    ])


def test_il_consumatore_piped_e_valido() -> None:
    """`find_files → read_files(from_step=1)` è §4.1, non un piano incompleto."""
    res = Validator(_catalogo()).check(_piano({"from_step": 1}))
    assert res.ok, [e.detail for e in res.errors]


def test_le_entries_gia_risolte_valgono_come_il_from_step() -> None:
    """Dopo la risoluzione il dato viaggia in `entries`: stessa proprietà,
    altra forma. Qui il gruppo non nomina `entries` e il riempimento andrebbe
    su `paths`."""
    schema = dict(_LETTORE, requires_one_of=[["path", "paths", "name"]])
    res = Validator(_catalogo(schema)).check(
        _piano({"entries": [{"path": "/x/a.md"}]}))
    assert res.ok, [e.detail for e in res.errors]


def test_senza_piping_il_vincolo_resta() -> None:
    """Nessun produttore a monte e nessun dato: il piano è davvero incompleto."""
    res = Validator(_catalogo()).check(_piano({"parse": "auto"}))
    assert not res.ok
    assert "requires_one_of" in res.errors[0].detail


def test_un_gruppo_di_soli_scalari_non_lo_soddisfa_il_piping() -> None:
    """Il piping proietta un vettore in un arg-lista; uno scalare arriva solo
    se TUTTE le entries concordano, e la sua assenza ha un controllo proprio.
    Tollerarlo qui sarebbe dichiarare valido un piano che a valle non lo è."""
    res = Validator(_catalogo(_SOLO_SCALARI)).check(_piano({"from_step": 1}))
    assert not res.ok
    assert "requires_one_of" in res.errors[0].detail


def test_un_from_step_fuori_range_non_diventa_una_scusa() -> None:
    """`from_step=0` è già un errore di limiti: non deve anche far passare il
    disgiuntivo, altrimenti un piano rotto ne uscirebbe con un errore in meno."""
    res = Validator(_catalogo()).check(_piano({"from_step": 0}))
    codici = {e.code for e in res.errors}
    assert "from_step_invalid" in codici
    assert "invalid_args" in codici


def test_i_gruppi_del_catalogo_reale_sopravvivono_al_piping() -> None:
    """Invariante di politica: nel catalogo vero, ogni disgiuntivo che contiene
    un arg-lista deve essere soddisfacibile da un consumatore piped. È la forma
    generale del difetto: non erano nove casi, era una proprietà mancante."""
    from loader import load_catalog
    validator_probe = Validator([])
    interessati = 0
    for executor in load_catalog():
        schema = getattr(executor, "args_schema", None) or {}
        gruppi = [g for g in (schema.get("requires_one_of") or [])
                  if isinstance(g, list) and g and "from_step" not in g]
        if not gruppi:
            continue
        proprieta = schema.get("properties") or {}
        if not any((proprieta.get(k) or {}).get("type") == "array"
                   for g in gruppi for k in g):
            continue
        interessati += 1
        assert validator_probe._check_args({"from_step": 1}, schema) is None, \
            f"{executor.name}: un consumatore piped viene ancora bocciato"
    assert interessati >= 5, "catalogo inatteso: l'invariante non sta misurando nulla"
