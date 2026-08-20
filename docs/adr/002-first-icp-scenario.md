# ADR-002: First ICP — Fictional B2B SaaS Scenario

## Context
The project needs a concrete ICP to build and test against, but has no real product,
customers, or market to derive one from. The ICP config must still be a legitimate,
defensible construction — not an arbitrary placeholder — since the whole point of Step 2
(ICP methodology) is learning how to build one properly.

## Decision
Use a fictional mid-market B2B SaaS company as the ICP scenario. The actual ICP criteria
values are constructed in Step 6, using analogous reasoning and product-fit reasoning
(see Step 2 notes), not invented ahead of understanding the methodology.

## Alternatives considered
- **Real personal/freelance ICP** — would tie the project to actual outreach and real
  compliance stakes earlier than necessary. Rejected for the learning phase; the ICP config
  is designed to be swappable, so this remains an option later without a rewrite.
- **Well-known public example (e.g. "companies like X's customers")** — rejected as it
  invites copying a vendor's public ICP rather than reasoning one out ourselves, which
  defeats the learning objective for Step 2.

## Consequences
- The ICP has no real validation loop (no real closed-won data to check it against) — this
  is an accepted, explicit limitation of the learning scenario, not something we simulate
  artificially.
- Because ICP is config/data (not code), a real ICP can replace this one later without
  touching the pipeline.
