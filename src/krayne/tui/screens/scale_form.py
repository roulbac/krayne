"""Scale modal — input for target replica count."""

from __future__ import annotations

from functools import partial

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static
from textual.worker import Worker, WorkerState

from krayne.api.clusters import describe_cluster, scale_cluster
from krayne.errors import KrayneError


class ScaleFormScreen(ModalScreen[bool]):
    """Modal dialog for scaling a worker group."""

    def __init__(self, cluster_name: str, namespace: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.namespace = namespace

    def compose(self):
        with Vertical(id="scale-dialog"):
            yield Static(
                f"[bold]Scale cluster:[/bold] {self.cluster_name}",
                classes="dialog-title",
            )
            yield Static("Loading worker groups...", id="scale-wg-info")
            yield Input(placeholder="Replicas", id="scale-replicas", type="integer")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Scale", variant="primary", id="btn-scale")
                yield Button("Cancel", variant="default", id="btn-scale-cancel")

    def on_mount(self) -> None:
        self.run_worker(
            partial(describe_cluster, self.cluster_name, self.namespace),
            thread=True,
            name="fetch_for_scale",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "fetch_for_scale":
            if event.worker.state == WorkerState.SUCCESS:
                details = event.worker.result
                if details.worker_groups:
                    wg = details.worker_groups[0]
                    self._worker_group_name = wg.name
                    info_label = self.query_one("#scale-wg-info", Static)
                    info_label.update(
                        f"Worker group: [bold]{wg.name}[/bold]  "
                        f"Current replicas: [bold]{wg.replicas}[/bold]"
                    )
                    inp = self.query_one("#scale-replicas", Input)
                    inp.value = str(wg.replicas)
                    inp.focus()
                else:
                    self.query_one("#scale-wg-info", Static).update("[yellow]No worker groups found[/yellow]")
            elif event.worker.state == WorkerState.ERROR:
                self.query_one("#scale-wg-info", Static).update(f"[red]Error: {event.worker.error}[/red]")
        elif event.worker.name == "do_scale":
            if event.worker.state == WorkerState.SUCCESS:
                self.notify(f"Scaled '{self.cluster_name}' successfully", severity="information", timeout=3)
                self.dismiss(True)
            elif event.worker.state == WorkerState.ERROR:
                error = event.worker.error
                msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
                self.notify(msg, severity="error", timeout=5)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-scale":
            self._do_scale()
        elif event.button.id == "btn-scale-cancel":
            self.dismiss(False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scale-replicas":
            self._do_scale()

    def _do_scale(self) -> None:
        val = self.query_one("#scale-replicas", Input).value.strip()
        if not val:
            self.notify("Replicas value required", severity="warning")
            return
        try:
            replicas = int(val)
        except ValueError:
            self.notify("Replicas must be an integer", severity="warning")
            return

        wg_name = getattr(self, "_worker_group_name", "worker")
        self.run_worker(
            partial(
                scale_cluster,
                self.cluster_name,
                self.namespace,
                wg_name,
                replicas,
            ),
            thread=True,
            name="do_scale",
        )

    def key_escape(self) -> None:
        self.dismiss(False)
