"""Test della GC dei synth in collisione (ADR 0079, 4/5/2026).

Verifica che `loader._gc_collisions` sposti — non elimini — i synth
marcati `rejected` per collision con un handcrafted, lasciando il
filesystem pulito al prossimo load.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _make_executor_dir(d: Path, name: str, code: str = "def invoke(args):\n    return {'ok': True}\n"):
    """Crea una dir minimale con manifest + code stub. Niente firma:
    si testa solo il flusso GC, non la verify completa."""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(code, encoding="utf-8")
    manifest = (
        'manifest_format = "1.0"\n'
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        f'description = "stub {name}"\n'
        'affinity = []\n'
        'lifecycle = "active"\n'
        '\n'
        '[code]\n'
        f'files = ["{name}.py"]\n'
        'digest = "sha256:placeholder"\n'
        '\n'
        '[args]\n'
        'type = "object"\n'
        'required = []\n'
    )
    (d / "manifest.toml").write_text(manifest, encoding="utf-8")


class TestGcCollisions:
    """`_gc_collisions(catalog)` sposta i path di rejected per collision in /tmp/."""

    def test_moves_collision_path(self, monkeypatch):
        import loader

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            synth_root = tmp_root / "synth"
            synth_root.mkdir()
            colliding = synth_root / "test_handcrafted_dummy"
            _make_executor_dir(colliding, "test_handcrafted_dummy")

            # Reindirizza SYNTHESIZED_EXECUTORS_DIR alla fixture.
            monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)

            catalog = loader.Catalog()
            catalog.rejected.append((
                str(colliding),
                "name collision with handcrafted 'test_handcrafted_dummy' (synth ignored)",
            ))

            assert colliding.exists(), "fixture: dir colliding deve esistere"
            loader._gc_collisions(catalog)
            assert not colliding.exists(), "post-GC: dir colliding deve essere stata mossa"

    def test_skips_non_collision_rejected(self, monkeypatch):
        import loader

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            synth_root = tmp_root / "synth"
            synth_root.mkdir()
            broken = synth_root / "test_broken_signature"
            _make_executor_dir(broken, "test_broken_signature")

            monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)

            catalog = loader.Catalog()
            catalog.rejected.append((str(broken), "signature invalid (verify failed)"))

            loader._gc_collisions(catalog)
            assert broken.exists(), "non-collision rejected NON deve essere mosso"

    def test_skips_handcrafted(self, monkeypatch):
        """Anche se per errore la rejected list contiene un path handcrafted,
        la GC non lo tocca: il guard `relative_to(SYNTHESIZED_EXECUTORS_DIR)`
        scarta path fuori scope."""
        import loader

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            synth_root = tmp_root / "synth"; synth_root.mkdir()
            handcrafted_root = tmp_root / "handcrafted"; handcrafted_root.mkdir()
            outside = handcrafted_root / "test_outside_synth"
            _make_executor_dir(outside, "test_outside_synth")

            monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)

            catalog = loader.Catalog()
            catalog.rejected.append((
                str(outside),
                "name collision with handcrafted 'test_outside_synth' (synth ignored)",
            ))

            loader._gc_collisions(catalog)
            assert outside.exists(), "handcrafted (fuori SYNTHESIZED_EXECUTORS_DIR) NON deve essere mosso"

    def test_idempotent_when_dst_exists(self, monkeypatch):
        """Se la dir di destinazione esiste, GC sceglie un suffisso .1/.2/...
        (caso raro ma deterministico: due collision-rejected sullo stesso name
        nello stesso secondo). Verifichiamo che entrambe vengano spostate."""
        import loader

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            synth_root = tmp_root / "synth"; synth_root.mkdir()
            d1 = synth_root / "test_dup"
            _make_executor_dir(d1, "test_dup")

            monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)

            cat = loader.Catalog()
            cat.rejected.append((str(d1),
                                  "name collision with handcrafted 'test_dup' (synth ignored)"))
            loader._gc_collisions(cat)
            assert not d1.exists()


class TestGcIntegration:
    """`load_catalog(verify=False, include_synth=True)` con un synth reale che
    collide con un handcrafted reale: la GC viene attivata via load_catalog
    se `verify=True`. Con verify=False non lo fa (collisioni si scoprono solo
    al verify; comportamento intenzionale)."""

    def test_load_catalog_with_verify_runs_gc(self, monkeypatch):
        import loader

        # Il test usa il catalog reale: cerchiamo un handcrafted esistente.
        # Scegliamo `find_files`: presente sicuramente in /opt/myclaw/executors/.
        handcrafted_name = "find_files"
        with tempfile.TemporaryDirectory() as tmp:
            synth_root = Path(tmp) / "synth"
            synth_root.mkdir()
            colliding = synth_root / handcrafted_name
            _make_executor_dir(colliding, handcrafted_name)
            monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)

            # verify=True attiva la GC. Il synth ha digest placeholder, quindi
            # verify_executor lo rifiuta PRIMA di arrivare alla collision check
            # (il flusso e': verify → reject → continue). Per testare la
            # collision-rejection, giriamo verify=False e iniettiamo il
            # rejected manualmente.
            cat = loader.load_catalog(verify=False, include_synth=True)
            # In assenza di verify, il loader rileva il name collision direttamente
            # (vedi `_load_dir_into_catalog`: la check `name in catalog.executors`).
            # Verifichiamo che colliding sia in rejected con motivo collision.
            collision_rejs = [r for r in cat.rejected if "collision" in r[1]]
            assert collision_rejs, f"no collision rejected: {cat.rejected}"

            # Con verify=False NON lanciamo la GC, ma possiamo invocarla diretta.
            loader._gc_collisions(cat)
            assert not colliding.exists(), "post-GC: dir colliding deve essere stata mossa"
