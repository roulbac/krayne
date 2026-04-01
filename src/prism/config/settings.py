from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import yaml


PRISM_DIR = Path.home() / ".prism"
PRISM_CONFIG_FILE = PRISM_DIR / "config.yaml"


DEFAULT_KUBECONFIG = Path.home() / ".kube" / "config"


@dataclass
class PrismSettings:
    """User-level settings persisted across CLI invocations."""

    kubeconfig: str | None = None
    kube_context: str | None = None


def load_prism_settings() -> PrismSettings:
    """Load settings from ``~/.prism/config.yaml``, returning defaults if absent."""
    if not PRISM_CONFIG_FILE.exists():
        return PrismSettings()
    raw = yaml.safe_load(PRISM_CONFIG_FILE.read_text()) or {}
    return PrismSettings(
        kubeconfig=raw.get("kubeconfig"),
        kube_context=raw.get("kube_context"),
    )


def save_prism_settings(settings: PrismSettings) -> None:
    """Write settings to ``~/.prism/config.yaml``, creating the directory if needed."""
    PRISM_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings.kubeconfig is not None:
        data["kubeconfig"] = settings.kubeconfig
    if settings.kube_context is not None:
        data["kube_context"] = settings.kube_context
    PRISM_CONFIG_FILE.write_text(yaml.safe_dump(data, default_flow_style=False))


def clear_prism_settings() -> None:
    """Remove the settings file if it exists."""
    if PRISM_CONFIG_FILE.exists():
        PRISM_CONFIG_FILE.unlink()


# ---------------------------------------------------------------------------
# Async variants (used by async internal implementations)
# ---------------------------------------------------------------------------


async def _async_load_prism_settings() -> PrismSettings:
    """Async version of :func:`load_prism_settings`."""
    config_path = anyio.Path(PRISM_CONFIG_FILE)
    if not await config_path.exists():
        return PrismSettings()
    raw = yaml.safe_load(await config_path.read_text()) or {}
    return PrismSettings(
        kubeconfig=raw.get("kubeconfig"),
        kube_context=raw.get("kube_context"),
    )


async def _async_save_prism_settings(settings: PrismSettings) -> None:
    """Async version of :func:`save_prism_settings`."""
    await anyio.Path(PRISM_DIR).mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if settings.kubeconfig is not None:
        data["kubeconfig"] = settings.kubeconfig
    if settings.kube_context is not None:
        data["kube_context"] = settings.kube_context
    await anyio.Path(PRISM_CONFIG_FILE).write_text(
        yaml.safe_dump(data, default_flow_style=False)
    )


async def _async_clear_prism_settings() -> None:
    """Async version of :func:`clear_prism_settings`."""
    config_path = anyio.Path(PRISM_CONFIG_FILE)
    if await config_path.exists():
        await config_path.unlink()
