"""Create cluster flow — guided creation with quick and advanced modes."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static, Switch
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
    """Full-screen create cluster flow with quick and advanced modes."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "submit", "Create", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._advanced: bool = False
        self._extra_worker_groups: int = 0
        self._creating: bool = False

    def compose(self):
        header = HeaderBar()
        header.view_title = "Create Cluster"
        yield header

        with Vertical(id="create-flow-screen"):
            # Mode toggle
            with Horizontal(id="create-mode-bar"):
                yield Button("Quick", variant="primary", id="btn-mode-quick")
                yield Button("Advanced", variant="default", id="btn-mode-advanced")

            yield Static("", id="step-indicator")

            # ── Quick + shared fields ───────────────
            with Vertical(classes="form-section", id="section-basics"):
                yield Static("[bold]Cluster[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Name:")
                    yield Input(placeholder="my-cluster", id="input-name")
                with Horizontal(classes="form-row"):
                    yield Label("Namespace:")
                    yield Input(value="default", id="input-namespace")

            # Quick mode: simple worker count
            with Vertical(classes="form-section", id="section-quick-compute"):
                yield Static("[bold]Workers[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Worker Count:")
                    yield Input(value="1", id="input-quick-workers", type="integer")
                with Horizontal(classes="form-row"):
                    yield Label("Enable GPUs:")
                    yield Switch(value=False, id="switch-quick-gpu")
                with Horizontal(classes="form-row"):
                    yield Label("GPU Type:")
                    yield Input(value="t4", id="input-quick-gpu-type")

            # Advanced mode: head + worker groups
            with Vertical(classes="form-section", id="section-advanced-head"):
                yield Static("[bold]Head Node[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("CPUs:")
                    yield Input(value=DEFAULT_CPUS, id="input-head-cpus")
                with Horizontal(classes="form-row"):
                    yield Label("Memory:")
                    yield Input(value=DEFAULT_HEAD_MEMORY, id="input-head-memory")
                with Horizontal(classes="form-row"):
                    yield Label("GPUs:")
                    yield Input(value="0", id="input-head-gpus", type="integer")

            with Vertical(classes="form-section", id="section-advanced-workers"):
                yield Static("[bold]Worker Group 1[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Group Name:")
                    yield Input(value="worker", id="input-wg0-name")
                with Horizontal(classes="form-row"):
                    yield Label("Replicas:")
                    yield Input(value="1", id="input-wg0-replicas", type="integer")
                with Horizontal(classes="form-row"):
                    yield Label("CPUs:")
                    yield Input(value=DEFAULT_CPUS, id="input-wg0-cpus")
                with Horizontal(classes="form-row"):
                    yield Label("Memory:")
                    yield Input(value=DEFAULT_MEMORY, id="input-wg0-memory")
                with Horizontal(classes="form-row"):
                    yield Label("GPUs:")
                    yield Input(value="0", id="input-wg0-gpus", type="integer")
                with Horizontal(classes="form-row"):
                    yield Label("GPU Type:")
                    yield Input(value="t4", id="input-wg0-gpu-type")

            # Extra worker groups container
            yield Container(id="extra-wg-container")
            yield Button("+ Add Worker Group", variant="default", id="btn-add-wg")

            # Services
            with Vertical(classes="form-section", id="section-services"):
                yield Static("[bold]Services[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Notebook:")
                    yield Switch(value=True, id="switch-notebook")
                with Horizontal(classes="form-row"):
                    yield Label("Code Server:")
                    yield Switch(value=True, id="switch-code-server")
                with Horizontal(classes="form-row"):
                    yield Label("SSH:")
                    yield Switch(value=True, id="switch-ssh")

            # Review section
            with Vertical(id="review-section"):
                yield Static("", id="review-content")

            yield Static("", id="form-error")

            with Horizontal(classes="form-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", variant="default", id="btn-cancel")

        status = StatusBar()
        yield status

    def on_mount(self) -> None:
        self.add_class(self.app.terminal_class)
        # Set namespace from app
        self.query_one("#input-namespace", Input).value = self.app.namespace
        self.query_one("#input-name", Input).focus()
        # Start in quick mode
        self._set_mode(advanced=False)
        self._set_status_hints()

    def _set_status_hints(self) -> None:
        bar = self.query_one(StatusBar)
        bar.set_hints([
            ("Ctrl+S", "Create"),
            ("Esc", "Cancel"),
        ])

    def _set_mode(self, advanced: bool) -> None:
        self._advanced = advanced

        # Toggle button styles
        btn_quick = self.query_one("#btn-mode-quick", Button)
        btn_adv = self.query_one("#btn-mode-advanced", Button)
        btn_quick.variant = "default" if advanced else "primary"
        btn_adv.variant = "primary" if advanced else "default"

        # Toggle section visibility
        self.query_one("#section-quick-compute").display = not advanced
        self.query_one("#section-advanced-head").display = advanced
        self.query_one("#section-advanced-workers").display = advanced
        self.query_one("#extra-wg-container").display = advanced
        self.query_one("#btn-add-wg").display = advanced

        # Step indicator
        step = self.query_one("#step-indicator", Static)
        if advanced:
            step.update("[dim]Advanced: Basics → Head → Workers → Services → Review[/dim]")
        else:
            step.update("[dim]Quick: Basics → Workers → Services → Create[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-mode-quick":
            self._set_mode(advanced=False)
        elif btn_id == "btn-mode-advanced":
            self._set_mode(advanced=True)
        elif btn_id == "btn-create":
            self.action_submit()
        elif btn_id == "btn-cancel":
            self.action_cancel()
        elif btn_id == "btn-add-wg":
            self._add_worker_group()

    def _add_worker_group(self) -> None:
        self._extra_worker_groups += 1
        idx = self._extra_worker_groups
        container = self.query_one("#extra-wg-container")

        section = Vertical(classes="form-section", id=f"section-wg{idx}")
        section.mount(Static(f"[bold]Worker Group {idx + 1}[/bold]", classes="form-section-title"))

        for label_text, input_id, default in [
            ("Group Name:", f"input-wg{idx}-name", f"worker-{idx + 1}"),
            ("Replicas:", f"input-wg{idx}-replicas", "1"),
            ("CPUs:", f"input-wg{idx}-cpus", DEFAULT_CPUS),
            ("Memory:", f"input-wg{idx}-memory", DEFAULT_MEMORY),
            ("GPUs:", f"input-wg{idx}-gpus", "0"),
            ("GPU Type:", f"input-wg{idx}-gpu-type", "t4"),
        ]:
            row = Horizontal(classes="form-row")
            row.mount(Label(label_text))
            row.mount(Input(value=default, id=input_id))
            section.mount(row)

        container.mount(section)

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_submit(self) -> None:
        if self._creating:
            return
        error_label = self.query_one("#form-error", Static)
        error_label.remove_class("visible")

        try:
            config = self._build_config()
        except (ValueError, TypeError) as exc:
            error_label.update(f"[red]{exc}[/red]")
            error_label.add_class("visible")
            return
        except Exception as exc:
            error_label.update(f"[red]{exc}[/red]")
            error_label.add_class("visible")
            return

        # Show review
        self._show_review(config)

        self._creating = True
        self.run_worker(
            partial(create_cluster, config),
            thread=True,
            name="create_cluster",
        )

    def _build_config(self) -> ClusterConfig:
        name = self.query_one("#input-name", Input).value.strip()
        if not name:
            raise ValueError("Cluster name is required")

        namespace = self.query_one("#input-namespace", Input).value.strip() or "default"

        if self._advanced:
            return self._build_advanced_config(name, namespace)
        else:
            return self._build_quick_config(name, namespace)

    def _build_quick_config(self, name: str, namespace: str) -> ClusterConfig:
        workers = int(self.query_one("#input-quick-workers", Input).value.strip() or "1")
        use_gpu = self.query_one("#switch-quick-gpu", Switch).value
        gpu_type = self.query_one("#input-quick-gpu-type", Input).value.strip()

        wg = WorkerGroupConfig(
            name="worker",
            replicas=workers,
            gpus=1 if use_gpu else 0,
            gpu_type=gpu_type if use_gpu else "t4",
        )

        return ClusterConfig(
            name=name,
            namespace=namespace,
            worker_groups=[wg],
            services=self._build_services(),
        )

    def _build_advanced_config(self, name: str, namespace: str) -> ClusterConfig:
        head = HeadNodeConfig(
            cpus=self.query_one("#input-head-cpus", Input).value.strip(),
            memory=self.query_one("#input-head-memory", Input).value.strip(),
            gpus=int(self.query_one("#input-head-gpus", Input).value.strip() or "0"),
        )

        worker_groups: list[WorkerGroupConfig] = []

        # Primary worker group
        worker_groups.append(self._read_worker_group(0))

        # Extra worker groups
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

    def _show_review(self, config: ClusterConfig) -> None:
        lines: list[str] = []
        lines.append("[bold]Creating cluster...[/bold]")
        lines.append(f"  Name: {config.name}")
        lines.append(f"  Namespace: {config.namespace}")
        for wg in config.worker_groups:
            lines.append(f"  Workers: {wg.name} x{wg.replicas}")
            if wg.gpus:
                lines.append(f"    GPUs: {wg.gpus} ({wg.gpu_type})")
        review = self.query_one("#review-content", Static)
        review.update("\n".join(lines))

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
