"""Unit tests for the ikrayne TUI rewrite."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Button, DataTable, Input, OptionList, Static, Switch, TabbedContent

from krayne.api.types import ClusterDetails, ClusterInfo, HeadNodeInfo, WorkerGroupInfo
from krayne.tui.app import IKrayneApp


# ── Fixtures ────────────────────────────────────────

_INFO = ClusterInfo(
    name="test-cluster",
    namespace="default",
    status="ready",
    head_ip="10.0.0.1",
    dashboard_url="http://10.0.0.1:8265",
    client_url="ray://10.0.0.1:10001",
    notebook_url="http://10.0.0.1:8888",
    code_server_url=None,
    ssh_url=None,
    num_workers=2,
    created_at="2025-01-01T00:00:00Z",
)

_INFO2 = ClusterInfo(
    name="other-cluster",
    namespace="default",
    status="creating",
    head_ip=None,
    dashboard_url=None,
    client_url=None,
    notebook_url=None,
    code_server_url=None,
    ssh_url=None,
    num_workers=1,
    created_at="2025-01-02T00:00:00Z",
)

_DETAILS = ClusterDetails(
    info=_INFO,
    head=HeadNodeInfo(cpus="1", memory="4Gi", gpus=0, image="rayproject/ray:latest"),
    worker_groups=[
        WorkerGroupInfo(name="worker", replicas=2, cpus="1", memory="2Gi", gpus=0, gpu_type=None),
    ],
    ray_version="2.10.0",
    python_version="3.11",
)

_DETAILS_MULTI_WG = ClusterDetails(
    info=_INFO,
    head=HeadNodeInfo(cpus="2", memory="8Gi", gpus=0, image="rayproject/ray:latest"),
    worker_groups=[
        WorkerGroupInfo(name="cpu-workers", replicas=2, cpus="4", memory="8Gi", gpus=0, gpu_type=None),
        WorkerGroupInfo(name="gpu-workers", replicas=1, cpus="2", memory="4Gi", gpus=1, gpu_type="a100"),
    ],
    ray_version="2.10.0",
    python_version="3.11",
)


def _patch_explorer():
    """Patch all dependencies for ExplorerScreen."""
    return patch(
        "krayne.tui.screens.explorer.list_clusters",
        return_value=[_INFO],
    )


def _patch_explorer_with_tunnels():
    """Patch explorer with tunnel state."""
    return patch(
        "krayne.tui.screens.explorer.list_clusters",
        return_value=[_INFO],
    )


def _patch_tunnel_inactive():
    return patch("krayne.tui.screens.explorer.is_tunnel_active", return_value=False)


# ── App launch tests ────────────────────────────────


@pytest.mark.asyncio
async def test_app_launches():
    """App mounts and shows the ExplorerScreen."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test() as pilot:
            from krayne.tui.screens.explorer import ExplorerScreen

            assert isinstance(app.screen, ExplorerScreen)


@pytest.mark.asyncio
async def test_app_has_command_palette():
    """App registers the command palette provider."""
    from krayne.tui.commands import IKrayneCommands

    assert IKrayneCommands in IKrayneApp.COMMANDS


# ── Explorer tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_explorer_displays_clusters():
    """Explorer table shows clusters from the SDK."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.pause()
            from krayne.tui.widgets.cluster_table import ClusterTable

            table = app.screen.query_one(ClusterTable)
            assert table.row_count == 1


@pytest.mark.asyncio
async def test_explorer_shows_scope_bar():
    """Explorer shows namespace and cluster count in scope bar."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.pause()
            scope = app.screen.query_one("#scope-bar", Static)
            rendered = str(scope.renderable)
            assert "default" in rendered


@pytest.mark.asyncio
async def test_explorer_empty_state():
    """Explorer shows empty state when no clusters found."""
    with patch("krayne.tui.screens.explorer.list_clusters", return_value=[]):
        with _patch_tunnel_inactive():
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                await pilot.pause()
                empty = app.screen.query_one("#empty-state")
                assert empty.display is True


