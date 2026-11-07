"""The k9s-style main screen: header, resource table, command/filter prompt."""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import Key, Resize
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import DataTable, Input, Static
from textual.worker import Worker, WorkerState

from stratus.backend.base import BackendError, ServerInfo
from stratus.specs.models import ClusterSpec, Spec, SpecKind, spec_from_yaml
from stratus.specs.store import StoredSpec, _short_error
from stratus.tui.screens.detail import DetailScreen, describe_table, yaml_block
from stratus.tui.screens.dialogs import AutostopScreen, ConfirmScreen, KindPickerScreen, PromptScreen
from stratus.tui.screens.editor import EditorResult, YamlEditorScreen
from stratus.tui.screens.form import FormResult, FormScreen
from stratus.tui.screens.help import HelpScreen
from stratus.tui.screens.logs import LogScreen
from stratus.tui.views import (
    TOP_VIEWS,
    Action,
    ClusterJobsView,
    ClustersView,
    PoolsView,
    PoolWorkersView,
    StorageView,
    USAGE_WINDOWS,
    UsageView,
    WorkspacesView,
    Ctx,
    ManagedJobsView,
    ReplicasView,
    RequestsView,
    ServicesView,
    SpecsView,
    View,
    VolumesView,
    all_aliases,
    view_for_command,
)
from stratus.tui.widgets.header import Header

NARROW = 110
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "FAILED_SETUP", "FAILED_PRECHECKS", "FAILED_NO_RESOURCE", "FAILED_CONTROLLER", "CANCELLED"}
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _sort_key(value: Any) -> tuple:
    if hasattr(value, "sort_value"):
        return (0, float(value.sort_value), "")
    text = value.plain if isinstance(value, Text) else str(value)
    if _NUM.match(text):
        return (0, float(text), "")
    return (1, 0.0, text.lower())


