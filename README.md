# Krayne

**CLI and SDK for creating, managing, and scaling Ray clusters on Kubernetes.**

Krayne wraps the [KubeRay](https://ray-project.github.io/kuberay/) operator behind a clean, opinionated interface so ML practitioners can get distributed compute without touching Kubernetes manifests.

A fast and intuitive terminal TUI (Terminal User Interface) is also available.

![ikrayne demo](docs/assets/ikrayne-demo.gif)

Navigate clusters, create with prefilled forms, scale, delete, and toggle tunnels — all with keyboard shortcuts. See the [Interactive TUI guide](https://roulbac.github.io/krayne/guide/interactive-tui/) for details.


## Quickstart

```bash
pip install krayne
```

Create a Ray cluster with a single command:

```bash
krayne create my-cluster --gpus-per-worker 1 --workers 2
```

Or use the Python SDK:

```python
from krayne.api import create_cluster
from krayne.config import ClusterConfig, WorkerGroupConfig

config = ClusterConfig(
    name="hello-world",
    worker_groups=[WorkerGroupConfig(replicas=2)],
)
info = create_cluster(config, wait=True)
print(info.client_url)   # ray://10.0.0.1:10001
```

## Interactive TUI

Krayne includes a k9s-style interactive terminal UI:

```bash
krayne tui
```

Or run it directly without installing: `uvx krayne tui`

## Features

- **Zero-config defaults** — every command works with no flags. Sensible defaults get you a working cluster instantly.
- **CLI and SDK** — the CLI is a thin shell over the Python SDK. Anything you do from the terminal, you can do from code.
- **Interactive TUI** — k9s-style terminal UI for keyboard-driven cluster management.
- **Functional API** — stateless free functions, not class hierarchies. Easy to test, easy to compose.
- **Pydantic config** — validated configuration with YAML override support. No silent failures.
- **Rich output** — beautiful terminal tables via Rich, with `--output json` for scripting.

## CLI Overview

```
krayne create <name>      Create a new Ray cluster
krayne get                List clusters in a namespace
krayne describe <name>    Show detailed cluster info
krayne scale <name>       Scale a worker group
krayne delete <name>      Delete a cluster
krayne tui                Launch interactive TUI
```

All commands support `-n/--namespace`, `--output json`, and `--debug` flags.

## Documentation

Full documentation is available at the [Krayne docs site](https://roulbac.github.io/krayne/).

- [Getting Started](https://roulbac.github.io/krayne/guide/quickstart/)
- [CLI Reference](https://roulbac.github.io/krayne/reference/cli/)
- [Python SDK Reference](https://roulbac.github.io/krayne/reference/sdk/)
- [Configuration](https://roulbac.github.io/krayne/guide/configuration/)
- [Architecture](https://roulbac.github.io/krayne/architecture/)
- [Error Handling](https://roulbac.github.io/krayne/reference/errors/)

## Requirements

- Python 3.10+
- A Kubernetes cluster with the [KubeRay operator](https://ray-project.github.io/kuberay/) installed
- A valid kubeconfig (or running inside the cluster)

## Development

```bash
# Clone and install
git clone https://github.com/roulbac/krayne.git
cd krayne
uv sync

# Run tests
uv run pytest

# Run integration tests (sandbox is provisioned automatically by test fixtures)
uv run pytest -m integration
```

## Acknowledgements

Krayne is inspired by [Spotify-Ray (sp-ray)](https://engineering.atspotify.com/2023/02/unleashing-ml-innovation-at-spotify-with-ray/), Spotify's internal platform for running Ray on Kubernetes. The sp-ray team demonstrated that a CLI and SDK with sensible defaults, progressive disclosure of complexity, and managed KubeRay infrastructure can let ML practitioners focus on business logic instead of Kubernetes manifests. Krayne follows this philosophy as an open-source tool for the broader community.

## License

Apache 2.0
