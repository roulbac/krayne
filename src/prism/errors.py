class PrismError(Exception):
    """Base exception for all Prism errors."""


class ClusterNotFoundError(PrismError):
    """Raised when a cluster does not exist."""

    def __init__(self, name: str, namespace: str) -> None:
        self.name = name
        self.namespace = namespace
        super().__init__(f"Cluster '{name}' not found in namespace '{namespace}'")


class ClusterAlreadyExistsError(PrismError):
    """Raised when creating a cluster that already exists."""

    def __init__(self, name: str, namespace: str) -> None:
        self.name = name
        self.namespace = namespace
        super().__init__(
            f"Cluster '{name}' already exists in namespace '{namespace}'"
        )


class ConfigValidationError(PrismError):
    """Raised when cluster configuration is invalid."""


class ClusterTimeoutError(PrismError):
    """Raised when waiting for a cluster exceeds the timeout."""

    def __init__(self, name: str, namespace: str, timeout: int) -> None:
        self.name = name
        self.namespace = namespace
        self.timeout = timeout
        super().__init__(
            f"Cluster '{name}' in namespace '{namespace}' not ready after {timeout}s"
        )


class KubeConnectionError(PrismError):
    """Raised when the Kubernetes API is unreachable."""


class NamespaceNotFoundError(PrismError):
    """Raised when the specified namespace does not exist."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(f"Namespace '{namespace}' not found")


class SandboxError(PrismError):
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
            "Sandbox 'prism-sandbox' already exists. "
            "Run 'prism sandbox teardown' first."
        )


class SandboxNotFoundError(SandboxError):
    """No sandbox container to tear down."""

    def __init__(self) -> None:
        super().__init__("No sandbox found. Run 'prism sandbox setup' first.")
