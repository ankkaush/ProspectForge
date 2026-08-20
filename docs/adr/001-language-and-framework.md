# ADR-001: Language & Framework — Python + FastAPI

## Context
ProspectForge needs structured validation everywhere it touches external data: provider API
responses, LLM structured output, and internal data contracts (Account, Contact, Evidence,
QualificationResult). It also needs to be approachable for a learning project without
sacrificing production-realistic patterns (async I/O, typed contracts, testability).

## Decision
Build ProspectForge in Python, using FastAPI for the (thin) HTTP surface and pydantic for all
data contracts, provider response mapping, and LLM structured output validation.

## Alternatives considered
- **Node/TypeScript** — would reuse experience from SPEED2LEAD and keep one language across
  projects. Rejected for this project because pydantic + FastAPI gives structured
  validation (shape enforcement) for external/LLM data essentially for free, and Python's
  data/AI tooling ecosystem is deeper for the enrichment and LLM-extraction work this project
  is built around. Node remains a legitimate choice; this is a project-specific call, not a
  universal one.

## Consequences
- New language-ecosystem learning curve, accepted deliberately as part of the project's
  learning goals.
- pydantic models double as both API schemas and internal data contracts, reducing
  duplication between "what the API accepts" and "what the pipeline works with internally."
- Async support (FastAPI, httpx) is available for the pipeline's I/O-bound provider calls,
  though the initial implementation may run pipeline stages synchronously/sequentially for
  simplicity — async is available when volume or latency actually calls for it, not adopted
  reflexively.
