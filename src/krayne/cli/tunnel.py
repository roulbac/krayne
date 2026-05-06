from __future__ import annotations

import typer

from krayne.cli import app as _state
from krayne.errors import KrayneError
from krayne.output import format_json, format_tunnel_panel


@_state.app.command("tun-open")
def tun_open(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """Open tunnels for cluster services to localhost."""
    from krayne.tunnel import is_tunnel_active, load_tunnel_state, start_tunnels

    try:
        if is_tunnel_active(name, namespace):
            state = load_tunnel_state(name, namespace)
            assert state is not None
            if _state._output_json:
                format_json(state.tunnels, _state.console)
            else:
                _state.console.print(format_tunnel_panel(name, state.tunnels))
                _state.console.print("Tunnel already active.", style="dim")
            return

        info = _state._get_cluster(name, namespace, kubeconfig=_state._kubeconfig)
        if info.status not in ("ready", "running"):
            raise KrayneError(
                f"Cluster '{name}' is not ready (status: {info.status}). "
                "Wait for it to be ready before tunnelling."
            )

        services = _state._get_cluster_services(name, namespace, kubeconfig=_state._kubeconfig)
        if not services:
            raise KrayneError("No services detected on this cluster.")

        tunnels = start_tunnels(name, namespace, services, kubeconfig=_state._kubeconfig)

        if _state._output_json:
            format_json(tunnels, _state.console)
        else:
            _state.console.print(format_tunnel_panel(name, tunnels))
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("tun-close")
def tun_close(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """Stop tunnels for a cluster."""
    from krayne.tunnel import stop_tunnels

    try:
        stopped = stop_tunnels(name, namespace)
        if stopped:
            _state.console.print(f"Tunnel for '{name}' stopped.", style="green")
        else:
            _state.console.print(f"No active tunnel for '{name}'.", style="dim")
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("tui")
def tui() -> None:
    """Launch the interactive terminal UI."""
    from krayne.tui.entry import main as tui_main

    tui_main()
