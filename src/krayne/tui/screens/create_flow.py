"""Create cluster flow — tabbed creation form."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static, Switch, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from krayne.api.clusters import create_cluster
from krayne.config.models import (
    DEFAULT_CPUS,
    DEFAULT_HEAD_MEMORY,
    DEFAULT_MEMORY,
    ClusterConfig,
    HeadNodeConfig,
    ServicesConfig,
    WorkerGroupConfig,
)
from krayne.errors import KrayneError
from krayne.tui.widgets.header import HeaderBar
from krayne.tui.widgets.status_bar import StatusBar


class CreateFlowScreen(Screen):
    """Full-screen tabbed create cluster flow."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "submit", "Create", show=False),
        Binding("ctrl+shift+comma", "prev_tab", "Prev Tab", show=False, priority=True),
        Binding("ctrl+shift+full_stop", "next_tab", "Next Tab", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._extra_worker_groups: int = 0
        self._creating: bool = False

    # ── UI ──────────────────────────────────────────────

    def compose(self):
        header = HeaderBar()
        header.view_title = "Create Cluster"
        yield header

        with Vertical(id="create-flow-screen"):
            with TabbedContent(id="create-tabs"):
                # ── Tab 1: Cluster ──────────────────
                with TabPane("Cluster", id="tab-cluster"):
                    yield Static("[bold]Cluster[/bold]", classes="form-section-title")
                    with Horizontal(classes="form-row"):
                        yield Label("Name:")
                        yield Input(placeholder="my-cluster", id="input-name")
                        yield Label("Namespace:")
                        yield Input(value="default", id="input-namespace")

                # ── Tab 2: Head Node ────────────────
                with TabPane("Head Node", id="tab-head"):
                    yield Static("[bold]Head Node[/bold]", classes="form-section-title")
                    with Horizontal(classes="form-row"):
                        yield Label("CPUs:")
                        yield Input(value=DEFAULT_CPUS, id="input-head-cpus")
                        yield Label("Memory:")
                        yield Input(value=DEFAULT_HEAD_MEMORY, id="input-head-memory")
                    with Horizontal(classes="form-row"):
                        yield Label("GPU Type:")
                        yield Input(value="t4", id="input-head-gpu-type")
                        yield Label("GPUs:")
                        yield Input(value="0", id="input-head-gpus", type="integer")

                # ── Tab 3: Workers ──────────────────
                with TabPane("Workers", id="tab-workers"):
                    with Vertical(classes="form-section", id="section-wg0"):
                        yield Static("[bold]Worker Group 1[/bold]", classes="form-section-title")
                        with Horizontal(classes="form-row"):
                            yield Label("Group Name:")
                            yield Input(value="worker", id="input-wg0-name")
                            yield Label("Replicas:")
                            yield Input(value="1", id="input-wg0-replicas", type="integer")
                        with Horizontal(classes="form-row"):
                            yield Label("CPUs:")
                            yield Input(value=DEFAULT_CPUS, id="input-wg0-cpus")
                            yield Label("Memory:")
                            yield Input(value=DEFAULT_MEMORY, id="input-wg0-memory")
                        with Horizontal(classes="form-row"):
                            yield Label("GPU Type:")
                            yield Input(value="t4", id="input-wg0-gpu-type")
                            yield Label("GPUs:")
                            yield Input(value="0", id="input-wg0-gpus", type="integer")

                    yield Container(id="extra-wg-container")
                    yield Button("+ Add Worker Group", variant="default", id="btn-add-wg")

                # ── Tab 4: Services ─────────────────
                with TabPane("Services", id="tab-services"):
                    yield Static("[bold]Services[/bold]", classes="form-section-title")
                    with Horizontal(classes="form-row"):
                        yield Label("Notebook:")
                        yield Switch(value=True, id="switch-notebook")
                        yield Label("Code Server:")
                        yield Switch(value=True, id="switch-code-server")
                        yield Label("SSH:")
                        yield Switch(value=True, id="switch-ssh")

                # ── Tab 5: Review ───────────────────
                with TabPane("Review", id="tab-review"):
                    yield Static("", id="review-content")

            yield Static("", id="form-error")

            with Horizontal(classes="form-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", variant="default", id="btn-cancel")

        status = StatusBar()
        yield status

    def on_mount(self) -> None:
        self.add_class(self.app.terminal_class)
        self.query_one("#input-namespace", Input).value = self.app.namespace
        self.query_one("#input-name", Input).focus()
        self._set_status_hints()

    def _set_status_hints(self) -> None:
        bar = self.query_one(StatusBar)
        bar.set_hints([
            ("Tab/Shift+Tab", "Next/Prev field"),
            ("Ctrl+</>/", "Prev/Next tab"),
            ("Ctrl+S", "Create"),
            ("Esc", "Cancel"),
        ])

    # ── Tab navigation ──────────────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-review":
            self._update_review()

    def action_next_tab(self) -> None:
        self.query_one("#create-tabs Tabs").action_next_tab()

    def action_prev_tab(self) -> None:
        self.query_one("#create-tabs Tabs").action_previous_tab()

    # ── Button dispatch ─────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-create":
            self.action_submit()
        elif btn_id == "btn-cancel":
            self.action_cancel()
        elif btn_id == "btn-add-wg":
            self._add_worker_group()

    # ── Worker groups ───────────────────────────────────

    def _add_worker_group(self) -> None:
        self._extra_worker_groups += 1
        idx = self._extra_worker_groups
        container = self.query_one("#extra-wg-container")

        section = Vertical(classes="form-section", id=f"section-wg{idx}")
        section.mount(Static(f"[bold]Worker Group {idx + 1}[/bold]", classes="form-section-title"))

        pairs = [
            [("Group Name:", f"input-wg{idx}-name", f"worker-{idx + 1}"),
             ("Replicas:", f"input-wg{idx}-replicas", "1")],
            [("CPUs:", f"input-wg{idx}-cpus", DEFAULT_CPUS),
             ("Memory:", f"input-wg{idx}-memory", DEFAULT_MEMORY)],
            [("GPU Type:", f"input-wg{idx}-gpu-type", "t4"),
             ("GPUs:", f"input-wg{idx}-gpus", "0")],
        ]
        for pair in pairs:
            row = Horizontal(classes="form-row")
            for label_text, input_id, default in pair:
                row.mount(Label(label_text))
                row.mount(Input(value=default, id=input_id))
            section.mount(row)

        container.mount(section)

    # ── Actions ─────────────────────────────────────────

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_submit(self) -> None:
        if self._creating:
            return
        error_label = self.query_one("#form-error", Static)
        error_label.remove_class("visible")

        # Validate all tabs
        errors = self._validate_all()
        if errors:
            first_error = errors[0]
            msg = f"{first_error[0]}: {first_error[1]}"
            error_label.update(f"[red]{msg}[/red]")
            error_label.add_class("visible")
            self.notify(msg, severity="error", timeout=5)
            return

        try:
            config = self._build_config()
        except Exception as exc:
            error_label.update(f"[red]{exc}[/red]")
            error_label.add_class("visible")
            return

        self._creating = True
        self.run_worker(
            partial(create_cluster, config),
            thread=True,
            name="create_cluster",
        )

    # ── Validation ──────────────────────────────────────

    def _validate_all(self) -> list[tuple[str, str]]:
        """Validate all tabs. Returns list of (tab_label, error_message)."""
        errors: list[tuple[str, str]] = []

        # Cluster tab
        if not self.query_one("#input-name", Input).value.strip():
            errors.append(("Cluster", "Cluster name is required"))

        # Head Node tab
        if not self.query_one("#input-head-cpus", Input).value.strip():
            errors.append(("Head Node", "CPUs is required"))
        if not self.query_one("#input-head-memory", Input).value.strip():
            errors.append(("Head Node", "Memory is required"))

        # Workers tab
        for idx in range(self._extra_worker_groups + 1):
            prefix = f"#input-wg{idx}"
            group_label = f"Worker group {idx + 1}"
            if not self.query_one(f"{prefix}-replicas", Input).value.strip():
                errors.append(("Workers", f"{group_label}: replicas is required"))
            if not self.query_one(f"{prefix}-cpus", Input).value.strip():
                errors.append(("Workers", f"{group_label}: CPUs is required"))
            if not self.query_one(f"{prefix}-memory", Input).value.strip():
                errors.append(("Workers", f"{group_label}: memory is required"))

        return errors

    # ── Config building ─────────────────────────────────

    def _build_config(self) -> ClusterConfig:
        name = self.query_one("#input-name", Input).value.strip()
        namespace = self.query_one("#input-namespace", Input).value.strip() or "default"

        head = HeadNodeConfig(
            cpus=self.query_one("#input-head-cpus", Input).value.strip(),
            memory=self.query_one("#input-head-memory", Input).value.strip(),
            gpus=int(self.query_one("#input-head-gpus", Input).value.strip() or "0"),
        )

        worker_groups: list[WorkerGroupConfig] = []
        worker_groups.append(self._read_worker_group(0))
        for i in range(1, self._extra_worker_groups + 1):
            try:
                worker_groups.append(self._read_worker_group(i))
            except Exception:
                pass

        return ClusterConfig(
            name=name,
            namespace=namespace,
            head=head,
            worker_groups=worker_groups,
            services=self._build_services(),
        )

    def _read_worker_group(self, idx: int) -> WorkerGroupConfig:
        prefix = f"#input-wg{idx}"
        return WorkerGroupConfig(
            name=self.query_one(f"{prefix}-name", Input).value.strip() or "worker",
            replicas=int(self.query_one(f"{prefix}-replicas", Input).value.strip() or "1"),
            cpus=self.query_one(f"{prefix}-cpus", Input).value.strip(),
            memory=self.query_one(f"{prefix}-memory", Input).value.strip(),
            gpus=int(self.query_one(f"{prefix}-gpus", Input).value.strip() or "0"),
            gpu_type=self.query_one(f"{prefix}-gpu-type", Input).value.strip(),
        )

    def _build_services(self) -> ServicesConfig:
        return ServicesConfig(
            notebook=self.query_one("#switch-notebook", Switch).value,
            code_server=self.query_one("#switch-code-server", Switch).value,
            ssh=self.query_one("#switch-ssh", Switch).value,
        )

    # ── Review ──────────────────────────────────────────

    def _update_review(self) -> None:
        """Refresh the review tab with current form state."""
        errors = self._validate_all()

        if errors:
            lines = ["[bold]Review[/bold]", ""]
            lines.append("[bold red]Fix the following before creating:[/bold red]")
            lines.append("")
            for tab_label, error_msg in errors:
                lines.append(f"  [red]\u2717[/red] {tab_label}: {error_msg}")
            self.query_one("#review-content", Static).update("\n".join(lines))
            return

        try:
            config = self._build_config()
        except Exception as exc:
            self.query_one("#review-content", Static).update(
                f"[red]Cannot preview: {exc}[/red]"
            )
            return

        lines: list[str] = []
        lines.append("[bold]Review[/bold]")
        lines.append("")
        lines.append(f"  [dim]Name:[/dim]       {config.name}")
        lines.append(f"  [dim]Namespace:[/dim]  {config.namespace}")

        lines.append("")
        lines.append("[bold]Head Node[/bold]")
        lines.append(f"  [dim]CPUs:[/dim]   {config.head.cpus}")
        lines.append(f"  [dim]Memory:[/dim] {config.head.memory}")
        lines.append(f"  [dim]GPUs:[/dim]   {config.head.gpus}")

        lines.append("")
        lines.append("[bold]Workers[/bold]")
        for wg in config.worker_groups:
            parts = [f"{wg.replicas} replica(s)", f"{wg.cpus} CPUs", f"{wg.memory} mem"]
            if wg.gpus:
                parts.append(f"{wg.gpus} GPUs ({wg.gpu_type})")
            lines.append(f"  {wg.name}: {', '.join(parts)}")

        lines.append("")
        lines.append("[bold]Services[/bold]")
        svc = config.services
        for name, enabled in [("Notebook", svc.notebook), ("Code Server", svc.code_server), ("SSH", svc.ssh)]:
            icon = "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]"
            lines.append(f"  {icon} {name}")

        lines.append("")
        lines.append("[green]Ready to create \u2714[/green]")

        self.query_one("#review-content", Static).update("\n".join(lines))

    # ── Worker result handling ──────────────────────────

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "create_cluster":
            return
        self._creating = False
        if event.worker.state == WorkerState.SUCCESS:
            info = event.worker.result
            self.notify(f"Cluster '{info.name}' created", severity="information", timeout=3)
            self.app.pop_screen()
        elif event.worker.state == WorkerState.ERROR:
            error = event.worker.error
            error_label = self.query_one("#form-error", Static)
            msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
            error_label.update(f"[red]{msg}[/red]")
            error_label.add_class("visible")
