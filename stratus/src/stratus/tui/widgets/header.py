"""k9s-style header: context on the left, hotkeys in the middle, logo on the right."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from stratus import __version__
from stratus.backend.base import ServerInfo
from stratus.tui.views import TOP_VIEWS, Action

LOGO = r"""
     _             _
 ___| |_ _ __ __ _| |_ _   _ ___
/ __| __| '__/ _` | __| | | / __|
\__ \ |_| | | (_| | |_| |_| \__ \
|___/\__|_|  \__,_|\__|\__,_|___/
"""


class Header(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._srv: ServerInfo | None = None
        self._acts: tuple[Action, ...] = ()
        self._src = ""
        self._every = 0.0
        self._busy = False

    def set_context(self, info: ServerInfo | None = None, source: str | None = None, refresh: float | None = None) -> None:
        if info is not None:
            self._srv = info
        if source is not None:
            self._src = source
        if refresh is not None:
            self._every = refresh
        self._render_all()

    def set_actions(self, actions: tuple[Action, ...]) -> None:
        self._acts = actions
        self._render_all()

    def set_loading(self, loading: bool) -> None:
        if loading != self._busy:
            self._busy = loading
            self._render_all()

    def _render_all(self) -> None:
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(ratio=3, no_wrap=True)
        grid.add_column(ratio=5)
        grid.add_column(width=34, no_wrap=True)
        grid.add_row(self._ctx_table(), self._hint_table(), Text(LOGO.strip("\n"), style="bold #f59e0b"))
        self.update(grid)

    def _ctx_table(self) -> Table:
        t = Table.grid(padding=(0, 1))
        t.add_column(style="#f59e0b bold", no_wrap=True)
        t.add_column(no_wrap=True)
        info = self._srv
        if info is None:
            server = Text("connecting…", style="yellow")
        elif info.healthy:
            server = Text("● ", style="green") + Text(info.endpoint, style="bold")
        else:
            server = Text("● ", style="red") + Text(info.endpoint, style="bold") + Text(f"  {info.error}", style="red")
        t.add_row("Server:", server)
        t.add_row("Source:", Text(self._src or "-", style="dim"))
        t.add_row("User:", (info.user if info and info.user else "-"))
        t.add_row("SkyPilot:", (info.version if info and info.version else "-") + (f" (api {info.api_version})" if info and info.api_version else ""))
        t.add_row("stratus:", __version__)
        refresh = f"{self._every:g}s" if self._every else "-"
        t.add_row("Refresh:", Text(refresh) + (Text("  ⟳", style="yellow") if self._busy else Text("")))
        return t

    def _hint_table(self) -> Table:
        nav = Table.grid(padding=(0, 1))
        hints = [a for a in self._acts if a.hint]
        jumps = [(v.hotkey, v.name) for v in TOP_VIEWS if v.hotkey]
        cols = 3
        for _ in range(cols):
            nav.add_column(no_wrap=True)
        cells: list[Text] = []
        for a in hints:
            style = "bold red" if a.danger else "bold #38bdf8"
            cells.append(Text(f"<{a.display_key}>", style=style) + Text(f" {a.label}", style="dim"))
        rows = [cells[i : i + cols] for i in range(0, len(cells), cols)]
        for row in rows[:5]:
            nav.add_row(*row, *[Text("")] * (cols - len(row)))
        jump_line = Text("  ").join(Text(f"<{k}>", style="bold magenta") + Text(f" {n}", style="dim") for k, n in jumps)
        t = Table.grid()
        t.add_row(nav)
        t.add_row(jump_line)
        return t
