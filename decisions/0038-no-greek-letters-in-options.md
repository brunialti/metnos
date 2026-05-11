---
id: 0038
title: No greek letters in option labels
date: 2026-04-27
status: accepted
area: ux
---

## Context

When Claude presents alternatives to Roberto in chat — "would you
prefer X or Y?", "do you want option α or β?" — the labels matter
operationally. The reply is typed; greek letters require an input
method (alt-codes, character maps, special keyboard layouts) that
add friction for no reason.

The choice came up early when a multi-option tradeoff was framed
with α / β / γ labels. Roberto's response: pick latin letters or
numbers. The friction of typing α is not worth the conceptual
nicety.

## Decision

When presenting alternatives in chat, label them with `(a)`, `(b)`,
`(c)` or numbers. Never with greek letters.

The rule applies regardless of language or register of the
conversation. If more than 3–4 labels are needed, switch from
letters to numbers (the greek-letter-overflow case turns into a
plain numbered list, which is also more legible).

## Alternatives considered

**Greek letters for symbolic / mathematical contexts.** Pro:
expressive when discussing parameters or weights. Con: in normal
conversation the burden of typing them is real; the discriminator
"this is symbolic" vs "this is conversational" is fuzzy and would
turn into "I think this option deserves α", which defeats the
rule. Rejected.

**Bullets without labels.** Pro: even simpler. Con: when responding,
"the second option" is more error-prone than "(b)"; the labels
help disambiguation. Rejected.

**Names instead of letters** (e.g. "the cautious approach" / "the
ambitious approach"). Pro: more meaningful. Con: works when there
are 2 options with sharp framing, fails when there are 4 nuanced
ones; falls back to letters anyway. Kept as an option for 2-way
choices; the rule is about labels when labels are used.

## Consequences

The rule is small but recurrent. Every chat session that lays out
options applies it. Drafts that use α / β / γ get caught and
rewritten before the message goes out.

Pairs with ADR 0036 (no third-party names) and the broader UX
discipline: friction in user input is a tax that compounds over
many interactions. Removing the tax wherever the cost is zero
preserves attention for the actually-hard choices.
