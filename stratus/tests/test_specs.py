import pytest
import yaml
from pydantic import ValidationError

from stratus.specs.models import (
    ClusterSpec,
    JobSpec,
    Resources,
    ServiceSpec,
    SpecKind,
    VolumeSpec,
    parse_kv_lines,
    parse_mount_lines,
    spec_from_yaml,
)


def roundtrip(spec):
    return spec_from_yaml(spec.kind, spec.name, spec.to_yaml())


def test_cluster_yaml_is_plain_skypilot():
    spec = ClusterSpec(
        name="dev",
        resources=Resources(infra="k8s", accelerators="L4:1", cpus="4+", memory="16", ports="8888, 6006"),
        setup="pip install torch\npip install -e .",
        run="python train.py",
        envs={"WANDB_MODE": "offline"},
        volumes={"/data": "datasets"},
        autostop_minutes=30,
        autostop_down=True,
    )
    data = yaml.safe_load(spec.to_yaml())
    assert data["resources"] == {
        "infra": "k8s",
        "accelerators": "L4:1",
        "cpus": "4+",
        "memory": 16,
        "ports": [8888, 6006],
        "autostop": {"idle_minutes": 30, "down": True},
    }
    assert data["setup"] == "pip install torch\npip install -e .\n"
    assert data["volumes"] == {"/data": "datasets"}
    assert roundtrip(spec) == spec


def test_unknown_keys_survive_roundtrip():
    text = """
name: x
resources:
  infra: aws/us-east-1
  instance_type: p4d.24xlarge
  autostop: 10
file_mounts:
  /ckpt: s3://bucket
config:
  kubernetes:
    pod_config: {}
run: echo hi
"""
    spec = spec_from_yaml(SpecKind.CLUSTER, "x", text)
    assert spec.autostop_minutes == 10
    assert spec.resources.extra == {"instance_type": "p4d.24xlarge"}
    assert spec.extra["file_mounts"] == {"/ckpt": "s3://bucket"}
    again = yaml.safe_load(spec.to_yaml())
    assert again["resources"]["instance_type"] == "p4d.24xlarge"
    assert again["file_mounts"] == {"/ckpt": "s3://bucket"}
    assert "config" in again


def test_legacy_cloud_region_becomes_infra():
    spec = spec_from_yaml(SpecKind.CLUSTER, "c", "resources:\n  cloud: gcp\n  region: us-central1\n  accelerators: {A100: 8}\n")
    assert spec.resources.infra == "gcp/us-central1"
    assert spec.resources.accelerators == "A100:8"


@pytest.mark.parametrize("name", ["Dev", "1abc", "a_b", "", "x" * 64, "trailing-"])
def test_bad_names(name):
    with pytest.raises(ValidationError):
        ClusterSpec(name=name)


@pytest.mark.parametrize("field,value", [("cpus", "four"), ("memory", "8GB"), ("accelerators", "A100 x1"), ("ports", "http")])
def test_bad_resources(field, value):
    with pytest.raises(ValidationError):
        Resources(**{field: value})


def test_infra_auto_is_none():
    assert Resources(infra="auto").infra is None


def test_job_requires_run_and_recovery_roundtrip():
    with pytest.raises(ValidationError):
        JobSpec(name="j")
    spec = JobSpec(name="j", run="python x.py", recovery_strategy="eager_next_region", max_restarts_on_errors=3)
    data = yaml.safe_load(spec.to_yaml())
    assert data["resources"]["job_recovery"] == {"strategy": "EAGER_NEXT_REGION", "max_restarts_on_errors": 3}
    assert roundtrip(spec) == spec


def test_service_fixed_and_autoscaled():
    fixed = ServiceSpec(name="s", run="python -m http.server 8080", port=8080, min_replicas=2)
    data = yaml.safe_load(fixed.to_yaml())
    assert list(data)[:2] == ["name", "service"]
    assert data["service"]["replicas"] == 2
    # SkyServe needs the port as service.ports (LB target) *and* resources.ports (opened on replicas)
    assert data["service"]["ports"] == 8080
    assert data["resources"]["ports"] == [8080]
    extra = ServiceSpec(name="s", run="x", port=8080, resources=Resources(ports=["9090"]))
    assert yaml.safe_load(extra.to_yaml())["resources"]["ports"] == [8080, 9090]
    assert roundtrip(extra) == extra
    assert roundtrip(fixed) == fixed

    auto = fixed.model_copy(update={"max_replicas": 5, "target_qps_per_replica": 2.5})
    auto = ServiceSpec.model_validate(auto.model_dump())
    data = yaml.safe_load(auto.to_yaml())
    assert data["service"]["replica_policy"] == {"min_replicas": 2, "max_replicas": 5, "target_qps_per_replica": 2.5}
    assert roundtrip(auto) == auto


