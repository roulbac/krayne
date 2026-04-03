"""Main cluster list screen — the default view of ikrayne."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import Worker, WorkerState

from krayne.api.clusters import list_clusters
from krayne.api.types import ClusterInfo
from krayne.errors import KrayneError


def _style_status(status: str) -> str:
    if status in ("ready", "running"):
        return f"[green]{status}[/green]"
    if status in ("pods-pending", "containers-creating", "creating"):
        return f"[yellow]{status}[/yellow]"
    if status in ("image-pull-error", "crash-loop", "unschedulable", "pods-failed"):
        return f"[red]{status}[/red]"
    return f"[dim]{status}[/dim]"


class ClusterListScreen(Screen):
    """Displays a table of Ray clusters with keyboard-driven actions."""

    BINDINGS = [
        Binding("c", "create", "Create", show=True),
        Binding("s", "scale", "Scale", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("t", "tunnel", "Tunnel", show=True),
        Binding("slash", "filter", "Filter", show=True, key_display="/"),
        Binding("n", "namespace", "Namespace", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    _clusters: list[ClusterInfo] = []
    _filter_text: str = ""

    def compose(self):
        yield Static("ikrayne — Ray Cluster Manager", id="header-bar")
        yield Static("", id="namespace-label")
        table = DataTable(id="cluster-table")
        table.cursor_type = "row"
        yield table
        with Container(id="filter-container"):
            yield Input(placeholder="Filter by name...", id="filter-input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Name", "Namespace", "Status", "Workers", "Created")
        self._update_namespace_label()
        self._do_refresh()
        self.set_interval(5, self._do_refresh)

    def watch_app_namespace(self) -> None:
        self._update_namespace_label()
        self._do_refresh()

    def _update_namespace_label(self) -> None:
        label = self.query_one("#namespace-label", Static)
        label.update(f"  Namespace: {self.app.namespace}")

    def _do_refresh(self) -> None:
        self.run_worker(
            partial(list_clusters, namespace=self.app.namespace),
            thread=True,
            name="refresh_clusters",
            group="refresh",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "refresh_clusters":
            if event.worker.state == WorkerState.SUCCESS:
                self._clusters = event.worker.result
                self._rebuild_table()
            elif event.worker.state == WorkerState.ERROR:
                error = event.worker.error
                if isinstance(error, KrayneError):
                    self.notify(str(error), severity="error", timeout=5)
                else:
                    self.notify(f"Error: {error}", severity="error", timeout=5)
        elif event.worker.name == "toggle_tunnel":
            if event.worker.state == WorkerState.SUCCESS:
                msg = event.worker.result
                self.notify(str(msg), severity="information", timeout=3)
                self._do_refresh()
            elif event.worker.state == WorkerState.ERROR:
                self.notify(str(event.worker.error), severity="error", timeout=5)

    def _rebuild_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for cluster in self._clusters:
            if self._filter_text and self._filter_text.lower() not in cluster.name.lower():
                continue
            table.add_row(
                cluster.name,
                cluster.namespace,
                _style_status(cluster.status),
                str(cluster.num_workers),
                cluster.created_at,
                key=f"{cluster.namespace}/{cluster.name}",
            )

    def _get_selected_cluster(self) -> ClusterInfo | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        for cluster in self._clusters:
            if f"{cluster.namespace}/{cluster.name}" == row_key.value:
                return cluster
        return None

    # ── Events ───────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open detail screen when a row is selected (Enter)."""
        row_key = event.row_key
        for cluster in self._clusters:
            if f"{cluster.namespace}/{cluster.name}" == row_key.value:
                from krayne.tui.screens.cluster_detail import ClusterDetailScreen

                self.app.push_screen(ClusterDetailScreen(cluster.name, cluster.namespace))
                return

    # ── Actions ──────────────────────────────────────

    def action_create(self) -> None:
        from krayne.tui.screens.create_form import CreateFormScreen

        self.app.push_screen(CreateFormScreen())

    def action_scale(self) -> None:
        cluster = self._get_selected_cluster()
        if cluster is None:
            self.notify("No cluster selected", severity="warning")
            return
        from krayne.tui.screens.scale_form import ScaleFormScreen

        self.app.push_screen(ScaleFormScreen(cluster.name, cluster.namespace))

    def action_delete(self) -> None:
        cluster = self._get_selected_cluster()
        if cluster is None:
            self.notify("No cluster selected", severity="warning")
            return
        from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

        self.app.push_screen(DeleteConfirmScreen(cluster.name, cluster.namespace))

    def action_tunnel(self) -> None:
        cluster = self._get_selected_cluster()
        if cluster is None:
            self.notify("No cluster selected", severity="warning")
            return
        self.run_worker(
            partial(self._toggle_tunnel, cluster.name, cluster.namespace),
            thread=True,
            name="toggle_tunnel",
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

    def action_filter(self) -> None:
        container = self.query_one("#filter-container")
        if container.has_class("visible"):
            container.remove_class("visible")
            self._filter_text = ""
            self._rebuild_table()
        else:
            container.add_class("visible")
            self.query_one("#filter-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._filter_text = event.value
            self._rebuild_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            container = self.query_one("#filter-container")
            container.remove_class("visible")

    def action_namespace(self) -> None:
        from krayne.tui.screens.namespace_input import NamespaceInputScreen

        self.app.push_screen(NamespaceInputScreen())

    def action_refresh(self) -> None:
        self._do_refresh()
