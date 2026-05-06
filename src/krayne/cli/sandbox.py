from __future__ import annotations

import typer

from krayne.cli import app as _state
from krayne.errors import KrayneError
from krayne.output import (
    build_sandbox_progress_table,
    format_json,
    format_sandbox_setup_success,
    format_sandbox_status,
)

sandbox_app = typer.Typer(
    name="sandbox",
    help="Manage a local development sandbox (k3s + KubeRay).",
    no_args_is_help=True,
)
_state.app.add_typer(sandbox_app)


@sandbox_app.command("setup")
def sandbox_setup() -> None:
    """Set up a local k3s cluster with KubeRay."""
    from rich.live import Live

    from krayne.sandbox.manager import SETUP_STEPS

    steps: dict[str, str] = {s: "pending" for s in SETUP_STEPS}

    try:
        with Live(
            build_sandbox_progress_table(steps), console=_state.console, refresh_per_second=4
        ) as live:

            def _on_progress(step: str, status: str) -> None:
                steps[step] = status
                live.update(build_sandbox_progress_table(steps))

            kubeconfig_path = _state._setup_sandbox(on_progress=_on_progress)

        format_sandbox_setup_success(kubeconfig_path, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)


@sandbox_app.command("teardown")
def sandbox_teardown() -> None:
    """Tear down the local sandbox cluster."""
    try:
        _state._teardown_sandbox()
        _state.console.print("Sandbox removed.", style="green")
    except KrayneError as exc:
        _state._handle_error(exc)


@sandbox_app.command("status")
def sandbox_status_cmd() -> None:
    """Show the current status of the sandbox."""
    try:
        status = _state._sandbox_status()
        if _state._output_json:
            format_json(status, _state.console)
        else:
            format_sandbox_status(status, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)
