from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from krayne.errors import ConfigValidationError


PRISM_DIR = Path.home() / ".krayne"
PRISM_CONFIG_FILE = PRISM_DIR / "config.yaml"


DEFAULT_KUBECONFIG = Path.home() / ".kube" / "config"


@dataclass
class KrayneSettings:
    """User-level settings persisted across CLI invocations."""

    kubeconfig: str | None = None
    kube_context: str | None = None


def load_krayne_settings() -> KrayneSettings:
    """Load settings from ``~/.krayne/config.yaml``.

    Returns defaults when the file is absent.  Raises
    :class:`ConfigValidationError` when the file is malformed, when the
    kubeconfig it references does not exist, when ``kube_context`` is
    set without ``kubeconfig``, or when the context is not present in
    the referenced kubeconfig.
    """
    if not PRISM_CONFIG_FILE.exists():
        return KrayneSettings()

    try:
        raw = yaml.safe_load(PRISM_CONFIG_FILE.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            f"Malformed YAML in {PRISM_CONFIG_FILE}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"Expected a YAML mapping in {PRISM_CONFIG_FILE}, "
            f"got {type(raw).__name__}"
        )

    settings = KrayneSettings(
        kubeconfig=raw.get("kubeconfig"),
        kube_context=raw.get("kube_context"),
    )

    if settings.kube_context is not None and settings.kubeconfig is None:
        raise ConfigValidationError(
            f"{PRISM_CONFIG_FILE}: 'kube_context' is set but 'kubeconfig' is "
            "not. Run `krayne init` to reconfigure."
        )

    if settings.kubeconfig is not None:
        kubeconfig_path = Path(settings.kubeconfig).expanduser()
        if not kubeconfig_path.exists():
            raise ConfigValidationError(
                f"{PRISM_CONFIG_FILE}: kubeconfig '{settings.kubeconfig}' "
                "does not exist. Run `krayne init` to reconfigure."
            )
        if settings.kube_context is not None:
            _assert_context_in_kubeconfig(
                kubeconfig_path, settings.kube_context
            )

    return settings


def _assert_context_in_kubeconfig(
    kubeconfig_path: Path, context: str
) -> None:
    try:
        raw: Any = yaml.safe_load(kubeconfig_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            f"Malformed kubeconfig at {kubeconfig_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"Expected a YAML mapping in {kubeconfig_path}, "
            f"got {type(raw).__name__}"
        )
    contexts = {
        c.get("name")
        for c in (raw.get("contexts") or [])
        if isinstance(c, dict)
    }
    if context not in contexts:
        available = ", ".join(sorted(n for n in contexts if n)) or "<none>"
        raise ConfigValidationError(
            f"{PRISM_CONFIG_FILE}: context '{context}' not found in "
            f"kubeconfig '{kubeconfig_path}'. Available: {available}. "
            "Run `krayne init` to reconfigure."
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
