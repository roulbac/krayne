"""Unit tests for the ikrayne TUI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import DataTable, Input, Static, Switch, Button

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

_DETAILS = ClusterDetails(
    info=_INFO,
    head=HeadNodeInfo(cpus="1", memory="4Gi", gpus=0, image="rayproject/ray:latest"),
    worker_groups=[
        WorkerGroupInfo(name="worker", replicas=2, cpus="1", memory="2Gi", gpus=0, gpu_type=None),
    ],
    ray_version="2.10.0",
    python_version="3.11",
)

# ── App tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_app_launches():
    """App mounts and shows the cluster list screen."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            # The default screen should be ClusterListScreen
            from krayne.tui.screens.cluster_list import ClusterListScreen

            assert isinstance(app.screen, ClusterListScreen)


@pytest.mark.asyncio
async def test_cluster_list_displays_clusters():
    """Cluster list table shows clusters returned by the SDK."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[_INFO]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            assert table.row_count == 1


@pytest.mark.asyncio
async def test_cluster_list_filter():
    """Filter bar filters clusters by name."""
    info2 = ClusterInfo(
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
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[_INFO, info2]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            assert table.row_count == 2

            # Activate filter and type
            await pilot.press("slash")
            filter_input = app.screen.query_one("#filter-input", Input)
            filter_input.value = "test"
            await pilot.pause()
            assert table.row_count == 1


@pytest.mark.asyncio
async def test_namespace_label():
    """Namespace label reflects the app namespace."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            label = app.screen.query_one("#namespace-label", Static)
            assert "default" in str(label.renderable)


@pytest.mark.asyncio
async def test_create_form_opens_on_c():
    """Pressing 'c' opens the create form screen."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            from krayne.tui.screens.create_form import CreateFormScreen

            assert isinstance(app.screen, CreateFormScreen)


@pytest.mark.asyncio
async def test_create_form_prefilled_defaults():
    """Create form has prefilled default values from ClusterConfig."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()

            screen = app.screen
            assert screen.query_one("#input-namespace", Input).value == "default"
            assert screen.query_one("#input-head-cpus", Input).value == "1"
            assert screen.query_one("#input-head-memory", Input).value == "4Gi"
            assert screen.query_one("#input-head-gpus", Input).value == "0"
            assert screen.query_one("#input-wg-name", Input).value == "worker"
            assert screen.query_one("#input-wg-replicas", Input).value == "1"
            assert screen.query_one("#input-wg-cpus", Input).value == "1"
            assert screen.query_one("#input-wg-memory", Input).value == "2Gi"
            assert screen.query_one("#input-wg-gpus", Input).value == "0"
            assert screen.query_one("#input-wg-gpu-type", Input).value == "t4"
            assert screen.query_one("#switch-notebook", Switch).value is True
            assert screen.query_one("#switch-code-server", Switch).value is True
            assert screen.query_one("#switch-ssh", Switch).value is True


@pytest.mark.asyncio
async def test_create_form_validates_empty_name():
    """Create form shows error when name is empty."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()

            # Try to submit without a name
            screen = app.screen
            btn = screen.query_one("#btn-create", Button)
            btn.press()
            await pilot.pause()

            error_label = screen.query_one("#form-error", Static)
            assert error_label.has_class("visible")


@pytest.mark.asyncio
async def test_create_form_escape_returns():
    """Escape in create form returns to cluster list."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            from krayne.tui.screens.create_form import CreateFormScreen

            assert isinstance(app.screen, CreateFormScreen)
            await pilot.press("escape")
            await pilot.pause()
            from krayne.tui.screens.cluster_list import ClusterListScreen

            assert isinstance(app.screen, ClusterListScreen)


@pytest.mark.asyncio
async def test_help_screen_opens():
    """Pressing '?' opens the help screen."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            from krayne.tui.screens.help import HelpScreen

            assert isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_detail_screen_opens_on_enter():
    """Pressing Enter on a selected cluster opens the detail screen."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[_INFO]):
        with patch(
            "krayne.tui.screens.cluster_detail.describe_cluster",
            return_value=_DETAILS,
        ):
            app = IKrayneApp()
            async with app.run_test() as pilot:
                # Wait for the table to populate
                for _ in range(5):
                    await pilot.pause()
                table = app.screen.query_one(DataTable)
                if table.row_count > 0:
                    # Focus the table and press Enter
                    table.focus()
                    await pilot.pause()
                    await pilot.press("enter")
                    for _ in range(5):
                        await pilot.pause()
                    from krayne.tui.screens.cluster_detail import ClusterDetailScreen

                    assert isinstance(app.screen, ClusterDetailScreen)


@pytest.mark.asyncio
async def test_namespace_switch():
    """Pressing 'n' opens namespace modal."""
    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=[]):
        app = IKrayneApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            from krayne.tui.screens.namespace_input import NamespaceInputScreen

            assert isinstance(app.screen, NamespaceInputScreen)
