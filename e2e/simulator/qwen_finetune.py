"""qwen_finetune.py — Fine-tune Qwen3-Embedding-0.6B per query→canonical_object mapping.

Approccio:
1. Load Qwen3-Embedding-0.6B
2. Training set: (query, canonical_object_anchor) pair da real_queries.json
3. MultipleNegativesRankingLoss (contrastive): per ogni query, gli altri obj sono negativi
4. Eval su FROZEN test set vs CURRENT (hardcoded+affinity)

Output:
- /tmp/qwen_ft_metnos/ : modello salvato
- Stampa metriche coverage + precision + recall + F1
"""
from __future__ import annotations
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OBJ19 = ["files","dirs","packages","messages","events","contacts","places",
         "processes","urls","numbers","images","signatures","texts",
         "proposals","persons","tasks","inputs","credentials","entries"]

# Canonical object "anchors" (testual prototype per ogni object)
OBJ_ANCHORS = {
    "files": "file documento testo /tmp/data.txt pdf csv",
    "dirs": "cartella directory folder sottocartella",
    "packages": "pacchetto installato apt deb python pip",
    "messages": "email mail messaggio posta gmail allegato",
    "events": "evento appuntamento calendario riunione agenda",
    "contacts": "contatto rubrica indirizzo",
    "places": "luogo posto farmacia ristorante hotel vicino",
    "processes": "processo ram cpu memoria disco uptime",
    "urls": "url sito web pagina google https link",
    "numbers": "numero calcolo data ora tempo",
    "images": "foto immagine pic jpg foto scattate",
    "signatures": "hash firma checksum md5 sha256",
    "texts": "testo paragrafo riga linea estratto",
    "proposals": "proposta synth introvertiva pending",
    "persons": "persona ospite chi-è guest profilo",
    "tasks": "task promemoria timer scheduler ricordami",
    "inputs": "input form dialog valore",
    "credentials": "password credenziali account oauth login",
    "entries": "voce elemento lista record",
}


def load_pairs() -> list[tuple[str, str]]:
    pairs = []
    for ln in Path("/tmp/training_data.jsonl").read_text().splitlines():
        d = json.loads(ln)
        if d["object"] in OBJ_ANCHORS:
            pairs.append((d["query"], d["object"]))
    return pairs


def split(pairs: list, train_ratio=0.8) -> tuple[list, list]:
    random.seed(42)
    shuffled = pairs[:]
    random.shuffle(shuffled)
    n = int(len(shuffled) * train_ratio)
    return shuffled[:n], shuffled[n:]


def main():
    import torch
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    pairs = load_pairs()
    print(f"Total pairs: {len(pairs)}")
    train_pairs, eval_pairs = split(pairs)
    print(f"Train: {len(train_pairs)}, Eval: {len(eval_pairs)}")

    print("Loading Qwen3-Embedding-0.6B...")
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
    print(f"Model loaded. Dim: {model.get_sentence_embedding_dimension()}")

    # Training examples: (query, anchor) positive pair
    train_examples = [
        InputExample(texts=[q, OBJ_ANCHORS[obj]])
        for q, obj in train_pairs
    ]
    train_loader = DataLoader(train_examples, shuffle=True, batch_size=16)
    train_loss = losses.MultipleNegativesRankingLoss(model)

    print("Training 2 epochs...")
    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=2,
        warmup_steps=10,
        show_progress_bar=True,
        output_path="/tmp/qwen_ft_metnos",
    )

    print("\n=== EVAL ===")
    # Embed anchors
    anchor_embs = model.encode(
        [OBJ_ANCHORS[o] for o in OBJ19],
        convert_to_tensor=True, normalize_embeddings=True,
    )

    # Eval on held-out + FROZEN
    def eval_set(pairs, name):
        if not pairs:
            print(f"{name}: empty"); return
        q_embs = model.encode(
            [q for q,_ in pairs], convert_to_tensor=True, normalize_embeddings=True
        )
        scores = q_embs @ anchor_embs.T
        preds = [OBJ19[i] for i in scores.argmax(dim=1).tolist()]
        correct = sum(1 for (q,exp), p in zip(pairs, preds) if exp == p)
        n = len(pairs)
        print(f"{name}: {correct}/{n} = {100*correct/n:.1f}%")
        # show 5 wrong examples
        wrong = [(q,e,p) for (q,e), p in zip(pairs, preds) if e != p]
        for q, e, p in wrong[:5]:
            print(f"  WRONG: '{q[:50]}' exp={e}, got={p}")

    eval_set(eval_pairs, "Eval-holdout")

    # Load FROZEN test set queries
    try:
        frozen = json.loads(Path("test_set_FROZEN.json").read_text())
        frozen_pairs = []
        for r in frozen:
            q = r.get("query","")
            path = r.get("expected_path",[])
            if not q or not path: continue
            first = path[0]
            if "_" not in first: continue
            obj = first.split("_",1)[1].split("_",1)[0]
            if obj in OBJ_ANCHORS:
                frozen_pairs.append((q, obj))
        eval_set(frozen_pairs, "FROZEN test set")
    except Exception as e:
        print(f"FROZEN load fail: {e}")

    # Comparison baseline (current hardcoded+affinity)
    print("\n=== BASELINE (CURRENT) on same eval ===")
    from compare_synonyms import src_hardcoded, src_affinity
    def src_current(q):
        r = src_hardcoded(q)
        if r: return r
        return src_affinity(q)
    correct = 0
    for q, exp in eval_pairs:
        if src_current(q) == exp:
            correct += 1
    print(f"CURRENT on holdout: {correct}/{len(eval_pairs)} = {100*correct/len(eval_pairs):.1f}%")


if __name__ == "__main__":
    main()
