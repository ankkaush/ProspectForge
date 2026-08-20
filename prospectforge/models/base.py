"""Shared base pieces used by several models, kept intentionally small."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ForgeModel(BaseModel):
    """Base class for every ProspectForge data contract.

    Pydantic (the library all these models are built on) is a data
    validation tool: you describe the *shape* a piece of data must have -
    field names, types, which are required - as a Python class, and pydantic
    checks any data assigned to it against that shape automatically. If a
    field is the wrong type, or a required field is missing, construction
    fails immediately with a clear error, instead of the bad data quietly
    flowing three pipeline stages downstream before something breaks.

    That matters most at the boundaries where ProspectForge doesn't control
    the data: a provider's API response, an LLM's output, an ICP config file.
    Pydantic validation is a *shape* guarantee, not a *truth* guarantee - a
    well-typed field can still hold a hallucinated or wrong value. That's why
    provenance (which object told us this, and how confident are we) is
    modeled explicitly (see Evidence, ProviderRecord) rather than assumed
    from the fact that a field validated successfully.
    """

    model_config = {"use_enum_values": False}
