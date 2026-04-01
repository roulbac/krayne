from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml
from pydantic import ValidationError

from prism.config.models import ClusterConfig
from prism.errors import ConfigValidationError


def load_config_from_yaml(
    path: str | Path,
    overrides: dict[str, Any] | None = None,
) -> ClusterConfig:
    """Load a ClusterConfig from a YAML file with optional field overrides.

    Parameters
    ----------
    path:
        Path to the YAML configuration file.
    overrides:
        Dict of field values that take precedence over the YAML contents.
        Supports dot-notation keys like ``"head.cpus"`` for nested fields.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"Expected a YAML mapping at top level, got {type(raw).__name__}"
        )

    if overrides:
        for key, value in overrides.items():
            _deep_set(raw, key.split("."), value)

    try:
        return ClusterConfig(**raw)
    except ValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc


def _deep_set(data: dict, keys: list[str], value: Any) -> None:
    for key in keys[:-1]:
        data = data.setdefault(key, {})
    data[keys[-1]] = value


async def _async_load_config_from_yaml(
    path: str | Path,
    overrides: dict[str, Any] | None = None,
) -> ClusterConfig:
    """Async version of :func:`load_config_from_yaml`."""
    raw = yaml.safe_load(await anyio.Path(path).read_text())
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"Expected a YAML mapping at top level, got {type(raw).__name__}"
        )

    if overrides:
        for key, value in overrides.items():
            _deep_set(raw, key.split("."), value)

    try:
        return ClusterConfig(**raw)
    except ValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc
