"""Delete confirmation modal."""

from __future__ import annotations

from functools import partial

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.worker import Worker, WorkerState

from krayne.api.clusters import delete_cluster
from krayne.errors import KrayneError
from krayne.tunnel import stop_tunnels


class DeleteConfirmScreen(ModalScreen[bool]):
    """Confirmation dialog before deleting a cluster."""

    def __init__(self, cluster_name: str, namespace: str) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.namespace = namespace

    def compose(self):
        with Vertical(id="delete-dialog"):
            yield Static(
                f"[bold red]Delete cluster '{self.cluster_name}'?[/bold red]",
                classes="dialog-title",
            )
            yield Static(
                f"This will permanently delete the cluster in namespace '{self.namespace}'.\n"
                "Any active tunnels will be closed.",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Delete", variant="error", id="btn-confirm-delete")
                yield Button("Cancel", variant="default", id="btn-cancel-delete")

    def on_mount(self) -> None:
        self.query_one("#btn-cancel-delete", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-delete":
            self._do_delete()
        elif event.button.id == "btn-cancel-delete":
            self.dismiss(False)

    def _do_delete(self) -> None:
        self.run_worker(
            partial(self._delete, self.cluster_name, self.namespace),
            thread=True,
            name="do_delete",
        )

    @staticmethod
    def _delete(name: str, namespace: str) -> None:
        stop_tunnels(name, namespace)
        delete_cluster(name, namespace)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "do_delete":
            return
        if event.worker.state == WorkerState.SUCCESS:
            self.notify(f"Cluster '{self.cluster_name}' deleted", severity="information", timeout=3)
            self.dismiss(True)
        elif event.worker.state == WorkerState.ERROR:
            error = event.worker.error
            msg = str(error) if isinstance(error, KrayneError) else f"Error: {error}"
            self.notify(msg, severity="error", timeout=5)
            self.dismiss(False)

    def key_escape(self) -> None:
        self.dismiss(False)
