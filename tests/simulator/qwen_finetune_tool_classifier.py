"""qwen_finetune_tool_classifier.py — Train Qwen3-Emb FT for (query → first_tool).

Clone di `qwen_finetune_v2.py`. Differenze:
- Output: tool_name (multi-class, ~50 classi attive su ~84 typing universe).
- Anchor text per ogni tool = `manifest.description.<lang>` (1a frase) +
  `affinity` keywords. Fallback se manifest non disponibile: usa
  typing_cache (`semantic_type`/`output.type`) + `tool_name` stesso.
- Dataset estratto runtime da test_set_FROZEN_v2.json + real_queries_v2.json
  + real_queries.json (dedup by query, expected_path[0] = label).
- Stop labels `_*` (builtin universal helpers) e `request_new_executor`.
- Loss: MultipleNegativesRankingLoss (in-batch hard negatives).
- Holdout: 20% stratified-ish (random.seed(42) come v2/v3).

§7.11: zero hardcoded paths — usa Path(__file__).resolve().parents.
§7.9: trainer deterministico (seed 42); inferenza è classifier scope.

CLI:
  python3 qwen_finetune_tool_classifier.py            # full 10 epoch
  python3 qwen_finetune_tool_classifier.py --epochs 1 # dry-run 1 epoch
  python3 qwen_finetune_tool_classifier.py --dry-run  # alias 1 epoch
"""
from __future__ import annotations
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

# Repo layout: /opt/metnos/tests/simulator/<this file>
_SIM_DIR = Path(__file__).resolve().parent
_METNOS_ROOT = _SIM_DIR.parents[1]  # /opt/metnos
sys.path.insert(0, str(_SIM_DIR))
sys.path.insert(0, str(_METNOS_ROOT))

# Default tool sources (relative to simulator dir)
DATA_SOURCES = ["test_set_FROZEN_v2.json", "real_queries_v2.json", "real_queries.json"]
TYPING_CACHE_DIR = _SIM_DIR / "typing_cache"
EXECUTORS_DIR = _METNOS_ROOT / "executors"
OUTPUT_DIR_DEFAULT = Path("/tmp/qwen_ft_tool_classifier_v1")

# Skip labels that are not real tools / not pickable
_SKIP_FIRST_TOOLS = {"request_new_executor"}


def _norm_tool(name: str) -> str:
    """Normalize tool name (strip leading underscore for builtins)."""
    return name.lstrip("_")


def _load_typing_universe() -> list[str]:
    """Universe of tool names from typing_cache (84 entries)."""
    if not TYPING_CACHE_DIR.exists():
        return []
    out = []
    for p in sorted(TYPING_CACHE_DIR.glob("*.json")):
        out.append(_norm_tool(p.stem))
    return out


# ---------- Dataset extraction ----------

def _extract_pairs(sources: list[str]) -> list[tuple[str, str]]:
    """Return [(query, first_tool)] dedup by query.

    Aggregate ordering: last source wins on duplicate (FROZEN_v2 listed FIRST
    so older real_queries[_v2] override only if labels differ — we WANT
    FROZEN_v2 to win because it's the latest authoritative test_set; so
    we process in REVERSE order so FROZEN_v2 is processed LAST).
    """
    acc: dict[str, str] = {}
    for src in reversed(sources):  # FROZEN_v2 last → wins
        p = _SIM_DIR / src
        if not p.exists():
            continue
        for r in json.loads(p.read_text()):
            q = (r.get("query") or "").strip()
            ep = r.get("expected_path") or []
            if not q or not ep:
                continue
            first = ep[0]
            if not first or first in _SKIP_FIRST_TOOLS:
                continue
            acc[q] = _norm_tool(first)
    return list(acc.items())


def _split(pairs: list, train_ratio=0.8, seed=42) -> tuple[list, list]:
    """Random split (seed 42, matches v2/v3)."""
    random.seed(seed)
    shuffled = pairs[:]
    random.shuffle(shuffled)
    n = int(len(shuffled) * train_ratio)
    return shuffled[:n], shuffled[n:]


# ---------- Anchor text per tool ----------

