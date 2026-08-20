# ADR-004: First CRM — HubSpot

## Context
ProspectForge's pipeline output must land somewhere a real sales team would actually look —
a CRM — to make the end-to-end demonstration (Step 26) meaningful. The CRM integration must
be learnable and testable without enterprise licensing, and must not become the thing the
internal data model is designed around.

## Decision
Implement HubSpot as the first `CRMAdapter`, using its free developer/sandbox tier and
REST API.

## Alternatives considered
- **Salesforce** — the enterprise standard and instructive to study (its Lead/Contact/
  Account/Opportunity model directly informed our Step 1 domain vocabulary), but its API
  and sandbox setup carry more operational overhead for a first, learning-focused
  integration.
- **Microsoft Dynamics** — considered per the original brief's research scope, but has a
  smaller free/learning-tier footprint than HubSpot for this purpose.

## Consequences
- All HubSpot-specific object mapping (Company/Contact/Deal properties, auth) is isolated in
  `crm/adapters/hubspot.py`, behind the `CRMAdapter` interface. The internal `ProspectRecord`
  model is not shaped around HubSpot's object model.
- CRM sync must be idempotent and must check for existing records before creating new ones
  (see Step 18) — this is a design requirement independent of which CRM is used, and will be
  tested against HubSpot's specific matching behavior.
