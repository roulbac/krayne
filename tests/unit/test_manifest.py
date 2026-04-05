from __future__ import annotations

from krayne.config import AutoscalerConfig, ClusterConfig, HeadNodeConfig, WorkerGroupConfig
from krayne.config.models import ServicesConfig
from krayne.kube.manifest import RAY_IMAGE, build_manifest


class TestBuildManifest:
    def test_minimal_manifest(self):
        cfg = ClusterConfig(name="basic")
        m = build_manifest(cfg)

        assert m["apiVersion"] == "ray.io/v1"
        assert m["kind"] == "RayCluster"
        assert m["metadata"]["name"] == "basic"
        assert m["metadata"]["namespace"] == "default"
        assert m["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "krayne"

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
        assert workers[0]["replicas"] == 0
        assert workers[0]["minReplicas"] == 0
        assert workers[0]["maxReplicas"] == 1

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
        """Default services (notebook + ssh + code-server) appear on headService ports."""
        cfg = ClusterConfig(name="svcports")
        m = build_manifest(cfg)
        svc_ports = m["spec"]["headGroupSpec"]["headService"]["spec"]["ports"]
        port_names = {p["name"] for p in svc_ports}
        assert "notebook" in port_names
        assert "ssh" in port_names
        assert "code-server" in port_names
        assert "gcs-server" not in port_names  # Ray adds these itself

    def test_head_service_no_extra_ports(self):
        """All services disabled: headService has no extra ports."""
        cfg = ClusterConfig(
            name="bare",
            services=ServicesConfig(notebook=False, code_server=False, ssh=False),
        )
        m = build_manifest(cfg)
        svc_spec = m["spec"]["headGroupSpec"]["headService"]["spec"]
        assert "ports" not in svc_spec
        assert svc_spec["type"] == "ClusterIP"

    def test_head_service_all_services(self):
        """All services enabled: notebook, ssh, code-server on headService."""
        cfg = ClusterConfig(
            name="all",
            services=ServicesConfig(notebook=True, code_server=True, ssh=True),
        )
        m = build_manifest(cfg)
        svc_ports = m["spec"]["headGroupSpec"]["headService"]["spec"]["ports"]
        port_names = {p["name"] for p in svc_ports}
        assert port_names == {"notebook", "ssh", "code-server"}

    def test_no_init_containers(self):
        """No init containers regardless of services config."""
        cfg = ClusterConfig(name="noinit")
        m = build_manifest(cfg)
        pod_spec = m["spec"]["headGroupSpec"]["template"]["spec"]
        assert "initContainers" not in pod_spec

    def test_no_volumes(self):
        """No shared volumes regardless of services config."""
        cfg = ClusterConfig(name="novol")
        m = build_manifest(cfg)
        pod_spec = m["spec"]["headGroupSpec"]["template"]["spec"]
        assert "volumes" not in pod_spec

    def test_lifecycle_hook_default_services(self):
        """Default services produce a postStart hook that installs + starts services."""
        cfg = ClusterConfig(name="hooks")
        m = build_manifest(cfg)
        hook = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["lifecycle"]["postStart"]["exec"]["command"]
        assert hook[0] == "/bin/sh"
        assert hook[1] == "-c"
        assert "jupyter notebook" in hook[2]
        assert "code-server" in hook[2]
        assert "sshd" in hook[2]

    def test_lifecycle_hook_installs_notebook(self):
        """Notebook service installs via pip in postStart hook."""
        cfg = ClusterConfig(name="nb", services=ServicesConfig(notebook=True, code_server=False, ssh=False))
        m = build_manifest(cfg)
        hook_cmd = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "pip install" in hook_cmd
        assert "jupyter notebook" in hook_cmd

    def test_lifecycle_hook_installs_code_server_standalone(self):
        """Code-server installed via standalone tarball in postStart hook."""
        cfg = ClusterConfig(name="cs", services=ServicesConfig(notebook=False, code_server=True, ssh=False))
        m = build_manifest(cfg)
        hook_cmd = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "wget -qO-" in hook_cmd
        assert "code-server" in hook_cmd
        assert "tar -xz" in hook_cmd
        assert "8443" in hook_cmd

    def test_lifecycle_hook_notebook_only(self):
        cfg = ClusterConfig(
            name="nb",
            services=ServicesConfig(notebook=True, code_server=False, ssh=False),
        )
        m = build_manifest(cfg)
        hook_cmd = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "jupyter notebook" in hook_cmd
        assert "sshd" not in hook_cmd
        assert "code-server" not in hook_cmd

    def test_lifecycle_hook_ssh_only(self):
        cfg = ClusterConfig(
            name="sshonly",
            services=ServicesConfig(notebook=False, code_server=False, ssh=True),
        )
        m = build_manifest(cfg)
        hook_cmd = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]["lifecycle"]["postStart"]["exec"]["command"][2]
        assert "sshd" in hook_cmd
        assert "jupyter" not in hook_cmd

    def test_no_lifecycle_hook_when_no_services(self):
        cfg = ClusterConfig(
            name="bare",
            services=ServicesConfig(notebook=False, code_server=False, ssh=False),
        )
        m = build_manifest(cfg)
        container = m["spec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
        assert "lifecycle" not in container


class TestAutoscalingManifest:
    def test_default_enables_autoscaling(self):
        cfg = ClusterConfig(name="auto")
        m = build_manifest(cfg)
        assert m["spec"]["enableInTreeAutoscaling"] is True
        assert "autoscalerOptions" in m["spec"]

    def test_autoscaler_options_defaults(self):
        cfg = ClusterConfig(name="auto")
        m = build_manifest(cfg)
        opts = m["spec"]["autoscalerOptions"]
        assert opts["upscalingMode"] == "Default"
        assert opts["idleTimeoutSeconds"] == 60
        assert opts["resources"]["requests"]["cpu"] == "500m"
        assert opts["resources"]["requests"]["memory"] == "512Mi"
        assert opts["resources"]["limits"]["cpu"] == "500m"
        assert opts["resources"]["limits"]["memory"] == "512Mi"

    def test_custom_autoscaler_options(self):
        cfg = ClusterConfig(
            name="auto",
            autoscaler=AutoscalerConfig(
                idle_timeout_seconds=120,
                upscaling_mode="Aggressive",
                cpu="1",
                memory="1Gi",
            ),
        )
        m = build_manifest(cfg)
        opts = m["spec"]["autoscalerOptions"]
        assert opts["upscalingMode"] == "Aggressive"
        assert opts["idleTimeoutSeconds"] == 120
        assert opts["resources"]["requests"]["cpu"] == "1"
        assert opts["resources"]["requests"]["memory"] == "1Gi"

    def test_autoscaling_disabled_omits_keys(self):
        cfg = ClusterConfig(
            name="static",
            autoscaler=AutoscalerConfig(enabled=False),
        )
        m = build_manifest(cfg)
        assert "enableInTreeAutoscaling" not in m["spec"]
        assert "autoscalerOptions" not in m["spec"]

    def test_worker_min_max_replicas(self):
        cfg = ClusterConfig(
            name="auto",
            worker_groups=[
                WorkerGroupConfig(
                    name="scalable",
                    replicas=2,
                    min_replicas=0,
                    max_replicas=10,
                )
            ],
        )
        m = build_manifest(cfg)
        wg = m["spec"]["workerGroupSpecs"][0]
        assert wg["replicas"] == 2
        assert wg["minReplicas"] == 0
        assert wg["maxReplicas"] == 10
