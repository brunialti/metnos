"""compare_all.py — Comparativa COMPLETA tutti i synonym engines sul medesimo set.

Engines testati:
1. Hardcoded only (vocab.py _OBJECT_SYNONYMS_*)
2. Affinity only (manifest-derived)
3. Hardcoded + Affinity (CURRENT production)
4. BGE-M3 zero-shot (sentence)
5. Qwen3-Embedding-0.6B zero-shot
6. Qwen3-Embedding-0.6B fine-tuned v1 (325 pair, 2 epoch)
7. Qwen3-Embedding-0.6B fine-tuned v2 (870 pair, 5 epoch)

Test set: HOLDOUT (65 pair, never seen) + FROZEN (415 pair).
Metriche: accuracy, precision, recall, F1, latency, disk, manutenzione.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/opt/metnos')

from qwen_finetune import load_pairs as _load_v1
from qwen_finetune import split as _split

OBJ19 = ["files","dirs","packages","messages","events","contacts","places",
         "processes","urls","numbers","images","signatures","texts",
         "proposals","persons","tasks","inputs","credentials","entries"]

OBJ_ANCHORS = {
    "files": "file documento testo /tmp/data.txt pdf csv leggi/scrivi file",
    "dirs": "cartella directory folder sottocartella alberatura",
    "packages": "pacchetto installato apt deb python pip software",
    "messages": "email mail messaggio posta gmail allegato inbox mittente",
    "events": "evento appuntamento calendario riunione agenda compleanno",
    "contacts": "contatto rubrica indirizzo email salvato",
    "places": "luogo posto farmacia ristorante hotel vicino geografico",
    "processes": "processo ram cpu memoria disco uptime systemctl",
    "urls": "url sito web pagina google https link risorsa",
    "numbers": "numero calcolo data ora tempo timezone matematica",
    "images": "foto immagine pic jpg foto scattate album fotografico",
    "signatures": "hash firma checksum md5 sha256 digest crittografico",
    "texts": "testo paragrafo riga linea estratto contenuto testuale",
    "proposals": "proposta synth introvertiva pending accettata rifiutata",
    "persons": "persona ospite chi-è guest profilo identità individuo",
    "tasks": "task promemoria timer scheduler ricordami ricorrenza",
    "inputs": "input form dialog valore richiesta utente",
    "credentials": "password credenziali account oauth login chiave",
    "entries": "voce elemento lista record entry oggetto interno",
}


def load_holdout():
    pairs = _load_v1()
    _, eval_pairs = _split(pairs)
    return eval_pairs


def load_frozen():
    frozen = json.loads(Path("test_set_FROZEN.json").read_text())
    pairs = []
    for r in frozen:
        q = r.get("query","")
        path = r.get("expected_path",[])
        if not q or not path: continue
        first = path[0]
        if "_" not in first: continue
        obj = first.split("_",1)[1].split("_",1)[0]
        if obj in OBJ19:
            pairs.append((q, obj))
    return pairs


def measure(name, fn, pairs):
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
    return {
        "name": name,
        "acc": 100*correct/n if n else 0,
        "cov": 100*(correct+wrong)/n if n else 0,
        "prec": 100*correct/(correct+wrong) if (correct+wrong)>0 else 0,
        "ms_per_q": elapsed*1000/n if n else 0,
    }


def main():
    holdout = load_holdout()
    frozen = load_frozen()
    print(f"Holdout: {len(holdout)} pair, FROZEN: {len(frozen)} pair")
    print()

    rows = []

    # 1-3. Lex engines
    from compare_synonyms import src_hardcoded, src_affinity
    def src_current(q): return src_hardcoded(q) or src_affinity(q)
    rows.append(("Hardcoded only", src_hardcoded, "0 KB", "MANUTENZIONE manuale"))
    rows.append(("Affinity only", src_affinity, "<1 MB", "Auto da manifest §7.3"))
    rows.append(("CURRENT (Hard+Aff)", src_current, "<1 MB", "Solo hardcoded manutenzione"))

    # 4. BGE-M3 zero-shot
    try:
        from compare_synonyms import src_embedding
        OBJ_LIST = OBJ19
        def src_bge_zs(q):
            r = src_embedding(q, OBJ_LIST)
            if r and r[1] >= 0.5: return r[0]
            return None
        rows.append(("BGE-M3 zero-shot", src_bge_zs, "560 MB", "Pre-trained, nessuna"))
    except Exception as e:
        print(f"BGE-M3 skip: {e}")

    # 5. Qwen3-Emb zero-shot
    print("Loading Qwen3-Embedding-0.6B (zero-shot)...")
    from sentence_transformers import SentenceTransformer
    try:
        zs_model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
        anchor_emb_zs = zs_model.encode(
            [OBJ_ANCHORS[o] for o in OBJ19],
            convert_to_tensor=True, normalize_embeddings=True,
        )
        def src_qwen_zs(q):
            emb = zs_model.encode(q, convert_to_tensor=True, normalize_embeddings=True)
            scores = anchor_emb_zs @ emb
            idx = int(scores.argmax())
            score = float(scores[idx])
            if score >= 0.3: return OBJ19[idx]
            return None
        rows.append(("Qwen3-Emb zero-shot", src_qwen_zs, "1.2 GB", "Nessuna training"))
    except Exception as e:
        print(f"Qwen zero-shot skip: {e}")

    # 6. Qwen3-Emb fine-tuned v1
    if Path("/tmp/qwen_ft_metnos").exists():
        print("Loading Qwen3-FT v1...")
        ft1 = SentenceTransformer("/tmp/qwen_ft_metnos")
        anchor_emb_ft1 = ft1.encode(
            [OBJ_ANCHORS[o] for o in OBJ19],
            convert_to_tensor=True, normalize_embeddings=True,
        )
        def src_qwen_ft1(q):
            emb = ft1.encode(q, convert_to_tensor=True, normalize_embeddings=True)
            scores = anchor_emb_ft1 @ emb
            idx = int(scores.argmax())
            return OBJ19[idx]
        rows.append(("Qwen3-FT v1 (325, 2ep)", src_qwen_ft1, "1.2 GB", "Auto re-train weekly"))

    # 7. Qwen3-Emb fine-tuned v2 (if done)
    if Path("/tmp/qwen_ft_metnos_v2").exists() and any(Path("/tmp/qwen_ft_metnos_v2").iterdir()):
        print("Loading Qwen3-FT v2...")
        ft2 = SentenceTransformer("/tmp/qwen_ft_metnos_v2")
        anchor_emb_ft2 = ft2.encode(
            [OBJ_ANCHORS[o] for o in OBJ19],
            convert_to_tensor=True, normalize_embeddings=True,
        )
        def src_qwen_ft2(q):
            emb = ft2.encode(q, convert_to_tensor=True, normalize_embeddings=True)
            scores = anchor_emb_ft2 @ emb
            idx = int(scores.argmax())
            return OBJ19[idx]
        rows.append(("Qwen3-FT v2 (870, 5ep)", src_qwen_ft2, "1.2 GB", "Auto re-train weekly"))
    else:
        print("Qwen3-FT v2 not yet trained — skipping")

    # Evaluate ALL
    print()
    print(f"{'Engine':<24} {'Holdout':>10} {'FROZEN':>10} {'ms/q':>8} {'Disk':>10} {'Manutenzione':>26}")
    print("-"*94)
    for name, fn, disk, maint in rows:
        m_h = measure(name, fn, holdout)
        m_f = measure(name, fn, frozen)
        print(f"{name:<24} {m_h['acc']:>9.1f}% {m_f['acc']:>9.1f}% {m_h['ms_per_q']:>7.1f} {disk:>10} {maint:>26}")


if __name__ == "__main__":
    main()
