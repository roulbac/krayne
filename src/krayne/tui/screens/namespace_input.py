"""Namespace switch modal."""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class NamespaceInputScreen(ModalScreen[str | None]):
    """Modal dialog for switching namespace."""

    def compose(self):
        with Vertical(id="namespace-dialog"):
            yield Static("[bold]Switch Namespace[/bold]", classes="dialog-title")
            yield Input(placeholder="Namespace", id="namespace-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Switch", variant="primary", id="btn-ns-switch")
                yield Button("Cancel", variant="default", id="btn-ns-cancel")

    def on_mount(self) -> None:
        inp = self.query_one("#namespace-input", Input)
        inp.value = self.app.namespace
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ns-switch":
            self._do_switch()
        elif event.button.id == "btn-ns-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "namespace-input":
            self._do_switch()

    def _do_switch(self) -> None:
        ns = self.query_one("#namespace-input", Input).value.strip()
        if ns:
            self.app.namespace = ns
            self.dismiss(ns)
        else:
            self.notify("Namespace cannot be empty", severity="warning")

    def key_escape(self) -> None:
        self.dismiss(None)