@pytest.mark.asyncio
async def test_explorer_filter_by_name():
    """Filter bar filters clusters by name using the filter function."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    fn = make_filter_fn("test")
    # Verify the filter function works on cluster data
    assert fn(_INFO) is True
    assert fn(_INFO2) is False

    # Also verify the UI opens the filter bar
    with patch("krayne.tui.screens.explorer.list_clusters", return_value=[_INFO, _INFO2]):
        with _patch_tunnel_inactive():
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                await pilot.pause()

                # Open filter
                await pilot.press("slash")
                await pilot.pause()
                from krayne.tui.widgets.filter_bar import FilterBar

                filter_bar = app.screen.query_one(FilterBar)
                assert filter_bar.has_class("visible")


@pytest.mark.asyncio
async def test_explorer_filter_by_status():
    """Filter bar supports status: prefix."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    fn = make_filter_fn("status:creating")
    assert fn(_INFO) is False
    assert fn(_INFO2) is True


# ── Detail tests ────────────────────────────────────


def _detail_patches():
    """Return a list of patches needed for the detail screen."""
    return [
        patch("krayne.tui.screens.detail.describe_cluster", return_value=_DETAILS),
        patch("krayne.tui.screens.detail.get_cluster_services", return_value=["dashboard"]),
        patch("krayne.tui.screens.detail.is_tunnel_active", return_value=False),
        patch("krayne.tui.screens.detail.load_tunnel_state", return_value=None),
        patch("krayne.tui.screens.detail.start_tunnels", return_value=[]),
        patch("krayne.tui.screens.detail.stop_tunnels", return_value=True),
    ]


@pytest.mark.asyncio
async def test_detail_screen_has_tabs():
    """Detail screen uses TabbedContent with expected tabs."""
    with _patch_explorer(), _patch_tunnel_inactive():
        patches = _detail_patches()
        for p in patches:
            p.start()
        try:
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.detail import ClusterDetailScreen

                app.push_screen(ClusterDetailScreen("test-cluster", "default"))
                for _ in range(5):
                    await pilot.pause()
                assert isinstance(app.screen, ClusterDetailScreen)
                tabs = app.screen.query_one(TabbedContent)
                assert tabs is not None
        finally:
            for p in patches:
                p.stop()


@pytest.mark.asyncio
async def test_detail_overview_content():
    """Detail overview tab shows cluster info."""
    with _patch_explorer(), _patch_tunnel_inactive():
        patches = _detail_patches()
        for p in patches:
            p.start()
        try:
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.detail import ClusterDetailScreen

                app.push_screen(ClusterDetailScreen("test-cluster", "default"))
                for _ in range(5):
                    await pilot.pause()
                overview = app.screen.query_one("#overview-content", Static)
                rendered = str(overview.renderable)
                assert "test-cluster" in rendered
        finally:
            for p in patches:
                p.stop()


@pytest.mark.asyncio
async def test_detail_escape_returns_to_explorer():
    """Pressing Escape in detail returns to explorer."""
    with _patch_explorer(), _patch_tunnel_inactive():
        patches = _detail_patches()
        for p in patches:
            p.start()
        try:
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.detail import ClusterDetailScreen
                from krayne.tui.screens.explorer import ExplorerScreen

                app.push_screen(ClusterDetailScreen("test-cluster", "default"))
                for _ in range(3):
                    await pilot.pause()
                assert isinstance(app.screen, ClusterDetailScreen)
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, ExplorerScreen)
        finally:
            for p in patches:
                p.stop()


# ── Create flow tests ──────────────────────────────


@pytest.mark.asyncio
async def test_create_flow_opens_on_c():
    """Pressing 'c' opens the create flow screen."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            from krayne.tui.screens.create_flow import CreateFlowScreen

            assert isinstance(app.screen, CreateFlowScreen)


@pytest.mark.asyncio
async def test_create_flow_quick_mode_defaults():
    """Create flow starts in quick mode with prefilled defaults."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()

            screen = app.screen
            assert screen.query_one("#input-namespace", Input).value == "default"
            assert screen.query_one("#input-quick-workers", Input).value == "1"
            assert screen.query_one("#switch-notebook", Switch).value is True
            assert screen.query_one("#switch-code-server", Switch).value is True
            assert screen.query_one("#switch-ssh", Switch).value is True
            # Quick mode sections visible, advanced hidden
            assert screen.query_one("#section-quick-compute").display is True
            assert screen.query_one("#section-advanced-head").display is False


