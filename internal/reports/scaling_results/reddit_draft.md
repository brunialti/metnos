# How robust is a local-LLM agent at composing multi-step plans? I stress-tested mine across 117 multi-domain requests.

I run a self-hosted personal assistant (local Qwen 3.6 35B, no frontier model in
the loop for routing). The hard part isn't answering a single question — it's
when one request bundles **many actions across many domains**:

> *"Find the log files in /tmp older than a week, zip them, email me the archive,
> then delete the originals"*

That's 4 actions over 2 domains. Real requests go further — files **and** mail
**and** calendar **and** photos **and** web, in one sentence.

I wanted to know the **intrinsic limits**: at how many actions, and how many
distinct domains, does the planner start dropping steps or scrambling the order?
So I built a grid — actions 2→8 on one axis, domains 1→7 on the other — and
generated ~117 natural-language requests scaling both dimensions, then scored
every produced plan against a known-correct one.

## Two things have to go right, so I measured them separately

**1. Structure** — does it pick the *right tools in the right order*?
[STRUCTURE HEATMAP]

**2. Arguments** — does each step get the *right parameters* (dates, paths,
recipients, filters) pulled from the text?
[ARGS HEATMAP]

## The finding

The binding constraint is **the number of domains, not the number of actions.**
Depth scales fine — 8 chained actions compose cleanly. But once a request spans
~6 *distinct* domains, the LLM starts silently dropping a whole producer.

## What actually fixed it (the interesting part)

Every failure I traced to a **cause**, not a symptom, and the fix had to be
general + deterministic — no per-case patches, no synonym lists:

- **Dropped producers** → the coverage check was per-*verb*; with 6 domains
  sharing the verb "find", a dropped `find_photos` hid behind another "find".
  Fix: per-*(verb, object)* coverage, rebuilt from the (reliable, ordered)
  intent decomposition.
- **Scrambled order** → deterministic reorder onto the intent's clause order.
- **"photos" classified as "files"** in long requests → not ignorance (it's
  correct in isolation) but *cross-clause contamination*. Fix: a clause-
  independence rule, not a foto→images dictionary.
- **Argument filling** → extract each step's args from *its own clause*, not the
  whole sentence (otherwise "yesterday" from one clause lands on another's step).
- **Boolean flags** (e.g. `unread_only`) → fired from the flag's *own schema
  description* via stem match — zero hardcoded keywords, works for any flag.

Net result: structurally correct up to **6 actions × 5 domains at 100%**, and
argument-filling close behind — all from a local model, with the heavy lifting
done by deterministic guards around it, not by asking the LLM to be perfect.

## A worked example

[WORKED EXAMPLE: query → intent decomposition → executor sequence → filled args]

Happy to go deeper on the architecture (intent extractor → constrained proposer
→ deterministic structure guards) if there's interest.
