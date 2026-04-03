"""Create-cluster form screen with prefilled defaults."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Static, Switch
from textual.worker import Worker, WorkerState

from krayne.api.clusters import create_cluster
from krayne.config.models import (
    ClusterConfig,
    DEFAULT_CPUS,
    DEFAULT_HEAD_MEMORY,
    DEFAULT_MEMORY,
    HeadNodeConfig,
    ServicesConfig,
    WorkerGroupConfig,
)
from krayne.errors import KrayneError


class CreateFormScreen(Screen):
    """Full-screen form for creating a new Ray cluster."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Create", show=True),
    ]

    def compose(self):
        with Vertical(id="create-form-screen"):
            yield Static("[bold]Create Ray Cluster[/bold]", id="form-title")

            # Cluster section
            with Vertical(classes="form-section"):
                yield Static("[bold cyan]CLUSTER[/bold cyan]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Name:")
                    yield Input(placeholder="my-cluster", id="input-name")
                with Horizontal(classes="form-row"):
                    yield Label("Namespace:")
                    yield Input(value="default", id="input-namespace")

            # Head node section
            with Vertical(classes="form-section"):
                yield Static("[bold cyan]HEAD NODE[/bold cyan]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("CPUs:")
                    yield Input(value=DEFAULT_CPUS, id="input-head-cpus")
                with Horizontal(classes="form-row"):
                    yield Label("Memory:")
                    yield Input(value=DEFAULT_HEAD_MEMORY, id="input-head-memory")
                with Horizontal(classes="form-row"):
                    yield Label("GPUs:")
                    yield Input(value="0", id="input-head-gpus")

            # Worker group section
            with Vertical(classes="form-section"):
                yield Static("[bold cyan]WORKER GROUP[/bold cyan]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Group Name:")
                    yield Input(value="worker", id="input-wg-name")
                with Horizontal(classes="form-row"):
                    yield Label("Replicas:")
                    yield Input(value="1", id="input-wg-replicas")
                with Horizontal(classes="form-row"):
                    yield Label("CPUs:")
                    yield Input(value=DEFAULT_CPUS, id="input-wg-cpus")
                with Horizontal(classes="form-row"):
                    yield Label("Memory:")
                    yield Input(value=DEFAULT_MEMORY, id="input-wg-memory")
                with Horizontal(classes="form-row"):
                    yield Label("GPUs:")
                    yield Input(value="0", id="input-wg-gpus")
                with Horizontal(classes="form-row"):
                    yield Label("GPU Type:")
                    yield Input(value="t4", id="input-wg-gpu-type")

            # Services section
            with Vertical(classes="form-section"):
                yield Static("[bold cyan]SERVICES[/bold cyan]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Label("Notebook:")
                    yield Switch(value=True, id="switch-notebook")
                with Horizontal(classes="form-row"):
                    yield Label("Code Server:")
                    yield Switch(value=True, id="switch-code-server")
                with Horizontal(classes="form-row"):
                    yield Label("SSH:")
                    yield Switch(value=True, id="switch-ssh")

            yield Static("", id="form-error")

            with Horizontal(classes="form-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", variant="default", id="btn-cancel")

        yield Footer()

    def on_mount(self) -> None:
        ns = self.query_one("#input-namespace", Input)
        ns.value = self.app.namespace
        self.query_one("#input-name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self.action_submit()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_submit(self) -> None:
        error_label = self.query_one("#form-error", Static)
        error_label.remove_class("visible")

        name = self.query_one("#input-name", Input).value.strip()
        if not name:
            error_label.update("[red]Name is required[/red]")
            error_label.add_class("visible")
            return

        try:
            config = ClusterConfig(
                name=name,
                namespace=self.query_one("#input-namespace", Input).value.strip() or "default",
                head=HeadNodeConfig(
                    cpus=self.query_one("#input-head-cpus", Input).value.strip(),
                    memory=self.query_one("#input-head-memory", Input).value.strip(),
                    gpus=int(self.query_one("#input-head-gpus", Input).value.strip()),
                ),
                worker_groups=[
                    WorkerGroupConfig(
                        name=self.query_one("#input-wg-name", Input).value.strip() or "worker",
                        replicas=int(self.query_one("#input-wg-replicas", Input).value.strip()),
                        cpus=self.query_one("#input-wg-cpus", Input).value.strip(),
                        memory=self.query_one("#input-wg-memory", Input).value.strip(),
                        gpus=int(self.query_one("#input-wg-gpus", Input).value.strip()),
                        gpu_type=self.query_one("#input-wg-gpu-type", Input).value.strip(),
                    )
                ],
                services=ServicesConfig(
                    notebook=self.query_one("#switch-notebook", Switch).value,
                    code_server=self.query_one("#switch-code-server", Switch).value,
                    ssh=self.query_one("#switch-ssh", Switch).value,
                ),
            )
        except (ValueError, TypeError) as exc:
            error_label.update(f"[red]Validation error: {exc}[/red]")
            error_label.add_class("visible")
            return
        except Exception as exc:
            error_label.update(f"[red]{exc}[/red]")
            error_label.add_class("visible")
            return

        self.run_worker(
            partial(create_cluster, config),
            thread=True,
            name="create_cluster",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "create_cluster":
            return
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
