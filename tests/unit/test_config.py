"""Unit tests for prism.config."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from prism.config import (
    ClusterConfig,
    HeadNodeConfig,
    ServicesConfig,
    WorkerGroupConfig,
    load_config_from_yaml,
)
from prism.errors import ConfigValidationError


# ---------------------------------------------------------------------------
# ClusterConfig defaults
# ---------------------------------------------------------------------------


class TestClusterConfigDefaults:
    def test_minimal_config(self):
        cfg = ClusterConfig(name="test")
        assert cfg.name == "test"
        assert cfg.namespace == "default"
        assert cfg.head.cpus == 15
        assert cfg.head.memory == "48Gi"
        assert cfg.head.gpus == 0
        assert len(cfg.worker_groups) == 1
        assert cfg.worker_groups[0].replicas == 1

    def test_services_defaults(self):
        cfg = ClusterConfig(name="test")
        assert cfg.services.notebook is True
        assert cfg.services.vscode_server is False
        assert cfg.services.ssh is True
        assert cfg.services.tutorials is False

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


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


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
        assert cfg.head.cpus == 8
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
        assert cfg.head.cpus == 4

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


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_model_dump_and_reload(self):
        cfg = ClusterConfig(
            name="rt",
            namespace="ns",
            head=HeadNodeConfig(cpus=4, memory="16Gi"),
            worker_groups=[WorkerGroupConfig(name="w", replicas=3, gpus=2)],
            services=ServicesConfig(vscode_server=True),
        )
        data = cfg.model_dump()
        reloaded = ClusterConfig(**data)
        assert reloaded == cfg
