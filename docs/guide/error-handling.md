# Error Handling

Prism provides clear, actionable error messages in both the CLI and SDK. All errors inherit from a single base class, making them easy to catch and handle.

---

## How errors appear in the CLI

By default, errors are displayed as Rich panels with a clean message:

```title="Terminal output"
$ prism describe nonexistent-cluster
╭──── Error ─────────────────────────────────╮
│ Cluster 'nonexistent-cluster' not found in │
│ namespace 'default'                        │
╰────────────────────────────────────────────╯
```

### Debug mode

Use `--debug` to see the full Python traceback:

```bash
$ prism describe nonexistent-cluster --debug
Traceback (most recent call last):
  File ".../prism/cli/app.py", line 42, in describe
    details = describe_cluster(name, namespace, client=client)
  File ".../prism/api/clusters.py", line 89, in describe_cluster
    obj = client.get_ray_cluster(name, namespace)
  ...
prism.errors.ClusterNotFoundError: Cluster 'nonexistent-cluster'
not found in namespace 'default'
```

---

## Error hierarchy

All Prism exceptions inherit from `PrismError`:

```mermaid
classDiagram
  class PrismError {
    Base exception
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
    Wraps Pydantic error
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

  PrismError <|-- ClusterNotFoundError
  PrismError <|-- ClusterAlreadyExistsError
  PrismError <|-- ConfigValidationError
  PrismError <|-- ClusterTimeoutError
  PrismError <|-- KubeConnectionError
  PrismError <|-- NamespaceNotFoundError
```

---

## Handling errors in Python

### Catch all Prism errors

```python
from prism.api import create_cluster
from prism.errors import PrismError

try:
    info = create_cluster(config)
except PrismError as e:
    print(f"Prism error: {e}")
```

### Catch specific errors

```python
from prism.api import get_cluster, create_cluster
from prism.errors import (
    ClusterNotFoundError,
    ClusterAlreadyExistsError,
    ClusterTimeoutError,
    NamespaceNotFoundError,
)

# Handle missing cluster
try:
    info = get_cluster("my-cluster")
except ClusterNotFoundError as e:
    print(f"Cluster '{e.name}' not found in '{e.namespace}'")

# Handle duplicate name
try:
    info = create_cluster(config)
except ClusterAlreadyExistsError as e:
    print(f"Cluster '{e.name}' already exists in '{e.namespace}'")

# Handle timeout
try:
    info = create_cluster(config, wait=True, timeout=60)
except ClusterTimeoutError as e:
    print(f"Cluster '{e.name}' not ready after {e.timeout}s")
```

---

## Common errors and solutions

| Error | Cause | Solution |
|---|---|---|
| `ClusterNotFoundError` | Cluster name doesn't exist in the namespace | Check spelling, verify namespace with `-n` |
| `ClusterAlreadyExistsError` | A cluster with that name already exists | Choose a different name or delete the existing cluster |
| `ConfigValidationError` | Invalid YAML or config values | Check YAML syntax, field names, and value types |
| `ClusterTimeoutError` | Cluster didn't reach `ready` in time | Increase `--timeout`, check pod status with `prism describe` |
| `KubeConnectionError` | Can't reach Kubernetes API | Check kubeconfig, verify cluster is running, run `prism init` |
| `NamespaceNotFoundError` | Specified namespace doesn't exist | Create the namespace first with `kubectl create namespace <name>` |

---

## Troubleshooting workflow

```mermaid
flowchart TD
  Error["Error occurred"] --> Debug{"Add --debug flag"}
  Debug --> Traceback["Read traceback"]
  Traceback --> Type{"Error type?"}
  Type -->|Connection| CheckKube["Check kubeconfig<br/>prism init"]
  Type -->|NotFound| CheckName["Check cluster name<br/>prism get"]
  Type -->|Timeout| CheckPods["Check pod status<br/>prism describe"]
  Type -->|Config| CheckYAML["Validate YAML<br/>Check field names"]
```

1. **Add `--debug`** to get the full traceback
2. **Identify the error type** from the exception class name
3. **Follow the solution** from the table above
4. If the issue persists, check the KubeRay operator logs:
   ```bash
   kubectl logs -l app.kubernetes.io/name=kuberay-operator
   ```

---

## What's next

- [Error Types Reference](../reference/errors.md) — complete exception definitions and attributes
- [CLI Reference](../reference/cli.md) — `--debug` flag documentation
