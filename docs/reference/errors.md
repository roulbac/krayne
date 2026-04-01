# Error Types

Prism defines a flat exception hierarchy rooted at `PrismError`. All exceptions are importable from `prism.errors`.

```python
from prism.errors import (
    PrismError,
    ClusterNotFoundError,
    ClusterAlreadyExistsError,
    ConfigValidationError,
    ClusterTimeoutError,
    KubeConnectionError,
    NamespaceNotFoundError,
)
```

## Exception hierarchy

```mermaid
classDiagram
  class PrismError {
    Base exception for all Prism errors
  }
  class ClusterNotFoundError {
    +name: str
    +namespace: str
  }
  class ClusterAlreadyExistsError {
    +name: str
    +namespace: str
  }
  class ConfigValidationError {
    Wraps Pydantic ValidationError
  }
  class ClusterTimeoutError {
    +name: str
    +namespace: str
    +timeout: int
  }
  class KubeConnectionError {
    K8s API unreachable
  }
  class NamespaceNotFoundError {
    +namespace: str
  }
  class SandboxError {
    Base for sandbox errors
  }
  class DockerNotFoundError
  class SandboxAlreadyExistsError
  class SandboxNotFoundError

  PrismError <|-- ClusterNotFoundError
  PrismError <|-- ClusterAlreadyExistsError
  PrismError <|-- ConfigValidationError
  PrismError <|-- ClusterTimeoutError
  PrismError <|-- KubeConnectionError
  PrismError <|-- NamespaceNotFoundError
  PrismError <|-- SandboxError
  SandboxError <|-- DockerNotFoundError
  SandboxError <|-- SandboxAlreadyExistsError
  SandboxError <|-- SandboxNotFoundError
```

All exceptions inherit from `PrismError`, so you can catch all Prism errors with a single handler.

---

## Exceptions

### `PrismError`

Base exception for all Prism errors. Catch this to handle any Prism-specific error.

```python
from prism.errors import PrismError

try:
    info = create_cluster(config)
except PrismError as e:
    print(f"Prism error: {e}")
```

---

### `ClusterNotFoundError`

Raised when an operation targets a cluster that does not exist.

**Attributes:**

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Cluster name |
| `namespace` | `str` | Kubernetes namespace |

**Raised by:** `get_cluster`, `describe_cluster`, `scale_cluster`, `delete_cluster`, `wait_until_ready`

```python
from prism.errors import ClusterNotFoundError

try:
    info = get_cluster("nonexistent")
except ClusterNotFoundError as e:
    print(f"Cluster '{e.name}' not found in '{e.namespace}'")
```

---

### `ClusterAlreadyExistsError`

Raised when creating a cluster with a name that's already in use in the target namespace.

**Attributes:**

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Cluster name |
| `namespace` | `str` | Kubernetes namespace |

**Raised by:** `create_cluster`

---

### `ConfigValidationError`

Raised when cluster configuration is invalid — wraps Pydantic `ValidationError` with a clear message.

**Raised by:** `load_config_from_yaml`, `ClusterConfig` construction

```python
from prism.errors import ConfigValidationError

try:
    config = load_config_from_yaml("bad-config.yaml")
except ConfigValidationError as e:
    print(f"Invalid config: {e}")
```

---

### `ClusterTimeoutError`

Raised when `wait_until_ready` exceeds the specified timeout.

**Attributes:**

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Cluster name |
| `namespace` | `str` | Kubernetes namespace |
| `timeout` | `int` | Timeout that was exceeded (seconds) |

**Raised by:** `create_cluster` (with `wait=True`), `wait_until_ready`

---

### `KubeConnectionError`

Raised when the Kubernetes API is unreachable — no valid kubeconfig, network issues, or API server errors.

**Raised by:** Any SDK function that communicates with Kubernetes.

---

### `NamespaceNotFoundError`

Raised when the specified Kubernetes namespace does not exist.

**Attributes:**

| Attribute | Type | Description |
|---|---|---|
| `namespace` | `str` | The namespace that was not found |

**Raised by:** `create_cluster`

---

### `SandboxError`

Base exception for sandbox-related errors.

---

### `DockerNotFoundError`

Raised when Docker CLI is not available or the Docker daemon is not running.

**Raised by:** `setup_sandbox`

---

### `SandboxAlreadyExistsError`

Raised when a sandbox container already exists.

**Raised by:** `setup_sandbox`

---

### `SandboxNotFoundError`

Raised when attempting to tear down a sandbox that doesn't exist.

**Raised by:** `teardown_sandbox`

---

## Which functions raise which errors

| Function | Possible Errors |
|---|---|
| `create_cluster` | `ClusterAlreadyExistsError`, `NamespaceNotFoundError`, `ClusterTimeoutError`, `KubeConnectionError`, `ConfigValidationError` |
| `get_cluster` | `ClusterNotFoundError`, `KubeConnectionError` |
| `list_clusters` | `KubeConnectionError` |
| `describe_cluster` | `ClusterNotFoundError`, `KubeConnectionError` |
| `scale_cluster` | `ClusterNotFoundError`, `PrismError` (worker group not found), `KubeConnectionError` |
| `delete_cluster` | `ClusterNotFoundError`, `KubeConnectionError` |
| `wait_until_ready` | `ClusterTimeoutError`, `ClusterNotFoundError`, `KubeConnectionError` |
| `setup_sandbox` | `DockerNotFoundError`, `SandboxAlreadyExistsError` |
| `teardown_sandbox` | `SandboxNotFoundError` |
