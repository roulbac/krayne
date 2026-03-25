# Error Handling

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

```
PrismError
├── ClusterNotFoundError
├── ClusterAlreadyExistsError
├── ConfigValidationError
├── ClusterTimeoutError
├── KubeConnectionError
└── NamespaceNotFoundError
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

## CLI error display

The CLI catches all `PrismError` subclasses and renders them as Rich panels:

```
╭──── Error ────╮
│ Cluster 'foo' │
│ not found in  │
│ namespace     │
│ 'default'     │
╰───────────────╯
```

Use `--debug` to see the full Python traceback instead:

```bash
prism describe foo --debug
```
