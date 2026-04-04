"""Cluster detail screen — tabbed workspace for deep inspection."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from krayne.api.clusters import describe_cluster, get_cluster_services
from krayne.api.types import ClusterDetails
from krayne.errors import KrayneError
from krayne.tunnel import (
    SERVICE_PORTS,
    is_tunnel_active,
    load_tunnel_state,
    start_tunnels,
    stop_tunnels,
)
from krayne.tui.widgets.cluster_table import _age, _style_status
from krayne.tui.widgets.header import HeaderBar
from krayne.tui.widgets.status_bar import StatusBar


class ClusterDetailScreen(Screen):
    """Full-screen tabbed detail view for a single cluster."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("s", "scale", "Scale", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("t", "toggle_all_tunnels", "Tunnels", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(self, cluster_name: str, namespace: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.namespace = namespace
        self._details: ClusterDetails | None = None
        self._services: list[str] = []

    def compose(self):
        header = HeaderBar()
        header.view_title = "Detail"
        header.cluster_name = self.cluster_name
        header.namespace = self.namespace
        yield header

        with TabbedContent(id="detail-tabs"):
            with TabPane("Overview", id="tab-overview"):
                yield Static("Loading...", id="overview-content")
            with TabPane("Worker Groups", id="tab-workers"):
                yield Static("Loading...", id="workers-content")
            with TabPane("Services", id="tab-services"):
                yield Static("Loading...", id="services-content")
            with TabPane("Tunnels", id="tab-tunnels"):
                yield Static("Loading...", id="tunnels-content")
            with TabPane("Config", id="tab-config"):
                yield Static("Loading...", id="config-content")

        status = StatusBar()
        yield status

    def on_mount(self) -> None:
        self.add_class(self.app.terminal_class)
        self._set_status_hints()
        self._fetch_details()

    def _set_status_hints(self) -> None:
        bar = self.query_one(StatusBar)
        bar.set_hints([
            ("Esc", "Back"),
            ("s", "Scale"),
            ("d", "Delete"),
            ("r", "Refresh"),
            ("?", "Help"),
        ])

    def _fetch_details(self) -> None:
        self.run_worker(
            partial(self._load_data, self.cluster_name, self.namespace),
            thread=True,
            name="fetch_details",
        )

    @staticmethod
    def _load_data(name: str, namespace: str) -> tuple[ClusterDetails, list[str]]:
        details = describe_cluster(name, namespace)
        services = get_cluster_services(name, namespace)
        return details, services

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "fetch_details":
            if event.worker.state == WorkerState.SUCCESS:
                self._details, self._services = event.worker.result
                self._render_all_tabs()
            elif event.worker.state == WorkerState.ERROR:
                error = event.worker.error
                msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
                self.query_one("#overview-content", Static).update(f"[red]{msg}[/red]")
        elif event.worker.name == "tunnel_action":
            if event.worker.state == WorkerState.SUCCESS:
                self.notify(str(event.worker.result), severity="information", timeout=3)
                self._fetch_details()
            elif event.worker.state == WorkerState.ERROR:
                self.notify(str(event.worker.error), severity="error", timeout=5)

    # ── Tab rendering ───────────────────────────────

    def _render_all_tabs(self) -> None:
        self._render_overview()
        self._render_workers()
        self._render_services()
        self._render_tunnels()
        self._render_config()

    def _render_overview(self) -> None:
        if not self._details:
            return
        d = self._details
        info = d.info

        tunnel_active = is_tunnel_active(self.cluster_name, self.namespace)
        tunnel_state = load_tunnel_state(self.cluster_name, self.namespace) if tunnel_active else None

        lines: list[str] = []
        lines.append(f"[bold]{info.name}[/bold]")
        lines.append("")
        lines.append(f"  [dim]Status:[/dim]     {_style_status(info.status)}")
        lines.append(f"  [dim]Namespace:[/dim]  {info.namespace}")
        lines.append(f"  [dim]Age:[/dim]        {_age(info.created_at)}")
        lines.append(f"  [dim]Workers:[/dim]    {info.num_workers}")
        lines.append(f"  [dim]Ray:[/dim]        {d.ray_version}")
        if info.head_ip:
            lines.append(f"  [dim]Head IP:[/dim]   {info.head_ip}")

        # Service availability summary
        lines.append("")
        lines.append("[bold]Services[/bold]")
        for svc in ["dashboard", "notebook", "client", "code-server", "ssh"]:
            url = getattr(info, f"{svc.replace('-', '_')}_url", None)
            if url:
                lines.append(f"  [green]\u25cf[/green] {svc}")
            elif svc in self._services:
                lines.append(f"  [yellow]\u25cb[/yellow] {svc} [dim](no endpoint yet)[/dim]")

        # Tunnel summary
        lines.append("")
        lines.append("[bold]Tunnels[/bold]")
        if tunnel_state and tunnel_state.tunnels:
            for t in tunnel_state.tunnels:
                lines.append(f"  [green]\u25cf[/green] {t.service}: {t.local_url}")
        else:
            lines.append("  [dim]No active tunnels[/dim]")

        self.query_one("#overview-content", Static).update("\n".join(lines))

    def _render_workers(self) -> None:
        if not self._details:
            return
        lines: list[str] = []
        for wg in self._details.worker_groups:
            lines.append(f"[bold]{wg.name}[/bold]")
            lines.append(f"  [dim]Replicas:[/dim] {wg.replicas}")
            lines.append(f"  [dim]CPUs:[/dim]     {wg.cpus}")
            lines.append(f"  [dim]Memory:[/dim]   {wg.memory}")
            gpu_line = f"  [dim]GPUs:[/dim]     {wg.gpus}"
            if wg.gpu_type:
                gpu_line += f" ({wg.gpu_type})"
            lines.append(gpu_line)
            lines.append("")

        if not self._details.worker_groups:
            lines.append("[dim]No worker groups configured[/dim]")

        self.query_one("#workers-content", Static).update("\n".join(lines))

    def _render_services(self) -> None:
        if not self._details:
            return
        info = self._details.info
        tunnel_state = load_tunnel_state(self.cluster_name, self.namespace)
        tunnel_map: dict[str, str] = {}
        if tunnel_state:
            for t in tunnel_state.tunnels:
                tunnel_map[t.service] = t.local_url

        lines: list[str] = []
        for svc_name in SERVICE_PORTS:
            url_attr = svc_name.replace("-", "_") + "_url"
            endpoint = getattr(info, url_attr, None)
            available = svc_name in self._services
            tunnel_url = tunnel_map.get(svc_name)
            tunnel_active = svc_name in tunnel_map

            # Status indicator
            if available:
                status = "[green]\u25cf[/green] available"
            else:
                status = "[dim]\u25cb unavailable[/dim]"

            # Endpoint display
            if tunnel_active and tunnel_url:
                ep = f"[cyan]{tunnel_url}[/cyan]"
            elif endpoint:
                ep = f"[dim]{endpoint}[/dim]"
            else:
                ep = "[dim]\u2014[/dim]"

            # Tunnel state
            if tunnel_active:
                tun = "[green]tunnel open[/green]"
            elif available:
                tun = "[dim]tunnel closed[/dim]"
            else:
                tun = ""

            lines.append(f"  [bold]{svc_name:<14}[/bold] {status:<30} {ep}  {tun}")

        if not lines:
            lines.append("[dim]No services detected[/dim]")

        self.query_one("#services-content", Static).update("\n".join(lines))

    def _render_tunnels(self) -> None:
        if not self._details:
            return
        tunnel_state = load_tunnel_state(self.cluster_name, self.namespace)
        tunnel_map: dict[str, str] = {}
        if tunnel_state:
            for t in tunnel_state.tunnels:
                tunnel_map[t.service] = t.local_url

        lines: list[str] = []
        lines.append("[bold]Per-Service Tunnel Control[/bold]")
        lines.append("[dim]Use the Services tab buttons or these keyboard shortcuts:[/dim]")
        lines.append("")

        for svc_name in SERVICE_PORTS:
            available = svc_name in self._services
            tunnel_url = tunnel_map.get(svc_name)
            tunnel_active = svc_name in tunnel_map

            if tunnel_active and tunnel_url:
                status = f"[green]\u25cf active[/green]  {tunnel_url}"
            elif available:
                status = "[dim]\u25cb closed[/dim]"
            else:
                status = "[dim]\u2014 unavailable[/dim]"

            lines.append(f"  [bold]{svc_name:<14}[/bold] {status}")

        lines.append("")
        if tunnel_map:
            lines.append(f"[dim]{len(tunnel_map)} tunnel(s) active[/dim]")
        else:
            lines.append("[dim]No active tunnels[/dim]")

        self.query_one("#tunnels-content", Static).update("\n".join(lines))

    def _render_config(self) -> None:
        if not self._details:
            return
        d = self._details
        info = d.info
        lines: list[str] = []

        lines.append("[bold]Head Node[/bold]")
        lines.append(f"  CPUs: {d.head.cpus}    Memory: {d.head.memory}    GPUs: {d.head.gpus}")
        lines.append(f"  Image: {d.head.image}")

        lines.append("")
        lines.append("[bold]Worker Groups[/bold]")
        for wg in d.worker_groups:
            lines.append(f"  [bold]{wg.name}[/bold]")
            parts = [f"Replicas: {wg.replicas}", f"CPUs: {wg.cpus}", f"Memory: {wg.memory}"]
            if wg.gpus:
                gpu = f"GPUs: {wg.gpus}"
                if wg.gpu_type:
                    gpu += f" ({wg.gpu_type})"
                parts.append(gpu)
            lines.append(f"    {', '.join(parts)}")

        lines.append("")
        lines.append("[bold]Services[/bold]")
        for svc in self._services:
            lines.append(f"  \u2713 {svc}")
        if not self._services:
            lines.append("  [dim]None detected[/dim]")

        self.query_one("#config-content", Static).update("\n".join(lines))

    # ── Tunnel actions ──────────────────────────────

    @staticmethod
    def _toggle_all_tunnels(name: str, namespace: str, services: list[str]) -> str:
        if is_tunnel_active(name, namespace):
            stop_tunnels(name, namespace)
            return f"All tunnels closed for {name}"
        start_tunnels(name, namespace, services)
        return f"All tunnels opened for {name}"

    # ── Actions ─────────────────────────────────────

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_scale(self) -> None:
        from krayne.tui.screens.scale_flow import ScaleFlowScreen

        self.app.push_screen(ScaleFlowScreen(self.cluster_name, self.namespace))

    def action_delete(self) -> None:
        from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

        def _on_deleted(deleted: bool) -> None:
            if deleted:
                self.app.pop_screen()

        self.app.push_screen(
            DeleteConfirmScreen(self.cluster_name, self.namespace),
            callback=_on_deleted,
        )

    def action_toggle_all_tunnels(self) -> None:
        self.run_worker(
            partial(
                self._toggle_all_tunnels,
                self.cluster_name,
                self.namespace,
                self._services,
            ),
            thread=True,
            name="tunnel_action",
        )

    def action_refresh(self) -> None:
        self._fetch_details()
