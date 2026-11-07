"""Streaming log viewer (requests, cluster jobs, managed jobs, services)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, RichLog, Static

from stratus.backend.base import LogSink

StreamFn = Callable[[LogSink, threading.Event], None]


class LogScreen(Screen):
    LABEL = "Logs"
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
        Binding("f", "toggle_follow", "Follow"),
        Binding("w", "toggle_wrap", "Wrap"),
        Binding("s", "save", "Save to file"),
        Binding("g", "top", "Top", show=False),
        Binding("G", "bottom", "Bottom", show=False),
    ]

    def __init__(self, title: str, stream: StreamFn, subtitle: str = "") -> None:
        super().__init__()
        self._title = title
        self._subtitle = subtitle
        self._stream = stream
        self._stop = threading.Event()
        self._lines: list[str] = []
        self._follow = True
        self._wrap = True
        self._done = False

    def compose(self) -> ComposeResult:
        yield Static("", id="log-title")
        yield RichLog(id="log", highlight=False, markup=False, wrap=True, auto_scroll=True, max_lines=20000)
        yield Footer()

    def on_mount(self) -> None:
        self._update_title()
        thread = threading.Thread(target=self._run, name=f"logs:{self._title}", daemon=True)
        thread.start()

    def _run(self) -> None:
        try:
            self._stream(self._sink, self._stop)
        except Exception as exc:  # noqa: BLE001
            self._sink(f"[stratus] {exc}")
        finally:
            if not self._stop.is_set():
                try:
                    self.app.call_from_thread(self._finished)
                except RuntimeError:
                    pass

    def _sink(self, line: str) -> None:
        if self._stop.is_set():
            return
        try:
            self.app.call_from_thread(self._append, line)
        except RuntimeError:  # app shutting down
            self._stop.set()

    def _append(self, line: str) -> None:
        # SkyPilot prints "⚙︎" with a variation selector, which some terminals
        # render as a wide glyph that eats the rest of the line.
        line = line.replace("\ufe0e", "").replace("\ufe0f", "")
        self._lines.append(line)
        self.query_one(RichLog).write(Text.from_ansi(line))

    def _finished(self) -> None:
        self._done = True
        self.query_one(RichLog).write(Text("── stream ended ──", style="dim"))
        self._update_title()

    def _update_title(self) -> None:
        flags = [
            "[green]following[/green]" if self._follow else "[dim]paused[/dim]",
            "wrap" if self._wrap else "[dim]nowrap[/dim]",
        ]
        if self._done:
            flags.append("[dim]ended[/dim]")
        sub = f"  [dim]{self._subtitle}[/dim]" if self._subtitle else ""
        self.query_one("#log-title", Static).update(f"[b #f59e0b] {self.LABEL}[/] [b]{self._title}[/b]{sub}   " + " · ".join(flags))

    def action_close(self) -> None:
        self._stop.set()
        self.dismiss()

    def action_toggle_follow(self) -> None:
        self._follow = not self._follow
        log = self.query_one(RichLog)
        log.auto_scroll = self._follow
        if self._follow:
            log.scroll_end(animate=False)
        self._update_title()

    def action_toggle_wrap(self) -> None:
        self._wrap = not self._wrap
        log = self.query_one(RichLog)
        log.wrap = self._wrap
        log.clear()
        for line in self._lines:
            log.write(Text.from_ansi(line))
        self._update_title()

    def action_top(self) -> None:
        self.query_one(RichLog).scroll_home(animate=False)

    def action_bottom(self) -> None:
        self.query_one(RichLog).scroll_end(animate=False)

    def action_save(self) -> None:
        from stratus.settings import stratus_home

        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self._title)
        path = stratus_home() / "logs" / f"{safe}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(self._lines) + "\n")
        self.notify(f"Saved {len(self._lines)} lines to {path}")
