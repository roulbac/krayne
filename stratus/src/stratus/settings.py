"""Where stratus keeps its state and how it finds the SkyPilot API server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ENDPOINT_ENV = "SKYPILOT_API_SERVER_ENDPOINT"
DEFAULT_ENDPOINT = "http://127.0.0.1:46580"


def stratus_home() -> Path:
    """Root directory for stratus state (``$STRATUS_HOME`` or ``~/.stratus``)."""
    return Path(os.environ.get("STRATUS_HOME", Path.home() / ".stratus")).expanduser()


def specs_dir() -> Path:
    return stratus_home() / "specs"


def config_path() -> Path:
    return stratus_home() / "config.yaml"


def sandbox_dir() -> Path:
    return stratus_home() / "sandbox"


@dataclass
class Settings:
    endpoint: str | None = None
    refresh_seconds: float = 3.0
    editor: str | None = None


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return Settings(
        endpoint=data.get("endpoint"),
        refresh_seconds=float(data.get("refresh_seconds", 3.0)),
        editor=data.get("editor"),
    )


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in vars(settings).items() if v is not None}
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _sky_config_endpoint() -> str | None:
    """Endpoint configured for the SkyPilot CLI itself (``sky api login``)."""
    path = Path(os.environ.get("SKYPILOT_CONFIG", Path.home() / ".sky" / "config.yaml")).expanduser()
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return None
    api = data.get("api_server") if isinstance(data, dict) else None
    if isinstance(api, dict) and api.get("endpoint"):
        return str(api["endpoint"])
    return None


def resolve_endpoint(explicit: str | None = None) -> tuple[str, str]:
    """Return ``(endpoint, source)``.

    Order: ``--endpoint`` flag, ``$SKYPILOT_API_SERVER_ENDPOINT``,
    ``~/.stratus/config.yaml``, ``~/.sky/config.yaml``, then the local default.
    """
    if explicit:
        return explicit.rstrip("/"), "--endpoint"
    env = os.environ.get(ENDPOINT_ENV)
    if env:
        return env.rstrip("/"), f"${ENDPOINT_ENV}"
    settings = load_settings()
    if settings.endpoint:
        return settings.endpoint.rstrip("/"), str(config_path())
    sky = _sky_config_endpoint()
    if sky:
        return sky.rstrip("/"), "~/.sky/config.yaml"
    return DEFAULT_ENDPOINT, "default"
