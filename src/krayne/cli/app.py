from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from krayne import __version__
from krayne.api import (
    create_cluster as _create_cluster,
    delete_cluster as _delete_cluster,
    describe_cluster as _describe_cluster,
    get_cluster as _get_cluster,
    get_cluster_services as _get_cluster_services,
    list_clusters as _list_clusters,
    scale_cluster as _scale_cluster,
)
from krayne.config import save_krayne_settings
from krayne.errors import KrayneError
from krayne.sandbox import (
    sandbox_status as _sandbox_status,
    setup_sandbox as _setup_sandbox,
    teardown_sandbox as _teardown_sandbox,
)

app = typer.Typer(
    name="krayne",
    help="Create, manage, and scale Ray clusters on Kubernetes.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

# Command modules read these at call time via ``krayne.cli.app._<name>``
# so the CLI callback's writes are visible across the package.
_debug: bool = False
_output_json: bool = False
_kubeconfig: str | None = None


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"krayne {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
    kubeconfig: Optional[str] = typer.Option(None, "--kubeconfig", help="Path to kubeconfig file."),
) -> None:
    """Krayne — Ray clusters made simple."""
    global _debug, _output_json, _kubeconfig
    _debug = debug
    _output_json = output == "json"
    _kubeconfig = kubeconfig


def _handle_error(exc: Exception) -> None:
    if _debug:
        err_console.print_exception()
    else:
        err_console.print(Panel(str(exc), title="Error", border_style="red"))
    raise typer.Exit(1)


def _current_krayne_context_for(kubeconfig: Path) -> str | None:
    """Return the kube_context saved in ~/.krayne/config.yaml when it's
    tied to *kubeconfig*, else ``None``.

    Read raw (not via ``load_krayne_settings``) so a stale or invalid
    settings file doesn't prevent ``krayne init`` from running — the
    whole point of re-init is to fix such a file.
    """
    import yaml as _yaml

    from krayne.config.settings import PRISM_CONFIG_FILE

    if not PRISM_CONFIG_FILE.exists():
        return None
    try:
        raw = _yaml.safe_load(PRISM_CONFIG_FILE.read_text()) or {}
    except _yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None
    saved_kubeconfig = raw.get("kubeconfig")
    if saved_kubeconfig is None:
        return None
    try:
        same = Path(saved_kubeconfig).expanduser().resolve() == kubeconfig
    except OSError:
        same = False
    if not same:
        return None
    ctx = raw.get("kube_context")
    return ctx if isinstance(ctx, str) else None


# Register commands by importing their modules.  Each module attaches
# its commands to ``app`` (or to a sub-Typer added to ``app``) at
# import time.
from krayne.cli import clusters as _clusters  # noqa: E402, F401
from krayne.cli import sandbox as _sandbox  # noqa: E402, F401
from krayne.cli import tunnel as _tunnel  # noqa: E402, F401
