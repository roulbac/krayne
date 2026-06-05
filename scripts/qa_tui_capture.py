"""Drive the Krayne TUI headlessly, assert button/key flows, and capture a
PNG per UI state for visual QA.

This is the engine behind the `claude-tui-qa` GitHub Actions workflow. The TUI
has been hard to QA because "does it *look* right" is not something a normal
assert can check. This script splits the problem in two:

  * **Flow / behavior** (deterministic) — it drives the real key bindings and
    buttons with Textual's ``Pilot`` and asserts on widget state (screen
    transitions, prefilled defaults, validation errors, focus, responsive
    layout, ...). Results are written to ``flow-results.json``.

  * **Appearance** (visual) — at every meaningful state it exports the screen
    via ``app.export_screenshot()`` (SVG) and renders it to a real PNG with a
    headless Chromium (correct fonts), one named file per state. A human — or a
    multimodal agent reading the PNGs — judges whether each screen looks right.

No live cluster is needed: every SDK call the screens make is mocked, so this
runs anywhere ``uv sync`` + ``playwright install chromium`` succeeds.

Usage::

    uv run python scripts/qa_tui_capture.py                 # drive + render PNGs
    uv run python scripts/qa_tui_capture.py --no-render      # drive only (SVGs + JSON)
    uv run python scripts/qa_tui_capture.py --out-dir DIR    # output location

Outputs (default ``qa-artifacts/tui/``): ``NN-<state>.png`` frames,
``flow-results.json`` (machine-readable flow checks), and ``summary.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from krayne.api.types import ClusterDetails, ClusterInfo, HeadNodeInfo, WorkerGroupInfo

# ── Mock data (covers the status color buckets + services) ───────────────────

CLUSTERS = [
    ClusterInfo(
        name="prod-training", namespace="default", status="ready",
        head_ip="10.42.0.15", dashboard_url="http://10.42.0.15:8265",
        client_url="ray://10.42.0.15:10001", notebook_url="http://10.42.0.15:8888",
        code_server_url="http://10.42.0.15:8443", ssh_url=None,
        num_workers=4, autoscaling_enabled=True, created_at="2026-05-30T08:30:00Z",
    ),
    ClusterInfo(
        name="dev-experiment", namespace="default", status="running",
        head_ip="10.42.0.22", dashboard_url="http://10.42.0.22:8265",
        client_url="ray://10.42.0.22:10001", notebook_url=None,
        code_server_url=None, ssh_url=None,
        num_workers=2, autoscaling_enabled=True, created_at="2026-06-01T10:15:00Z",
    ),
    ClusterInfo(
        name="staging-pipeline", namespace="default", status="creating",
        head_ip=None, dashboard_url=None, client_url=None, notebook_url=None,
        code_server_url=None, ssh_url=None,
        num_workers=1, autoscaling_enabled=False, created_at="2026-06-04T11:45:00Z",
    ),
    ClusterInfo(
        name="batch-etl", namespace="default", status="pods-pending",
        head_ip=None, dashboard_url=None, client_url=None, notebook_url=None,
        code_server_url=None, ssh_url=None,
        num_workers=3, autoscaling_enabled=True, created_at="2026-06-04T12:05:00Z",
    ),
    ClusterInfo(
        name="broken-image", namespace="default", status="image-pull-error",
        head_ip=None, dashboard_url=None, client_url=None, notebook_url=None,
        code_server_url=None, ssh_url=None,
        num_workers=2, autoscaling_enabled=True, created_at="2026-06-05T07:10:00Z",
    ),
    ClusterInfo(
        name="stuck-scheduler", namespace="default", status="unschedulable",
        head_ip=None, dashboard_url=None, client_url=None, notebook_url=None,
        code_server_url=None, ssh_url=None,
        num_workers=1, autoscaling_enabled=False, created_at="2026-06-05T07:40:00Z",
    ),
]

DETAILS = ClusterDetails(
    info=CLUSTERS[0],
    head=HeadNodeInfo(cpus="4", memory="8Gi", gpus=0, image="rayproject/ray:2.43.0-py312"),
    worker_groups=[
        WorkerGroupInfo(
            name="gpu-workers", replicas=4, min_replicas=0, max_replicas=10,
            cpus="2", memory="16Gi", gpus=1,
        ),
    ],
    ray_version="2.43.0", python_version="3.12",
)

DETAILS_MULTI = ClusterDetails(
    info=CLUSTERS[0],
    head=HeadNodeInfo(cpus="2", memory="8Gi", gpus=0, image="rayproject/ray:2.43.0-py312"),
    worker_groups=[
        WorkerGroupInfo(
            name="cpu-workers", replicas=2, min_replicas=0, max_replicas=10,
            cpus="4", memory="8Gi", gpus=0,
        ),
        WorkerGroupInfo(
            name="gpu-workers", replicas=1, min_replicas=0, max_replicas=5,
            cpus="2", memory="4Gi", gpus=1,
        ),
    ],
    ray_version="2.43.0", python_version="3.12",
)

SERVICES = ["dashboard", "client", "notebook", "code-server"]


# ── Result accumulation ──────────────────────────────────────────────────────


@dataclass
class Check:
    id: str
    scenario: str
    kind: str          # "flow" or "state"
    description: str
    passed: bool
    detail: str = ""


@dataclass
class Frame:
    name: str          # file stem, e.g. "01-explorer-standard"
    scenario: str
    title: str
    svg: str = field(repr=False, default="")


class Session:
    """Accumulates flow checks and screenshot frames across the run."""

    def __init__(self) -> None:
        self.checks: list[Check] = []
        self.frames: list[Frame] = []
        self._app = None

    def bind(self, app) -> None:
        self._app = app

    def check(self, cid: str, scenario: str, description: str, passed: bool,
              detail: str = "", kind: str = "flow") -> bool:
        self.checks.append(Check(cid, scenario, kind, description, bool(passed), detail))
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {cid}: {description}" + (f"  ({detail})" if detail else ""))
        return passed

    def snap(self, name: str, scenario: str, title: str) -> None:
        svg = self._app.export_screenshot()
        self.frames.append(Frame(name=name, scenario=scenario, title=title, svg=svg))
        print(f"  [snap] {name}")


# ── Pilot helpers ─────────────────────────────────────────────────────────────


async def _pause(pilot, n: int = 3) -> None:
    for _ in range(n):
        await pilot.pause()


async def _switch_tab(app, tabs_id: str, tab_id: str, pilot) -> None:
    """Switch a TabbedContent tab without focus snap-back (reliable for capture)."""
    from textual.widgets import TabbedContent

    app.screen.set_focus(None)
    tabs = app.screen.query_one(f"#{tabs_id}", TabbedContent)
    tabs.active = tab_id
    await _pause(pilot)


async def _select_cluster(app, key: str, pilot) -> bool:
    """Move the explorer's table cursor to the row with *key* (e.g.
    'default/prod-training') so subsequent actions target a known cluster.
    Best-effort: the table sorts by name, so row 0 is otherwise arbitrary."""
    from krayne.tui.widgets.cluster_table import ClusterTable

    table = app.screen.query_one(ClusterTable)
    table.focus()
    for idx, row_key in enumerate(table.rows):
        if row_key.value == key:
            table.move_cursor(row=idx)
            await _pause(pilot)
            return True
    await _pause(pilot)
    return False


def _input_value(screen, wid: str) -> str:
    from textual.widgets import Input
    return screen.query_one(wid, Input).value


def _explorer_patches():
    return [
        patch("krayne.tui.screens.explorer.list_clusters", return_value=CLUSTERS),
        patch("krayne.tui.screens.explorer.is_tunnel_active", return_value=False),
    ]


def _detail_patches():
    return [
        patch("krayne.tui.screens.detail.describe_cluster", return_value=DETAILS),
        patch("krayne.tui.screens.detail.get_cluster_services", return_value=SERVICES),
        patch("krayne.tui.screens.detail.is_tunnel_active", return_value=False),
        patch("krayne.tui.screens.detail.load_tunnel_state", return_value=None),
        patch("krayne.tui.screens.detail.check_service_health",
              return_value={svc: "available" for svc in SERVICES}),
        patch("krayne.tui.screens._base.is_tunnel_active", return_value=False),
        patch("krayne.tui.screens._base.start_tunnels", return_value=[]),
        patch("krayne.tui.screens._base.stop_tunnels", return_value=True),
        patch("krayne.tui.screens.delete_confirm.is_tunnel_active", return_value=True),
        patch("krayne.tui.screens.namespace_picker._fetch_namespaces", return_value=[]),
    ]


# ── Scenarios ─────────────────────────────────────────────────────────────────


async def scenario_explorer_layout(s: Session) -> None:
    """TUI-1: launch, populated table, scope bar, responsive sizing + columns."""
    from krayne.tui.app import IKrayneApp
    from krayne.tui.screens.explorer import ExplorerScreen
    from krayne.tui.widgets.cluster_table import (
        COLUMNS_COMPACT, COLUMNS_STANDARD, COLUMNS_WIDE, ClusterTable,
    )
    from textual.widgets import Static

    app = IKrayneApp()
    async with app.run_test(size=(120, 35)) as pilot:
        s.bind(app)
        await _pause(pilot, 6)

        s.check("T1.launch", "TUI-1", "App launches into the Explorer screen",
                isinstance(app.screen, ExplorerScreen))
        table = app.screen.query_one(ClusterTable)
        s.check("T1.table", "TUI-1", "Cluster table is populated from the SDK",
                table.row_count == len(CLUSTERS),
                detail=f"row_count={table.row_count} expected={len(CLUSTERS)}")
        scope = str(app.screen.query_one("#scope-bar", Static).renderable)
        s.check("T1.scope", "TUI-1", "Scope bar shows namespace + cluster counts",
                "default" in scope and "Clusters:" in scope, detail=scope.strip())

        s.check("T1.standard", "TUI-1", "120x35 is classed 'standard'",
                app.terminal_class == "standard", detail=app.terminal_class)
        s.check("T1.cols-standard", "TUI-1", "Standard width uses the standard column set",
                table._current_columns == COLUMNS_STANDARD)
        s.snap("01-explorer-standard", "TUI-1", "Explorer — standard (120x35)")

        await pilot.resize_terminal(80, 24)
        await _pause(pilot)
        s.check("T1.compact", "TUI-1", "80x24 collapses to 'compact'",
                app.terminal_class == "compact", detail=app.terminal_class)
        s.check("T1.cols-compact", "TUI-1", "Compact width drops to the compact column set",
                table._current_columns == COLUMNS_COMPACT)
        s.snap("02-explorer-compact", "TUI-1", "Explorer — compact (80x24)")

        await pilot.resize_terminal(160, 45)
        await _pause(pilot)
        s.check("T1.wide", "TUI-1", "160x45 expands to 'wide'",
                app.terminal_class == "wide", detail=app.terminal_class)
        s.check("T1.cols-wide", "TUI-1", "Wide width uses the wide column set (adds Tunnels)",
                table._current_columns == COLUMNS_WIDE)
        s.snap("03-explorer-wide", "TUI-1", "Explorer — wide (160x45)")


async def scenario_explorer_interactions(s: Session) -> None:
    """TUI-2: filter bar open + applied; empty state."""
    from krayne.tui.app import IKrayneApp
    from krayne.tui.widgets.filter_bar import FilterBar, make_filter_fn

    # -- Filter --
    app = IKrayneApp()
    async with app.run_test(size=(120, 35)) as pilot:
        s.bind(app)
        await _pause(pilot, 6)

        await pilot.press("slash")
        await _pause(pilot)
        fb = app.screen.query_one(FilterBar)
        s.check("T2.filter-open", "TUI-2", "'/' reveals the filter bar",
                fb.has_class("visible"))
        s.snap("04-explorer-filter-open", "TUI-2", "Explorer — filter bar open")

        # The filter input is focused after '/', so type into it.
        await pilot.press(*list("status:ready"))
        await _pause(pilot, 2)
        from krayne.tui.widgets.cluster_table import ClusterTable
        table = app.screen.query_one(ClusterTable)
        # Ground truth: the table should match the real filter fn applied to the data.
        fn = make_filter_fn("status:ready", {})
        expected = sum(1 for c in CLUSTERS if fn(c)) if fn else len(CLUSTERS)
        s.check("T2.filter-applied", "TUI-2",
                "Typing 'status:ready' narrows the table to matching clusters",
                table.row_count == expected and 0 < table.row_count < len(CLUSTERS),
                detail=f"row_count={table.row_count} expected={expected} (of {len(CLUSTERS)})")
        s.snap("05-explorer-filtered", "TUI-2", "Explorer — filtered to status:ready")

        # Filter-function logic (unit-level, deterministic).
        fn = make_filter_fn("status:creating")
        s.check("T2.filter-fn", "TUI-2", "make_filter_fn('status:creating') matches only 'creating'",
                fn is not None and fn(CLUSTERS[2]) and not fn(CLUSTERS[0]))

    # -- Empty state (own session with an empty cluster list) --
    with patch("krayne.tui.screens.explorer.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            s.bind(app)
            await _pause(pilot, 6)
            empty = app.screen.query_one("#empty-state")
            from krayne.tui.widgets.cluster_table import ClusterTable
            table = app.screen.query_one(ClusterTable)
            s.check("T2.empty", "TUI-2",
                    "Empty namespace shows the empty-state and hides the table",
                    empty.display is True and table.display is False)
            s.snap("06-explorer-empty", "TUI-2", "Explorer — empty state")


async def scenario_create_flow(s: Session) -> None:
    """TUI-3: create form opens, prefilled defaults, tab nav, review, validation."""
    from krayne.tui.app import IKrayneApp
    from krayne.tui.screens.create_flow import CreateFlowScreen
    from krayne.tui.screens.explorer import ExplorerScreen
    from textual.widgets import Button, Input, Static, Switch, TabbedContent

    app = IKrayneApp()
    async with app.run_test(size=(120, 35)) as pilot:
        s.bind(app)
        await _pause(pilot, 6)

        await pilot.press("c")
        await _pause(pilot)
        s.check("T3.open", "TUI-3", "'c' opens the create flow",
                isinstance(app.screen, CreateFlowScreen))
        screen = app.screen
        defaults_ok = (
            _input_value(screen, "#input-namespace") == "default"
            and _input_value(screen, "#input-head-cpus") != ""
            and _input_value(screen, "#input-head-memory") != ""
            and _input_value(screen, "#input-wg0-cpus") != ""
            and _input_value(screen, "#input-wg0-memory") != ""
            and screen.query_one("#switch-notebook", Switch).value is True
            and screen.query_one("#switch-code-server", Switch).value is True
            and screen.query_one("#switch-ssh", Switch).value is True
        )
        s.check("T3.defaults", "TUI-3",
                "Form is prefilled (namespace, head, worker, all services on)", defaults_ok)
        s.snap("07-create-cluster-tab", "TUI-3", "Create — Cluster tab (defaults)")

        # Validate the key binding for tab navigation works (Ctrl+T -> next tab).
        await pilot.press("ctrl+t")
        await _pause(pilot)
        active_after_key = app.screen.query_one("#create-tabs", TabbedContent).active
        s.check("T3.tab-binding", "TUI-3", "Ctrl+T advances to the next tab",
                active_after_key == "tab-head", detail=f"active={active_after_key}")
        s.snap("08-create-head-tab", "TUI-3", "Create — Head Node tab")

        # Capture the remaining tabs deterministically.
        await _switch_tab(app, "create-tabs", "tab-workers", pilot)
        s.snap("09-create-workers-tab", "TUI-3", "Create — Workers tab")
        await _switch_tab(app, "create-tabs", "tab-autoscaling", pilot)
        s.snap("10-create-autoscaling-tab", "TUI-3", "Create — Autoscaling tab")
        await _switch_tab(app, "create-tabs", "tab-services", pilot)
        s.snap("11-create-services-tab", "TUI-3", "Create — Services tab")

        # Fill a name so the Review tab renders a summary instead of errors.
        app.screen.query_one("#input-name", Input).value = "qa-demo"
        await _switch_tab(app, "create-tabs", "tab-review", pilot)
        review = str(app.screen.query_one("#review-content", Static).renderable)
        s.check("T3.review", "TUI-3", "Review tab summarizes the config once valid",
                "qa-demo" in review and "Ready to create" in review)
        s.snap("12-create-review-tab", "TUI-3", "Create — Review tab")

        # Validation: clear the name and press the Create button.
        await _switch_tab(app, "create-tabs", "tab-cluster", pilot)
        app.screen.query_one("#input-name", Input).value = ""
        app.screen.query_one("#btn-create", Button).press()
        await _pause(pilot, 2)
        err = app.screen.query_one("#form-error", Static)
        s.check("T3.validation", "TUI-3",
                "Submitting an empty name shows an inline validation error",
                err.has_class("visible"), detail=str(err.renderable))
        s.snap("13-create-validation-error", "TUI-3", "Create — validation error")

        await pilot.press("escape")
        await _pause(pilot)
        s.check("T3.escape", "TUI-3", "Escape returns from the create flow to the Explorer",
                isinstance(app.screen, ExplorerScreen))


async def scenario_detail(s: Session) -> None:
    """TUI-4: detail screen opens on Enter; five tabs; service rows; Escape back."""
    from krayne.tui.app import IKrayneApp
    from krayne.tui.screens.detail import ClusterDetailScreen
    from krayne.tui.screens.explorer import ExplorerScreen
    from textual.widgets import Static, TabPane

    app = IKrayneApp()
    async with app.run_test(size=(120, 35)) as pilot:
        s.bind(app)
        await _pause(pilot, 6)

        await _select_cluster(app, "default/prod-training", pilot)
        await pilot.press("enter")
        await _pause(pilot, 6)

        s.check("T4.open", "TUI-4", "Enter on a cluster opens the detail screen",
                isinstance(app.screen, ClusterDetailScreen))
        tab_ids = {p.id for p in app.screen.query(TabPane)}
        expected = {"tab-overview", "tab-workers", "tab-services", "tab-tunnels", "tab-config"}
        s.check("T4.tabs", "TUI-4", "Detail screen has the five expected tabs",
                tab_ids == expected, detail=str(sorted(tab_ids)))
        overview = str(app.screen.query_one("#overview-content", Static).renderable)
        s.check("T4.overview", "TUI-4", "Overview tab shows the cluster name",
                "prod-training" in overview)
        s.snap("14-detail-overview", "TUI-4", "Detail — Overview tab")

        await _switch_tab(app, "detail-tabs", "tab-workers", pilot)
        s.snap("15-detail-workers", "TUI-4", "Detail — Worker Groups tab")

        await _switch_tab(app, "detail-tabs", "tab-services", pilot)
        svc_txt = str(app.screen.query_one("#services-content", Static).renderable)
        s.check("T4.services", "TUI-4",
                "Services tab lists detected services with availability/endpoints",
                "dashboard" in svc_txt and "notebook" in svc_txt,
                detail="rendered services content")
        s.snap("16-detail-services", "TUI-4", "Detail — Services tab")

        await _switch_tab(app, "detail-tabs", "tab-tunnels", pilot)
        s.snap("17-detail-tunnels", "TUI-4", "Detail — Tunnels tab")
        await _switch_tab(app, "detail-tabs", "tab-config", pilot)
        s.snap("18-detail-config", "TUI-4", "Detail — Config tab")

        await pilot.press("escape")
        await _pause(pilot)
        s.check("T4.escape", "TUI-4", "Escape returns from detail to the Explorer",
                isinstance(app.screen, ExplorerScreen))


async def scenario_modals(s: Session) -> None:
    """TUI-5: scale (single + multi group), delete, namespace, help dialogs."""
    from krayne.tui.app import IKrayneApp
    from krayne.tui.screens.delete_confirm import DeleteConfirmScreen
    from krayne.tui.screens.help_overlay import HelpOverlay
    from krayne.tui.screens.namespace_picker import NamespacePickerScreen
    from krayne.tui.screens.scale_flow import ScaleFlowScreen
    from textual.widgets import Button, Input, OptionList, Static

    # Scale flow reads describe_cluster; start with the single-group mock.
    scale_patch = patch("krayne.tui.screens.scale_flow.describe_cluster", return_value=DETAILS)
    scale_mock = scale_patch.start()
    try:
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            s.bind(app)
            await _pause(pilot, 6)
            await _select_cluster(app, "default/prod-training", pilot)

            # -- Scale (single worker group) --
            await pilot.press("s")
            await _pause(pilot, 5)
            ok_scale = isinstance(app.screen, ScaleFlowScreen)
            s.check("T5.scale-open", "TUI-5", "'s' opens the scale dialog", ok_scale)
            if ok_scale:
                glist = app.screen.query_one("#scale-group-list", OptionList)
                reps = app.screen.query_one("#scale-replicas", Input)
                s.check("T5.scale-single", "TUI-5",
                        "Single worker group skips the picker and shows replicas",
                        glist.display is False and reps.display is True,
                        detail=f"replicas={reps.value}")
                s.snap("19-scale-single-group", "TUI-5", "Scale — single worker group")
            app.pop_screen()
            await _pause(pilot, 2)

            # -- Scale (multiple worker groups) --
            scale_mock.return_value = DETAILS_MULTI
            await pilot.press("s")
            await _pause(pilot, 5)
            if isinstance(app.screen, ScaleFlowScreen):
                glist = app.screen.query_one("#scale-group-list", OptionList)
                s.check("T5.scale-multi", "TUI-5",
                        "Multiple worker groups show the group picker",
                        glist.display is True)
                s.snap("20-scale-multi-group", "TUI-5", "Scale — multiple worker groups")
            else:
                s.check("T5.scale-multi", "TUI-5",
                        "Multiple worker groups show the group picker", False,
                        detail="scale dialog did not open")
            app.pop_screen()
            await _pause(pilot, 2)
            scale_mock.return_value = DETAILS

            # -- Delete confirm --
            await pilot.press("d")
            await _pause(pilot, 3)
            ok_del = isinstance(app.screen, DeleteConfirmScreen)
            s.check("T5.delete-open", "TUI-5", "'d' opens the delete confirmation", ok_del)
            if ok_del:
                cancel = app.screen.query_one("#btn-cancel-delete", Button)
                confirm = app.screen.query_one("#btn-confirm-delete", Button)
                s.check("T5.delete-focus", "TUI-5",
                        "Cancel is focused by default (guards against accidental deletes)",
                        cancel.has_focus)
                s.check("T5.delete-variant", "TUI-5",
                        "Confirm button uses the destructive 'error' variant",
                        confirm.variant == "error", detail=str(confirm.variant))
                names = " ".join(str(w.renderable) for w in app.screen.query(Static))
                s.check("T5.delete-name", "TUI-5", "Delete dialog names the target cluster",
                        app.screen.cluster_name in names,
                        detail=f"cluster={app.screen.cluster_name}")
                s.snap("21-delete-confirm", "TUI-5", "Delete — confirmation dialog")
            app.pop_screen()
            await _pause(pilot, 2)

            # -- Namespace picker --
            await pilot.press("n")
            await _pause(pilot, 3)
            ok_ns = isinstance(app.screen, NamespacePickerScreen)
            s.check("T5.ns-open", "TUI-5", "'n' opens the namespace picker", ok_ns)
            if ok_ns:
                nslist = app.screen.query_one("#ns-list", OptionList)
                s.check("T5.ns-list", "TUI-5", "Namespace picker lists at least 'default'",
                        nslist.option_count >= 1, detail=f"options={nslist.option_count}")
                s.snap("22-namespace-picker", "TUI-5", "Namespace — picker dialog")
            app.pop_screen()
            await _pause(pilot, 2)

            # -- Help overlay --
            await pilot.press("question_mark")
            await _pause(pilot, 3)
            ok_help = isinstance(app.screen, HelpOverlay)
            s.check("T5.help-open", "TUI-5", "'?' opens the help overlay", ok_help)
            if ok_help:
                content = str(app.screen.query_one("#help-content", Static).renderable)
                s.check("T5.help-content", "TUI-5", "Help overlay lists keyboard shortcuts",
                        "Create" in content or "Scale" in content or "Filter" in content)
                s.snap("23-help-overlay", "TUI-5", "Help — keyboard shortcuts overlay")
    finally:
        scale_patch.stop()


SCENARIOS = [
    scenario_explorer_layout,
    scenario_explorer_interactions,
    scenario_create_flow,
    scenario_detail,
    scenario_modals,
]


async def drive() -> Session:
    s = Session()
    patches = _explorer_patches() + _detail_patches()
    for p in patches:
        p.start()
    try:
        for fn in SCENARIOS:
            print(f"\n=== {fn.__name__} ===")
            try:
                await fn(s)
            except Exception as exc:  # noqa: BLE001 — record, don't abort the run
                import traceback
                tb = traceback.format_exc()
                s.check(f"{fn.__name__}.harness", fn.__name__, "Scenario ran without harness error",
                        False, detail=f"{type(exc).__name__}: {exc}")
                print(tb, file=sys.stderr)
    finally:
        for p in patches:
            p.stop()
    return s


# ── Rendering (SVG -> PNG via headless Chromium) ─────────────────────────────


def _render_pngs(frames: list[Frame], out_dir: Path) -> bool:
    try:
        import tempfile

        from PIL import Image
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        print(f"!! rendering deps unavailable ({exc}); skipping PNG render", file=sys.stderr)
        return False

    def svg_to_png(svg_text: str, browser) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", delete=False) as f:
            f.write(svg_text)
            svg_path = f.name
        page = browser.new_page()
        page.goto(f"file://{svg_path}")
        page.wait_for_timeout(400)
        el = page.query_selector("svg")
        box = el.bounding_box()
        png = page.screenshot(clip={"x": box["x"], "y": box["y"],
                                    "width": box["width"], "height": box["height"]})
        page.close()
        Path(svg_path).unlink(missing_ok=True)
        return png

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for fr in frames:
                png = svg_to_png(fr.svg, browser)
                img = Image.open(io.BytesIO(png))
                img.save(out_dir / f"{fr.name}.png")
                print(f"  rendered {fr.name}.png  {img.size}")
            browser.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"!! chromium render failed ({exc}); PNGs may be incomplete", file=sys.stderr)
        return False


# ── Output ────────────────────────────────────────────────────────────────────


def _write_outputs(s: Session, out_dir: Path, rendered: bool) -> dict:
    by_scenario: dict[str, dict] = {}
    for c in s.checks:
        b = by_scenario.setdefault(c.scenario, {"passed": 0, "failed": 0})
        b["passed" if c.passed else "failed"] += 1

    total = len(s.checks)
    failed = sum(1 for c in s.checks if not c.passed)
    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "out_dir": str(out_dir),
        "rendered": rendered,
        "harness_ok": all(not c.id.endswith(".harness") or c.passed for c in s.checks),
        "summary": {
            "total_checks": total, "passed": total - failed, "failed": failed,
            "by_scenario": by_scenario,
        },
        "frames": [
            {"name": f.name, "png": f"{f.name}.png", "scenario": f.scenario, "title": f.title}
            for f in s.frames
        ],
        "checks": [
            {"id": c.id, "scenario": c.scenario, "kind": c.kind,
             "description": c.description, "passed": c.passed, "detail": c.detail}
            for c in s.checks
        ],
    }
    (out_dir / "flow-results.json").write_text(json.dumps(result, indent=2))

    # Human-readable companion.
    lines = [f"# TUI flow checks — {result['generated_at']}", ""]
    lines.append(f"- Checks: **{total - failed}/{total} passed**"
                 + (f" ({failed} failed)" if failed else ""))
    lines.append(f"- Frames captured: **{len(s.frames)}**"
                 + (" (rendered to PNG)" if rendered else " (SVG only — render skipped)"))
    lines.append("")
    cur = None
    for c in s.checks:
        if c.scenario != cur:
            cur = c.scenario
            lines.append(f"## {cur}")
        mark = "x" if c.passed else " "
        extra = f" — {c.detail}" if c.detail else ""
        lines.append(f"- [{mark}] `{c.id}` ({c.kind}) {c.description}{extra}")
    lines.append("")
    lines.append("## Frames")
    for f in s.frames:
        lines.append(f"- `{f.name}.png` — [{f.scenario}] {f.title}")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="qa-artifacts/tui", help="output directory")
    ap.add_argument("--no-render", action="store_true",
                    help="skip PNG rendering (drive + assert + dump SVGs only)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Driving the Krayne TUI; artifacts -> {out_dir}")
    session = asyncio.run(drive())

    rendered = False
    if args.no_render:
        # Dump raw SVGs so the run is still inspectable without a browser.
        for fr in session.frames:
            (out_dir / f"{fr.name}.svg").write_text(fr.svg)
        print("\n(--no-render) wrote SVGs instead of PNGs")
    else:
        print("\nRendering SVG frames to PNG...")
        rendered = _render_pngs(session.frames, out_dir)

    result = _write_outputs(session, out_dir, rendered)
    sm = result["summary"]
    print(f"\nFlow checks: {sm['passed']}/{sm['total_checks']} passed, "
          f"{sm['failed']} failed.  Frames: {len(session.frames)}.")
    print(f"Wrote {out_dir / 'flow-results.json'} and {out_dir / 'summary.md'}")
    # Sentinel the QA agent / CI greps for to confirm the harness completed.
    print("QA_TUI_CAPTURE_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
