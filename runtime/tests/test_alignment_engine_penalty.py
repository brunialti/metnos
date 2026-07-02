"""alignment_engine v1.4 (2/7/2026): fit NEGATIVO = penalità senza gate.

Il judge può dichiarare che una proposta LAVORA CONTRO un telos (frontier a
pagamento vs t.parsimonia): il danno sottrae SEMPRE (niente gate), il
beneficio resta gated come in v1.3.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alignment_engine import FitEstimate, compose  # noqa: E402
from telos_loader import Telos  # noqa: E402


def _t(tid, w, thr):
    return Telos(id=tid, phrase=tid, weight=w, activation_threshold=thr,
                 notes="")


def test_negative_fit_subtracts_without_gate():
    telos = [_t("t.tempo", 0.5, 0.3), _t("t.parsimonia", 0.5, 0.35)]
    base = compose([FitEstimate("t.tempo", 0.8, "")], telos)
    hit = compose([FitEstimate("t.tempo", 0.8, ""),
                   FitEstimate("t.parsimonia", -0.6, "")], telos)
    assert hit < base                       # il conflitto costa
    # la penalità NON passa dal gate: anche |fit| sotto soglia sottrae
    hit_small = compose([FitEstimate("t.tempo", 0.8, ""),
                         FitEstimate("t.parsimonia", -0.1, "")], telos)
    assert hit_small < base


def test_positive_only_unchanged_vs_v13():
    # regressione: senza fit negativi la formula resta la v1.3
    telos = [_t("t.tempo", 0.5, 0.3), _t("t.ordine", 0.5, 0.4)]
    fits = [FitEstimate("t.tempo", 0.8, ""), FitEstimate("t.ordine", 0.5, "")]
    ea = compose(fits, telos)
    top = 0.5 * 0.8
    rest = 0.5 * 0.5
    assert abs(ea - (2.0 * top + 0.5 * rest) * 1.0 * 0.8) < 1e-9


def test_all_negative_floors_below_zero_then_score_clamps():
    telos = [_t("t.parsimonia", 1.0, 0.35)]
    ea = compose([FitEstimate("t.parsimonia", -1.0, "")], telos)
    assert ea < 0   # compose può andare sotto zero; score_from_ea clampa a 0
