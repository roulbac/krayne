"""Cluster detail screen — tabbed workspace for deep inspection."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.widgets import Static, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from krayne.api.clusters import describe_cluster, get_cluster_services
from krayne.api.types import ClusterDetails
from krayne.errors import KrayneError
from krayne.tunnel import is_tunnel_active, load_tunnel_state  # noqa: F401  (is_tunnel_active re-exported for legacy patch points)
from krayne.tui.screens._base import KrayneScreen, toggle_cluster_tunnels
from krayne.tui.screens._detail_tabs import (
    ConfigTab,
    OverviewTab,
    ServicesTab,
    TunnelsTab,
    WorkersTab,
)
from krayne.tui.widgets.header import HeaderBar
from krayne.tui.widgets.status_bar import StatusBar


class ClusterDetailScreen(KrayneScreen):
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
        yield header

        with TabbedContent(id="detail-tabs"):
            with TabPane("Overview", id="tab-overview"):
                yield OverviewTab("Loading...", id="overview-content")
            with TabPane("Worker Groups", id="tab-workers"):
                yield WorkersTab("Loading...", id="workers-content")
            with TabPane("Services", id="tab-services"):
                yield ServicesTab("Loading...", id="services-content")
            with TabPane("Tunnels", id="tab-tunnels"):
                yield TunnelsTab("Loading...", id="tunnels-content")
            with TabPane("Config", id="tab-config"):
                yield ConfigTab("Loading...", id="config-content")

        yield StatusBar()

    def on_mount(self) -> None:
        super().on_mount()
        self._set_status_hints([
            ("Esc", "Back"),
            ("s", "Scale"),
            ("d", "Delete"),
            ("r", "Refresh"),
            ("?", "Help"),
        ])
        self._fetch_details()

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

    def _render_all_tabs(self) -> None:
        if not self._details:
            return
        details = self._details
        services = self._services
        tunnel_map = self._load_tunnel_map()

        self.query_one(OverviewTab).update_data(details, services, tunnel_map)
        self.query_one(WorkersTab).update_data(details)
        self.query_one(ServicesTab).update_data(details, services, tunnel_map)
        self.query_one(TunnelsTab).update_data(services, tunnel_map)
        self.query_one(ConfigTab).update_data(details, services)

    def _load_tunnel_map(self) -> dict[str, str]:
        state = load_tunnel_state(self.cluster_name, self.namespace)
        if state is None:
            return {}
        return {t.service: t.local_url for t in state.tunnels}

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
                toggle_cluster_tunnels,
                self.cluster_name,
                self.namespace,
                self._services,
            ),
            thread=True,
            name="tunnel_action",
        )

    def action_refresh(self) -> None:
        self._fetch_details()
