# ADR-008: Persona Matching Is Deterministic Keyword Matching, Not AI

## Context
Step 12 needs to decide, for each contact a discovery source returns, whether their job title
makes them a plausible decision-maker for our fictional product. The roadmap's own guidance
for this step: "AI only if title normalization proves messy enough to need it - try
deterministic first."

## Decision
Match on two required, independent keyword lists (seniority, department) defined in
`PersonaConfig` - a contact matches only if their title contains at least one seniority
keyword ("VP", "Director", "Head of", ...) AND at least one department keyword ("Revenue",
"Sales Operations", "Engineering", ...). No LLM call is involved.

## Alternatives considered
- **LLM-based title classification** - rejected for now: job titles in this project's seed
  data (and in Apollo's real data, based on Steps 7/9's live checks) are short, structured
  strings, not ambiguous prose. A keyword match handles "VP of Revenue Operations" and
  "Director, Sales Ops" correctly without needing a model call, extra latency, or extra cost
  per contact. Revisit if real-world title messiness (abbreviations, non-English titles,
  highly unconventional naming) proves the deterministic approach insufficient - the
  `PersonDiscoveryProvider` boundary means swapping the matcher for an AI-assisted one later
  would touch persona/matcher.py only.
- **Seniority-only or department-only matching** - rejected: either alone over-matches
  (every VP regardless of function, or every person in Sales regardless of seniority).
  Requiring both is what keeps the persona meaningfully narrow.

## Consequences
- Fast, free, fully explainable - every match records exactly which two keywords fired (see
  `persona/matcher.py`'s `match_title` return value), satisfying the roadmap's requirement to
  surface "the persona rule that matched each" candidate.
- A title using unconventional phrasing (e.g. "Growth Lead" for what is functionally a VP
  Sales Ops role) will not match. This is an accepted limitation of a keyword approach,
  consistent with the roadmap's own framing - not a defect to silently work around now.
