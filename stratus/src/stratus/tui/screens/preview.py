"""Launch preview: SkyPilot's optimizer table for a spec, before anything is provisioned."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.widgets import RichLog

from stratus.tui.screens.logs import LogScreen, StreamFn

KUBERNETES_NOTE = (
    "Kubernetes rows show $0.00 because the API server has no `kubernetes.pricing` "
    "rates — read them as unpriced, not free."
)


class PreviewScreen(LogScreen):
    """Returns "launch", "edit" or None (cancel)."""

    LABEL = "Preview"

    BINDINGS = [
        Binding("y", "decide('launch')", "Launch"),
        Binding("e", "decide('edit')", "Back to form"),
        Binding("escape", "decide('')", "Cancel"),
    ]

    def __init__(self, title: str, stream: StreamFn, verb: str = "Launch") -> None:
        super().__init__(title, stream, subtitle=f"dry run — nothing is provisioned · y: {verb.lower()} · e: back to form · esc: cancel")
        self.verb = verb
        self._saw_k8s = False

    def _append(self, line: str) -> None:
        if "kubernetes" in line.lower():
            self._saw_k8s = True
        super()._append(line)

    def _finished(self) -> None:
        log = self.query_one(RichLog)
        if self._saw_k8s:
            log.write(Text(KUBERNETES_NOTE, style="yellow"))
        log.write(Text(f"Press y to {self.verb.lower()}, e to edit, esc to cancel.", style="bold"))
        super()._finished()

    def action_decide(self, choice: str) -> None:
        self._stop.set()
        self.dismiss(choice or None)

    def action_close(self) -> None:  # q / generic close → cancel
        self.action_decide("")