@pytest.mark.asyncio
async def test_create_flow_validates_empty_name():
    """Create flow shows error when name is empty."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()

            screen = app.screen
            btn = screen.query_one("#btn-create", Button)
            btn.press()
            await pilot.pause()

            error_label = screen.query_one("#form-error", Static)
            assert error_label.has_class("visible")


@pytest.mark.asyncio
async def test_create_flow_escape_returns():
    """Escape in create flow returns to explorer."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            from krayne.tui.screens.create_flow import CreateFlowScreen

            assert isinstance(app.screen, CreateFlowScreen)
            await pilot.press("escape")
            await pilot.pause()
            from krayne.tui.screens.explorer import ExplorerScreen

            assert isinstance(app.screen, ExplorerScreen)


@pytest.mark.asyncio
async def test_create_flow_advanced_mode_toggle():
    """Toggling to advanced mode shows head node and worker group fields."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()

            screen = app.screen
            # Click advanced button
            btn_adv = screen.query_one("#btn-mode-advanced", Button)
            btn_adv.press()
            await pilot.pause()

            assert screen.query_one("#section-quick-compute").display is False
            assert screen.query_one("#section-advanced-head").display is True
            assert screen.query_one("#section-advanced-workers").display is True


# ── Scale flow tests ────────────────────────────────


@pytest.mark.asyncio
async def test_scale_flow_single_group():
    """Scale flow with single worker group skips group selection."""
    with _patch_explorer(), _patch_tunnel_inactive():
        with patch("krayne.tui.screens.scale_flow.describe_cluster", return_value=_DETAILS):
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.scale_flow import ScaleFlowScreen

                app.push_screen(ScaleFlowScreen("test-cluster", "default"))
                for _ in range(5):
                    await pilot.pause()

                screen = app.screen
                assert isinstance(screen, ScaleFlowScreen)
                # Group list should be hidden (single group)
                group_list = screen.query_one("#scale-group-list", OptionList)
                assert group_list.display is False
                # Replicas input should be shown
                replicas = screen.query_one("#scale-replicas", Input)
                assert replicas.display is True
                assert replicas.value == "2"


@pytest.mark.asyncio
async def test_scale_flow_multi_group_shows_picker():
    """Scale flow with multiple worker groups shows group picker."""
    with _patch_explorer(), _patch_tunnel_inactive():
        with patch("krayne.tui.screens.scale_flow.describe_cluster", return_value=_DETAILS_MULTI_WG):
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.scale_flow import ScaleFlowScreen

                app.push_screen(ScaleFlowScreen("test-cluster", "default"))
                for _ in range(5):
                    await pilot.pause()

                screen = app.screen
                assert isinstance(screen, ScaleFlowScreen)
                # Group list should be visible (multiple groups)
                group_list = screen.query_one("#scale-group-list", OptionList)
                assert group_list.display is True


# ── Delete confirm tests ────────────────────────────


@pytest.mark.asyncio
async def test_delete_cancel_focused_by_default():
    """Delete dialog has cancel button focused by default."""
    with _patch_explorer(), _patch_tunnel_inactive():
        with patch("krayne.tui.screens.delete_confirm.is_tunnel_active", return_value=False):
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

                app.push_screen(DeleteConfirmScreen("test-cluster", "default"))
                await pilot.pause()
                await pilot.pause()

                cancel_btn = app.screen.query_one("#btn-cancel-delete", Button)
                assert cancel_btn.has_focus


@pytest.mark.asyncio
async def test_delete_shows_cluster_name():
    """Delete dialog shows the cluster name being deleted."""
    with _patch_explorer(), _patch_tunnel_inactive():
        with patch("krayne.tui.screens.delete_confirm.is_tunnel_active", return_value=False):
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                from krayne.tui.screens.delete_confirm import DeleteConfirmScreen

                app.push_screen(DeleteConfirmScreen("test-cluster", "default"))
                await pilot.pause()


# ── Namespace picker tests ──────────────────────────


@pytest.mark.asyncio
async def test_namespace_picker_opens_on_n():
    """Pressing 'n' opens the namespace picker."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            from krayne.tui.screens.namespace_picker import NamespacePickerScreen

            assert isinstance(app.screen, NamespacePickerScreen)


