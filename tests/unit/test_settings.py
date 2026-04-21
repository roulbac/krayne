from __future__ import annotations

from pathlib import Path

import pytest

from krayne.config.settings import (
    KrayneSettings,
    clear_krayne_settings,
    load_krayne_settings,
    save_krayne_settings,
)
from krayne.errors import ConfigValidationError


_KUBECONFIG_YAML = (
    "apiVersion: v1\n"
    "kind: Config\n"
    "contexts:\n"
    "- name: ctx-a\n"
    "  context:\n"
    "    cluster: c-a\n"
    "    user: u-a\n"
    "- name: ctx-b\n"
    "  context:\n"
    "    cluster: c-b\n"
    "    user: u-b\n"
    "current-context: ctx-a\n"
)


@pytest.fixture()
def kubeconfig_file(tmp_path: Path) -> Path:
    """Write a valid kubeconfig to a temp file and return its path."""
    path = tmp_path / "kubeconfig"
    path.write_text(_KUBECONFIG_YAML)
    return path


class TestLoadSettings:
    def test_missing_file_returns_defaults(self):
        settings = load_krayne_settings()
        assert settings.kubeconfig is None
        assert settings.kube_context is None

    def test_empty_file_returns_defaults(self):
        import krayne.config.settings as mod

        mod.PRISM_DIR.mkdir(parents=True, exist_ok=True)
        mod.PRISM_CONFIG_FILE.write_text("")
        settings = load_krayne_settings()
        assert settings.kubeconfig is None

    def test_malformed_yaml_raises(self):
        import krayne.config.settings as mod

        mod.PRISM_DIR.mkdir(parents=True, exist_ok=True)
        mod.PRISM_CONFIG_FILE.write_text("kubeconfig: [unterminated")
        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_krayne_settings()

    def test_missing_kubeconfig_file_raises(self):
        save_krayne_settings(KrayneSettings(kubeconfig="/does/not/exist"))
        with pytest.raises(ConfigValidationError, match="does not exist"):
            load_krayne_settings()

    def test_context_without_kubeconfig_raises(self):
        import krayne.config.settings as mod

        mod.PRISM_DIR.mkdir(parents=True, exist_ok=True)
        mod.PRISM_CONFIG_FILE.write_text("kube_context: ctx-a\n")
        with pytest.raises(ConfigValidationError, match="kubeconfig"):
            load_krayne_settings()

    def test_context_not_in_kubeconfig_raises(self, kubeconfig_file):
        save_krayne_settings(
            KrayneSettings(
                kubeconfig=str(kubeconfig_file), kube_context="nope"
            )
        )
        with pytest.raises(ConfigValidationError, match="not found"):
            load_krayne_settings()


class TestSaveAndLoad:
    def test_roundtrip(self, kubeconfig_file):
        save_krayne_settings(
            KrayneSettings(
                kubeconfig=str(kubeconfig_file), kube_context="ctx-a"
            )
        )
        settings = load_krayne_settings()
        assert settings.kubeconfig == str(kubeconfig_file)
        assert settings.kube_context == "ctx-a"

    def test_overwrite(self, tmp_path):
        first = tmp_path / "first"
        first.write_text(_KUBECONFIG_YAML)
        second = tmp_path / "second"
        second.write_text(_KUBECONFIG_YAML)
        save_krayne_settings(KrayneSettings(kubeconfig=str(first)))
        save_krayne_settings(KrayneSettings(kubeconfig=str(second)))
        settings = load_krayne_settings()
        assert settings.kubeconfig == str(second)


class TestClearSettings:
    def test_clear_removes_file(self, kubeconfig_file):
        import krayne.config.settings as mod

        save_krayne_settings(KrayneSettings(kubeconfig=str(kubeconfig_file)))
        assert mod.PRISM_CONFIG_FILE.exists()
        clear_krayne_settings()
        assert not mod.PRISM_CONFIG_FILE.exists()

    def test_clear_noop_when_missing(self):
        clear_krayne_settings()  # should not raise
