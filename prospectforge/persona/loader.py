"""Loads a PersonaConfig from a YAML file - same pattern and reasoning as
prospectforge/icp/loader.py: human-authored, reviewed, committed data, not
generated or edited at runtime.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import PersonaConfig

CONFIGS_DIR = Path(__file__).parent / "configs"


class PersonaConfigError(ValueError):
    pass


def load_persona_config(persona_id: str) -> PersonaConfig:
    path = CONFIGS_DIR / f"{persona_id}.yaml"
    if not path.exists():
        raise PersonaConfigError(f"No persona config found for id '{persona_id}' at {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise PersonaConfigError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise PersonaConfigError(f"{path} did not parse to a mapping (got {type(raw).__name__})")

    try:
        config = PersonaConfig.model_validate(raw)
    except ValidationError as exc:
        raise PersonaConfigError(f"Schema validation failed for {path}:\n{exc}") from exc

    if config.id != persona_id:
        raise PersonaConfigError(
            f"Config file {path} has id='{config.id}', but was loaded as "
            f"'{persona_id}' - the id field must match the filename."
        )

    return config
