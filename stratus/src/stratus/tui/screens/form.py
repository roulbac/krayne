"""Create/edit forms with a live YAML preview.

Ctrl+S saves the spec to the library *and* launches/applies it; Ctrl+O only
saves it. Ctrl+E opens the generated YAML in the raw editor (escape hatch).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Footer, Input, Label, Select, Static, Switch, TextArea

from stratus.specs.models import Spec, SpecKind
from stratus.tui.forms import SCHEMAS, FormError, FormField, default_form, form_to_spec, spec_to_form
from stratus.tui.screens.detail import yaml_block


@dataclass(frozen=True)
class FormResult:
    spec: Spec
    launch: bool
    raw: bool = False  # user asked to continue in the raw YAML editor


class FormScreen(Screen[FormResult | None]):
    BINDINGS = [
        Binding("ctrl+s", "submit(True)", "Save & launch", priority=True),
        Binding("ctrl+o", "submit(False)", "Save only", priority=True),
        Binding("ctrl+e", "raw", "Edit as YAML", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        kind: SpecKind,
        spec: Spec | None = None,
        *,
        editing: bool = False,
        launch_label: str | None = None,
        infra_suggestions: list[str] | None = None,
        volume_names: list[str] | None = None,
        pool_names: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.schema = SCHEMAS[kind]
        self.base = spec
        self.editing = editing
        self.values: dict[str, Any] = spec_to_form(spec) if spec is not None else default_form(kind)
        self.launch_label = launch_label or {
            SpecKind.CLUSTER: "Launch", SpecKind.JOB: "Submit", SpecKind.SERVICE: "Deploy", SpecKind.VOLUME: "Apply",
            SpecKind.POOL: "Apply",
        }[kind]
        self.infra_suggestions = infra_suggestions or []
        self.volume_names = volume_names or []
        self.pool_names = pool_names or []
        # After the first submit attempt, keep showing errors live as the user fixes them.
        self._show_errors = False

    # ── layout ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        verb = "Edit" if self.editing else "New"
        yield Static(
            f"[b #f59e0b] {verb} {self.kind.title.lower()}[/]   [dim]fill the form — stratus writes the YAML for you[/dim]",
            id="form-title",
        )
        with Horizontal(id="form-body"):
            with VerticalScroll(id="form-fields"):
                for section in self.schema.ordered_sections():
                    yield Static(f"[b]{section}[/b]", classes="form-section")
                    for f in self.schema.fields:
                        if f.section == section:
                            yield from self._field(f)
                yield Static("", id="e-_form", classes="field-error")
            with Vertical(id="form-preview"):
                yield Static("[b]YAML preview[/b] [dim]· saved to the spec library[/dim]", classes="form-section")
                with VerticalScroll():
                    yield Static("", id="preview")
        with Horizontal(id="form-buttons"):
            yield Button(f"{self.launch_label} (ctrl+s)", id="launch", variant="primary")
            yield Button("Save only (ctrl+o)", id="save")
            yield Button("Edit as YAML (ctrl+e)", id="raw")
            yield Button("Cancel (esc)", id="cancel")
        yield Footer()

    def _field(self, f: FormField):
        value = self.values.get(f.key, False if f.type == "bool" else "")
        label = f.label + (" *" if f.required else "")
        wid = f"f-{f.key}"
        help_text = f.help
        if f.key == "volumes" and self.volume_names:
            help_text += f" · existing: {', '.join(self.volume_names[:6])}"
        if f.key == "pool" and self.pool_names:
            help_text += f" · existing: {', '.join(self.pool_names[:6])}"
        if f.type == "area":
            yield Label(label + (f"  [dim]{help_text}[/dim]" if help_text else ""), classes="field-label")
            area = TextArea(str(value or ""), id=wid, classes="field-area", soft_wrap=True, show_line_numbers=False)
            yield area
        else:
            with Horizontal(classes="field-row"):
                yield Label(label, classes="field-name")
                if f.type == "bool":
                    yield Switch(value=bool(value), id=wid)
                elif f.type == "select":
                    # Select values must be non-empty; "" (= SkyPilot default) maps to a sentinel.
                    options = [(lbl, val or "__default__") for lbl, val in f.options]
                    known = {v for _, v in options}
                    current = value or "__default__"
                    if current not in known:  # a value set by hand in the YAML
                        options.append((str(current), str(current)))
                    yield Select(options, value=current, id=wid, allow_blank=False, compact=True)
                else:
                    suggestions = list(f.suggestions)
                    if f.key == "infra":
                        suggestions = self.infra_suggestions + [s for s in suggestions if s not in self.infra_suggestions]
                    elif f.key == "pool":
                        suggestions = list(self.pool_names)
                    yield Input(
                        str(value or ""), placeholder=f.placeholder, id=wid, compact=True, valid_empty=True,
                        type="integer" if f.type == "int" else "text",
                        suggester=SuggestFromList(suggestions, case_sensitive=False) if suggestions else None,
                    )
            if help_text:
                yield Static(f"[dim]{help_text}[/dim]", classes="field-help")
        yield Static("", id=f"e-{f.key}", classes="field-error")

    def on_mount(self) -> None:
        if self.editing:
            # Renaming an existing resource would create a new one; keep it fixed.
            self.query_one("#f-name", Input).disabled = True
            self.query_one("#f-infra", Input).focus() if self.kind is not SpecKind.VOLUME else self.query_one("#f-size", Input).focus()
        else:
            self.query_one("#f-name", Input).focus()
        self._refresh_preview()

    # ── values & preview ─────────────────────────────────────

    def collect(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.schema.fields:
            w = self.query_one(f"#f-{f.key}")
            if isinstance(w, Switch):
                out[f.key] = w.value
            elif isinstance(w, TextArea):
                out[f.key] = w.text
            elif isinstance(w, Select):
                v = w.value
                out[f.key] = "" if v in (Select.NULL, "__default__") or v is None else str(v)
            elif isinstance(w, Input):
                out[f.key] = w.value
        return out

    def _build(self) -> tuple[Spec | None, dict[str, str]]:
        try:
            return form_to_spec(self.kind, self.collect(), base=self.base), {}
        except FormError as exc:
            return None, exc.errors

    def _refresh_preview(self, show_errors: bool = False) -> Spec | None:
        self._show_errors = self._show_errors or show_errors
        show_errors = self._show_errors
        spec, errors = self._build()
        preview = self.query_one("#preview", Static)
        if spec is not None:
            preview.update(yaml_block(spec.to_yaml()))
        else:
            lines = "\n".join(f"#  {k if k != '_form' else 'form'}: {v}" for k, v in errors.items())
            preview.update(yaml_block(f"# fix these to see the YAML:\n{lines}"))
        for key in [f.key for f in self.schema.fields] + ["_form"]:
            box = self.query_one(f"#e-{key}", Static)
            show = show_errors and key in errors
            box.update(f"✗ {errors[key]}" if show else "")
            box.display = show
        return spec

    @on(Input.Changed)
    @on(Switch.Changed)
    @on(Select.Changed)
    @on(TextArea.Changed)
    def _changed(self, event) -> None:
        self._refresh_preview()

    # ── actions ──────────────────────────────────────────────

    def action_submit(self, launch: bool) -> None:
        spec = self._refresh_preview(show_errors=True)
        if spec is None:
            self.notify("Fix the highlighted fields first", severity="error")
            return
        self.dismiss(FormResult(spec, launch=launch))

    def action_raw(self) -> None:
        spec = self._refresh_preview(show_errors=True)
        if spec is None:
            self.notify("Fix the highlighted fields before switching to YAML", severity="error")
            return
        self.dismiss(FormResult(spec, launch=False, raw=True))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "launch":
            self.action_submit(True)
        elif bid == "save":
            self.action_submit(False)
        elif bid == "raw":
            self.action_raw()
        else:
            self.action_cancel()
