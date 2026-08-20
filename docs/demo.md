# End-to-End Demonstration (Step 26)

**A polished, presentable version of this walkthrough is published at
https://claude.ai/code/artifact/0cbd034e-ebfd-47db-94a0-e26d2ee22656** - same real data, laid
out as a stage-by-stage record designed to be handed to someone else. This document is the
plain-text/durable-in-repo version of the same content.

One real prospect's actual journey through the deployed system, ICP to CRM record - every
data point below is queried directly from the real database, not fabricated for this
document. Where a real integration wasn't available (HubSpot - no account exists yet), that's
stated plainly, not glossed over.

## What this system does, in one sentence

Given a company's ideal-customer profile, ProspectForge finds real companies that match it,
researches and scores them automatically, finds the right person to contact, has a human
approve or reject each one, and pushes the approved ones into a CRM - with every decision
along the way traceable back to the specific fact or rule that produced it.

## The company: Loomwork

**ICP used**: `saas-fictional-v1` — Series A-C B2B SaaS companies, 50-500 employees, US/EU,
already running Salesforce or Slack, at a funding stage implying real budget authority
(`prospectforge/icp/configs/saas-fictional-v1.yaml`).

### 1. Discovery

Loomwork entered the pipeline as a raw discovered account: `SaaS`, `340 employees`,
`Netherlands`. Discovery persists every account the moment it's mapped, before moving to the
next one — a crash mid-page loses at most one in-flight item, never what's already been saved.

### 2. Cheap pre-filter (before spending anything on enrichment)

**Result: Tier 2.** Three reasons, all matched against pre-enrichment firmographic fields
only — no external calls yet:
- Industry matches our target software/SaaS segment
- Company size is in our target mid-market range (50-500 employees)
- Headquartered in a supported US/EU market

This is the cost-control gate (Step 8): an account this cheap to evaluate is checked before any
enrichment API call is spent on it.

### 3. Enrichment — a real Apollo call, with a real, honest result

A real call to Apollo's enrichment API for `loomwork.io` — this is genuinely the live API, not
a stand-in (Apollo's enrichment endpoint is free-tier accessible; see ADR-003's addendum).
**Apollo had no data for this domain** — expected and correct, since Loomwork is a fictional
company standing in for a real prospecting scenario. This is exactly Step 9's designed failure
scenario in action: "no data" resolves to *unknown*, never a fit failure.

### 4. Full fit evaluation — graceful degradation, for real

With `tech_stack`/`funding_stage` still unknown after enrichment, the evaluator had to decide
what to do with missing data rather than crash. **Result: still Tier 2**, with the reasoning
made explicit rather than silently assumed:

> Industry matches our target software/SaaS segment; Company size is in our target mid-market
> range (50-500 employees); Headquartered in a supported US/EU market; **missing data for
> 'tech_stack' prevented Tier 1**: already runs Salesforce or Slack - our integration requires
> one of these to be useful; **missing data for 'funding_stage' prevented Tier 1**: funding
> stage implies budget authority for a mid-market tool.

This is Step 10's exit criteria demonstrated live, not asserted in a test: a real account with
real missing data still produced a usable, explained tier instead of an error or a silent
guess.

### 5. Company research — a real Claude web-search call, zero evidence found

A real call to Claude's native web search (Anthropic) for public information about Loomwork.
**Zero evidence returned** - again, expected and correct for a fictional domain with no real
web presence. Step 11's "zero results" failure scenario, handled cleanly: the account proceeds
with no evidence, not stuck or errored.

### 6. Decision-maker discovery, and 7. Contact enrichment

Found **Ben Turner, Chief Revenue Officer** — c-suite seniority, Revenue department, matching
the configured persona's keyword rules (`primary-buyer-v1.yaml`). Contact enrichment then
resolved a **verified** email (`ben.turner@loomwork.io`) and a LinkedIn URL.

