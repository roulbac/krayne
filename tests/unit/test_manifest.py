from __future__ import annotations

from prism.config import ClusterConfig, HeadNodeConfig, WorkerGroupConfig
from prism.config.models import ServicesConfig
from prism.kube.manifest import RAY_IMAGE, build_manifest


class TestBuildManifest:
    def test_minimal_manifest(self):
        cfg = ClusterConfig(name="basic")
        m = build_manifest(cfg)

        assert m["apiVersion"] == "ray.io/v1"
        assert m["kind"] == "RayCluster"
        assert m["metadata"]["name"] == "basic"
        assert m["metadata"]["namespace"] == "default"
        assert m["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "prism"

    def test_head_resources(self):
        cfg = ClusterConfig(name="h", head=HeadNodeConfig(cpus=8, memory="32Gi"))
        m = build_manifest(cfg)
        head = m["spec"]["headGroupSpec"]
        container = head["template"]["spec"]["containers"][0]
        assert container["resources"]["requests"]["cpu"] == "8"
        assert container["resources"]["requests"]["memory"] == "32Gi"
        assert container["image"] == RAY_IMAGE

    def test_head_custom_image(self):
        cfg = ClusterConfig(name="h", head=HeadNodeConfig(image="my/image:v1"))
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "my/image:v1"

    def test_head_with_gpus(self):
        cfg = ClusterConfig(name="h", head=HeadNodeConfig(gpus=2))
        m = build_manifest(cfg)
        res = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["resources"]
        assert res["limits"]["nvidia.com/gpu"] == 2

    def test_worker_group_defaults(self):
        cfg = ClusterConfig(name="w")
        m = build_manifest(cfg)
        workers = m["spec"]["workerGroupSpecs"]
        assert len(workers) == 1
        assert workers[0]["groupName"] == "worker"
        assert workers[0]["replicas"] == 1

    def test_gpu_worker_has_node_selector(self):
        cfg = ClusterConfig(
            name="gpu",
            worker_groups=[WorkerGroupConfig(gpus=1, gpu_type="a100")],
        )
        m = build_manifest(cfg)
        wg = m["spec"]["workerGroupSpecs"][0]
        ns = wg["template"]["spec"]["nodeSelector"]
        assert ns["cloud.google.com/gke-accelerator"] == "a100"
        res = wg["template"]["spec"]["containers"][0]["resources"]
        assert res["limits"]["nvidia.com/gpu"] == 1

    def test_cpu_worker_no_node_selector(self):
        cfg = ClusterConfig(name="cpu")
        m = build_manifest(cfg)
        wg = m["spec"]["workerGroupSpecs"][0]
        assert "nodeSelector" not in wg["template"]["spec"]

    def test_multiple_worker_groups(self):
        cfg = ClusterConfig(
            name="multi",
            worker_groups=[
                WorkerGroupConfig(name="cpu", replicas=3),
                WorkerGroupConfig(name="gpu", replicas=2, gpus=4, gpu_type="v100"),
            ],
        )
        m = build_manifest(cfg)
        workers = m["spec"]["workerGroupSpecs"]
        assert len(workers) == 2
        assert workers[0]["groupName"] == "cpu"
        assert workers[1]["groupName"] == "gpu"
        assert workers[1]["replicas"] == 2

    def test_head_container_ports(self):
        """Ray-head container only has the 3 base Ray ports."""
        cfg = ClusterConfig(name="ports")
        m = build_manifest(cfg)
        ports = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["ports"]
        port_names = {p["name"] for p in ports}
        assert port_names == {"gcs-server", "dashboard", "client"}

    def test_head_service_extra_ports_default(self):
        """Default services (notebook + ssh) appear on headService ports."""
        cfg = ClusterConfig(name="svcports")
        m = build_manifest(cfg)
        svc_ports = m["spec"]["headGroupSpec"]["headService"]["spec"]["ports"]
        port_names = {p["name"] for p in svc_ports}
        assert "notebook" in port_names
        assert "ssh" in port_names
        assert "gcs-server" not in port_names  # Ray adds these itself

    def test_head_service_no_extra_ports(self):
        """All services disabled: headService has no extra ports."""
        cfg = ClusterConfig(
            name="bare",
            services=ServicesConfig(notebook=False, vscode_server=False, ssh=False),
        )
        m = build_manifest(cfg)
        svc_spec = m["spec"]["headGroupSpec"]["headService"]["spec"]
        assert "ports" not in svc_spec
        assert svc_spec["type"] == "ClusterIP"

    def test_head_service_all_services(self):
        """All services enabled: notebook, ssh, vscode on headService."""
        cfg = ClusterConfig(
            name="all",
            services=ServicesConfig(notebook=True, vscode_server=True, ssh=True),
        )
        m = build_manifest(cfg)
        svc_ports = m["spec"]["headGroupSpec"]["headService"]["spec"]["ports"]
        port_names = {p["name"] for p in svc_ports}
        assert port_names == {"notebook", "ssh", "vscode"}

    def test_vscode_in_lifecycle_hook(self):
        """When vscode_server is enabled, code-server is started via lifecycle hook."""
        cfg = ClusterConfig(
            name="vs",
            services=ServicesConfig(vscode_server=True),
        )
        m = build_manifest(cfg)
        containers = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"]
        assert len(containers) == 1  # no sidecar
        hook_cmd = containers[0]["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "code-server" in hook_cmd
        assert "8443" in hook_cmd

    def test_no_vscode_in_hook_when_disabled(self):
        """When vscode_server is disabled (default), no code-server in hook."""
        cfg = ClusterConfig(name="novs")
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        hook_cmd = container["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "code-server" not in hook_cmd

    def test_lifecycle_hook_default_services(self):
        """Default services (notebook + ssh) produce a postStart lifecycle hook."""
        cfg = ClusterConfig(name="hooks")
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        hook = container["lifecycle"]["postStart"]["exec"]["command"]
        assert hook[0] == "/bin/sh"
        assert hook[1] == "-c"
        # Both notebook and ssh startup commands should be present
        assert "uv pip install --system notebook" in hook[2]
        assert "jupyter notebook" in hook[2]
        assert "sshd" in hook[2]

    def test_lifecycle_hook_notebook_only(self):
        cfg = ClusterConfig(
            name="nb",
            services=ServicesConfig(notebook=True, ssh=False),
        )
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        hook_cmd = container["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "uv pip install --system notebook" in hook_cmd
        assert "jupyter notebook" in hook_cmd
        assert "sshd" not in hook_cmd

    def test_lifecycle_hook_ssh_only(self):
        cfg = ClusterConfig(
            name="sshonly",
            services=ServicesConfig(notebook=False, ssh=True),
        )
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        hook_cmd = container["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "sshd" in hook_cmd
        assert "jupyter" not in hook_cmd

    def test_no_lifecycle_hook_when_no_services(self):
        cfg = ClusterConfig(
            name="bare",
            services=ServicesConfig(notebook=False, vscode_server=False, ssh=False),
        )
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        assert "lifecycle" not in container