def test_service_legacy_resources_port_becomes_service_port():
    spec = spec_from_yaml(SpecKind.SERVICE, "s", "service:\n  readiness_probe: /health\n  replicas: 1\nresources:\n  ports: 9000\nrun: x\n")
    assert spec.port == 9000 and spec.resources.ports == [] and spec.readiness_path == "/health"


def test_service_validation():
    with pytest.raises(ValidationError, match="run"):
        ServiceSpec(name="s")
    with pytest.raises(ValidationError):
        ServiceSpec(name="s", run="x", port=70000)
    with pytest.raises(ValidationError, match="target_qps"):
        ServiceSpec(name="s", run="x", resources=Resources(ports=["80"]), max_replicas=3)


def test_volume_roundtrip():
    vol = VolumeSpec(name="data", size="100Gi", storage_class="local-path", access_mode="ReadWriteOnce", labels={"team": "ml"})
    data = yaml.safe_load(vol.to_yaml())
    assert data == {
        "name": "data", "type": "k8s-pvc", "infra": "k8s", "size": "100Gi",
        "config": {"storage_class_name": "local-path", "access_mode": "ReadWriteOnce"}, "labels": {"team": "ml"},
    }
    assert roundtrip(vol) == vol
    with pytest.raises(ValidationError):
        VolumeSpec(name="v", size="lots")


def test_kv_parsers():
    assert parse_kv_lines("A=1\n# c\n\nB = two=2") == {"A": "1", "B": "two=2"}
    with pytest.raises(ValueError):
        parse_kv_lines("novalue")
    assert parse_mount_lines("/data=vol") == {"/data": "vol"}
    with pytest.raises(ValueError):
        parse_mount_lines("data=vol")


def test_specs_load_into_skypilot_task():
    """The YAML stratus writes must be accepted by SkyPilot itself."""
    sky = pytest.importorskip("sky")
    spec = ClusterSpec(
        name="dev", resources=Resources(infra="k8s", cpus="1", memory="2", ports=["8888"]),
        setup="echo setup", run="echo run", envs={"A": "1"}, autostop_minutes=15,
    )
    task = sky.Task.from_yaml_config(spec.to_yaml_dict())
    assert task.run == "echo run"
    (res,) = task.resources
    assert str(res.cloud).lower().startswith("kubernetes")

    job = JobSpec(name="j", run="echo hi", recovery_strategy="FAILOVER", max_restarts_on_errors=2)
    sky.Task.from_yaml_config(job.to_yaml_dict())

    svc = ServiceSpec(name="svc", run="python3 -m http.server 8080", port=8080, min_replicas=1)
    t = sky.Task.from_yaml_config(svc.to_yaml_dict())
    assert t.service is not None and t.service.ports == "8080"
    from sky.serve import serve_utils

    serve_utils.validate_service_task(t, pool=False)

    import sky.volumes

    sky.volumes.Volume.from_yaml_config(VolumeSpec(name="v", size="1Gi").to_yaml_dict())


def test_pool_spec_roundtrip_and_sky_accepts_it():
    from stratus.specs.models import PoolSpec

    pool = PoolSpec(name="eval-pool", resources=Resources(infra="k8s", cpus="2"), setup="pip install vllm", workers=3)
    data = yaml.safe_load(pool.to_yaml())
    assert list(data)[:2] == ["name", "pool"] and data["pool"] == {"workers": 3}
    assert "run" not in data
    assert roundtrip(pool) == pool
    with pytest.raises(ValidationError, match="run"):
        PoolSpec(name="p", run="echo")
    sky = pytest.importorskip("sky")
    task = sky.Task.from_yaml_config(pool.to_yaml_dict())
    assert task.service is not None


def test_job_pool_lives_in_header_comment():
    from stratus.specs.models import read_meta, write_meta

    job = JobSpec(name="j", run="echo hi", pool="eval-pool")
    assert "pool" not in yaml.safe_load(job.to_yaml())  # YAML stays plain SkyPilot
    text = write_meta(job) + job.to_yaml()
    assert read_meta(text) == {"pool": "eval-pool"}
    assert spec_from_yaml(SpecKind.JOB, "j", text).pool == "eval-pool"
    with pytest.raises(ValidationError):
        JobSpec(name="j", run="x", pool="Bad Pool")
