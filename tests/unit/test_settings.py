"""Unit tests for prism.config.settings (persistent settings)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prism.config.settings import (
    PrismSettings,
    clear_prism_settings,
    load_prism_settings,
    save_prism_settings,
)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Redirect settings I/O to a temp directory."""
    import prism.config.settings as mod

    monkeypatch.setattr(mod, "PRISM_DIR", tmp_path)
    monkeypatch.setattr(mod, "PRISM_CONFIG_FILE", tmp_path / "config.yaml")


class TestLoadSettings:
    def test_missing_file_returns_defaults(self):
        settings = load_prism_settings()
        assert settings.kubeconfig is None

    def test_empty_file_returns_defaults(self, tmp_path):
        (tmp_path / "config.yaml").write_text("")
        settings = load_prism_settings()
        assert settings.kubeconfig is None


class TestSaveAndLoad:
    def test_roundtrip(self):
        save_prism_settings(PrismSettings(kubeconfig="/tmp/my-kubeconfig"))
        settings = load_prism_settings()
        assert settings.kubeconfig == "/tmp/my-kubeconfig"

    def test_overwrite(self):
        save_prism_settings(PrismSettings(kubeconfig="/first"))
        save_prism_settings(PrismSettings(kubeconfig="/second"))
        settings = load_prism_settings()
        assert settings.kubeconfig == "/second"


class TestClearSettings:
    def test_clear_removes_file(self, tmp_path):
        save_prism_settings(PrismSettings(kubeconfig="/tmp/kube"))
        assert (tmp_path / "config.yaml").exists()
        clear_prism_settings()
        assert not (tmp_path / "config.yaml").exists()

    def test_clear_noop_when_missing(self):
        clear_prism_settings()  # should not raise
