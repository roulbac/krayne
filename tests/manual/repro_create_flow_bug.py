"""Headless repro for the post-create-flow input-leak bug.

Drives the TUI through:
  1. Open Explorer
  2. Press 'c' to open the Create flow
  3. Type a cluster name
  4. Press Ctrl+S to submit
  5. Wait for the create_cluster worker to succeed and pop the screen
  6. Press arrow keys on the Explorer
  7. Inspect focus, widget tree, and screen content for stray escape-sequence-like text

Run with: uv run python tests/manual/repro_create_flow_bug.py
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch


SAMPLE_OBJ = {
    "metadata": {
        "name": "test",
        "namespace": "default",
        "creationTimestamp": "2026-05-06T00:00:00Z",
    },
    "status": {"state": "ready", "head": {"podIP": "10.0.0.1"}},
    "spec": {
        "enableInTreeAutoscaling": True,
        "headGroupSpec": {
            "rayStartParams": {"dashboard-host": "0.0.0.0", "num-cpus": "0"},
            "template": {
                "spec": {
                    "containers": [{
                        "name": "ray-head",
                        "image": "rayproject/ray:2.55.1-py313",
                        "resources": {"requests": {"cpu": "1", "memory": "4Gi"}, "limits": {"cpu": "1", "memory": "4Gi"}},
                        "ports": [
                            {"containerPort": 6379, "name": "gcs-server"},
                            {"containerPort": 8265, "name": "dashboard"},
                            {"containerPort": 10001, "name": "client"},
                        ],
                    }],
                }
            },
        },
        "workerGroupSpecs": [{
            "groupName": "worker", "replicas": 0, "minReplicas": 0, "maxReplicas": 1,
            "rayStartParams": {},
            "template": {"spec": {"containers": [{
                "name": "ray-worker", "image": "rayproject/ray:2.55.1-py313",
                "resources": {"requests": {"cpu": "1", "memory": "2Gi"}, "limits": {"cpu": "1", "memory": "2Gi"}},
            }]}},
        }],
    },
}


def make_fake_client() -> MagicMock:
    client = MagicMock()
    client.create_ray_cluster.return_value = SAMPLE_OBJ
    client.get_ray_cluster.return_value = SAMPLE_OBJ
    client.list_ray_clusters.return_value = [SAMPLE_OBJ]
    client.list_pods.return_value = []
    client.get_head_node_port.return_value = None
    client.delete_ray_cluster.return_value = None
    return client


def dump_widget_tree(widget, indent=0) -> list[str]:
    lines = []
    cls = type(widget).__name__
    info = f"{'  ' * indent}{cls}"
    if getattr(widget, "id", None):
        info += f" #{widget.id}"
    if getattr(widget, "classes", None):
        info += f" .{'.'.join(widget.classes)}"
    if cls in ("Input", "Select", "Switch"):
        try:
            info += f"  value={getattr(widget, 'value', '?')!r}"
        except Exception:
            pass
    info += f"  display={widget.display}"
    lines.append(info)
    for child in getattr(widget, "children", []):
        lines.extend(dump_widget_tree(child, indent + 1))
    return lines


async def main() -> int:
    fake = make_fake_client()
    with patch("krayne.api.clusters.get_kube_client", return_value=fake), \
         patch("krayne.kube.client.get_kube_client", return_value=fake), \
         patch("krayne.tunnel.is_tunnel_active", return_value=False), \
         patch("krayne.tunnel.start_tunnels", return_value=[]), \
         patch("krayne.tunnel.stop_tunnels"):

        from krayne.tui.app import IKrayneApp

        app = IKrayneApp()
        async with app.run_test(size=(140, 40)) as pilot:
            print("=== STAGE 1: explorer mounted ===")
            await pilot.pause()
            print("focused:", app.focused)
            print("active screen:", type(app.screen).__name__)

            print("\n=== STAGE 2: open create flow ===")
            await pilot.press("c")
            await pilot.pause()
            print("active screen:", type(app.screen).__name__)
            print("focused:", app.focused)

            # Type a cluster name
            print("\n=== STAGE 3: type cluster name ===")
            from textual.widgets import Input
            name_input = app.screen.query_one("#input-name", Input)
            name_input.value = "repro-cluster"
            await pilot.pause()

            print("\n=== STAGE 4: submit (ctrl+s) ===")
            await pilot.press("ctrl+s")
            # Allow worker thread + pop_screen
            for _ in range(20):
                await pilot.pause()
                if type(app.screen).__name__ != "CreateClusterScreen":
                    break
            print("active screen after submit:", type(app.screen).__name__)
            print("focused:", app.focused)

            print("\n=== STAGE 5: press 'down' arrow keys ===")
            await pilot.press("down", "down", "down")
            await pilot.pause()
            print("focused after arrows:", app.focused)

            print("\n=== STAGE 6: full screen widget tree ===")
            for line in dump_widget_tree(app.screen):
                print(line)

            print("\n=== STAGE 7: full app widget tree (all screens) ===")
            for screen in app.screen_stack:
                print(f"--- screen {type(screen).__name__} ---")
                for line in dump_widget_tree(screen):
                    print(line)

            # Capture screenshot
            try:
                svg_path = "/tmp/krayne_after_create.svg"
                app.save_screenshot(svg_path)
                print(f"\nscreenshot saved: {svg_path}")
            except Exception as exc:
                print(f"screenshot failed: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
