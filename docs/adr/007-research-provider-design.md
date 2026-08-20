# ADR-007: Research Provider Design — Claude's Native Web Search, Not a Separate Search API

## Context
The roadmap sketched Step 11 (Company Research) as two separate concerns: a `ResearchSource`
(search/fetch) abstraction hitting a search API or a curated set of pages, and a separate
`research/extractor.py` making an LLM call to turn retrieved page content into structured
Evidence. That shape assumes we need our own search-provider integration (a second new
account/credential, a second adapter to maintain) in addition to the LLM call.

Claude's Messages API has a native, server-side web search tool (`web_search_20250305`),
confirmed current via live documentation lookup (2026-08-19): Claude decides when to search,
Anthropic executes the search server-side, and the response includes cited sources
(`web_search_tool_result` blocks with real URLs, and `citations` on the resulting text).

## Decision
Collapse search and extraction into one Claude API call, using the native web_search tool,
rather than building a separate search-provider adapter. `research/interface.py` still
defines a `ResearchProvider` port; `AnthropicWebSearchResearchProvider` is the one
implementation, doing both the search and the structured-extraction prompting internally.

`research/extractor.py` still exists as a separate module, per the roadmap's intent to keep
"LLM call" and "schema validation + retry-on-invalid-JSON" as distinct concerns — it contains
pure parsing/validation logic (`parse_claims`, `build_evidence`) with no API-calling code,
testable with plain strings. The provider (`anthropic_web_search.py`) owns the API call and
the retry-on-malformed-JSON loop; the extractor owns turning a JSON blob into validated
Evidence.

## The anti-hallucination check this design enables
Pydantic validation (and even well-formed JSON) only proves a claim is the right *shape*, not
that it's true. `build_evidence` cross-checks every claimed `source_url` against
`verified_urls` — the set of URLs the web_search tool actually returned in that same API
turn, extracted from the response's `web_search_tool_result` blocks — and drops any claim
citing a source that was never actually retrieved. This is a structural check, not "trust the
model's stated confidence."

## Alternatives considered
- **Separate search API (Brave, Bing, SerpAPI) + a second LLM call over fetched pages** —
  rejected: doubles the number of external integrations and credentials for this one step,
  contradicts "don't overbuild a crawler" from the roadmap's own framing of this step, and
  gives no clear benefit over Claude's server-executed search, which already returns cited,
  URL-backed results.
- **Trusting Claude's free-form citations without the source-verification cross-check** —
  rejected: citations prove Claude *can* cite a real source, not that every claim in its
  final structured answer actually does. The cross-check costs nothing extra (the URLs are
  already in the response) and catches a real failure mode.

## Consequences
- One new credential (`ANTHROPIC_API_KEY`), not two.
- Retrieval is still "deterministic" in the sense the roadmap meant: the *query* (built from
  account name/domain, in our prompt) is decided by our code, not the model's free judgment
  about what to research at all. What Claude searches *for*, within that prompt, is the
  model's call — same as it would be if we handed a model raw fetched pages to summarize.
- If Claude's web search tool is ever unavailable on our plan (the same kind of check that
  found Apollo's search endpoint gated on the free tier - see ADR-003's addendum), swapping to
  a separate search-provider adapter is the fallback path; the `ResearchProvider` port already
  isolates that decision to one new adapter file.
