"""fasttext_compare.py — Comparativa FastText vs Affinity vs FastText+Affinity vs CURRENT.

FastText IT model: facebook/fasttext-it-vectors (~4 GB, subword-aware).
Auto-fail se modello non disponibile, stampa warning chiaro.

Coverage + quality (precision/recall/F1) su test_set production (381 pair).
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/opt/metnos')

from compare_synonyms import TEST_PAIRS, src_hardcoded, src_affinity, _key_token


OBJ19 = ["files","dirs","packages","messages","events","contacts","places",
         "processes","urls","numbers","images","signatures","texts",
         "proposals","persons","tasks","inputs","credentials","entries"]


_FT_MODEL = None
_FT_OBJ_EMB = None


def _load_fasttext():
    """Load FastText IT via fasttext library or gensim."""
    global _FT_MODEL
    if _FT_MODEL is not None:
        return _FT_MODEL
    # Try direct fasttext library first
    cache = Path("~/.cache/huggingface/hub/models--facebook--fasttext-it-vectors").expanduser()
    bin_files = list(cache.rglob("*.bin"))
    bin_files = [f for f in bin_files if not str(f).endswith(".incomplete")]
    if not bin_files:
        return None
    bin_path = bin_files[0]
    try:
        import fasttext  # type: ignore
        _FT_MODEL = fasttext.load_model(str(bin_path))
        return _FT_MODEL
    except ImportError:
        pass
    try:
        from gensim.models.fasttext import load_facebook_model  # type: ignore
        _FT_MODEL = load_facebook_model(str(bin_path))
        return _FT_MODEL
    except Exception:
        pass
    return None


def _ft_obj_emb():
    """Embed 19 canonical objects (and IT synonyms)."""
    global _FT_OBJ_EMB
    if _FT_OBJ_EMB is not None:
        return _FT_OBJ_EMB
    model = _load_fasttext()
    if model is None:
        return None
    import numpy as np
    # FastText subword: any word works; embed canonical (EN) + IT synonym for context
    obj_words = {
        "files": ["file", "documenti"],
        "dirs": ["cartella", "directory"],
        "packages": ["pacchetto", "package"],
        "messages": ["mail", "messaggio"],
        "events": ["evento", "appuntamento", "agenda"],
        "contacts": ["contatto", "rubrica"],
        "places": ["luogo", "posto", "farmacia"],
        "processes": ["processo", "ram", "cpu"],
        "urls": ["url", "sito", "web"],
        "numbers": ["numero", "calcolo"],
        "images": ["foto", "immagine"],
        "signatures": ["hash", "firma"],
        "texts": ["testo"],
        "proposals": ["proposta"],
        "persons": ["persona", "ospite"],
        "tasks": ["task", "promemoria", "timer"],
        "inputs": ["input"],
        "credentials": ["credenziali", "password"],
        "entries": ["voce"],
    }
    embeddings = {}
    # FastText API: model.get_word_vector(word) or model[word]
    try:
        get_vec = model.get_word_vector
    except AttributeError:
        get_vec = lambda w: model.wv[w]
    for canon, syns in obj_words.items():
        vecs = []
        for w in syns:
            try:
                vecs.append(get_vec(w))
            except Exception:
                pass
        if vecs:
            avg = np.mean(vecs, axis=0)
            avg = avg / (np.linalg.norm(avg) + 1e-9)
            embeddings[canon] = avg
    _FT_OBJ_EMB = embeddings
    return _FT_OBJ_EMB


def src_fasttext(query: str, threshold: float = 0.4) -> str | None:
    """Embed query word-by-word avg, cosine vs 19 canonical IT-rich embeddings."""
    model = _load_fasttext()
    obj_emb = _ft_obj_emb()
    if model is None or obj_emb is None:
        return None
    import numpy as np
    tokens = _key_token(query)
    if not tokens:
        return None
    try:
        get_vec = model.get_word_vector
    except AttributeError:
        get_vec = lambda w: model.wv[w]
    vecs = []
    for t in tokens:
        try:
            vecs.append(get_vec(t))
        except Exception:
            pass
    if not vecs:
        return None
    q_emb = np.mean(vecs, axis=0)
    q_emb = q_emb / (np.linalg.norm(q_emb) + 1e-9)
    best_obj = None
    best_score = -1.0
    for obj, emb in obj_emb.items():
        s = float(q_emb @ emb)
        if s > best_score:
            best_score = s
            best_obj = obj
    if best_score >= threshold:
        return best_obj
    return None


def measure(name: str, fn, pairs: list) -> dict:
    t0 = time.time()
    correct = silent = wrong = 0
    for q, exp in pairs:
        r = fn(q)
        if r == exp:
            correct += 1
        elif r is None:
            silent += 1
        else:
            wrong += 1
    elapsed = time.time() - t0
    n = len(pairs)
    cov = 100*(correct+wrong)/n
    prec = 100*correct/(correct+wrong) if (correct+wrong)>0 else 0
    rec = 100*correct/n
    f1 = 2*prec*rec/(prec+rec) if (prec+rec)>0 else 0
    return {"name": name, "coverage": cov, "precision": prec, "recall": rec,
            "f1": f1, "ms_per_q": elapsed*1000/n,
            "correct": correct, "silent": silent, "wrong": wrong}


def main():
    print(f"Test set: {len(TEST_PAIRS)} auto pairs (production)")
    print()
    # FT availability check
    model = _load_fasttext()
    if model is None:
        print("⚠ FastText IT model NOT loaded (no .bin found or fasttext/gensim missing)")
        print(f"  Cache: ~/.cache/huggingface/hub/models--facebook--fasttext-it-vectors/")
        print()
    else:
        print("✓ FastText IT model loaded")
        print()

    # Define variants
    def src_ft_only(q): return src_fasttext(q, threshold=0.4)
    def src_aff_ft(q):
        r = src_affinity(q)
        if r: return r
        return src_fasttext(q, threshold=0.4)
    def src_ft_aff(q):
        r = src_fasttext(q, threshold=0.55)  # higher confidence first
        if r: return r
        return src_affinity(q)
    def src_current(q):
        # CURRENT production: hardcoded + affinity
        r = src_hardcoded(q)
        if r: return r
        return src_affinity(q)

    variants = [
        ("Affinity only", src_affinity),
        ("CURRENT (Hard+Aff)", src_current),
    ]
    if model is not None:
        variants.extend([
            ("FastText only (.4)", src_ft_only),
            ("Aff→FT (recall)", src_aff_ft),
            ("FT→Aff (.55)", src_ft_aff),
        ])

    print(f"{'Variant':<24} {'Cover':>6} {'Prec':>6} {'Rec':>6} {'F1':>6} {'ms/q':>7}")
    print("-"*64)
    results = []
    for name, fn in variants:
        m = measure(name, fn, TEST_PAIRS)
        results.append(m)
        print(f"{m['name']:<24} {m['coverage']:>5.1f}% {m['precision']:>5.1f}% {m['recall']:>5.1f}% {m['f1']:>5.1f}% {m['ms_per_q']:>6.1f}")


if __name__ == "__main__":
    main()
