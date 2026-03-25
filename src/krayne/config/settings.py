from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PRISM_DIR = Path.home() / ".krayne"
PRISM_CONFIG_FILE = PRISM_DIR / "config.yaml"


DEFAULT_KUBECONFIG = Path.home() / ".kube" / "config"


@dataclass
class KrayneSettings:
    """User-level settings persisted across CLI invocations."""

    kubeconfig: str | None = None
    kube_context: str | None = None


def load_krayne_settings() -> KrayneSettings:
    """Load settings from ``~/.krayne/config.yaml``, returning defaults if absent."""
    if not PRISM_CONFIG_FILE.exists():
        return KrayneSettings()
    raw = yaml.safe_load(PRISM_CONFIG_FILE.read_text()) or {}
    return KrayneSettings(
        kubeconfig=raw.get("kubeconfig"),
        kube_context=raw.get("kube_context"),
    )


def save_krayne_settings(settings: KrayneSettings) -> None:
    """Write settings to ``~/.krayne/config.yaml``, creating the directory if needed."""
    PRISM_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings.kubeconfig is not None:
        data["kubeconfig"] = settings.kubeconfig
    if settings.kube_context is not None:
        data["kube_context"] = settings.kube_context
    PRISM_CONFIG_FILE.write_text(yaml.safe_dump(data, default_flow_style=False))


def clear_krayne_settings() -> None:
    """Remove the settings file if it exists."""
    if PRISM_CONFIG_FILE.exists():
        PRISM_CONFIG_FILE.unlink()