def _read_manifest_desc(tool: str, lang: str = "it") -> Optional[str]:
    """Read manifest.description.<lang> first 1-2 sentences + affinity."""
    # Tool may be a builtin (no manifest) - return None
    md = EXECUTORS_DIR / tool / "manifest.toml"
    if not md.exists():
        return None
    try:
        try:
            import tomllib  # py 3.11+
        except ImportError:
            import tomli as tomllib
        data = tomllib.loads(md.read_text())
    except Exception:
        return None

    desc_block = data.get("description", {})
    raw = desc_block.get(lang) or desc_block.get("en") or desc_block.get("it") or ""
    if isinstance(raw, dict):
        # nested? unlikely
        raw = raw.get(lang) or next(iter(raw.values()), "")
    if not isinstance(raw, str):
        return None
    # First 1-2 sentences
    sentences = re.split(r"(?<=[.!?])\s+", raw.strip())
    head = " ".join(sentences[:2])[:280]

    affinity = data.get("affinity") or []
    aff_txt = " ".join(affinity[:12])
    return f"{head} {aff_txt}".strip() or None


def _typing_fallback(tool: str) -> str:
    """Fallback anchor: tool name word-split + typing cache hints."""
    # Tool typing: try with and without underscore prefix
    cands = [tool, f"_{tool}"]
    tc_blob = ""
    for c in cands:
        p = TYPING_CACHE_DIR / f"{c}.json"
        if p.exists():
            try:
                d = json.loads(p.read_text())
                bits = []
                ot = (d.get("output") or {}).get("type")
                if ot:
                    bits.append(f"output {ot}")
                for arg_name, arg_meta in (d.get("inputs") or {}).items():
                    st = arg_meta.get("semantic_type")
                    if st:
                        bits.append(f"{arg_name} {st}")
                tc_blob = " ".join(bits[:8])
            except Exception:
                pass
            break

    # Decompose verb_object[_qualifier]
    parts = tool.split("_")
    words = " ".join(parts)
    return f"{words} {tc_blob}".strip()


