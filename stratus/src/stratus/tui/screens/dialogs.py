"""Small modal dialogs: confirm, prompt, autostop, kind picker."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, Switch
from textual.widgets.option_list import Option

from stratus.specs.models import SpecKind


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "no", "No", show=False),
        Binding("n", "no", "No", show=False),
        Binding("y", "yes", "Yes", show=False),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Yes", danger: bool = True) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm = confirm_label
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog" + (" danger" if self._danger else "")):
            yield Static(f"[b]{self._title}[/b]", classes="dialog-title")
            yield Static(self._message, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="no")
                yield Button(f"{self._confirm} (y)", id="yes", variant="error" if self._danger else "primary")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, label: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._label = label
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"[b]{self._title}[/b]", classes="dialog-title")
            yield Label(self._label)
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            yield Static("[dim]enter to confirm · esc to cancel[/dim]")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AutostopScreen(ModalScreen[tuple[int, bool] | None]):
    """Returns ``(minutes, down)``; minutes = -1 cancels autostop."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, cluster: str, minutes: int, down: bool) -> None:
        super().__init__()
        self._cluster = cluster
        self._minutes = minutes
        self._down = down

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"[b]Autostop · {self._cluster}[/b]", classes="dialog-title")
            yield Label("Idle minutes before stopping (-1 to disable)")
            yield Input(value=str(self._minutes if self._minutes >= 0 else 30), id="minutes", type="integer")
            with Horizontal(classes="switch-row"):
                yield Switch(value=self._down, id="down")
                yield Label("Tear down instead of stop (autodown)")
            yield Static("", id="error", classes="field-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#minutes", Input).focus()

    def _submit(self) -> None:
        raw = self.query_one("#minutes", Input).value.strip()
        try:
            minutes = int(raw)
        except ValueError:
            self.query_one("#error", Static).update("enter a whole number of minutes")
            return
        if minutes < -1:
            self.query_one("#error", Static).update("use -1 to disable, or 0+ minutes")
            return
        self.dismiss((minutes, self.query_one("#down", Switch).value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class KindPickerScreen(ModalScreen[SpecKind | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("[b]Create what?[/b]", classes="dialog-title")
            yield OptionList(
                Option("Cluster        [dim]interactive dev box / multi-node cluster[/dim]", id="cluster"),
                Option("Managed job    [dim]batch run with auto-recovery[/dim]", id="job"),
                Option("Service        [dim]autoscaled model/API endpoint[/dim]", id="service"),
                Option("Volume         [dim]persistent disk (PVC, network volume)[/dim]", id="volume"),
                id="kinds",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(SpecKind(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ServiceLogTargetScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("[b]Which logs?[/b]", classes="dialog-title")
            yield OptionList(Option("Load balancer", id="load_balancer"), Option("Controller", id="controller"), id="targets")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
