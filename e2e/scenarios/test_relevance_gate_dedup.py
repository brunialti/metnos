"""Regressione: il gate di rilevanza (find_images_indices) deve usare l'INDICE
come identita' della entry, non il `path`.

Bug (3/6/2026): il filtro usava un set di `path` come chiave d'identita'. Con
path duplicati (symlink/copie) o None: (a) una copia sotto-soglia passava il
gate perche' un duplicato sopra-soglia metteva quel path nel set; (b) i punteggi
collassavano all'ultimo per-path (last-wins); (c) le entry senza path
bypassavano il gate in blocco. Fix: filtro + re-keying per indice originale.
Vedi /opt/suprastructure/usecase_filtro_pertinenza_immagini_duplicate.md.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))
sys.path.insert(0, str(_ROOT / "executors" / "find_images_indices"))
import find_images_indices as M  # noqa: E402

_gate = M._apply_relevance_gate


def test_dup_path_below_threshold_not_leaked_and_score_kept():
    # Due entry con lo STESSO path: una sopra-soglia (0.80), una sotto (0.30).
    entries = [{"path": "/a", "id": 0}, {"path": "/a", "id": 1}]
    out_e, out_s = _gate(entries, {0: (0.80, 0.0), 1: (0.30, 0.0)},
                         {0: 8.0, 1: 3.0}, 0.75)
    assert [e["id"] for e in out_e] == [0]      # solo la sopra-soglia
    assert out_s == {0: 8.0}                    # score della 0.80, non last-wins


def test_none_paths_each_gated_independently():
    entries = [{"id": 0}, {"id": 1}, {"id": 2}]  # nessun path -> None
    out_e, _ = _gate(entries, {0: (0.9, 0.0), 1: (0.1, 0.0), 2: (0.1, 0.0)},
                     {0: 9.0, 1: 1.0, 2: 1.0}, 0.5)
    assert [e["id"] for e in out_e] == [0]       # non passano tutte e 3


def test_unique_paths_no_regression():
    entries = [{"path": "/a"}, {"path": "/b"}, {"path": "/c"}]
    out_e, out_s = _gate(entries, {0: (0.9, 0.0), 1: (0.8, 0.0), 2: (0.3, 0.0)},
                         {0: 9, 1: 8, 2: 3}, 0.5)
    assert [e["path"] for e in out_e] == ["/a", "/b"]
    assert out_s == {0: 9, 1: 8}


def test_empty_keep_when_all_below():
    entries = [{"path": "/a"}, {"path": "/b"}]
    out_e, out_s = _gate(entries, {0: (0.1, 0.0), 1: (0.2, 0.0)},
                         {0: 1, 1: 2}, 0.9)
    assert out_e == [] and out_s == {}