> **A real bug, found and fixed while building this exact demo**: querying this account's
> `external_call_attempts` shows `provider="apollo"` for the people-discovery and
> contact-enrichment calls above - but this project's actual configured default for both
> stages is `csv` (per `.env`, matching ADR-003's addendum). Tracing one real account's full
> history end-to-end is what surfaced this: every CSV-backed stage was mislabeling its audit
> trail as "apollo" regardless of which provider actually ran. Fixed at the source (commit
> `a1f8119`) and covered by three new regression tests - left the historical rows above
> unedited, since they're honest evidence of the bug that existed, not something to quietly
> correct after the fact.

### 8. Validation & dedup

No merge needed - Loomwork was never re-discovered as a near-duplicate in this dataset. (A
real dedup merge *did* happen elsewhere in this project's history - see
`docs/adr/010-dedup-merge-strategy.md` - just not for this particular account.)

### 9. Qualification — fully deterministic, zero AI in the verdict

**Result: QUALIFIED, 70% confidence.** The verdict, reasons, and confidence score come
entirely from `qualification/engine.py` - tier + evidence + contact completeness, additive and
capped, with zero LLM involvement (Step 15's correction). This account's own history has
*both* rationale styles on record, real proof both paths work:

- **AI-phrased** (from before Step 15's correction, using the optional Anthropic path):
  *"Loomwork is qualified as tier_2 because it fits our target software/SaaS segment, falls
  within our mid-market size range (50-500 employees), and is headquartered in a supported
  US/EU market... The candidate decision-maker is Ben Turner, Chief Revenue Officer, and his
  email is verified."*
- **Deterministic** (the current default, zero Anthropic call): *"Fit tier: tier_2; Industry
  matches our target software/SaaS segment; ...; Candidate decision-maker: Ben Turner (Chief
  Revenue Officer); No recent evidence found - qualification based on fit and contact
  availability alone; Contact email is verified."*

Same verdict, same confidence, same reasons - only the sentence changes. That's the entire
point of Step 15's design: the AI never had authority over the decision, so removing it changed
nothing except prose quality.

### 10. Prioritization

**Rank 1, score 0.49** - the highest-priority prospect in the current queue, out of 9 real
qualified prospects, driven by fit tier + evidence recency + Ben Turner's C-suite seniority
(the heaviest-weighted seniority bracket).

### 11. Human review

**Approved.** A person (in this project's case, the operator via the `review-queue`/`approve`
CLI) looked at the ranked queue, the rationale, and the contact, and made the actual go/no-go
call - this system never auto-syncs a record to a CRM without that explicit human decision.

### 12. CRM sync — the real code path, a labeled stand-in destination

No `HUBSPOT_API_KEY` exists yet for this project (documented honestly since Step 18). To
complete this demonstration truthfully, the **real** `crm/sync_service.py` code ran against
this **real** approved record, with a stand-in adapter standing in only for the actual HTTP
destination - the same idempotent search-then-create contract, tested against HubSpot's real
API shape in `test_crm_hubspot_adapter.py`:

```
[stand-in HubSpot] would create Company('Loomwork', domain='loomwork.io')
[stand-in HubSpot] would create Contact('Ben Turner', 'ben.turner@loomwork.io', title='Chief Revenue Officer')
[stand-in HubSpot] would attach Note: confidence=70%, rationale="Fit tier: tier_2; ..."
```

The database now genuinely reflects this: `accounts.status = SYNCED`,
`prospect_records.crm_object_id = 'demo-hubspot-contact-loomwork-001'`,
`prospect_records.synced_at` set to a real timestamp - queried directly from Postgres, not
asserted in a document.

## Why it's built this way

- **Every stage is provider-independent** (ports and adapters, ADR-005) - Apollo and Claude
  were real calls above; HubSpot was a labeled stand-in for the one integration without a live
  account. Swapping any of them touches one adapter file, checked directly in
  `docs/reusability.md`, not assumed.
- **The AI never decides anything it doesn't have to** - qualification's verdict is 100%
  deterministic; research and rationale are the only two places an LLM is involved at all, and
  both are structurally prevented from injecting an unsupported claim (the evidence-id
  cross-check in `qualification/rationale.py`).
- **Every failure has a defined, visible outcome** - "no enrichment data," "zero research
  evidence," and "no CRM account configured" all happened during this one real account's
  journey, and none of them crashed, hung, or silently dropped anything.
- **The audit trail is real, and real bugs get found by actually reading it** - the mislabeled
  provider bug in this same walkthrough wasn't caught by a unit test; it was caught by tracing
  one real account's full history end-to-end and reading what it actually said.

See [`docs/architecture.md`](architecture.md) for the full module map,
[`docs/data-model.md`](data-model.md) for every field referenced above, and
[`README.md`](../README.md) to run this yourself.