def build_tool_anchors(tools: list[str], lang: str = "it") -> dict[str, str]:
    """Compose anchor text for every tool in `tools`."""
    out = {}
    for t in tools:
        desc = _read_manifest_desc(t, lang=lang)
        if not desc:
            desc = _typing_fallback(t)
        # Always prefix with the tool name itself (LLM helper)
        out[t] = f"{t.replace('_', ' ')}: {desc}".strip()
    return out


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true", help="Alias --epochs 1")
    ap.add_argument("--batch-size", type=int, default=24)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--output", type=str, default=str(OUTPUT_DIR_DEFAULT))
    ap.add_argument("--lang", type=str, default="it")
    ap.add_argument("--no-train", action="store_true",
                    help="Skip training; eval only on existing model.")
    args = ap.parse_args()

    if args.dry_run:
        args.epochs = 1

    # 1. Extract pairs
    pairs = _extract_pairs(DATA_SOURCES)
    print(f"[dataset] unique (query, first_tool) pairs: {len(pairs)}")
    label_set = sorted({t for _, t in pairs})
    print(f"[dataset] unique labels in training: {len(label_set)}")

    # 2. Build anchor text per tool (label_set only — anchor must cover labels)
    anchors = build_tool_anchors(label_set, lang=args.lang)
    missing_anchor = [t for t in label_set if not anchors.get(t)]
    if missing_anchor:
        print(f"[anchor] WARN: missing anchor for {len(missing_anchor)} tools: "
              f"{missing_anchor}")

    # 3. Train/eval split
    train_pairs, eval_pairs = _split(pairs)
    print(f"[split] train: {len(train_pairs)}, eval (holdout 20%): {len(eval_pairs)}")

    # 4. Filter eval to labels that have anchors
    eval_pairs = [(q, t) for q, t in eval_pairs if t in anchors]
    print(f"[split] eval (anchor-resolvable): {len(eval_pairs)}")

    import torch  # noqa
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    print("[model] Loading Qwen3-Embedding-0.6B ...")
    if args.no_train and Path(args.output).exists():
        model = SentenceTransformer(args.output)
        print(f"[model] Loaded fine-tuned from {args.output}")
    else:
        model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
        print(f"[model] Loaded base. Dim: {model.get_embedding_dimension()}")

    if not args.no_train:
        # 5. Build training InputExamples (anchor pairs only — Multi-Neg in-batch)
        train_examples = [
            InputExample(texts=[q, anchors[t]])
            for q, t in train_pairs if t in anchors
        ]
        print(f"[train] examples: {len(train_examples)}")
        loader = DataLoader(train_examples, shuffle=True, batch_size=args.batch_size)
        loss_fn = losses.MultipleNegativesRankingLoss(model)

        print(f"[train] {args.epochs} epoch(s), batch_size={args.batch_size}, lr={args.lr}")
        t0 = time.time()
        model.fit(
            train_objectives=[(loader, loss_fn)],
            epochs=args.epochs,
            warmup_steps=int(0.1 * len(loader) * args.epochs),
            show_progress_bar=True,
            optimizer_params={"lr": args.lr},
            output_path=args.output,
        )
        elapsed = time.time() - t0
        print(f"[train] elapsed: {elapsed:.0f}s ({elapsed/60:.1f}min)")
        per_epoch = elapsed / max(args.epochs, 1)
        print(f"[train] per-epoch: {per_epoch:.0f}s; "
              f"projected 10ep: {per_epoch*10:.0f}s ({per_epoch*10/60:.1f}min)")

    # 6. Eval — encode all anchors once, then per-query argmax + top-3
    label_list = label_set
    anchor_texts = [anchors[t] for t in label_list]
    print(f"[eval] encoding {len(anchor_texts)} anchors ...")
    anchor_emb = model.encode(
        anchor_texts, convert_to_tensor=True, normalize_embeddings=True
    )

    if not eval_pairs:
        print("[eval] empty holdout; aborting eval.")
        return

    queries = [q for q, _ in eval_pairs]
    expected = [t for _, t in eval_pairs]
    print(f"[eval] encoding {len(queries)} queries ...")
    q_emb = model.encode(
        queries, convert_to_tensor=True, normalize_embeddings=True
    )
    scores = q_emb @ anchor_emb.T  # [N_q, N_labels]

    # Top-3
    top_k = 3
    topk_idx = scores.topk(min(top_k, scores.shape[1]), dim=1).indices.tolist()

    top1_correct = 0
    top3_correct = 0
    wrong_samples = []
    for q, exp, idxs in zip(queries, expected, topk_idx):
        top1 = label_list[idxs[0]]
        top3 = [label_list[i] for i in idxs]
        if top1 == exp:
            top1_correct += 1
        if exp in top3:
            top3_correct += 1
        else:
            wrong_samples.append((q, exp, top3))

    n = len(eval_pairs)
    top1_acc = 100 * top1_correct / n
    top3_acc = 100 * top3_correct / n
    print()
    print("=" * 60)
    print(f"[HOLDOUT EVAL] n={n}")
    print(f"  Top-1 accuracy: {top1_correct}/{n} = {top1_acc:.1f}%")
    print(f"  Top-3 accuracy: {top3_correct}/{n} = {top3_acc:.1f}%")
    print(f"  Wrong (top-3 miss): {len(wrong_samples)}")
    print("=" * 60)

    if wrong_samples:
        print("\n[eval] Top-3 misses (first 10):")
        for q, exp, t3 in wrong_samples[:10]:
            print(f"  Q: '{q[:70]}'")
            print(f"     expected: {exp}")
            print(f"     top3:     {t3}")

    # Persist meta for inference
    meta_path = Path(args.output) / "metnos_tool_classifier_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({
        "labels": label_list,
        "anchors": anchors,
        "lang": args.lang,
        "top1_acc_holdout": top1_acc,
        "top3_acc_holdout": top3_acc,
        "n_train": len(train_pairs),
        "n_eval": n,
        "epochs": args.epochs,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2, ensure_ascii=False))
    print(f"\n[meta] wrote {meta_path}")


if __name__ == "__main__":
    main()
