"""Shared fixtures for krayne unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_krayne_settings(tmp_path, monkeypatch):
    """Redirect ``~/.krayne`` reads/writes to a per-test temp directory.

    Without this, ``load_krayne_settings`` would read the developer's
    real config file — which now raises when the referenced kubeconfig
    is absent, breaking tests on some machines.
    """
    import krayne.config.settings as mod

    krayne_dir = tmp_path / "krayne"
    monkeypatch.setattr(mod, "PRISM_DIR", krayne_dir)
    monkeypatch.setattr(mod, "PRISM_CONFIG_FILE", krayne_dir / "config.yaml")


@pytest.fixture(autouse=True)
def _clear_kube_client_cache():
    """Drop the cached :class:`DefaultKubeClient` between tests so that
    patches applied by one test don't leak into the next."""
    from krayne.kube.client import clear_kube_client_cache

    clear_kube_client_cache()
    yield
    clear_kube_client_cache()


@pytest.fixture(autouse=True)
def _skip_kuberay_check(monkeypatch):
    """Stub out the KubeRay CRD check — no real cluster in unit tests.

    Tests that need the real function should request the
    :func:`real_assert_kuberay_installed` fixture.
    """
    import krayne.kube.client as mod

    monkeypatch.setattr(mod, "assert_kuberay_installed", lambda **_: None)


@pytest.fixture()
def real_assert_kuberay_installed():
    """Return the un-stubbed :func:`assert_kuberay_installed` function."""
    import krayne.kube.client as mod

    # Bypass the autouse stub by going through the original unbound
    # function captured at import time.
    return _ORIGINAL_ASSERT_KUBERAY_INSTALLED


import krayne.kube.client as _kube_client_mod

_ORIGINAL_ASSERT_KUBERAY_INSTALLED = _kube_client_mod.assert_kuberay_installed
del _kube_client_mod
