from krayne.errors import (
    ClusterAlreadyExistsError,
    ClusterNotFoundError,
    ClusterTimeoutError,
    NamespaceNotFoundError,
)


def test_cluster_not_found_message():
    exc = ClusterNotFoundError("foo", "bar")
    assert "foo" in str(exc)
    assert "bar" in str(exc)
    assert exc.name == "foo"
    assert exc.namespace == "bar"


def test_cluster_already_exists_message():
    exc = ClusterAlreadyExistsError("foo", "bar")
    assert "already exists" in str(exc)


def test_cluster_timeout_message():
    exc = ClusterTimeoutError("foo", "bar", 60)
    assert "60" in str(exc)
    assert exc.timeout == 60


def test_namespace_not_found_message():
    exc = NamespaceNotFoundError("oops")
    assert "oops" in str(exc)
    assert exc.namespace == "oops"
