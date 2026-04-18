"""Cluster Explorer — the default home screen of the Krayne TUI."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial

from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Static
from textual.worker import Worker, WorkerState

from krayne.api.clusters import list_clusters
from krayne.api.types import ClusterInfo
from krayne.errors import KrayneError
from krayne.tunnel import is_tunnel_active
from krayne.tui.widgets.cluster_table import ClusterTable
from krayne.tui.widgets.filter_bar import FilterBar, make_filter_fn
from krayne.tui.widgets.header import HeaderBar
from krayne.tui.widgets.preview_panel import PreviewPanel
from krayne.tui.widgets.status_bar import StatusBar


class ExplorerScreen(Screen):
    """Cluster list with filtering, sorting, preview, and actions."""

    BINDINGS = [
        Binding("c", "create", "Create", show=False),
        Binding("s", "scale", "Scale", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("t", "tunnel", "Tunnel", show=False),
        Binding("slash", "filter", "Filter", show=False, key_display="/"),
        Binding("n", "namespace", "Namespace", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("tab", "cycle_sort", "Sort", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._clusters: list[ClusterInfo] = []
        self._tunnel_states: dict[str, bool] = {}

    def compose(self):
        yield HeaderBar()
        yield Static("", id="scope-bar")
        with Vertical(id="explorer-body"):
            yield ClusterTable(id="cluster-table")
            yield PreviewPanel()
        # Empty state (hidden by default)
        with Vertical(id="empty-state"):
            yield Static("[bold]No clusters found[/bold]")
            yield Static(" ")
            yield Static("[dim]Press [bold]c[/bold] to create a cluster or [bold]n[/bold] to switch namespace[/dim]")
        yield FilterBar(id="filter-bar")
        status = StatusBar()
        yield status

    def on_mount(self) -> None:
        # Add terminal class
        self.add_class(self.app.terminal_class)
        # Setup columns
        table = self.query_one(ClusterTable)
        table.setup_columns(self.app.terminal_class)
        # Update header
        self._update_header()
        self._update_scope_bar()
        self._set_status_hints()
        # Textual does not auto-wire `watch_app_<name>` — register the
        # cross-object watchers explicitly so resize and namespace
        # changes reach this screen.
        self.watch(self.app, "terminal_class", self._on_terminal_class_change, init=False)
        self.watch(self.app, "namespace", self._on_namespace_change, init=False)
        # Initial fetch
        self._do_refresh()
        self.set_interval(5, self._do_refresh)

    def _on_namespace_change(self, old: str, new: str) -> None:
        self._update_header()
        self._update_scope_bar()
        self._do_refresh()

    def _on_terminal_class_change(self, old: str, new: str) -> None:
        self.remove_class(old)
        self.add_class(new)
        table = self.query_one(ClusterTable)
        table.setup_columns(new)
        table.rebuild(self._clusters, self._get_filter_fn())

    # ── Data fetching ───────────────────────────────

    def _do_refresh(self) -> None:
        self.app.is_loading = True
        self.run_worker(
            partial(self._fetch_data, self.app.namespace),
            thread=True,
            name="refresh_clusters",
            group="refresh",
        )

    @staticmethod
    def _fetch_data(namespace: str) -> tuple[list[ClusterInfo], dict[str, bool]]:
        clusters = list_clusters(namespace=namespace)
        tunnel_states = {}
        for c in clusters:
            key = f"{c.namespace}/{c.name}"
            tunnel_states[key] = is_tunnel_active(c.name, c.namespace)
        return clusters, tunnel_states

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "refresh_clusters":
            if event.worker.state == WorkerState.SUCCESS:
                self._clusters, self._tunnel_states = event.worker.result
                self.app.is_loading = False
                now = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self.app.last_refresh = now
                self._rebuild_table()
                self._update_scope_bar()
                self._update_header()
                self._update_empty_state()
            elif event.worker.state == WorkerState.ERROR:
                self.app.is_loading = False
                error = event.worker.error
                msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
                self.notify(msg, severity="error", timeout=5)
        elif event.worker.name == "toggle_tunnel":
            if event.worker.state == WorkerState.SUCCESS:
                self.notify(str(event.worker.result), severity="information", timeout=3)
                self._do_refresh()
            elif event.worker.state == WorkerState.ERROR:
                self.notify(str(event.worker.error), severity="error", timeout=5)

    def _rebuild_table(self) -> None:
        table = self.query_one(ClusterTable)
        table.set_tunnel_states(self._tunnel_states)
        table.rebuild(self._clusters, self._get_filter_fn())

    def _get_filter_fn(self):
        filter_bar = self.query_one(FilterBar)
        text = filter_bar.filter_text
        return make_filter_fn(text, self._tunnel_states)

    # ── UI updates ──────────────────────────────────

    def _update_header(self) -> None:
        header = self.query_one(HeaderBar)
        header.namespace = self.app.namespace
        header.view_title = "Explorer"
        header.cluster_name = ""
        header.is_loading = self.app.is_loading
        header.last_refresh = self.app.last_refresh

    def _update_scope_bar(self) -> None:
        scope = self.query_one("#scope-bar", Static)
        total = len(self._clusters)
        healthy = sum(1 for c in self._clusters if c.status in ("ready", "running"))
        scope.update(
            f"  [dim]Namespace:[/dim] {self.app.namespace}  "
            f"[dim]Clusters:[/dim] {total}  "
            f"[dim]Healthy:[/dim] {healthy}/{total}"
        )

    def _update_empty_state(self) -> None:
        empty = self.query_one("#empty-state")
        table = self.query_one(ClusterTable)
        if not self._clusters:
            empty.display = True
            table.display = False
        else:
            empty.display = False
            table.display = True

    def _set_status_hints(self) -> None:
        bar = self.query_one(StatusBar)
        bar.set_hints([
            ("c", "Create"),
            ("Enter", "Detail"),
            ("s", "Scale"),
            ("d", "Delete"),
            ("t", "Tunnel"),
            ("/", "Filter"),
            ("n", "NS"),
            ("r", "Refresh"),
            ("?", "Help"),
        ])

    # ── Selection ───────────────────────────────────

    def _get_selected_cluster(self) -> ClusterInfo | None:
        table = self.query_one(ClusterTable)
        key = table.get_selected_key()
        if not key:
            return None
        for cluster in self._clusters:
            if f"{cluster.namespace}/{cluster.name}" == key:
                return cluster
        return None

    def on_data_table_cursor_moved(self, event: DataTable.CursorMoved) -> None:
        """Update preview panel when cursor moves."""
        cluster = self._get_selected_cluster()
        preview = self.query_one(PreviewPanel)
        if cluster:
            key = f"{cluster.namespace}/{cluster.name}"
            tunnel_active = self._tunnel_states.get(key, False)
            preview.update_cluster(cluster, tunnel_active)
            self.app.selected_cluster = key
        else:
            preview.update_cluster(None)
            self.app.selected_cluster = ""

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open detail screen on Enter."""
        cluster = self._get_selected_cluster()
        if cluster:
            from krayne.tui.screens.detail import ClusterDetailScreen

            self.app.push_screen(ClusterDetailScreen(cluster.name, cluster.namespace))

    # ── Actions ─────────────────────────────────────

    def action_create(self) -> None:
        from krayne.tui.screens.create_flow import CreateFlowScreen

        self.app.push_screen(CreateFlowScreen())

    def action_scale(self) -> None:
        cluster = self._get_selected_cluster()
        if not cluster:
            self.notify("No cluster selected", severity="warning")
            return
        from krayne.tui.screens.scale_flow import ScaleFlowScreen

        self.app.push_screen(ScaleFlowScreen(cluster.name, cluster.namespace))

    def action_delete(self) -> None:
        cluster = self._get_selected_cluster()
        if not cluster:
            self.notify("No cluster selected", severity="warning")
            return
        from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

        self.app.push_screen(DeleteConfirmScreen(cluster.name, cluster.namespace))

    def action_tunnel(self) -> None:
        cluster = self._get_selected_cluster()
        if not cluster:
            self.notify("No cluster selected", severity="warning")
            return
        self.run_worker(
            partial(self._toggle_tunnel, cluster.name, cluster.namespace),
            thread=True,
            name="toggle_tunnel",
        )

    @staticmethod
    def _toggle_tunnel(name: str, namespace: str) -> str:
        from krayne.api.clusters import get_cluster_services
        from krayne.tunnel import is_tunnel_active, start_tunnels, stop_tunnels

        if is_tunnel_active(name, namespace):
            stop_tunnels(name, namespace)
            return f"Tunnels closed for {name}"
        services = get_cluster_services(name, namespace)
        start_tunnels(name, namespace, services)
        return f"Tunnels opened for {name}"

    def action_filter(self) -> None:
        filter_bar = self.query_one(FilterBar)
        if filter_bar.has_class("visible"):
            filter_bar.remove_class("visible")
            filter_bar.clear()
            self._rebuild_table()
        else:
            filter_bar.add_class("visible")
            filter_bar.focus_input()

    def on_filter_bar_changed(self, event) -> None:
        """Rebuild table when filter text changes."""
        self._rebuild_table()

    def action_cycle_sort(self) -> None:
        table = self.query_one(ClusterTable)
        col = table.cycle_sort()
        self.notify(f"Sort: {col}", severity="information", timeout=2)
        self._rebuild_table()

    def action_namespace(self) -> None:
        from krayne.tui.screens.namespace_picker import NamespacePickerScreen

        self.app.push_screen(NamespacePickerScreen())

    def action_refresh(self) -> None:
        self._do_refresh()
