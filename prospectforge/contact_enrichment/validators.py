"""Re-exports the shared email validator from prospectforge/validation/
(Step 14) - this module defined is_plausible_email first, in Step 13,
before validation/rules.py existed as the intended shared home for
field-level validation used across the pipeline. Kept as a re-export
rather than deleted, so existing imports (contact_enrichment's own
providers) don't need to change.
"""

from __future__ import annotations

from prospectforge.validation.rules import is_plausible_email

__all__ = ["is_plausible_email"]
