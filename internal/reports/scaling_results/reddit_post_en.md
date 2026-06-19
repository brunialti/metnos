# FLAKY, TRICKY, RISKY: when better is the enemy of good — does the speed (MTP, cache) beat the uncertainty it introduces?

For a few months I've been building **Metnos**, a self-hosted personal assistant (it all runs at home, on a mini-PC, with a local LLM — no cloud). Like every system of this kind, one of its core parts is the **engine** that extracts the user's intent and builds the chain of executors/agents that produce the answer.

Because the executors are many (hundreds) and variable, you need sophisticated strategies to **disambiguate** the request, **narrow down** the candidates, and **wire** the pieces together to the result. And a single request can contain several actions across **different domains** (files, mail, database, calendar…): not trivial. So you use mixed techniques — **deterministic** and **statistical (LLM)**.

If you could do everything deterministically, that would be ideal: faster, and with certain results. Sadly you can't, unless you make the system rigid — the user would have to speak an "unnatural" subset of the language. What follows is the story of the **fight between the deterministic part and the statistical part** of the engine, and my attempt to find at which **size limits** — in terms of actions and domains — the engine "breaks."

## The architecture, in 3 lines

The user writes in natural language. An **intent extractor** (LLM) splits the sentence into clauses *(verb, object)*. A **proposer** (LLM, constrained) picks the executors and orders them. Finally a set of **deterministic guards** (pure code, no LLM) fix structure, order and arguments before execution.

## How I looked for the limits

I built a grid: the vertical axis is the **number of actions** (2→8), the horizontal one is the **number of domains** (1→7). I generated ~117 real requests that grow both dimensions, and compared each produced plan against a known-correct one. A cell is "green" only if the engine picks **the right executors, in the right order**.

*[IMAGE 1: the heatmap — upload `struct_iter5_final.png`]*

*Each cell is a difficulty level (N actions × M domains), 4 random requests. Green = all correct. The box up to **6 actions × 5 domains is 100%**; beyond that, it starts to degrade.*

## How complex a sentence really is

To give you a feel: a single sentence can contain 6 domains and 8 actions. Here it is, broken down into executors, colored by their domain:

*[IMAGE 2: the breakdown — upload `example_breakdown_en.png`]*

Notice one thing: the **same verb "find"** appears over 4 different objects (files, expenses, photos, web). That's exactly where the trouble starts.

## From early confidence to apparent randomness

At first the numbers were great: the 6×5 box at 100%. But measuring **the same request several times**, the same input would sometimes produce a correct plan, sometimes a broken one. It felt like **pure randomness** in performance. For a system that wants to be reliable, this is the worst kind of problem: you don't know if you have a bug or just bad luck.

## Hunting for the causes

I pushed Claude Code, iteratively, to run a multi-dimensional analysis, with one rule: **never a patch, always the root cause**. I isolated one component at a time:

- the intent extractor, called 5 times on the same input → **deterministic**;
- the proposer, same → **deterministic**;
- but the **full pipeline** → unstable.

The paradox resolved with a targeted test: the exact same call to the model, run after different requests, produced **different outputs** (in one case, even empty).

## The findings

Let me be as precise as I can about what I **measured** versus what is still a **hypothesis**.

**Measured (reproducible).** I isolated the engine's components one by one:
- the intent extractor, called 5 times on the same input → identical (deterministic);
- the proposer, same → deterministic;
- but the exact same HTTP call to the model (same text, same *seed*, same slot), run **after different requests**, produces **different outputs** on long sentences — in one case even empty. So: **the output depends on the internal state left by previous calls**, not just on the input. On short sentences it doesn't show.

**Here I got two hypotheses wrong in a row — the experiments disproved both (this is the most useful part of the story).**

*Hypothesis 1: speculative decoding (MTP).* A technique that speeds up generation by decoding several tokens "ahead" and then verifying them: the perfect suspect. Decisive control test: I launched **the exact same model, but without MTP**, and repeated the experiment on long outputs. Result: **flaky just the same.** MTP cleared.

*Hypothesis 2: the server's internal cache (KV-cache) reused across requests.* I tried to **disable it**, expecting it to stabilize things. The opposite happened: with the cache **on** the output was stable (3/3 identical), **turning it off** made it unstable (3/3 different). Wrong again.

*The real cause (consistent with all the data): floating-point math on GPU is not deterministic on long generations.* When the model processes several requests **in parallel** (to go fast), the order of the internal sums changes from run to run; on a few dozen tokens you don't notice, but over thousands of tokens these tiny differences accumulate and the final output diverges. It fits everything: the intent extractor (short output) is stable; the proposer (long output) wobbles; and the cache, when the request is identical, *pins* the compute path and makes it repeatable.

Takeaway of this sub-chapter: the obvious suspect is almost never the culprit, and the only way to know is to run the experiment. The title's question stays open: **is that speed worth the uncertainty it brings?**

**The discovery that flips the problem (measured).** The **downstream deterministic guards absorb ~14 out of 15 instabilities**: the LLM wobbles, but the deterministic code **normalizes** the output and the final plan is correct again. The "randomness" almost never reaches the user. An important practical consequence: **a test that measures the model's raw plan mostly measures noise**, not the system's quality. Two runs of the exact same code give different numbers, and many "errors" simply *flicker* between one measurement and the next.

**Real residual bugs (few, stable).** At 7-8 clauses the LLM sometimes **contaminates** a clause with a neighbor's object ("find the **photos**", after "find the **files**", wrongly becomes *files*). It's not ignorance (in isolation the mapping is right), it's an attention bias on long sentences. I fixed it with a deterministic guard that re-derives the object from the single clause's text.

## The takeaway

For days I iterated chasing the **philosopher's stone**: absolute certainty in a technology that is uncertain by definition. At some point you need the courage to **stop and accept a compromise**.

My current compromise: in the large majority of cases, even very complex requests (up to ~6 actions and ~5 domains) are solved correctly and repeatably. The rest are handled so that **there is never a silent error**: the user is always told about the problem and, if needed, asked to rephrase.

In my case there's an extra difficulty: Metnos is **multilingual by design**. I can't use single-language "tricks" — hardcoded synonyms, dictionaries — that would fix a problem in one language but leave it open in every other. Every solution has to be **general and deterministic**, or go through the model.

There's still a lot to do, but I'm very happy with what I learned and achieved. (Upstream and downstream of the engine there are also mechanisms to mitigate errors and to learn special cases, but that's another story.)
