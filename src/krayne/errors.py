class KrayneError(Exception):
    """Base exception for all Krayne errors."""


class ClusterNotFoundError(KrayneError):
    """Raised when a cluster does not exist."""

    def __init__(self, name: str, namespace: str) -> None:
        self.name = name
        self.namespace = namespace
        super().__init__(f"Cluster '{name}' not found in namespace '{namespace}'")


class ClusterAlreadyExistsError(KrayneError):
    """Raised when creating a cluster that already exists."""

    def __init__(self, name: str, namespace: str) -> None:
        self.name = name
        self.namespace = namespace
        super().__init__(
            f"Cluster '{name}' already exists in namespace '{namespace}'"
        )


class ConfigValidationError(KrayneError):
    """Raised when cluster configuration is invalid."""


class ClusterTimeoutError(KrayneError):
    """Raised when waiting for a cluster exceeds the timeout."""

    def __init__(self, name: str, namespace: str, timeout: int) -> None:
        self.name = name
        self.namespace = namespace
        self.timeout = timeout
        super().__init__(
            f"Cluster '{name}' in namespace '{namespace}' not ready after {timeout}s"
        )


class KubeConnectionError(KrayneError):
    """Raised when the Kubernetes API is unreachable."""


class KubeRayNotInstalledError(KrayneError):
    """Raised when the target cluster does not have KubeRay installed
    (the ``rayclusters.ray.io`` CRD is missing)."""

    def __init__(self, context: str | None = None) -> None:
        self.context = context
        where = f" (context: {context})" if context else ""
        super().__init__(
            f"KubeRay is not installed on the target cluster{where}: "
            "the 'rayclusters.ray.io' CRD was not found. Have your cluster admin "
            "install the KubeRay operator and try again."
        )


class NamespaceNotFoundError(KrayneError):
    """Raised when the specified namespace does not exist."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(f"Namespace '{namespace}' not found")


class SandboxError(KrayneError):
    """Base exception for sandbox-related errors."""


class DockerNotFoundError(SandboxError):
    """Docker CLI is not available or Docker daemon is not running."""

    def __init__(self) -> None:
        super().__init__(
            "Docker not found or not running. "
            "Install Docker and ensure the daemon is started."
        )


class SandboxAlreadyExistsError(SandboxError):
    """Sandbox container already exists."""

    def __init__(self) -> None:
        super().__init__(
            "Sandbox 'krayne-sandbox' already exists. "
            "Run 'krayne sandbox teardown' first."
        )


class SandboxNotFoundError(SandboxError):
    """No sandbox container to tear down."""

    def __init__(self) -> None:
        super().__init__("No sandbox found. Run 'krayne sandbox setup' first.")
