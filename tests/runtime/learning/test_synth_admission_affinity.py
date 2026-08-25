"""Test del Layer 2 di synth admission: affinity overlap guard
(ADR 0114, 8/5/2026 sera).

Bug live 8/5/2026: synth `find_texts` (5/8 termini affinity overlap con
handcrafted `find_urls`) ha hijackato il routing PLANNER per query web
("cerca", "search", "find", "web", "google"). Description NON allineata
al code, 7 chiamate ritornavano 0 entries.

Layer 2 = guard al catalog load: synth con Jaccard >= 0.5 verso UN
handcrafted (o un altro synth con identita' strutturale prioritaria) viene rejected. Audit log
JSONL in `~/.local/share/metnos/synth_audit/affinity_rejected.jsonl`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _make_executor(d: Path, name: str, *, affinity: list[str], mtime: float | None = None):
    """Crea una dir minimale con manifest che dichiara affinity. Senza firma:
    si testa solo il flusso affinity, non la verify completa."""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text("def invoke(args):\n    return {'ok': True}\n", encoding="utf-8")
    affinity_lit = ", ".join(f'"{a}"' for a in affinity)
    manifest = (
        'manifest_format = "1.0"\n'
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        f'[description]\nit = "stub {name}"\nen = "stub {name}"\n'
        f'affinity = [{affinity_lit}]\n'
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
    if mtime is not None:
        import os as _os
        _os.utime(d / "manifest.toml", (mtime, mtime))


def _make_catalog_with(executors_by_name):
    """Costruisce un Catalog in-memory popolato. Bypass tomllib parse."""
    import loader
    catalog = loader.Catalog()
    for name, info in executors_by_name.items():
        ex = loader.Executor(
            name=name,
            version="0.1.0",
            description=f"stub {name}",
            affinity=list(info["affinity"]),
            args_schema={},
            capabilities=[],
            tests=[],
            code_path=info.get("manifest_path"),
            manifest_path=info["manifest_path"],
            signed_by="(test)",
            lifecycle="active",
        )
        catalog.executors[name] = ex
    return catalog


class TestHandcraftedNeverRejectedSelf:
    """Un handcrafted non puo' essere rejected nemmeno se per caso ha affinity
    sovrapposte a un altro handcrafted: la guardia agisce SOLO su synth."""

    def test_handcrafted_pair_with_overlap_both_kept(self, tmp_path, monkeypatch):
        import loader
        # Due handcrafted con affinity 100% overlap → nessuno rifiutato.
        h1 = tmp_path / "find_urls"; _make_executor(h1, "find_urls", affinity=["web", "search", "find"])
        h2 = tmp_path / "find_files"; _make_executor(h2, "find_files", affinity=["web", "search", "find"])
        catalog = _make_catalog_with({
            "find_urls":  {"affinity": ["web", "search", "find"], "manifest_path": h1 / "manifest.toml"},
            "find_files": {"affinity": ["web", "search", "find"], "manifest_path": h2 / "manifest.toml"},
        })
        # Override SYNTHESIZED_EXECUTORS_DIR con qualcosa fuori scope, cosi'
        # nessuno dei due risulta synth.
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", tmp_path / "_unused_synth_dir")
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        assert rejected == []
        assert "find_urls" in catalog.executors
        assert "find_files" in catalog.executors


class TestSynthHighOverlapRejected:
    """Synth con jaccard >= 0.5 verso un handcrafted = rejected."""

    def test_synth_find_texts_rejected_against_find_urls(self, tmp_path, monkeypatch):
        import loader
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        h_root = tmp_path / "handcrafted"; h_root.mkdir()
        _make_executor(h_root / "find_urls", "find_urls",
                       affinity=["cerca", "search", "find", "web", "google", "url", "internet", "online"])
        _make_executor(synth_root / "find_texts", "find_texts",
                       affinity=["cerca", "search", "find", "web", "google", "testo"])
        # Setup catalog: find_urls handcrafted, find_texts synth.
        catalog = _make_catalog_with({
            "find_urls":  {"affinity": ["cerca", "search", "find", "web", "google", "url", "internet", "online"],
                          "manifest_path": h_root / "find_urls" / "manifest.toml"},
            "find_texts": {"affinity": ["cerca", "search", "find", "web", "google", "testo"],
                          "manifest_path": synth_root / "find_texts" / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        # Jaccard: |∩| / |∪| = 5 / 9 = 0.555... >= 0.5 → rejected.
        assert len(rejected) == 1
        assert rejected[0]["name"] == "find_texts"
        assert rejected[0]["overlapping_with"] == "find_urls"
        assert rejected[0]["jaccard"] >= 0.5
        assert "search" in rejected[0]["shared_terms"]
        # Catalog: find_texts rimosso, find_urls preservato.
        assert "find_texts" not in catalog.executors
        assert "find_urls" in catalog.executors
        # Catalog.rejected: ha entry con motivo affinity_overlap.
        assert any("affinity_overlap" in r[1] for r in catalog.rejected)


class TestSynthLowOverlapAdmitted:
    """Synth con jaccard < 0.5 = admitted senza problemi."""

    def test_synth_with_disjoint_affinity_admitted(self, tmp_path, monkeypatch):
        import loader
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        h_root = tmp_path / "h"; h_root.mkdir()
        _make_executor(h_root / "find_urls", "find_urls",
                       affinity=["web", "url", "http", "search"])
        _make_executor(synth_root / "compute_signal", "compute_signal",
                       affinity=["audio", "fft", "spectrum", "signal"])
        catalog = _make_catalog_with({
            "find_urls":      {"affinity": ["web", "url", "http", "search"],
                              "manifest_path": h_root / "find_urls" / "manifest.toml"},
            "compute_signal": {"affinity": ["audio", "fft", "spectrum", "signal"],
                              "manifest_path": synth_root / "compute_signal" / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        assert rejected == []
        assert "compute_signal" in catalog.executors


class TestJaccardThresholdBoundary:
    """Esatto 0.5 → rejected (bordo inclusivo). 0.49 → admitted."""

    def test_exact_threshold_rejected(self, tmp_path, monkeypatch):
        import loader
        # 2 termini comuni su 4 unione = 0.5 → rejected.
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        h_root = tmp_path / "h"; h_root.mkdir()
        _make_executor(h_root / "find_urls", "find_urls",
                       affinity=["a", "b", "c"])
        _make_executor(synth_root / "find_x", "find_x",
                       affinity=["a", "b", "d"])
        # |∩|={a,b}=2, |∪|={a,b,c,d}=4, J=0.5
        catalog = _make_catalog_with({
            "find_urls": {"affinity": ["a", "b", "c"],
                         "manifest_path": h_root / "find_urls" / "manifest.toml"},
            "find_x":    {"affinity": ["a", "b", "d"],
                         "manifest_path": synth_root / "find_x" / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        assert len(rejected) == 1
        assert rejected[0]["jaccard"] == 0.5

    def test_below_threshold_admitted(self, tmp_path, monkeypatch):
        import loader
        # 2 termini comuni su 5 = 0.4 → admitted.
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        h_root = tmp_path / "h"; h_root.mkdir()
        _make_executor(h_root / "find_urls", "find_urls",
                       affinity=["a", "b", "c", "d"])
        _make_executor(synth_root / "find_x", "find_x",
                       affinity=["a", "b", "e"])
        # |∩|=2, |∪|=5, J=0.4
        catalog = _make_catalog_with({
            "find_urls": {"affinity": ["a", "b", "c", "d"],
                         "manifest_path": h_root / "find_urls" / "manifest.toml"},
            "find_x":    {"affinity": ["a", "b", "e"],
                         "manifest_path": synth_root / "find_x" / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        assert rejected == []
        assert "find_x" in catalog.executors


class TestAuditLogAppended:
    """Ogni rejection scrive una riga JSON in
    `~/.local/share/metnos/synth_audit/affinity_rejected.jsonl`."""

    def test_audit_log_has_entry(self, tmp_path, monkeypatch):
        import loader
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        h_root = tmp_path / "h"; h_root.mkdir()
        audit_dir = tmp_path / "audit"
        _make_executor(h_root / "find_urls", "find_urls",
                       affinity=["x", "y", "z"])
        _make_executor(synth_root / "find_evil", "find_evil",
                       affinity=["x", "y", "z"])  # 100% overlap
        catalog = _make_catalog_with({
            "find_urls": {"affinity": ["x", "y", "z"],
                         "manifest_path": h_root / "find_urls" / "manifest.toml"},
            "find_evil": {"affinity": ["x", "y", "z"],
                         "manifest_path": synth_root / "find_evil" / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", audit_dir)
        loader._check_affinity_overlap(catalog)
        log_path = audit_dir / "affinity_rejected.jsonl"
        assert log_path.exists(), "audit log file deve esistere"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["name"] == "find_evil"
        assert entry["overlapping_with"] == "find_urls"
        assert entry["jaccard"] == 1.0
        assert "ts" in entry
        assert set(entry["shared_terms"]) == {"x", "y", "z"}


class TestStableSynthIdentityWins:
    """Tra due synth vince l'identita' strutturale, non un mtime mutabile."""

    def test_lexical_identity_is_stable_when_mtimes_say_the_opposite(
        self, tmp_path, monkeypatch,
    ):
        import loader
        synth_root = tmp_path / "synth"; synth_root.mkdir()
        first_dir = synth_root / "find_a"
        _make_executor(first_dir, "find_a", affinity=["k1", "k2", "k3"], mtime=2000.0)
        second_dir = synth_root / "find_b"
        _make_executor(second_dir, "find_b", affinity=["k1", "k2", "k3"], mtime=1000.0)
        catalog = _make_catalog_with({
            "find_a": {"affinity": ["k1", "k2", "k3"],
                       "manifest_path": first_dir / "manifest.toml"},
            "find_b": {"affinity": ["k1", "k2", "k3"],
                       "manifest_path": second_dir / "manifest.toml"},
        })
        monkeypatch.setattr(loader, "SYNTHESIZED_EXECUTORS_DIR", synth_root)
        monkeypatch.setattr(loader, "_AFFINITY_AUDIT_DIR", tmp_path / "audit")
        rejected = loader._check_affinity_overlap(catalog)
        # find_a vince per identita', anche se il suo file ha mtime maggiore.
        assert len(rejected) == 1
        assert rejected[0]["name"] == "find_b"
        assert "find_a" in catalog.executors
        assert "find_b" not in catalog.executors
