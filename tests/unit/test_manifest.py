"""Unit tests for prism.kube.manifest (pure-function manifest builder)."""

from __future__ import annotations

from prism.config import ClusterConfig, HeadNodeConfig, WorkerGroupConfig
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
        assert container["resources"]["requests"]["cpu"] == 8
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

    def test_head_ports(self):
        cfg = ClusterConfig(name="ports")
        m = build_manifest(cfg)
        ports = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["ports"]
        port_names = {p["name"] for p in ports}
        assert {"gcs-server", "dashboard", "client"} == port_names
