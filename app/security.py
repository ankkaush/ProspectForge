"""API key authentication for the trigger endpoint.

See the Step 5 design discussion for why this is a static API key rather
than HMAC request signing: `POST /runs` is invoked by the operator or a
scheduler under the operator's control, not by an external third party
sending a payload whose authenticity needs independent verification. The
question this endpoint needs answered is "is this caller authorized," which
a shared secret answers directly.

`hmac.compare_digest` (not `==`) is used to compare the provided key against
the configured one - a plain string comparison can leak timing information
about how many leading characters matched, which an attacker can exploit to
guess the key byte-by-byte. This has nothing to do with HMAC request
signing; it's just the standard-library's constant-time comparison
function, reused here for its timing-safety property.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_api_key(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {get_settings().prospectforge_api_key}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
