# ADR-003: First Discovery/Enrichment Provider — Apollo.io

## Context
ProspectForge needs a real external data source for account discovery and enrichment to be
more than a theoretical exercise. The provider must be affordable for a learning project,
have a real API (not just a UI), and be swappable later without becoming the reason the
project can't proceed if pricing or access changes.

## Decision
Implement Apollo.io as the first `DiscoveryProvider` and `EnrichmentProvider` adapter, using
its free/basic API tier. No paid commitment is assumed or required to complete the roadmap.

## Alternatives considered
- **ZoomInfo** — enterprise-grade accuracy but enterprise pricing ($50K+/yr), unsuitable for
  a learning project.
- **Clearbit** — being folded into HubSpot's "Breeze Intelligence" credits, with standalone
  plans phased out; poor choice for a provider-independent architecture given its own
  standalone future is uncertain.
- **Clay** — a workflow/waterfall orchestrator over multiple providers, not a data source
  itself; significant operational overhead (real ongoing maintenance) inappropriate as a
  first, simplest implementation.
- **CSV import / manual seed list** — zero cost and zero dependency, but doesn't exercise
  real API auth, pagination, or rate-limit handling, which are explicit learning goals.

## Consequences
- All Apollo-specific behavior (auth, pagination, field mapping, rate limits) is isolated to
  `discovery/providers/apollo.py` and `enrichment/providers/apollo.py`. The core pipeline
  depends only on the `DiscoveryProvider` / `EnrichmentProvider` interfaces (see ADR-005).
- Free-tier rate/volume limits will shape how much real data we can pull during development;
  this is an accepted constraint, not a reason to build against fixtures only.
- A second provider is *not* being built solely to demonstrate the abstraction — the
  interface is proven by having one real implementation conform to it cleanly, not by
  having two.

## Addendum (2026-08-19): Apollo free tier has no search API access

A live test against a real Apollo API key showed this ADR's core assumption was wrong.
Both `mixed_companies/search` (discovery) and `mixed_people/search` (future decision-maker
discovery) return `403 API_INACCESSIBLE` on the Free plan, with Apollo's own error stating
plainly: *"not included in your Free plan and is not accessible, even with a master key. All
paid plans include full API access."* This isn't a credit-exhaustion or rate-limit issue —
it's a hard plan-tier gate on the endpoints this project's discovery and future
decision-maker-discovery stages depend on.

**Decision**: add a second `DiscoveryProvider` implementation,
`CsvDiscoveryProvider` (`discovery/providers/csv_provider.py`), backed by a seed CSV of
fictional accounts, and make it the *active* default (`DISCOVERY_PROVIDER=csv` in settings).
`ApolloDiscoveryProvider` remains fully built and tested — switching back once real API
access exists is a one-line config change, not a code change. This is not the "don't build a
second provider just to prove the abstraction" case this ADR originally ruled out — it's the
provider swap the whole architecture was built to absorb, forced by a real, discovered
constraint rather than a hypothetical one.

**Consequence for enrichment**: this project hasn't yet tested Apollo's enrichment endpoints
(Step 9) against the free plan — the same restriction may apply there too. That check is
deferred to Step 9, when enrichment is actually built, rather than assumed now.

**Open question, not yet decided**: whether to eventually pay for Apollo API access, or adopt
a different provider (e.g. one with a genuinely free search tier) once discovery/enrichment
need real data again for the end-to-end demonstration (Step 26). Revisit then.
