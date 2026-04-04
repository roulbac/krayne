"""Scale flow — multi-worker-group aware scaling dialog."""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from krayne.api.clusters import describe_cluster, scale_cluster
from krayne.api.types import WorkerGroupInfo
from krayne.errors import KrayneError


class ScaleFlowScreen(ModalScreen[bool]):
    """Modal dialog for scaling a specific worker group."""

    def __init__(self, cluster_name: str, namespace: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.namespace = namespace
        self._worker_groups: list[WorkerGroupInfo] = []
        self._selected_group: WorkerGroupInfo | None = None
        self._original_replicas: int = 0

    def compose(self):
        with Vertical(id="scale-dialog"):
            yield Static(
                f"[bold]Scale cluster:[/bold] {self.cluster_name}",
                classes="dialog-title",
            )
            yield Static("Loading worker groups...", id="scale-info")
            yield OptionList(id="scale-group-list")
            yield Static("", id="scale-current")
            yield Input(placeholder="New replica count", id="scale-replicas", type="integer")
            yield Static("", id="scale-effect")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Scale", variant="primary", id="btn-scale")
                yield Button("Cancel", variant="default", id="btn-scale-cancel")

    def on_mount(self) -> None:
        # Hide group list initially
        self.query_one("#scale-group-list").display = False
        self.query_one("#scale-current").display = False
        self.query_one("#scale-replicas").display = False
        self.query_one("#scale-effect").display = False
        self.query_one("#btn-scale").display = False

        self.run_worker(
            partial(describe_cluster, self.cluster_name, self.namespace),
            thread=True,
            name="fetch_for_scale",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "fetch_for_scale":
            if event.worker.state == WorkerState.SUCCESS:
                details = event.worker.result
                self._worker_groups = details.worker_groups
                self._setup_groups()
            elif event.worker.state == WorkerState.ERROR:
                self.query_one("#scale-info", Static).update(
                    f"[red]Error: {event.worker.error}[/red]"
                )
        elif event.worker.name == "do_scale":
            if event.worker.state == WorkerState.SUCCESS:
                self.notify(
                    f"Scaled '{self.cluster_name}' successfully",
                    severity="information",
                    timeout=3,
                )
                self.dismiss(True)
            elif event.worker.state == WorkerState.ERROR:
                error = event.worker.error
                msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
                self.notify(msg, severity="error", timeout=5)

    def _setup_groups(self) -> None:
        if not self._worker_groups:
            self.query_one("#scale-info", Static).update(
                "[yellow]No worker groups found[/yellow]"
            )
            return

        if len(self._worker_groups) == 1:
            # Single group — skip selection
            self._select_group(self._worker_groups[0])
        else:
            # Multiple groups — show picker
            info = self.query_one("#scale-info", Static)
            info.update("Select a worker group:")
            group_list = self.query_one("#scale-group-list", OptionList)
            group_list.display = True
            for wg in self._worker_groups:
                group_list.add_option(
                    Option(f"{wg.name}  (replicas: {wg.replicas}, CPUs: {wg.cpus}, Mem: {wg.memory})")
                )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if idx < len(self._worker_groups):
            self._select_group(self._worker_groups[idx])

    def _select_group(self, wg: WorkerGroupInfo) -> None:
        self._selected_group = wg
        self._original_replicas = wg.replicas

        info = self.query_one("#scale-info", Static)
        info.update(
            f"Worker group: [bold]{wg.name}[/bold]  "
            f"CPUs: {wg.cpus}  Memory: {wg.memory}  "
            f"GPUs: {wg.gpus}{f' ({wg.gpu_type})' if wg.gpu_type else ''}"
        )

        # Hide group list, show replica input
        self.query_one("#scale-group-list").display = False
        current = self.query_one("#scale-current", Static)
        current.display = True
        current.update(f"Current replicas: [bold]{wg.replicas}[/bold]")

        replicas_input = self.query_one("#scale-replicas", Input)
        replicas_input.display = True
        replicas_input.value = str(wg.replicas)
        replicas_input.focus()

        self.query_one("#scale-effect").display = True
        self.query_one("#btn-scale").display = True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "scale-replicas":
            self._update_effect()

    def _update_effect(self) -> None:
        effect = self.query_one("#scale-effect", Static)
        val = self.query_one("#scale-replicas", Input).value.strip()
        if not val:
            effect.update("")
            return
        try:
            new = int(val)
        except ValueError:
            effect.update("[red]Must be a number[/red]")
            return

        if new > self._original_replicas:
            effect.update(
                f"[green]\u2191 Scale up[/green] {self._original_replicas} \u2192 {new} "
                f"(+{new - self._original_replicas} replicas)"
            )
        elif new < self._original_replicas:
            effect.update(
                f"[yellow]\u2193 Scale down[/yellow] {self._original_replicas} \u2192 {new} "
                f"(-{self._original_replicas - new} replicas)\n"
                f"[dim]Scaling down will terminate running workers[/dim]"
            )
        else:
            effect.update(f"[dim]No change ({new} replicas)[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-scale":
            self._do_scale()
        elif event.button.id == "btn-scale-cancel":
            self.dismiss(False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scale-replicas":
            self._do_scale()

    def _do_scale(self) -> None:
        if not self._selected_group:
            self.notify("No worker group selected", severity="warning")
            return

        val = self.query_one("#scale-replicas", Input).value.strip()
        if not val:
            self.notify("Replica count is required", severity="warning")
            return
        try:
            replicas = int(val)
        except ValueError:
            self.notify("Replicas must be a number", severity="warning")
            return

        self.run_worker(
            partial(
                scale_cluster,
                self.cluster_name,
                self.namespace,
                self._selected_group.name,
                replicas,
            ),
            thread=True,
            name="do_scale",
        )

    def key_escape(self) -> None:
        self.dismiss(False)
