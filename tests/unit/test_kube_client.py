from __future__ import annotations

from unittest.mock import MagicMock, patch

from krayne.kube.client import DefaultKubeClient


def _client_without_config() -> DefaultKubeClient:
    """Build a DefaultKubeClient without touching kubeconfig loading."""
    client = DefaultKubeClient.__new__(DefaultKubeClient)
    client._core = MagicMock()
    client._custom = MagicMock()
    return client


class TestPortforward:
    def test_ports_passed_as_comma_separated_string(self):
        """Regression: the k8s portforward helper does ``value.split(',')`` on
        the ports query param, so it must be a string — a list raises
        ``'list' object has no attribute 'split'`` at connection time."""
        client = _client_without_config()
        with patch("krayne.kube.client.k8s_portforward") as mock_pf:
            client.portforward("head-pod", "default", [8265])

        assert mock_pf.call_count == 1
        kwargs = mock_pf.call_args.kwargs
        assert kwargs["ports"] == "8265"
        assert isinstance(kwargs["ports"], str)

    def test_multiple_ports_joined(self):
        client = _client_without_config()
        with patch("krayne.kube.client.k8s_portforward") as mock_pf:
            client.portforward("head-pod", "default", [8265, 10001])
        assert mock_pf.call_args.kwargs["ports"] == "8265,10001"

    def test_uses_isolated_api_client(self):
        """The k8s portforward helper swaps ``api_client.request`` for a
        websocket shim during the handshake (restoring it in a finally). On the
        shared ``self._core`` client that swap races with concurrent list/watch
        calls, which then fail with ``Missing required parameter `ports```.
        Each portforward must therefore run on its own ApiClient — so the
        connect method handed to the helper must NOT be bound to ``self._core``.
        """
        client = _client_without_config()
        captured: dict = {}

        def fake_pf(api_method, pod, ns, *, ports):
            captured["api_client"] = api_method.__self__.api_client
            return MagicMock()

        with patch("krayne.kube.client.k8s_portforward", side_effect=fake_pf):
            client.portforward("head-pod", "default", [8265])

        assert captured["api_client"] is not client._core.api_client


class TestHeadPodName:
    def _pod(self, name, phase, ready):
        pod = MagicMock()
        pod.metadata.name = name
        pod.status.phase = phase
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "True" if ready else "False"
        pod.status.conditions = [cond]
        return pod

    def test_returns_running_ready_head(self):
        client = _client_without_config()
        resp = MagicMock()
        resp.items = [self._pod("head-1", "Running", True)]
        client._core.list_namespaced_pod.return_value = resp

        assert client.head_pod_name("c", "default") == "head-1"
        # Verify the selector targets the head node.
        kwargs = client._core.list_namespaced_pod.call_args.kwargs
        assert "ray.io/node-type=head" in kwargs["label_selector"]

    def test_skips_not_ready_head(self):
        client = _client_without_config()
        resp = MagicMock()
        resp.items = [self._pod("head-1", "Running", False)]
        client._core.list_namespaced_pod.return_value = resp
        assert client.head_pod_name("c", "default") is None

    def test_skips_pending_head(self):
        client = _client_without_config()
        resp = MagicMock()
        resp.items = [self._pod("head-1", "Pending", True)]
        client._core.list_namespaced_pod.return_value = resp
        assert client.head_pod_name("c", "default") is None

    def test_none_when_no_pods(self):
        client = _client_without_config()
        resp = MagicMock()
        resp.items = []
        client._core.list_namespaced_pod.return_value = resp
        assert client.head_pod_name("c", "default") is None
