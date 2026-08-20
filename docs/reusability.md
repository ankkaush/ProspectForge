# Reusability Review (Step 25)

An honest audit: if a real client showed up tomorrow with a different ICP, a different
discovery/enrichment provider, and a different CRM, what survives unchanged?

## Method

Not a slide-level claim - grepped the actual codebase for the thing that would prove or
disprove it: every vendor-specific term (`apollo`, `hubspot`, `anthropic`, `claude`) outside
`providers/`/`adapters/` directories, then read every real hit in context (not just counted
matches). Checked three things specifically: (1) does core logic import a vendor SDK anywhere
it shouldn't, (2) do the shared port interfaces (`DiscoveryCriteria`, `CRMSyncInput`, etc.) use
provider-neutral field names or leak a vendor's own naming, (3) is anything hardcoded to *this*
fictional client's specific values rather than driven by config.

## Result: the boundary held

**Zero vendor SDK imports outside `providers/`/`adapters/` directories.** Every provider-name
string that does appear in core `service.py` files is one of exactly two legitimate cases, both
by design (ADR-005):

1. The `get_default_X_provider()` factory function in each module - the one, deliberate place
   provider selection happens, reading a `csv`/`apollo` (or `deterministic`/`anthropic`)
   setting.
2. The `provider="apollo"` label passed to `call_with_retry()` for the audit trail
   (`ExternalCallAttempt.provider` is a plain string field precisely so a new provider is never
   a schema change - see that model's docstring).

Every shared interface DTO (`DiscoveryCriteria.industries`/`employee_count_min`, not Apollo's
own field names; `CRMSyncInput.account_name`/`contact_email`, not HubSpot's `properties` object
shape) is genuinely provider-neutral - checked directly, not assumed.

## The concrete list

### Reusable as-is (zero changes)
`app/trigger.py` and every stage's `service.py` orchestration logic; all seven port interfaces
(`DiscoveryProvider`, `EnrichmentProvider`, `ResearchProvider`, `PersonDiscoveryProvider`,
`ContactEnrichmentProvider`, `RationaleProvider`, `CRMAdapter`); `infra/retry.py`,
`infra/circuit_breaker.py`, `infra/observability.py`; `app/orm.py`, `app/mappers.py`,
`app/db.py`, `app/logging.py`, `app/security.py`; dedup (`matchers.py`, `service.py`);
prioritization (`scorer.py`, `service.py`); qualification's deterministic engine
(`engine.py`); review (`service.py`); persona matching (`matcher.py` - plain keyword-list
matching, no hardcoded titles); ICP loading and the ICP→`DiscoveryCriteria` mapping
(`criteria.py` - reads the ICP's declared field names, not this scenario's specific values);
`prospectforge/validation/rules.py`; `prospectforge/privacy/erasure.py`.

### Reusable with a config change only (no code)
- **A new ICP**: one new YAML file (Step 6's own exit criteria - already proven true, not new
  information).
- **A new persona**: one new YAML file, plus overriding `PEOPLE_DISCOVERY_PERSONA_ID` - it
  currently defaults to `"primary-buyer-v1"` (this client's persona), which is a real, minor
  gap: a second deployment that forgets to override it would silently search for the wrong
  decision-makers rather than failing loudly. Worth a follow-up fail-fast check, not fixed
  here (see "not fixed this step" below).
- **Switching any single stage between `csv` and `apollo`** (or `deterministic`/`anthropic`
  for rationale) - one environment variable, already proven at Steps 7-18.

### Provider-specific (needs one new adapter file, nothing else)
`discovery/providers/`, `enrichment/providers/`, `people_discovery/providers/`,
`contact_enrichment/providers/` - Apollo today; a different discovery/enrichment vendor means
writing one new file implementing the existing interface. Same for
`research/providers/anthropic_web_search.py` and
`qualification/providers/anthropic_rationale.py` (a different LLM) and
`crm/adapters/hubspot.py` (a different CRM - Salesforce, Dynamics). The CSV-backed seed-data
providers are a dev/test convenience with this client's fictional data baked in as the default
path (`csv_path: Optional[Path] = None` on the provider's constructor, not currently exposed as
its own env var) - a real second client engagement would use real API access for these stages
instead of touching the CSV path at all.

### Would need re-verification, not just a new adapter
- **`research/extractor.py` and `qualification/rationale.py`'s parsing contracts.** Neither
  file imports Anthropic's SDK - structurally clean - but the *expected response shape* (the
  prompt design, the JSON schema each expects back) was shaped by testing against Claude's
  actual behavior (ADR-007). Swapping to a different LLM wouldn't require rewriting these
  files, but the contract needs live re-verification against the new provider's actual output,
  the same discipline this project already applied to Claude itself.
- **Fit tier thresholds and prioritization weights.** Fully parameterized, no hardcoded
  values in logic - but the actual numbers (`DEFAULT_WEIGHTS`, tier cutoffs) were reasoned
  through for one fictional scenario, not empirically validated against a real client's
  outcomes. A real second engagement should re-tune these against real data, not just inherit
  them.

## What "reusable" doesn't mean

Reusable means the boundary held - not that every default is generically correct. The
`people_discovery_persona_id` default and the un-tuned scoring weights are exactly the
difference: both are fully overridable through config with zero code change, but both would
silently produce wrong-for-this-client results if a second engagement just deployed the
defaults without deliberately revisiting them.

## Not fixed this step

The `people_discovery_persona_id` default silently pointing at this client's persona is a real,
small gap - the honest audit found it, and it's staying documented rather than "fixed" with a
fail-fast check, since that's a real code change (new validation logic) outside this step's
scope (an audit doc, per the roadmap - "None new" modules). Flagged here as the concrete
follow-up item a second engagement's setup checklist should include.

## What Step 2 of a second client engagement would concretely look like

1. Write a new `ICPConfig` YAML (Step 6's process, not new engineering).
2. Write a new `PersonaConfig` YAML; explicitly set `PEOPLE_DISCOVERY_PERSONA_ID` (don't rely
   on the default - see above).
3. **If the new client can use Apollo/Anthropic/HubSpot**: set the real API keys as env vars.
   Every stage, the whole reliability layer, review, and CRM sync work identically -
   zero code touched.
4. **If the new client needs a different discovery/enrichment/CRM vendor**: write one new
   adapter file per swapped integration, implementing the existing port interface. Nothing in
   `app/trigger.py`, any `service.py`, or any test file's *structure* changes - this is the
   literal claim ADR-005 made at Step 3, now checked rather than assumed.
5. Live-verify the research/rationale prompt contracts against real data for this client, same
   discipline as Steps 11/15 originally used against Claude.
6. Re-tune fit thresholds and prioritization weights against the new client's real qualified/
   disqualified outcomes, once enough real runs exist to reason from.

Steps 3-4 are the actual test of "reusable automation architecture" as a claim rather than a
slide: the pipeline's shape, its tests, and every module boundary hold regardless of which
integration is behind each port. Steps 5-6 are the honest caveat - what's reusable
*mechanically* still needs re-validating *empirically* against a real client's data, which no
architecture review can substitute for.
