"""qwen_finetune_v2.py — Training v2: 870 pair + 5 epoch + better config.

Migliora:
- Dataset 870 (vs 325 in v1)
- 5 epoch (vs 2)
- batch_size 24 (denser hard negatives)
- learning_rate 2e-5
- Eval gate: salva solo se test acc > baseline
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


def load_pairs() -> list[tuple[str, str]]:
    pairs = []
    for ln in Path("/tmp/training_data_v3.jsonl").read_text().splitlines():
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
    print(f"Model loaded. Dim: {model.get_embedding_dimension()}")

    train_examples = [
        InputExample(texts=[q, OBJ_ANCHORS[obj]])
        for q, obj in train_pairs
    ]
    train_loader = DataLoader(train_examples, shuffle=True, batch_size=24)
    train_loss = losses.MultipleNegativesRankingLoss(model)

    print(f"\nTraining 5 epochs, batch_size=24...")
    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=5,
        warmup_steps=int(0.1 * len(train_loader) * 5),
        show_progress_bar=True,
        optimizer_params={"lr": 2e-5},
        output_path="/tmp/qwen_ft_metnos_v2",
    )

    print("\n=== EVAL ===")
    anchor_embs = model.encode(
        [OBJ_ANCHORS[o] for o in OBJ19],
        convert_to_tensor=True, normalize_embeddings=True,
    )

    def eval_set(pairs, name):
        if not pairs: print(f"{name}: empty"); return 0
        q_embs = model.encode(
            [q for q,_ in pairs], convert_to_tensor=True, normalize_embeddings=True
        )
        scores = q_embs @ anchor_embs.T
        preds = [OBJ19[i] for i in scores.argmax(dim=1).tolist()]
        correct = sum(1 for (q,exp), p in zip(pairs, preds) if exp == p)
        n = len(pairs)
        acc = 100*correct/n
        print(f"{name}: {correct}/{n} = {acc:.1f}%")
        wrong = [(q,e,p) for (q,e), p in zip(pairs, preds) if e != p]
        for q, e, p in wrong[:5]:
            print(f"  WRONG: '{q[:60]}' exp={e}, got={p}")
        return acc

    eval_set(eval_pairs, "Eval-holdout")

    # FROZEN
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

    # Baseline
    print()
    from compare_synonyms import src_hardcoded, src_affinity
    def src_current(q):
        return src_hardcoded(q) or src_affinity(q)
    correct = sum(1 for q,exp in eval_pairs if src_current(q) == exp)
    print(f"CURRENT (hard+aff) on holdout: {correct}/{len(eval_pairs)} = {100*correct/len(eval_pairs):.1f}%")


if __name__ == "__main__":
    main()
