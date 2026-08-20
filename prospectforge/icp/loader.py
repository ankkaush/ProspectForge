"""Loads an ICPConfig from a YAML file and validates it beyond what
pydantic's field types alone can check.

Why YAML files on disk rather than a database table: nothing in the
pipeline yet needs to create or update an ICP config at runtime - it's
authored by a human, reviewed, and committed, matching Step 2's point that
ICP construction is a deliberate, reviewable decision, not something the
system generates. A database table for this would be complexity with no
current consumer; if ProspectForge later needs to edit ICPs through an
API/UI, that's a real reason to add one then.

Configs live in prospectforge/icp/configs/<icp_config_id>.yaml. The `id`
field inside the file must match the filename it's loaded by - a cheap
consistency check that catches a copy-pasted config whose internal id
wasn't updated.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import ACCOUNT_FIELD_PHASES, ICPConfig

CONFIGS_DIR = Path(__file__).parent / "configs"


class ICPConfigError(ValueError):
    """Raised for any problem with an ICP config - malformed YAML, a
    schema violation, or a criterion whose declared phase doesn't match
    the field it actually checks."""


def load_icp_config(icp_config_id: str) -> ICPConfig:
    path = CONFIGS_DIR / f"{icp_config_id}.yaml"
    if not path.exists():
        raise ICPConfigError(f"No ICP config found for id '{icp_config_id}' at {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ICPConfigError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ICPConfigError(f"{path} did not parse to a mapping (got {type(raw).__name__})")

    try:
        config = ICPConfig.model_validate(raw)
    except ValidationError as exc:
        raise ICPConfigError(f"Schema validation failed for {path}:\n{exc}") from exc

    if config.id != icp_config_id:
        raise ICPConfigError(
            f"Config file {path} has id='{config.id}', but was loaded as "
            f"'{icp_config_id}' - the id field must match the filename."
        )

    _validate_criterion_phases(config, path)

    return config


def _validate_criterion_phases(config: ICPConfig, path: Path) -> None:
    """The check the roadmap's Step 6 failure scenario names explicitly:
    a criterion claiming a field is available pre-enrichment when it's
    actually only known after enrichment (or vice versa) is a config bug,
    caught here rather than silently misleading Step 8's cheap filter."""

    all_criteria = [
        *config.tier_1_criteria,
        *config.tier_2_criteria,
        *config.disqualifiers,
    ]
    mismatches = [
        c
        for c in all_criteria
        if ACCOUNT_FIELD_PHASES[c.field] != c.phase
    ]
    if mismatches:
        details = "; ".join(
            f"field='{c.field}' declared phase={c.phase.value}, "
            f"actual phase={ACCOUNT_FIELD_PHASES[c.field].value}"
            for c in mismatches
        )
        raise ICPConfigError(
            f"{path} has criteria with an incorrect enrichment phase: {details}"
        )