class MainScreen(Screen):
    BINDINGS = [
        Binding("colon", "command", "Command", show=False),
        Binding("slash", "filter", "Filter", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("ctrl+r", "force_refresh", "Refresh", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back_or_quit", "Back/Quit", show=False),
        Binding("less_than_sign", "sort(-1)", "Sort", show=False),
        Binding("greater_than_sign", "sort(1)", "Sort", show=False),
        Binding("U", "toggle_users", "Mine/all users", show=False),
        *[Binding(v.hotkey, f"jump('{v.name}')", v.title, show=False) for v in TOP_VIEWS if v.hotkey],
    ]

    def __init__(self, initial_view: str = "clusters") -> None:
        super().__init__()
        cls = view_for_command(initial_view) or ClustersView
        self.stack: list[View] = [cls()]
        self.rows: list[Any] = []
        self.extra: Any = None
        self.error: str | None = None
        self.filter = ""
        self.sort_col: int | None = None
        self.sort_reverse = False
        self.prompt_mode: str | None = None  # "command" | "filter"
        self.info: ServerInfo | None = None
        self._row_index: dict[str, Any] = {}
        self._loaded_once = False

    # ── composition ──────────────────────────────────────────

    @property
    def view(self) -> View:
        return self.stack[-1]

    @property
    def ctx(self) -> Ctx:
        return Ctx(self.app.backend, self.app.store)

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        yield Static("", id="title-bar")
        yield Input(id="prompt", suggester=SuggestFromList(all_aliases(), case_sensitive=False))
        yield DataTable(id="table", cursor_type="row", zebra_stripes=False)
        yield Static("", id="empty")
        yield Static("", id="crumbs")

    def on_mount(self) -> None:
        header = self.query_one(Header)
        header.set_context(source=self.app.endpoint_source, refresh=self.app.refresh_seconds)
        header.set_scope(all_users=getattr(self.app.backend, "all_users", False), workspace=self.app.workspace)
        self.query_one("#prompt", Input).display = False
        self._apply_view()
        self.set_interval(self.app.refresh_seconds, self.refresh_data)
        self.query_one(DataTable).focus()

    def on_screen_resume(self) -> None:
        if self._loaded_once:
            self.refresh_data()

    def on_resize(self, event: Resize) -> None:
        self._render_table()

    # ── data ─────────────────────────────────────────────────

    def refresh_data(self, force: bool = False) -> None:
        if self.app.screen is not self:
            return  # don't poll the server while a form or log view is open
        view = self.view
        self.query_one(Header).set_loading(True)
        self.run_worker(lambda: self._fetch(view, force), thread=True, exclusive=True, group="fetch", name="fetch")

    def _fetch(self, view: View, force: bool) -> tuple[View, list[Any], Any, ServerInfo, str | None]:
        backend = self.app.backend
        info = backend.server_info()
        ctx = self.ctx
        extra = None
        try:
            if isinstance(view, SpecsView) or info.healthy:
                if force and isinstance(view, ClustersView):
                    rows = backend.list_clusters(refresh=True)
                    rows = sorted(rows, key=lambda r: r.name)
                else:
                    rows = view.fetch(ctx)
                extra = view.fetch_extra(ctx, rows)
                return view, rows, extra, info, None
            return view, [], extra, info, f"SkyPilot API server unavailable: {info.error}"
        except BackendError as exc:
            return view, [], extra, info, str(exc)
        except Exception as exc:  # noqa: BLE001 - keep the UI alive
            return view, [], extra, info, f"{type(exc).__name__}: {exc}"

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch":
            return
        if event.state == WorkerState.SUCCESS:
            view, rows, extra, info, error = event.worker.result
            self.query_one(Header).set_loading(False)
            self.info = info
            self.query_one(Header).set_context(info=info)
            if view is not self.view:
                return  # stale result from a view we already left
            self._loaded_once = True
            self.rows, self.extra, self.error = rows, extra, error
            self._render_table()
        elif event.state in (WorkerState.ERROR, WorkerState.CANCELLED):
            self.query_one(Header).set_loading(False)

    # ── rendering ────────────────────────────────────────────

    def _apply_view(self) -> None:
        self.rows, self.extra, self.error = [], None, None
        self.sort_col, self.sort_reverse = None, False
        self._loaded_once = False
        self.query_one(Header).set_actions(self.view.actions)
        self._render_table(columns_changed=True)
        self._render_crumbs()
        self.refresh_data()

    def _visible_columns(self) -> list[int]:
        narrow = self.size.width and self.size.width < NARROW
        return [i for i, c in enumerate(self.view.columns) if c.narrow or not narrow]

    def _filtered(self) -> list[tuple[Any, list[Any]]]:
        ctx = self.ctx
        out = []
        needle = self.filter.lower()
        for row in self.rows:
            cells = self.view.cells(row, ctx, self.extra)
            if needle:
                hay = " ".join(c.plain if isinstance(c, Text) else str(c) for c in cells).lower()
                if needle not in hay:
                    continue
            out.append((row, cells))
        if self.sort_col is not None:
            out.sort(key=lambda rc: _sort_key(rc[1][self.sort_col]), reverse=self.sort_reverse)
        return out

    def _render_table(self, columns_changed: bool = False) -> None:
        table = self.query_one(DataTable)
        visible = self._visible_columns()
        previous = self.selected_key()
        if columns_changed or len(table.columns) != len(visible):
            table.clear(columns=True)
            for i in visible:
                col = self.view.columns[i]
                label = col.label
                if self.sort_col == i:
                    label += " ↓" if self.sort_reverse else " ↑"
                table.add_column(label, key=f"c{i}")
        else:
            table.clear()
            for idx, i in enumerate(visible):
                col = self.view.columns[i]
                label = col.label + ((" ↓" if self.sort_reverse else " ↑") if self.sort_col == i else "")
                table.columns[list(table.columns)[idx]].label = Text(label)
        rows = self._filtered()
        self._row_index = {}
        for row, cells in rows:
            key = self.view.key(row)
            self._row_index[key] = row
            table.add_row(*[cells[i] for i in visible], key=key)
        if previous and previous in self._row_index:
            table.move_cursor(row=table.get_row_index(previous), animate=False)
        self._render_title(len(rows))
        empty = self.query_one("#empty", Static)
        if self.error:
            empty.update(Text.from_markup(f"[red]✗ {_escape(self.error)}[/red]\n\n[dim]ctrl-r to retry · :infra to check enabled clouds[/dim]"))
            empty.display = True
        elif not rows and self._loaded_once:
            hint = self.view.empty_hint if not self.filter else f"No rows match /{_escape(self.filter)} (esc clears)"
            empty.update(Text.from_markup(f"[dim]{hint}[/dim]"))
            empty.display = True
        elif not self._loaded_once:
            empty.update(Text.from_markup("[dim]loading…[/dim]"))
            empty.display = True
        else:
            empty.display = False

    def _render_title(self, count: int) -> None:
        v = self.view
        scope = f"({v.crumb})" if v.crumb != v.name else "(all)"
        filt = f" [b #f59e0b]</{_escape(self.filter)}>[/]" if self.filter else ""
        summary = v.summary(self.rows, self.extra) if self.rows else ""
        summary = f"  {summary}" if summary else ""
        self.query_one("#title-bar", Static).update(
            Text.from_markup(f" [b #38bdf8]{v.title}[/]{scope}[[b]{count}[/b]]{filt}{summary} ")
        )

    def _render_crumbs(self) -> None:
        crumbs = Text("")
        for i, v in enumerate(self.stack):
            style = "bold black on #f59e0b" if i == len(self.stack) - 1 else "black on #38bdf8"
            crumbs.append(f" <{v.crumb}> ", style=style)
            crumbs.append(" ")
        self.query_one("#crumbs", Static).update(crumbs)

    # ── selection helpers ────────────────────────────────────

    def selected_key(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:  # noqa: BLE001
            return None
        return key.value if key is not None else None

    def selected_row(self) -> Any | None:
        key = self.selected_key()
        return self._row_index.get(key) if key else None

    # ── view navigation ──────────────────────────────────────

    def switch_view(self, cls: type[View]) -> None:
        self.stack = [cls()]
        self.filter = ""
        self._apply_view()

    def push_view(self, view: View) -> None:
        self.stack.append(view)
        self.filter = ""
        self._apply_view()

    def action_jump(self, name: str) -> None:
        cls = view_for_command(name)
        if cls:
            self.switch_view(cls)

    def action_back(self) -> None:
        if self.prompt_mode:
            self._close_prompt(clear_filter=self.prompt_mode == "filter")
            return
        if self.filter:
            self.filter = ""
            self._render_table()
            return
        if len(self.stack) > 1:
            self.stack.pop()
            self._apply_view()

    def action_back_or_quit(self) -> None:
        if len(self.stack) > 1:
            self.action_back()
        else:
            self.app.exit()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen(self.view))

    def action_toggle_users(self) -> None:
        backend = self.app.backend
        backend.all_users = not backend.all_users
        self.query_one(Header).set_scope(all_users=backend.all_users)
        self.notify("Showing everyone's resources" if backend.all_users else "Showing only your resources", timeout=3)
        self.refresh_data()

    def action_force_refresh(self) -> None:
        self.notify("Refreshing…" if not isinstance(self.view, ClustersView) else "Refreshing cluster status from the clouds…", timeout=2)
        self.refresh_data(force=True)

    def action_sort(self, direction: int) -> None:
        visible = self._visible_columns()
        if not visible:
            return
        if self.sort_col is None:
            self.sort_col = visible[0] if direction > 0 else visible[-1]
            self.sort_reverse = False
        else:
            pos = visible.index(self.sort_col) if self.sort_col in visible else 0
            if direction > 0 and pos == len(visible) - 1 and not self.sort_reverse:
                self.sort_reverse = True
            else:
                nxt = (pos + direction) % len(visible)
                self.sort_col, self.sort_reverse = visible[nxt], False
        self._render_table()

    # ── prompt (command & filter) ────────────────────────────

    def action_command(self) -> None:
        self._open_prompt("command", ":")

    def action_filter(self) -> None:
        self._open_prompt("filter", "/")

    def _open_prompt(self, mode: str, prefix: str) -> None:
        prompt = self.query_one("#prompt", Input)
        self.prompt_mode = mode
        prompt.display = True
        prompt.placeholder = ("view name: clusters, jobs, services, volumes, specs, requests, infra, q" if mode == "command" else "filter rows…")
        prompt.value = self.filter if mode == "filter" else ""
        prompt.border_title = prefix
        prompt.set_class(mode == "command", "command")
        prompt.focus()

    def _close_prompt(self, clear_filter: bool = False) -> None:
        prompt = self.query_one("#prompt", Input)
        if clear_filter:
            self.filter = ""
            self._render_table()
        self.prompt_mode = None
        prompt.display = False
        self.query_one(DataTable).focus()

    @on(Input.Changed, "#prompt")
    def _prompt_changed(self, event: Input.Changed) -> None:
        if self.prompt_mode == "filter":
            self.filter = event.value.strip()
            self._render_table()

    @on(Input.Submitted, "#prompt")
    def _prompt_submitted(self, event: Input.Submitted) -> None:
        mode, value = self.prompt_mode, event.value.strip()
        self._close_prompt()
        if mode == "filter":
            self.filter = value
            self._render_table()
            return
        if not value:
            return
        if value in ("q", "q!", "quit", "exit"):
            self.app.exit()
        elif value in ("help", "h", "?"):
            self.action_help()
        else:
            cls = view_for_command(value.split()[0])
            if cls is None:
                self.notify(f"Unknown command :{value}", severity="error")
            else:
                self.switch_view(cls)

    # ── key dispatch to view actions ─────────────────────────

    def _match_action(self, event: Key) -> Action | None:
        for a in self.view.actions:
            if a.key == event.key or (len(a.key) == 1 and event.character == a.key):
                return a
        return None

    def on_key(self, event: Key) -> None:
        if self.prompt_mode or not isinstance(self.focused, DataTable):
            return
        if event.key == "enter":
            return  # handled by RowSelected
        action = self._match_action(event)
        if action is not None:
            event.stop()
            event.prevent_default()
            self.dispatch_action(action)

    @on(DataTable.RowSelected, "#table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        for a in self.view.actions:
            if a.key == "enter":
                self.dispatch_action(a)
                return

    def dispatch_action(self, action: Action) -> None:
        handler = getattr(self, f"act_{action.name}", None)
        if handler is None:
            self.notify(f"{action.label}: not available here", severity="warning")
            return
        if action.name in ("create", "window"):
            handler()
            return
        row = self.selected_row()
        if row is None:
            self.notify("Nothing selected", severity="warning")
            return
        handler(row)

    # ── generic plumbing ─────────────────────────────────────

    def run_backend(self, desc: str, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None) -> None:
        """Run a blocking backend call in a thread; report errors as toasts."""

        def work() -> None:
            try:
                result = fn()
            except BackendError as exc:
                self.app.call_from_thread(self.notify, f"{desc} failed: {exc}", severity="error", timeout=8)
                return
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(self.notify, f"{desc} failed: {type(exc).__name__}: {exc}", severity="error", timeout=8)
                return
            if on_done is not None:
                self.app.call_from_thread(on_done, result)

        threading.Thread(target=work, name=desc, daemon=True).start()

    def submit(
        self,
        desc: str,
        fn: Callable[[], str],
        *,
        follow: bool = False,
        then_tail: Callable[[Any, threading.Event], None] | None = None,
        record: tuple[SpecKind, str, str] | None = None,
    ) -> None:
        """Submit a SkyPilot request; optionally open its logs."""

        def done(request_id: str) -> None:
            if record:
                self.app.store.record(record[0], record[1], record[2], request_id)
            if follow:
                self.open_request_logs(request_id, desc, then_tail=then_tail)
            else:
                self.notify(f"{desc}: submitted (request {request_id[:13]}) · :requests to follow", timeout=5)
            self.refresh_data()

        self.run_backend(desc, fn, done)

    def open_request_logs(self, request_id: str, title: str, then_tail: Callable[[Any, threading.Event], None] | None = None) -> None:
        backend = self.app.backend

        def stream(sink, stop: threading.Event) -> None:
            backend.stream_request_logs(request_id, sink, stop)
            if then_tail is not None and not stop.is_set():
                sink("")
                sink("── job output (esc to detach; the job keeps running) ──")
                then_tail(sink, stop)

        self.app.push_screen(LogScreen(title, stream, subtitle=f"request {request_id[:13]} · esc detaches, it keeps running"))

    def confirm(self, title: str, message: str, on_yes: Callable[[], None], label: str = "Yes", danger: bool = True) -> None:
        def cb(ok: bool | None) -> None:
            if ok:
                on_yes()

        self.app.push_screen(ConfirmScreen(title, message, label, danger), cb)

    # ── specs: create / edit / apply ─────────────────────────

    def _server_yaml(self, row: Any) -> str | None:
        for attr in ("creation_yaml", "user_yaml", "yaml"):
            text = getattr(row, attr, None)
            if text:
                return text
        return None

    def _kind_and_name(self, row: Any) -> tuple[SpecKind, str] | None:
        if isinstance(row, StoredSpec):
            return row.kind, row.name
        kind = self.view.spec_kind
        if kind is None:
            return None
        return kind, row.name

    def _spec_for(self, row: Any) -> tuple[Spec | None, str]:
        """Return (spec, origin) where origin is 'library', 'server' or 'none'."""
        kn = self._kind_and_name(row)
        if kn is None:
            return None, "none"
        kind, name = kn
        if isinstance(row, StoredSpec):
            return row.spec, "library"
        if self.app.store.exists(kind, name):
            stored = self.app.store.load(kind, name)
            return stored.spec, "library"
        text = self._server_yaml(row)
        if text:
            try:
                return spec_from_yaml(kind, name, text), "server"
            except Exception:  # noqa: BLE001
                return None, "server-invalid"
        return None, "none"

    def _form_context(self) -> dict[str, Any]:
        return {"infra_suggestions": list(self.app.infra_cache), "volume_names": list(self.app.volume_cache),
                "pool_names": list(self.app.pool_cache)}

    def open_form(self, kind: SpecKind, spec: Spec | None = None, editing: bool = False) -> None:
        def cb(result: FormResult | None) -> None:
            if result is not None:
                self._handle_form(result, editing)

        label = None
        if kind is SpecKind.SERVICE and editing:
            label = "Rolling update"
        elif kind is SpecKind.CLUSTER and editing:
            label = "Relaunch"
        self.app.push_screen(FormScreen(kind, spec, editing=editing, launch_label=label, **self._form_context()), cb)

    def _handle_form(self, result: FormResult, editing: bool) -> None:
        spec = result.spec
        store = self.app.store

        def save_and_go() -> None:
            path = store.save(spec)
            if result.raw:
                self.open_raw_editor(spec.kind, spec.name)
            elif result.launch:
                self.apply_spec(spec)
            else:
                self.notify(f"Saved {_tilde(path)}")
                self.refresh_data()

        if not editing and store.exists(spec.kind, spec.name):
            self.confirm(
                "Overwrite spec?",
                f"A {spec.kind.title.lower()} spec named [b]{spec.name}[/b] already exists in the library. Replace it?",
                save_and_go,
                label="Overwrite",
            )
        else:
            save_and_go()

    def apply_spec(self, spec: Spec) -> None:
        backend = self.app.backend
        kind = spec.kind
        if kind is SpecKind.CLUSTER:
            tail = None
            if isinstance(spec, ClusterSpec) and spec.run:
                def tail(sink, stop, name=spec.name):
                    backend.stream_cluster_job_logs(name, None, sink, stop)
            self.submit(f"launch {spec.name}", lambda: backend.launch_cluster(spec), follow=True, then_tail=tail,
                        record=(kind, spec.name, "launch"))
        elif kind is SpecKind.JOB:
            self.submit(f"jobs launch {spec.name}", lambda: backend.launch_managed_job(spec), follow=True,
                        record=(kind, spec.name, "launch"))
        elif kind is SpecKind.SERVICE:
            def up_or_update() -> str:
                existing = {s.name for s in backend.list_services()}
                if spec.name in existing:
                    return backend.serve_update(spec)
                return backend.serve_up(spec)

            self.submit(f"serve {spec.name}", up_or_update, follow=True, record=(kind, spec.name, "serve"))
        elif kind is SpecKind.VOLUME:
            self.submit(f"volume apply {spec.name}", lambda: backend.apply_volume(spec), follow=True,
                        record=(kind, spec.name, "apply"))
        elif kind is SpecKind.POOL:
            self.submit(f"pool apply {spec.name}", lambda: backend.pool_apply(spec), follow=True,
                        record=(kind, spec.name, "apply"))

    def open_raw_editor(self, kind: SpecKind, name: str, seed: Spec | None = None) -> None:
        store = self.app.store
        path = store.path_for(kind, name)
        if not path.exists():
            if seed is None:
                self.notify("No spec to edit", severity="error")
                return
            store.save(seed)
        editor = self.app.external_editor
        if editor:
            with self.app.suspend():
                subprocess.call([*shlex.split(editor), str(path)])
            stored = store.load(kind, name)
            if stored.spec is None:
                self.notify(f"Saved, but invalid: {stored.error} — press E to fix", severity="error", timeout=8)
            else:
                self.notify(f"Saved {_tilde(path)} · R to relaunch / apply", timeout=6)
            self.refresh_data()
            return

        def cb(result: EditorResult | None) -> None:
            if result is None:
                return
            store.save_text(kind, name, result.text)
            if result.launch:
                self.apply_spec(result.spec)
            else:
                self.notify(f"Saved {_tilde(path)}")
                self.refresh_data()

        self.app.push_screen(YamlEditorScreen(kind, name, path.read_text(), _tilde(path)), cb)

    # ── actions: shared ──────────────────────────────────────

    def act_create(self) -> None:
        kind = self.view.spec_kind
        if kind is not None:
            self.open_form(kind)
            return

        def picked(k: SpecKind | None) -> None:
            if k is not None:
                self.open_form(k)

        self.app.push_screen(KindPickerScreen(), picked)

    def act_edit(self, row: Any) -> None:
        spec, origin = self._spec_for(row)
        if spec is None:
            if origin == "library" or origin == "server-invalid":
                self.notify("Spec isn't valid for the form; opening the YAML editor", severity="warning")
                kn = self._kind_and_name(row)
                if kn and self.app.store.exists(*kn):
                    self.open_raw_editor(*kn)
                return
            self.notify("No spec found for this resource (it was created outside stratus without YAML)", severity="warning")
            return
        self.open_form(spec.kind, spec, editing=True)

    def act_edit_raw(self, row: Any) -> None:
        kn = self._kind_and_name(row)
        if kn is None:
            return
        spec, origin = self._spec_for(row)
        if not self.app.store.exists(*kn) and spec is None:
            self.notify("No spec for this resource yet", severity="warning")
            return
        self.open_raw_editor(kn[0], kn[1], seed=spec)

    def act_relaunch(self, row: Any) -> None:
        spec, origin = self._spec_for(row)
        if spec is None:
            self.notify("No valid spec to relaunch from — press e to create one", severity="warning")
            return
        verb = {SpecKind.CLUSTER: "Relaunch", SpecKind.JOB: "Resubmit", SpecKind.SERVICE: "Roll out", SpecKind.VOLUME: "Re-apply",
                SpecKind.POOL: "Re-apply"}[spec.kind]
        src = "the spec library" if origin == "library" else "the YAML the server recorded"
        self.confirm(f"{verb} {spec.name}?", f"{verb} [b]{spec.name}[/b] from {src}.", lambda: self.apply_spec(spec), label=verb, danger=False)

    def act_import_spec(self, row: Any) -> None:
        kn = self._kind_and_name(row)
        if kn is None:
            return
        if self.app.store.exists(*kn):
            self.notify("Already in the spec library", timeout=3)
            return
        spec, origin = self._spec_for(row)
        if spec is None:
            self.notify("The server has no usable YAML for this resource", severity="warning")
            return
        path = self.app.store.save(spec)
        self.notify(f"Imported to {_tilde(path)}")
        self.refresh_data()

    def act_yaml(self, row: Any) -> None:
        kn = self._kind_and_name(row)
        if kn and self.app.store.exists(*kn):
            path = self.app.store.path_for(*kn)
            text = path.read_text()
            self.app.push_screen(DetailScreen(f"YAML · {kn[1]}", yaml_block(text), copy_text=text, note=f"{_tilde(path)} · E to edit"))
            return
        text = self._server_yaml(row)
        if text:
            self.app.push_screen(DetailScreen(f"YAML · {row.name}", yaml_block(text), copy_text=text, note="as recorded by the server · i to import"))
        else:
            self.notify("No YAML recorded for this resource", severity="warning")

    def act_describe(self, row: Any) -> None:
        data = {}
        for f in dataclasses.fields(row):
            if f.name in ("creation_yaml", "user_yaml", "yaml", "replicas", "details"):
                continue
            data[f.name] = getattr(row, f.name)
        if getattr(row, "details", None) and isinstance(row.details, dict):
            data.update({k: v for k, v in row.details.items() if v})
        elif getattr(row, "details", None):
            data["details"] = row.details
        body: list[Any] = [describe_table(data)]
        text = self._server_yaml(row)
        if text:
            body += [Text("\nYAML", style="bold #f59e0b"), yaml_block(text)]
        name = getattr(row, "name", None) or getattr(row, "request_id", "")
        self.app.push_screen(DetailScreen(f"Describe · {name}", body))

    # ── actions: clusters ────────────────────────────────────

    def act_drill(self, row: Any) -> None:
        if isinstance(self.view, ClustersView):
            self.push_view(ClusterJobsView(row.name))
        elif isinstance(self.view, ServicesView):
            self.push_view(ReplicasView(row.name))
        elif isinstance(self.view, PoolsView):
            self.push_view(PoolWorkersView(row.name))

    def act_logs(self, row: Any) -> None:
        backend = self.app.backend
        v = self.view
        if isinstance(v, ClustersView):
            self.app.push_screen(LogScreen(f"{row.name} (latest job)", lambda s, e: backend.stream_cluster_job_logs(row.name, None, s, e)))
        elif isinstance(v, ClusterJobsView):
            self.app.push_screen(LogScreen(f"{v.cluster} job {row.job_id}", lambda s, e: backend.stream_cluster_job_logs(v.cluster, row.job_id, s, e)))
        elif isinstance(v, ManagedJobsView):
            follow = row.status.upper() not in TERMINAL_JOB_STATUSES
            self.app.push_screen(LogScreen(f"managed job {row.job_id} ({row.name})",
                                           lambda s, e: backend.stream_managed_job_logs(row.job_id, s, e, follow=follow)))
        elif isinstance(v, ServicesView):
            self.app.push_screen(LogScreen(f"{row.name} load balancer", lambda s, e: backend.stream_service_logs(row.name, "load_balancer", None, s, e)))
        elif isinstance(v, PoolsView):
            self.app.push_screen(LogScreen(f"pool {row.name} controller", lambda s, e: backend.stream_pool_logs(row.name, "controller", None, s, e)))
        elif isinstance(v, PoolWorkersView):
            self.app.push_screen(LogScreen(f"pool {v.service} worker {row.replica_id}", lambda s, e: backend.stream_pool_logs(v.service, "replica", row.replica_id, s, e)))
        elif isinstance(v, ReplicasView):
            self.app.push_screen(LogScreen(f"{v.service} replica {row.replica_id}", lambda s, e: backend.stream_service_logs(v.service, "replica", row.replica_id, s, e)))
        elif isinstance(v, RequestsView):
            self.open_request_logs(row.request_id, f"{row.name} {row.cluster or ''}".strip())

    def act_controller_logs(self, row: Any) -> None:
        backend = self.app.backend
        if isinstance(self.view, ManagedJobsView):
            follow = row.status.upper() not in TERMINAL_JOB_STATUSES
            self.app.push_screen(LogScreen(f"managed job {row.job_id} controller",
                                           lambda s, e: backend.stream_managed_job_logs(row.job_id, s, e, controller=True, follow=follow)))
        elif isinstance(self.view, ServicesView):
            self.app.push_screen(LogScreen(f"{row.name} controller", lambda s, e: backend.stream_service_logs(row.name, "controller", None, s, e)))

    def act_shell(self, row: Any) -> None:
        if row.status != "UP":
            self.notify(f"{row.name} is {row.status}; start it first (u)", severity="warning")
            return
        self.notify(f"Connecting to {row.name}…", timeout=2)

        def go(argv: list[str]) -> None:
            with self.app.suspend():
                print(f"\n[stratus] {' '.join(argv)}   (exit the shell to return to stratus)\n", flush=True)
                subprocess.call(argv)
            self.refresh_data()

        self.run_backend(f"ssh {row.name}", lambda: self.app.backend.ssh_argv(row.name), go)

    def act_exec(self, row: Any) -> None:
        backend = self.app.backend

        def cb(command: str | None) -> None:
            if not command:
                return

            def tail(sink, stop):
                backend.stream_cluster_job_logs(row.name, None, sink, stop)

            self.submit(f"exec on {row.name}", lambda: backend.exec_on_cluster(row.name, command), follow=True, then_tail=tail)

        self.app.push_screen(PromptScreen(f"Exec on {row.name}", "Command to run on the cluster (sky exec):", placeholder="nvidia-smi"), cb)

    def act_stop(self, row: Any) -> None:
        backend = self.app.backend
        if _is_k8s(row.infra):
            self.notify("Kubernetes clusters can't be stopped — use ctrl-d to tear down, or autostop with tear-down (a)",
                        severity="warning", timeout=6)
            return
        self.confirm(f"Stop {row.name}?", f"Stop cluster [b]{row.name}[/b]? Disks are kept; restart with [b]u[/b].",
                     lambda: self.submit(f"stop {row.name}", lambda: backend.stop_cluster(row.name)), label="Stop")

    def act_start(self, row: Any) -> None:
        backend = self.app.backend
        self.submit(f"start {row.name}", lambda: backend.start_cluster(row.name), follow=True)

    def act_autostop(self, row: Any) -> None:
        backend = self.app.backend

        def cb(result: tuple[int, bool] | None) -> None:
            if result is None:
                return
            minutes, down = result
            if minutes >= 0 and not down and _is_k8s(row.infra):
                self.notify("Kubernetes clusters can't be stopped — enable 'tear down instead of stop'", severity="error", timeout=6)
                return
            self.submit(f"autostop {row.name}", lambda: backend.set_autostop(row.name, minutes, down))

        self.app.push_screen(AutostopScreen(row.name, row.autostop, row.to_down), cb)

    def act_down(self, row: Any) -> None:
        backend = self.app.backend
        v = self.view
        if isinstance(v, ClustersView):
            self.confirm(f"Tear down {row.name}?", f"Terminate cluster [b]{row.name}[/b] and delete its disks. This cannot be undone.\n"
                         "[dim]Its spec stays in the library, so you can relaunch it later.[/dim]",
                         lambda: self.submit(f"down {row.name}", lambda: backend.down_cluster(row.name)), label="Down")
        elif isinstance(v, ServicesView):
            self.confirm(f"Tear down service {row.name}?", f"Terminate service [b]{row.name}[/b] and all its replicas.",
                         lambda: self.submit(f"serve down {row.name}", lambda: backend.serve_down(row.name)), label="Down")
        elif isinstance(v, VolumesView):
            self.confirm(f"Delete volume {row.name}?", f"Delete volume [b]{row.name}[/b] and [b]all data on it[/b].",
                         lambda: self.submit(f"delete volume {row.name}", lambda: backend.delete_volume(row.name)), label="Delete")
        elif isinstance(v, PoolsView):
            self.confirm(f"Tear down pool {row.name}?", f"Terminate pool [b]{row.name}[/b] and its {row.total_workers} worker(s).\n"
                         "[dim]Jobs queued on it will fail to schedule.[/dim]",
                         lambda: self.submit(f"pool down {row.name}", lambda: backend.pool_down(row.name)), label="Down")
        elif isinstance(v, StorageView):
            self.confirm(f"Delete bucket {row.name}?", f"Delete storage [b]{row.name}[/b] ({row.stores}) and [b]all objects in it[/b].",
                         lambda: self.submit(f"delete storage {row.name}", lambda: backend.delete_storage(row.name)), label="Delete")

    # ── actions: usage & workspaces ──────────────────────────

    def act_window(self, row: Any = None) -> None:
        v = self.view
        if not isinstance(v, UsageView):
            return
        idx = USAGE_WINDOWS.index(v.days) if v.days in USAGE_WINDOWS else 0
        v.days = USAGE_WINDOWS[(idx + 1) % len(USAGE_WINDOWS)]
        self.notify(f"Window: {'last ' + str(v.days) + ' day(s)' if v.days else 'all time'}", timeout=2)
        self._render_crumbs()
        self.refresh_data()

    def act_switch_workspace(self, row: Any) -> None:
        if row.active:
            self.notify(f"Already in workspace {row.name}", timeout=2)
            return

        def done(_: Any) -> None:
            self.app.workspace = row.name
            self.app.save_workspace(row.name)
            self.query_one(Header).set_scope(workspace=row.name)
            self.notify(f"Workspace: {row.name} — new launches go here", timeout=4)
            self.refresh_data()

        self.run_backend(f"switch to {row.name}", lambda: self.app.backend.set_workspace(row.name), done)

    def act_cancel(self, row: Any) -> None:
        backend = self.app.backend
        v = self.view
        if isinstance(v, ClusterJobsView):
            self.confirm(f"Cancel job {row.job_id}?", f"Cancel job [b]{row.job_id}[/b] ({row.name}) on {v.cluster}?",
                         lambda: self.submit(f"cancel job {row.job_id}", lambda: backend.cancel_cluster_job(v.cluster, row.job_id)), label="Cancel job")
        elif isinstance(v, ManagedJobsView):
            self.confirm(f"Cancel managed job {row.job_id}?", f"Cancel managed job [b]{row.job_id}[/b] ({row.name})?",
                         lambda: self.submit(f"cancel managed job {row.job_id}", lambda: backend.cancel_managed_job(row.job_id)), label="Cancel job")
        elif isinstance(v, RequestsView):
            self.confirm(f"Cancel request?", f"Cancel API request [b]{row.request_id[:13]}[/b] ({row.name})?",
                         lambda: self.run_backend("cancel request", lambda: backend.cancel_request(row.request_id),
                                                  lambda _: (self.notify("Request cancelled"), self.refresh_data())), label="Cancel request")

    def act_terminate(self, row: Any) -> None:
        backend = self.app.backend
        v = self.view
        self.confirm(f"Terminate replica {row.replica_id}?", f"Terminate replica [b]{row.replica_id}[/b] of {v.service}? The autoscaler will replace it.",
                     lambda: self.submit(f"terminate replica {row.replica_id}", lambda: backend.terminate_replica(v.service, row.replica_id)), label="Terminate")

    # ── actions: spec library ────────────────────────────────

    def act_launch_spec(self, row: StoredSpec) -> None:
        if row.spec is None:
            self.notify(f"Invalid spec: {row.error} — press E to fix", severity="error")
            return
        verb = {SpecKind.CLUSTER: "Launch cluster", SpecKind.JOB: "Submit job", SpecKind.SERVICE: "Deploy service", SpecKind.VOLUME: "Apply volume",
                SpecKind.POOL: "Apply pool"}[row.kind]
        self.confirm(f"{verb} {row.name}?", f"{verb} [b]{row.name}[/b] from {_tilde(row.path)}.", lambda: self.apply_spec(row.spec), label=verb.split()[0], danger=False)

    def act_duplicate(self, row: StoredSpec) -> None:
        if row.spec is None:
            self.notify("Fix the spec before duplicating it", severity="error")
            return

        def cb(name: str | None) -> None:
            if not name:
                return
            try:
                new = row.spec.model_copy(update={"name": name})
                new = type(new).model_validate(new.model_dump())
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Invalid name: {_short_error(exc)}", severity="error")
                return
            if self.app.store.exists(row.kind, name):
                self.notify(f"{name} already exists", severity="error")
                return
            self.app.store.save(new)
            self.notify(f"Created {name}")
            self.refresh_data()

        self.app.push_screen(PromptScreen(f"Duplicate {row.name}", "Name for the copy:", value=f"{row.name}-copy"), cb)

    def act_delete_spec(self, row: StoredSpec) -> None:
        def go() -> None:
            self.app.store.delete(row.kind, row.name)
            self.notify(f"Deleted {_tilde(row.path)}")
            self.refresh_data()

        self.confirm("Delete spec file?", f"Delete [b]{_tilde(row.path)}[/b]? Running resources are not affected.", go, label="Delete")

    def act_path(self, row: StoredSpec) -> None:
        self.app.copy_to_clipboard(str(row.path))
        self.notify(f"{row.path}  (copied)", timeout=6)


def _tilde(path: Any) -> str:
    from pathlib import Path

    p = Path(path)
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def _is_k8s(infra: str | None) -> bool:
    return (infra or "").lower().startswith(("kubernetes", "k8s"))


def _escape(text: str) -> str:
    return text.replace("[", r"\[")


def default_editor() -> str | None:
    return os.environ.get("VISUAL") or os.environ.get("EDITOR")