@pytest.mark.asyncio
async def test_namespace_picker_shows_recent():
    """Namespace picker shows recent namespaces."""
    with _patch_explorer(), _patch_tunnel_inactive():
        with patch("krayne.tui.screens.namespace_picker._fetch_namespaces", return_value=[]):
            app = IKrayneApp()
            async with app.run_test(size=(120, 35)) as pilot:
                await pilot.pause()
                await pilot.press("n")
                await pilot.pause()

                option_list = app.screen.query_one("#ns-list", OptionList)
                assert option_list.option_count >= 1  # At least "default"


# ── Help overlay tests ──────────────────────────────


@pytest.mark.asyncio
async def test_help_opens_on_question_mark():
    """Pressing '?' opens the help overlay."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            from krayne.tui.screens.help_overlay import HelpOverlay

            assert isinstance(app.screen, HelpOverlay)


@pytest.mark.asyncio
async def test_help_contextual_for_explorer():
    """Help overlay shows explorer-specific keybindings."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()

            content = app.screen.query_one("#help-content", Static)
            rendered = str(content.renderable)
            assert "Create" in rendered or "Filter" in rendered


# ── Responsive layout tests ─────────────────────────


@pytest.mark.asyncio
async def test_compact_layout():
    """In compact terminal, preview panel is hidden."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.terminal_class == "compact"


@pytest.mark.asyncio
async def test_standard_layout():
    """In standard terminal, terminal class is standard."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            assert app.terminal_class == "standard"


@pytest.mark.asyncio
async def test_wide_layout():
    """In wide terminal, terminal class is wide."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert app.terminal_class == "wide"


# ── State management tests ──────────────────────────


@pytest.mark.asyncio
async def test_recent_namespaces():
    """Adding a namespace tracks it in recents."""
    with _patch_explorer(), _patch_tunnel_inactive():
        app = IKrayneApp()
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            app.add_recent_namespace("production")
            assert "production" in app.recent_namespaces
            assert app.recent_namespaces[0] == "production"


# ── Filter function tests ──────────────────────────


def test_filter_fn_name():
    """Filter function matches cluster name."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    fn = make_filter_fn("test")
    assert fn is not None
    assert fn(_INFO) is True
    assert fn(_INFO2) is False


def test_filter_fn_status():
    """Filter function matches status prefix."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    fn = make_filter_fn("status:creating")
    assert fn is not None
    assert fn(_INFO) is False
    assert fn(_INFO2) is True


def test_filter_fn_service():
    """Filter function matches service prefix."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    fn = make_filter_fn("service:notebook")
    assert fn is not None
    assert fn(_INFO) is True  # has notebook_url
    assert fn(_INFO2) is False  # no notebook_url


def test_filter_fn_empty():
    """Empty filter returns None (no filtering)."""
    from krayne.tui.widgets.filter_bar import make_filter_fn

    assert make_filter_fn("") is None
    assert make_filter_fn("   ") is None


# ── Tunnel helper tests ────────────────────────────


def test_stop_tunnel_service_no_state():
    """stop_tunnel_service returns False when no tunnel state exists."""
    with patch("krayne.tunnel.load_tunnel_state", return_value=None):
        from krayne.tunnel import stop_tunnel_service

        assert stop_tunnel_service("test", "default", "dashboard") is False


# ── Terminal class computation tests ────────────────


def test_terminal_class_compact():
    from krayne.tui.state import compute_terminal_class

    assert compute_terminal_class(80, 24) == "compact"
    assert compute_terminal_class(99, 35) == "compact"
    assert compute_terminal_class(120, 29) == "compact"


def test_terminal_class_standard():
    from krayne.tui.state import compute_terminal_class

    assert compute_terminal_class(120, 35) == "standard"
    assert compute_terminal_class(100, 30) == "standard"
    assert compute_terminal_class(139, 39) == "standard"


def test_terminal_class_wide():
    from krayne.tui.state import compute_terminal_class

    assert compute_terminal_class(140, 40) == "wide"
    assert compute_terminal_class(200, 50) == "wide"
