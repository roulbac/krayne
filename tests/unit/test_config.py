from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from krayne.config import (
    DEFAULT_CPUS,
    DEFAULT_HEAD_MEMORY,
    DEFAULT_MEMORY,
    AutoscalerConfig,
    ClusterConfig,
    HeadNodeConfig,
    ServicesConfig,
    WorkerGroupConfig,
    load_config_from_yaml,
)
from krayne.errors import ConfigValidationError


class TestClusterConfigDefaults:
    def test_minimal_config(self):
        cfg = ClusterConfig(name="test")
        assert cfg.name == "test"
        assert cfg.namespace == "default"
        assert cfg.head.cpus == DEFAULT_CPUS
        assert cfg.head.memory == DEFAULT_HEAD_MEMORY
        assert cfg.head.gpus == 0
        assert len(cfg.worker_groups) == 1
        assert cfg.worker_groups[0].replicas == 0
        assert cfg.worker_groups[0].min_replicas == 0
        assert cfg.worker_groups[0].max_replicas == 1

    def test_autoscaler_defaults(self):
        cfg = ClusterConfig(name="test")
        assert cfg.autoscaler.enabled is True
        assert cfg.autoscaler.idle_timeout_seconds == 60
        assert cfg.autoscaler.upscaling_mode == "Default"
        assert cfg.autoscaler.cpu == "500m"
        assert cfg.autoscaler.memory == "512Mi"

    def test_services_defaults(self):
        cfg = ClusterConfig(name="test")
        assert cfg.services.notebook is True
        assert cfg.services.code_server is True
        assert cfg.services.ssh is True

    def test_custom_worker_groups(self):
        cfg = ClusterConfig(
            name="gpu",
            worker_groups=[
                WorkerGroupConfig(name="a100", replicas=4, gpus=8, gpu_type="a100"),
                WorkerGroupConfig(name="cpu", replicas=2),
            ],
        )
        assert len(cfg.worker_groups) == 2
        assert cfg.worker_groups[0].gpus == 8
        assert cfg.worker_groups[1].gpus == 0

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ClusterConfig(name="bad", unknown_field="oops")


class TestWorkerGroupAutoscaling:
    def test_default_replicas(self):
        wg = WorkerGroupConfig()
        assert wg.replicas == 0
        assert wg.min_replicas == 0
        assert wg.max_replicas == 1

    def test_max_replicas_auto_adjusted(self):
        """When replicas > max_replicas, max_replicas is auto-adjusted."""
        wg = WorkerGroupConfig(replicas=3)
        assert wg.max_replicas == 3

    def test_min_replicas_exceeds_replicas_raises(self):
        with pytest.raises(ValidationError, match="min_replicas"):
            WorkerGroupConfig(min_replicas=5, replicas=2, max_replicas=10)

    def test_min_exceeds_replicas_and_max_raises(self):
        """min_replicas > replicas always raises, even if min > max too."""
        with pytest.raises(ValidationError, match="min_replicas"):
            WorkerGroupConfig(min_replicas=10, replicas=2, max_replicas=5)

    def test_explicit_min_max(self):
        wg = WorkerGroupConfig(replicas=2, min_replicas=1, max_replicas=5)
        assert wg.min_replicas == 1
        assert wg.replicas == 2
        assert wg.max_replicas == 5


class TestYamlLoading:
    def test_load_basic_yaml(self, tmp_path: Path):
        yaml_file = tmp_path / "cluster.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: from-yaml
            namespace: ml-team
            head:
              cpus: 8
              memory: 32Gi
            worker_groups:
              - name: gpu-workers
                replicas: 2
                gpus: 1
                gpu_type: a100
            """)
        )
        cfg = load_config_from_yaml(yaml_file)
        assert cfg.name == "from-yaml"
        assert cfg.namespace == "ml-team"
        assert cfg.head.cpus == "8"
        assert cfg.worker_groups[0].gpu_type == "a100"

    def test_overrides_take_precedence(self, tmp_path: Path):
        yaml_file = tmp_path / "cluster.yaml"
        yaml_file.write_text("name: original\nnamespace: ns1\n")
        cfg = load_config_from_yaml(yaml_file, overrides={"name": "overridden"})
        assert cfg.name == "overridden"
        assert cfg.namespace == "ns1"

    def test_nested_overrides(self, tmp_path: Path):
        yaml_file = tmp_path / "cluster.yaml"
        yaml_file.write_text("name: test\n")
        cfg = load_config_from_yaml(yaml_file, overrides={"head.cpus": 4})
        assert cfg.head.cpus == "4"

    def test_invalid_yaml_top_level(self, tmp_path: Path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigValidationError, match="YAML mapping"):
            load_config_from_yaml(yaml_file)

    def test_invalid_fields_in_yaml(self, tmp_path: Path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("name: test\nbogus_field: 123\n")
        with pytest.raises(ConfigValidationError):
            load_config_from_yaml(yaml_file)


class TestYamlWithAutoscaling:
    def test_load_yaml_with_autoscaler(self, tmp_path: Path):
        yaml_file = tmp_path / "cluster.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: auto-cluster
            autoscaler:
              enabled: true
              idle_timeout_seconds: 120
              upscaling_mode: Aggressive
            worker_groups:
              - name: gpu-workers
                replicas: 2
                min_replicas: 0
                max_replicas: 10
                gpus: 1
                gpu_type: a100
            """)
        )
        cfg = load_config_from_yaml(yaml_file)
        assert cfg.autoscaler.enabled is True
        assert cfg.autoscaler.idle_timeout_seconds == 120
        assert cfg.autoscaler.upscaling_mode == "Aggressive"
        assert cfg.worker_groups[0].min_replicas == 0
        assert cfg.worker_groups[0].replicas == 2
        assert cfg.worker_groups[0].max_replicas == 10

    def test_load_yaml_autoscaler_disabled(self, tmp_path: Path):
        yaml_file = tmp_path / "cluster.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: static-cluster
            autoscaler:
              enabled: false
            worker_groups:
              - replicas: 3
                min_replicas: 3
                max_replicas: 3
            """)
        )
        cfg = load_config_from_yaml(yaml_file)
        assert cfg.autoscaler.enabled is False
        assert cfg.worker_groups[0].replicas == 3


class TestRoundTrip:
    def test_model_dump_and_reload(self):
        cfg = ClusterConfig(
            name="rt",
            namespace="ns",
            head=HeadNodeConfig(cpus=4, memory="16Gi"),
            worker_groups=[WorkerGroupConfig(name="w", replicas=3, min_replicas=1, max_replicas=5, gpus=2)],
            services=ServicesConfig(code_server=True),
        )
        data = cfg.model_dump()
        reloaded = ClusterConfig(**data)
        assert reloaded == cfg

    def test_round_trip_with_autoscaler(self):
        cfg = ClusterConfig(
            name="rt",
            autoscaler=AutoscalerConfig(enabled=True, idle_timeout_seconds=120),
        )
        data = cfg.model_dump()
        reloaded = ClusterConfig(**data)
        assert reloaded == cfg
