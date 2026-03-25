"""Persistent Prism settings stored at ~/.prism/config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PRISM_DIR = Path.home() / ".prism"
PRISM_CONFIG_FILE = PRISM_DIR / "config.yaml"


@dataclass
class PrismSettings:
    """User-level settings persisted across CLI invocations."""

    kubeconfig: str | None = None


def load_prism_settings() -> PrismSettings:
    """Load settings from ``~/.prism/config.yaml``, returning defaults if absent."""
    if not PRISM_CONFIG_FILE.exists():
        return PrismSettings()
    raw = yaml.safe_load(PRISM_CONFIG_FILE.read_text()) or {}
    return PrismSettings(kubeconfig=raw.get("kubeconfig"))


def save_prism_settings(settings: PrismSettings) -> None:
    """Write settings to ``~/.prism/config.yaml``, creating the directory if needed."""
    PRISM_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings.kubeconfig is not None:
        data["kubeconfig"] = settings.kubeconfig
    PRISM_CONFIG_FILE.write_text(yaml.safe_dump(data, default_flow_style=False))


def clear_prism_settings() -> None:
    """Remove the settings file if it exists."""
    if PRISM_CONFIG_FILE.exists():
        PRISM_CONFIG_FILE.unlink()
