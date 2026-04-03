"""Cluster detail screen — shows full cluster information."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState

from krayne.api.clusters import describe_cluster
from krayne.api.types import ClusterDetails
from krayne.errors import KrayneError
from krayne.tunnel import is_tunnel_active, load_tunnel_state


def _style_status(status: str) -> str:
    if status in ("ready", "running"):
        return f"[green]{status}[/green]"
    if status in ("pods-pending", "containers-creating", "creating"):
        return f"[yellow]{status}[/yellow]"
    if status in ("image-pull-error", "crash-loop", "unschedulable", "pods-failed"):
        return f"[red]{status}[/red]"
    return f"[dim]{status}[/dim]"


class ClusterDetailScreen(Screen):
    """Full-screen detail view for a single cluster."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back", show=True),
        Binding("s", "scale", "Scale", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("t", "tunnel", "Tunnel", show=True),
    ]

    def __init__(self, cluster_name: str, namespace: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.namespace = namespace
        self._details: ClusterDetails | None = None

    def compose(self):
        yield Static("Loading...", id="detail-content")
        yield Footer()

    def on_mount(self) -> None:
        self._fetch_details()

    def _fetch_details(self) -> None:
        self.run_worker(
            partial(describe_cluster, self.cluster_name, self.namespace),
            thread=True,
            name="fetch_details",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "fetch_details":
            if event.worker.state == WorkerState.SUCCESS:
                self._details = event.worker.result
                self._render_details()
            elif event.worker.state == WorkerState.ERROR:
                error = event.worker.error
                content = self.query_one("#detail-content", Static)
                if isinstance(error, KrayneError):
                    content.update(f"[red]Error: {error}[/red]")
                else:
                    content.update(f"[red]Error: {error}[/red]")
        elif event.worker.name == "toggle_tunnel_detail":
            if event.worker.state == WorkerState.SUCCESS:
                self.notify(str(event.worker.result), severity="information", timeout=3)
                self._fetch_details()
            elif event.worker.state == WorkerState.ERROR:
                self.notify(str(event.worker.error), severity="error", timeout=5)

    def _render_details(self) -> None:
        if self._details is None:
            return
        d = self._details
        info = d.info

        tunnel_active = is_tunnel_active(self.cluster_name, self.namespace)
        tunnel_state = load_tunnel_state(self.cluster_name, self.namespace) if tunnel_active else None

        lines: list[str] = []
        lines.append(f"[bold cyan]Cluster:[/bold cyan] {info.name}")
        lines.append(f"[bold cyan]Namespace:[/bold cyan] {info.namespace}")
        lines.append(f"[bold cyan]Status:[/bold cyan] {_style_status(info.status)}")
        lines.append(f"[bold cyan]Workers:[/bold cyan] {info.num_workers}")
        lines.append(f"[bold cyan]Created:[/bold cyan] {info.created_at}")
        lines.append(f"[bold cyan]Ray Version:[/bold cyan] {d.ray_version}")
        lines.append(f"[bold cyan]Python Version:[/bold cyan] {d.python_version}")

        if info.head_ip:
            lines.append(f"[bold cyan]Head IP:[/bold cyan] {info.head_ip}")

        lines.append("")
        lines.append("[bold $accent]HEAD NODE[/bold $accent]")
        lines.append(f"  CPUs: {d.head.cpus}    Memory: {d.head.memory}    GPUs: {d.head.gpus}")
        lines.append(f"  Image: {d.head.image}")

        lines.append("")
        lines.append("[bold $accent]WORKER GROUPS[/bold $accent]")
        for wg in d.worker_groups:
            lines.append(f"  [bold]{wg.name}[/bold]")
            lines.append(f"    Replicas: {wg.replicas}    CPUs: {wg.cpus}    Memory: {wg.memory}")
            gpu_info = f"    GPUs: {wg.gpus}"
            if wg.gpu_type:
                gpu_info += f" ({wg.gpu_type})"
            lines.append(gpu_info)

        lines.append("")
        lines.append("[bold $accent]TUNNELS[/bold $accent]")
        if tunnel_state and tunnel_state.tunnels:
            for t in tunnel_state.tunnels:
                lines.append(f"  [green]●[/green] {t.service}: {t.local_url} → :{t.remote_port}")
        else:
            lines.append("  [dim]No active tunnels (press t to open)[/dim]")

        if info.dashboard_url:
            lines.append("")
            lines.append("[bold $accent]URLS[/bold $accent]")
            if info.dashboard_url:
                lines.append(f"  Dashboard: {info.dashboard_url}")
            if info.client_url:
                lines.append(f"  Client: {info.client_url}")
            if info.notebook_url:
                lines.append(f"  Notebook: {info.notebook_url}")

        content = self.query_one("#detail-content", Static)
        content.update("\n".join(lines))

    # ── Actions ──────────────────────────────────────

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def action_scale(self) -> None:
        from krayne.tui.screens.scale_form import ScaleFormScreen

        self.app.push_screen(ScaleFormScreen(self.cluster_name, self.namespace))

    def action_delete(self) -> None:
        from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

        def _on_deleted(deleted: bool) -> None:
            if deleted:
                self.app.pop_screen()

        self.app.push_screen(
            DeleteConfirmScreen(self.cluster_name, self.namespace),
            callback=_on_deleted,
        )

    def action_tunnel(self) -> None:
        self.run_worker(
            partial(self._toggle_tunnel, self.cluster_name, self.namespace),
            thread=True,
            name="toggle_tunnel_detail",
        )

    @staticmethod
    def _toggle_tunnel(name: str, namespace: str) -> str:
        from krayne.tunnel import is_tunnel_active, start_tunnels, stop_tunnels
        from krayne.api.clusters import get_cluster_services

        if is_tunnel_active(name, namespace):
            stop_tunnels(name, namespace)
            return f"Tunnels closed for {name}"
        services = get_cluster_services(name, namespace)
        start_tunnels(name, namespace, services)
        return f"Tunnels opened for {name}"
