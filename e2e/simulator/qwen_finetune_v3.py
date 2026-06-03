"""qwen_finetune_v3.py — Re-train Qwen FT v3 con dataset frozen+failed extra.

Dataset: training_data_v4.jsonl (886 pair = 870 v3 + 43 da frozen fails - dedup).
Output: /tmp/qwen_ft_metnos_v3/

Setup uguale a v2: 5 epoch, MultipleNegativesRankingLoss, batch 24.
"""
from __future__ import annotations
import json, random, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from qwen_finetune_v2 import OBJ_ANCHORS, split

OBJ19 = list(OBJ_ANCHORS.keys())


def main():
    import torch
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    pairs = []
    for ln in Path("/tmp/training_data_v4.jsonl").read_text().splitlines():
        d = json.loads(ln)
        if d["object"] in OBJ_ANCHORS:
            pairs.append((d["query"], d["object"]))
    print(f"Total pairs: {len(pairs)}")
    train_pairs, eval_pairs = split(pairs)
    print(f"Train: {len(train_pairs)}, Eval: {len(eval_pairs)}")

    print("Loading Qwen3-Embedding-0.6B...")
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")

    examples = [
        InputExample(texts=[q, OBJ_ANCHORS[obj]])
        for q, obj in train_pairs
    ]
    loader = DataLoader(examples, shuffle=True, batch_size=24)
    loss_fn = losses.MultipleNegativesRankingLoss(model)

    print(f"\nTraining 5 epochs, batch=24...")
    t0 = time.time()
    model.fit(
        train_objectives=[(loader, loss_fn)],
        epochs=5,
        warmup_steps=int(0.1 * len(loader) * 5),
        show_progress_bar=True,
        optimizer_params={"lr": 2e-5},
        output_path="/tmp/qwen_ft_metnos_v3",
    )
    print(f"Train time: {time.time()-t0:.0f}s")

    # Eval
    anchor_emb = model.encode(
        [OBJ_ANCHORS[o] for o in OBJ19],
        convert_to_tensor=True, normalize_embeddings=True,
    )
    q_embs = model.encode(
        [q for q,_ in eval_pairs], convert_to_tensor=True, normalize_embeddings=True
    )
    scores = q_embs @ anchor_emb.T
    preds = [OBJ19[i] for i in scores.argmax(dim=1).tolist()]
    correct = sum(1 for (q,exp), p in zip(eval_pairs, preds) if exp == p)
    print(f"\nEval-holdout: {correct}/{len(eval_pairs)} = {100*correct/len(eval_pairs):.1f}%")

    # FROZEN
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
    q_embs = model.encode(
        [q for q,_ in frozen_pairs], convert_to_tensor=True, normalize_embeddings=True
    )
    scores = q_embs @ anchor_emb.T
    preds = [OBJ19[i] for i in scores.argmax(dim=1).tolist()]
    correct = sum(1 for (q,exp), p in zip(frozen_pairs, preds) if exp == p)
    print(f"FROZEN: {correct}/{len(frozen_pairs)} = {100*correct/len(frozen_pairs):.1f}%")


if __name__ == "__main__":
    main()
