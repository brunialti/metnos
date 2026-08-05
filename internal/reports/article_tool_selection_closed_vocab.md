# I deleted the vector DB from my agent's tool selection. Same recall, none of the cost.

*(closed-vocab tool naming · CPU-only · runs on a homelab box · frozen reproducible bench in-repo)*

I run a self-hosted personal assistant (local Gemma as planner, ~96 tools, Telegram +
HTTP). Like everyone, I started tool selection the textbook way: embed every tool
description, embed the query, nearest-neighbour, feed top-k to the planner. BGE-M3,
ONNX, deterministic. It worked.

Then I ripped the embedding model out of that path entirely. The tool-selection step now
runs on token overlap over the tool *names* plus four typed rules — pure CPU, no model
loaded, ~17ms. On my frozen evaluation set recall **didn't move**: a dead tie with the
dense baseline, at every catalog size I tested. Which is the whole point — if it's a tie,
the embedding model and the vector index aren't buying anything, and on a low-power box
they cost plenty.

Here's what happened, with the bench you can re-run.

## First, the part that is NOT mine to claim

"Lexical beats dense for tool retrieval" is **already in the literature** — I'm not
discovering it. On ToolBench, plain BM25 reaches NDCG@5 0.853 vs 0.834 for dense
retrieval. Sparse-vs-dense for decoder-only retrievers shows sparse winning on several
benchmarks ([arXiv:2502.15526](https://arxiv.org/abs/2502.15526)). And it's documented
that LLM tool choice is fragile w.r.t. names/descriptions
([arXiv:2505.18135](https://arxiv.org/abs/2505.18135)). Current best practice is *hybrid*
(sparse + dense), not lexical-only.

So if your reaction is "duh, BM25" — you're right, and that's not the point.

## The part I could not find in the literature

The papers above debate retrieval over tool names and descriptions written in **free
natural language**. My setup does the opposite: the tool vocabulary is a **closed,
compositional grammar by construction**, decided up front.

Every tool is `verb_object[_qualifier]`. 23 verbs (`read, write, move, find, list,
filter, get, send, …`), 21 objects (`files, messages, events, images, persons, …`),
a small closed set of qualifiers. `find_images_indices`. `move_messages`.
`read_files_pdf`. New term → it goes through a governance gate (necessary / general /
understandable), not into the model's lap.

When the vocabulary is closed and compositional, the retrieval problem partly collapses:
the query tokens and the tool tokens live in the *same small lexicon*, so token overlap
is already a strong signal. The embedding step stops earning its keep — not by luck, but
because the naming was engineered to make it redundant. I haven't found a write-up that
frames it this way (closed vocab as a deliberate move to remove the retrieval embedding
on a real production agent). If you have one, link it — I'd genuinely like to read it.

On top of token overlap there are four typed rules (CPU, no LLM):
path/extension pattern boosts, query-pattern boosts, verb→producer-family compatibility,
and a rare-token-unmatched penalty.

## The numbers (production config, reproducible)

96 real tools, 234 organic queries (PII-scrubbed, frozen in the repo), deterministic,
seed 42, **no LLM in the loop**. Ground truth = the tool production actually called.
The `PRODUCTION` row is literally what runs live (`METNOS_PREFILTER` unset →
token-flat legacy, `METNOS_PREFILTER_RULES=1`). These are exactly what the bench prints
on a clean clone — no embedding model, nothing to install:

| Strategy | Recall@5 | Recall@1 | mean ms | needs model |
|---|---:|---:|---:|:--:|
| **token_flat + rules (PRODUCTION)** | **0.786** | **0.487** | ~10 | no |
| token_flat (no rules) | 0.765 | 0.466 | ~2 | no |
| trie / verb_first / hybrid_cascade | ~0.74–0.75 | ~0.46 | <3 | no |
| fts5 | 0.641 | 0.372 | <1 | no |
| bloom | 0.530 | 0.410 | ~1 | no |

These are deterministic — the bench pins the hash seed and the catalog is frozen, so a
clean clone prints exactly these numbers (I fixed a real production bug finding this: the
tool pool used to wobble ±1pp run-to-run because a tie-break iterated a set in hash order;
it's now stable).

And the dense baseline? With BGE-M3 installed (optional and heavy — the bench skips it
otherwise and says so), `selective_semantic` lands at **~0.76 R@5** — it never pulls ahead
of the lexical path, at 3–6× the latency and a half-gigabyte model. Apples-to-apples,
`token_flat` *without* rules (0.765) and dense (~0.76) are a flat tie; the lift to 0.786 is
the typed rules (recall@1 0.466 → 0.487), not the embedding. Either way the embedding model
isn't paying for itself.

## Re-run it yourself

The corpus and the 96-tool catalog are frozen and PII-scrubbed in the repo; the strategies
are the real ones the agent ships (the bench calls them, doesn't reimplement them). No
private turn logs, no model download:

```
git clone https://github.com/brunialti/metnos-prefilter-bench
cd metnos-prefilter-bench
python3 tests/benchmarks/repro_prefilter_bench.py --mode comparison
```

The production method is three files: `runtime/prefilter.py`, `runtime/prefilter_rules.py`,
`runtime/executor_typing.py`. (`token_flat_v2` is also in the tree but is an opt-in **not**
wired into production — the table labels the live config explicitly so you don't have to
take my word for it.) The `selective_semantic` rows need the BGE-M3 model; see
`tests/benchmarks/README.md` to reproduce the dense baseline.

## Caveats I'd raise before you copy this

- It works **because** the vocabulary is closed. If your tools are arbitrary third-party
  MCP servers with free-text names, you don't have this lever and hybrid retrieval is
  probably still your best bet.
- 96 tools. At thousands of tools the constant-time embedding lookup may pull ahead again.
- Single agent, single user, my query distribution. The frozen set is organic but it's
  *mine*. Re-run on yours before believing the number.

## Does it hold as the catalog grows? (the part that surprised me)

I expected dense to pull ahead once the tool pool got big — more tools, more chance a
lexical match is ambiguous, embeddings to the rescue. So I padded the catalog with **hard
negatives**: synthetic `verb_object_qualifier` tools recombined from the *same* closed
vocabulary, carrying real affinity tokens, so they collide lexically with the queries
instead of being trivially separable. Then I swept the pool from 84 to 1000 tools:

| Strategy | 84 tools | 250 | 500 | 1000 | slope 84→1000 |
|---|---:|---:|---:|---:|---:|
| `token_flat` (closed-vocab lexical) | 0.705 | 0.722 | 0.705 | 0.692 | **−1.3pp** |
| `selective_semantic` (BGE-M3 dense) | 0.705 | 0.722 | 0.705 | 0.692 | **−1.3pp** |
| `verb_first` | 0.692 | 0.645 | 0.628 | 0.615 | −7.7pp |
| `trie` | 0.679 | 0.624 | 0.611 | 0.590 | −9.0pp |
| `fts5` | 0.624 | 0.594 | 0.487 | 0.427 | −19.7pp |

Dense and closed-vocab token matching are **identical at every pool size** — same recall,
same −1.3pp slope out to 1000 tools. The embedding model doesn't pull ahead, even where I
built the test to let it. (The strategies that *do* collapse — fts5, trie, verb_first — are
the ones that throw away token structure.) So this isn't "lexical wins" — it's "dense
earns nothing here, at any scale I can produce."

## Why you'd care on a low-power box / homelab

If recall is a tie at every scale, the whole question becomes: **what does the embedding
path cost you that the token matcher doesn't?** For a self-hosted assistant on a mini-PC,
an old laptop, or a Pi-class box, that cost is the whole story:

1. **No heavy install.** The production path is Python stdlib + a closed vocabulary —
   tens of KB of code. The dense baseline needs `onnxruntime`/`sentence-transformers` and a
   ~half-gigabyte BGE-M3 model pulled at setup. On a low-power homelab that dependency stack
   is the difference between "clones and runs" and "fights a torch wheel for an afternoon."
2. **Runs on light hardware.** Token matching is CPU-only, no GPU, no model resident in
   RAM, no vector index to keep warm. The embedding model wants memory and ideally an
   accelerator just to break even on a metric where it already ties.
3. **Faster, and no cold start.** ~17 ms on CPU vs ~63 ms for the dense re-rank — and the
   dense path also pays a multi-second model load the first time, which on a box you reboot
   often is a tax every cold start. Token matching has no warm-up.
4. **Deterministic and boring to operate.** No embedding-model version to pin, no index to
   rebuild when a tool changes, no silent drift when you swap model revisions. The pool is a
   pure function of the query and the tool names.

The trade you're making: you have to *own your tool vocabulary* (closed, compositional
naming). If you do — and on a self-hosted system you usually can — you get the same tool
selection quality with none of the embedding infrastructure. On a low-power box that's not
a micro-optimization; it's whether the thing fits at all.

## And it makes the *frontier* calls cheap (even orchestrated from the edge)

The prefilter doesn't just pick the right tool — it keeps the pool **small**: ~8 tools out
of 96, not all 96. That matters most exactly when a hard turn escalates to a frontier model
(Opus, GPT-5) that bills per input token.

Put all 96 tool definitions in the prompt and you're carrying **~44k tokens** of tool
schema before the user even speaks. Prefilter to a targeted 8-tool pool and it's **~3.7k**
— about **92% fewer input tokens, on every call**. And the saving *grows* with your catalog:
the more tools you own, the more a cheap upstream selector earns.

The selector that buys you that is 17 ms of CPU with no model loaded — so it can live on an
edge box that does the *selection* locally and sends only a tight, relevant tool set up to
the expensive model for the *reasoning*. Cheap local gatekeeper, small frontier prompt. The
embedding-based alternative would put a half-gigabyte model on that same edge box to reach
the same tool pool — for a metric where, as above, it ties.

## Who this actually helps (two fronts)

I see this paying off in two different kinds of system:

1. **Light / edge self-hosted.** Your tool set is reasonably stable over time — you're not
   minting new tools every hour. So you pay the naming discipline once and from then on you
   ride the upside: fast selection, tiny footprint, no model resident, runs on the hardware
   you already have idling. The embedding index would be pure overhead for a catalog that
   barely changes.

2. **Agentic systems that generate their own tools.** This is the one I didn't expect to
   matter as much as it does. If your agent *synthesizes* tools on the fly, every new tool
   normally means re-embedding and rebuilding the vector index before it's selectable. With
   a closed compositional grammar, a freshly minted `verb_object_qualifier` is selectable
   **the instant it exists** — the token matcher already speaks its name, no index rebuild,
   no embedding pass. And it **scales**: in the sweep above, recall held flat (−1.3pp) out to
   1000 tools, identical to the dense baseline — so a catalog the agent keeps growing doesn't
   erode selection. The cost is a naming discipline the generator must obey; the payoff is a
   selector that's fast, effective, and never goes stale against your own tool growth.

Either way the bargain is the same: spend a little governance on *how tools are named*, and
you get to delete a whole moving part.

**Reproduce it / dig in:**
- Benchmark repo (clone & run): https://github.com/brunialti/metnos-prefilter-bench
- The assistant it comes from (project & architecture docs): https://metnos.com
