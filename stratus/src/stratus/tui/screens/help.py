"""Help overlay listing global and per-view keys."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from stratus.tui.views import TOP_VIEWS, View

GLOBAL_KEYS = [
    (":", "command mode — :clusters :jobs :services :pools :volumes :storage :cost :specs :requests :infra :ws :q"),
    ("1-9, 0", "jump to a view"),
    ("U", "toggle scope: only my resources / every user's"),
    ("/", "filter rows (esc clears)"),
    ("ctrl-r", "refresh now (clusters: query clouds for fresh status)"),
    ("< / >", "sort by previous / next column; again to reverse"),
    ("esc", "back / clear filter"),
    ("q", "back; quits at the top level"),
    ("?", "this help"),
    ("ctrl-c", "quit"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "close", "Close", show=False)]

    def __init__(self, view: View) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        t = Table.grid(padding=(0, 3))
        t.add_column(style="bold #38bdf8", no_wrap=True)
        t.add_column()
        t.add_row(Text("Global", style="bold #f59e0b"), "")
        for k, d in GLOBAL_KEYS:
            t.add_row(k, d)
        t.add_row("", "")
        t.add_row(Text(f"{self.view.title}", style="bold #f59e0b"), "")
        for a in self.view.actions:
            t.add_row(Text(a.display_key, style="bold red" if a.danger else "bold #38bdf8"), a.label)
        t.add_row("", "")
        t.add_row(Text("Forms", style="bold #f59e0b"), "")
        t.add_row("ctrl-s", "save the spec and launch / apply it")
        t.add_row("ctrl-p", "preview placement & cost (dry run), then launch or go back")
        t.add_row("ctrl-o", "save the spec only")
        t.add_row("ctrl-e", "continue in the raw YAML editor")
        t.add_row("", "")
        t.add_row(Text("Views", style="bold #f59e0b"), "")
        for v in TOP_VIEWS:
            t.add_row(f"{v.hotkey or ' '}  :{v.name}", f"{v.title}  [dim]({', '.join(a for a in v.aliases if a != v.name)})[/dim]")
        t.add_row("", "")
        t.add_row(Text("Specs", style="bold #f59e0b"), "Everything you create is saved as plain SkyPilot YAML in")
        t.add_row("", "~/.stratus/specs/<kind>/<name>.yaml — usable directly with the sky CLI.")
        t.add_row("", "E opens it in $EDITOR (or the built-in editor); e re-opens the form.")
        with VerticalScroll(classes="help-box"):
            yield Static(t)
            yield Static("[dim]esc to close[/dim]")

    def action_close(self) -> None:
        self.dismiss()
