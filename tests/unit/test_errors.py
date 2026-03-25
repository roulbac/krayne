from krayne.errors import (
    ClusterAlreadyExistsError,
    ClusterNotFoundError,
    ClusterTimeoutError,
    KubeConnectionError,
    NamespaceNotFoundError,
    KrayneError,
)


def test_all_exceptions_inherit_from_krayne_error():
    for exc_cls in (
        ClusterNotFoundError,
        ClusterAlreadyExistsError,
        ClusterTimeoutError,
        KubeConnectionError,
        NamespaceNotFoundError,
    ):
        assert issubclass(exc_cls, KrayneError)


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
