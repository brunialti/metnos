"""train.py — Fine-tune Qwen3-Embedding-0.6B per intent classification Metnos.

Sorgenti dataset:
1. Seed bundled: `runtime/intent_classifier/seed_pairs.jsonl` (~870 pair).
2. Turn log: `~/.local/share/metnos/turns/*.jsonl` ultimi 30 giorni.
3. Augmented synth: opzionale (off di default per evitare LLM call al boot).

Output:
- `~/.local/share/metnos/intent_classifier/v<N>/` (new version LWW).
- `~/.local/share/metnos/intent_classifier/retrain_audit.jsonl` (append).

Promotion gate:
- Min eval accuracy: 70% (env `METNOS_INTENT_MIN_ACC`, default 0.70).
- Min Δ vs current: 0 pp (env `METNOS_INTENT_MIN_DELTA`).
- Se sotto threshold: salva audit "rejected" e non promuove.

Usage:
    python -m runtime.intent_classifier.train [--initial] [--no-turnlog] [--epochs N]

ENV:
- `METNOS_INTENT_EPOCHS` (default 5)
- `METNOS_INTENT_BATCH` (default 24)
- `METNOS_INTENT_MIN_ACC` (default 0.70)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

# Module-level imports must work even when invoked as script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.intent_classifier.anchors import (
    ANCHORS_IT, ANCHORS_EN, OBJECTS, for_lang as anchors_for_lang,
)


def load_seed_pairs() -> list[dict]:
    seed = Path(__file__).parent / "seed_pairs.jsonl"
    if not seed.exists():
        return []
    return [json.loads(l) for l in seed.read_text().splitlines() if l.strip()]


def load_turnlog_pairs(days: int = 30) -> list[dict]:
    """Estrai (query, object) da turn log ultimi N giorni, solo answer-ok."""
    out = []
    turn_dir = Path.home() / ".local" / "share" / "metnos" / "turns"
    if not turn_dir.exists():
        return out
    import datetime as _dt
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    for fp in sorted(turn_dir.glob("*.jsonl")):
        try:
            file_date = _dt.date.fromisoformat(fp.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        for ln in fp.read_text(errors="ignore").splitlines():
            try:
                t = json.loads(ln)
            except json.JSONDecodeError:
                continue
            q = t.get("user_query") or t.get("query")
            if not q or t.get("final_kind") != "answer":
                continue
            steps = t.get("plan") or t.get("steps") or []
            if not steps or not isinstance(steps, list):
                continue
            first = steps[0]
            tool = first.get("executor") or first.get("chosen_tool") or ""
            if "_" not in tool:
                continue
            parts = tool.split("_")
            if len(parts) < 2:
                continue
            obj = parts[1]
            if obj in OBJECTS:
                out.append({"query": q.strip(), "object": obj, "src": "turn_log"})
    return out


def dedup_pairs(*pair_lists: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for plist in pair_lists:
        for d in plist:
            key = (d["query"].lower().strip(), d["object"])
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
    return out


def split_train_eval(pairs: list[dict], train_ratio: float = 0.8, seed: int = 42):
    rng = random.Random(seed)
    shuffled = pairs[:]
    rng.shuffle(shuffled)
    n = int(len(shuffled) * train_ratio)
    return shuffled[:n], shuffled[n:]


def next_version_dir() -> Path:
    base = Path.home() / ".local" / "share" / "metnos" / "intent_classifier"
    base.mkdir(parents=True, exist_ok=True)
    versions = sorted(base.glob("v*"), key=lambda p: p.name)
    if not versions:
        return base / "v1"
    last = versions[-1].name
    try:
        n = int(last[1:])
    except ValueError:
        n = 1
    return base / f"v{n+1}"


def current_version_dir() -> Optional[Path]:
    base = Path.home() / ".local" / "share" / "metnos" / "intent_classifier"
    if not base.exists():
        return None
    versions = sorted(base.glob("v*"), key=lambda p: p.name)
    return versions[-1] if versions else None


def eval_model(model, anchor_emb, pairs: list[dict]) -> float:
    if not pairs:
        return 0.0
    queries = [p["query"] for p in pairs]
    expected = [p["object"] for p in pairs]
    q_embs = model.encode(queries, convert_to_tensor=True, normalize_embeddings=True)
    scores = q_embs @ anchor_emb.T
    preds = [OBJECTS[i] for i in scores.argmax(dim=1).tolist()]
    correct = sum(1 for e, p in zip(expected, preds) if e == p)
    return correct / len(pairs)


def audit_append(record: dict) -> None:
    base = Path.home() / ".local" / "share" / "metnos" / "intent_classifier"
    base.mkdir(parents=True, exist_ok=True)
    fp = base / "retrain_audit.jsonl"
    with open(fp, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def train_and_promote(args: argparse.Namespace) -> int:
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    lang = args.lang
    anchors = anchors_for_lang(lang)

    seed = load_seed_pairs()
    turnlog = [] if args.no_turnlog else load_turnlog_pairs(days=args.turnlog_days)
    pairs = dedup_pairs(seed, turnlog)
    pairs = [p for p in pairs if p.get("object") in anchors]
    print(f"Dataset: seed={len(seed)}, turn_log={len(turnlog)}, dedup={len(pairs)}")

    if len(pairs) < 50:
        print(f"ERROR: insufficient training data ({len(pairs)} < 50)")
        audit_append({
            "ts": time.time(), "decision": "abort_insufficient_data",
            "n_pairs": len(pairs),
        })
        return 1

    train_pairs, eval_pairs = split_train_eval(pairs)
    print(f"Train: {len(train_pairs)}, Eval: {len(eval_pairs)}")

    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
    examples = [
        InputExample(texts=[p["query"], anchors[p["object"]]])
        for p in train_pairs
    ]
    loader = DataLoader(examples, shuffle=True, batch_size=args.batch_size)
    loss_fn = losses.MultipleNegativesRankingLoss(model)

    out_dir = next_version_dir()
    print(f"Training {args.epochs} epochs, batch={args.batch_size}, lr={args.lr}")
    print(f"Output: {out_dir}")
    t0 = time.time()
    model.fit(
        train_objectives=[(loader, loss_fn)],
        epochs=args.epochs,
        warmup_steps=int(0.1 * len(loader) * args.epochs),
        show_progress_bar=True,
        optimizer_params={"lr": args.lr},
        output_path=str(out_dir),
    )
    train_time = time.time() - t0

    # Eval
    anchor_emb = model.encode(
        [anchors[o] for o in OBJECTS],
        convert_to_tensor=True, normalize_embeddings=True,
    )
    eval_acc = eval_model(model, anchor_emb, eval_pairs)
    print(f"\nEval accuracy: {eval_acc*100:.1f}%")

    # Promotion gate
    min_acc = float(os.environ.get("METNOS_INTENT_MIN_ACC", "0.70"))
    promote = eval_acc >= min_acc

    # Compare to current model if exists
    current_dir = current_version_dir()
    current_acc = None
    if current_dir and current_dir != out_dir:
        try:
            cur_model = SentenceTransformer(str(current_dir))
            cur_anchor_emb = cur_model.encode(
                [anchors[o] for o in OBJECTS],
                convert_to_tensor=True, normalize_embeddings=True,
            )
            current_acc = eval_model(cur_model, cur_anchor_emb, eval_pairs)
            print(f"Current ({current_dir.name}) on same eval: {current_acc*100:.1f}%")
            min_delta = float(os.environ.get("METNOS_INTENT_MIN_DELTA", "0.0"))
            if eval_acc < current_acc + min_delta:
                promote = False
                print(f"REJECT: Δ {(eval_acc-current_acc)*100:.1f}pp < required {min_delta*100:.1f}pp")
        except Exception as e:
            print(f"Current eval failed: {e}")

    decision = "promoted" if promote else "rejected"
    print(f"\nDecision: {decision}")

    audit_append({
        "ts": time.time(),
        "decision": decision,
        "version": out_dir.name,
        "n_train": len(train_pairs),
        "n_eval": len(eval_pairs),
        "n_seed": len(seed),
        "n_turnlog": len(turnlog),
        "eval_acc": eval_acc,
        "current_acc": current_acc,
        "min_acc": min_acc,
        "train_time_s": train_time,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "lang": lang,
    })

    if not promote:
        # Remove rejected model dir
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
        return 2
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lang", default="it", choices=["it", "en"])
    p.add_argument("--epochs", type=int,
                    default=int(os.environ.get("METNOS_INTENT_EPOCHS", "5")))
    p.add_argument("--batch-size", type=int,
                    default=int(os.environ.get("METNOS_INTENT_BATCH", "24")))
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--no-turnlog", action="store_true",
                    help="Don't include turn log data (initial install only)")
    p.add_argument("--turnlog-days", type=int, default=30)
    p.add_argument("--initial", action="store_true",
                    help="Initial install training (seed only, lower gate)")
    args = p.parse_args()
    if args.initial:
        args.no_turnlog = True
        os.environ.setdefault("METNOS_INTENT_MIN_ACC", "0.65")  # lower gate, no current to compare
    sys.exit(train_and_promote(args))


if __name__ == "__main__":
    main()
