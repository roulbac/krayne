from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from krayne.cli import app as _state
from krayne.config import (
    AutoscalerConfig,
    ClusterConfig,
    DEFAULT_HEAD_CPUS,
    DEFAULT_HEAD_MEMORY,
    KrayneSettings,
    WorkerGroupConfig,
    load_config_from_yaml,
)
from krayne.errors import KrayneError
from krayne.output import (
    format_cluster_created,
    format_cluster_details,
    format_cluster_list,
    format_init_success,
    format_json,
)


@_state.app.command("create")
def create(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    gpus_per_worker: int = typer.Option(0, "--gpus-per-worker"),
    cpus_in_head: str = typer.Option(DEFAULT_HEAD_CPUS, "--cpus-in-head"),
    memory_in_head: str = typer.Option(DEFAULT_HEAD_MEMORY, "--memory-in-head"),
    workers: int = typer.Option(0, "--workers", help="Desired worker replicas (initial count)."),
    min_workers: int = typer.Option(0, "--min-workers", help="Minimum worker replicas for autoscaling."),
    max_workers: int = typer.Option(1, "--max-workers", help="Maximum worker replicas for autoscaling."),
    no_autoscaling: bool = typer.Option(False, "--no-autoscaling", help="Disable autoscaling (pin replicas)."),
    timeout: int = typer.Option(300, "--timeout"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="YAML config file."),
) -> None:
    """Create a new Ray cluster."""
    import time

    from rich.live import Live

    try:
        if file:
            overrides = {"name": name, "namespace": namespace}
            config = load_config_from_yaml(file, overrides=overrides)
        else:
            wg = WorkerGroupConfig(
                replicas=workers,
                min_replicas=workers if no_autoscaling else min_workers,
                max_replicas=workers if no_autoscaling else max_workers,
                gpus=gpus_per_worker,
            )
            autoscaler = AutoscalerConfig(enabled=not no_autoscaling)
            config = ClusterConfig(
                name=name,
                namespace=namespace,
                head={"cpus": cpus_in_head, "memory": memory_in_head},  # type: ignore[arg-type]
                worker_groups=[wg],
                autoscaler=autoscaler,
            )

        info = _state._create_cluster(config, kubeconfig=_state._kubeconfig)

        if _state._output_json:
            format_json(info, _state.console)
            return

        try:
            deadline = time.monotonic() + timeout
            with Live(
                format_cluster_created(info, _state.console, live=True),
                console=_state.console,
                refresh_per_second=2,
            ) as live:
                while info.status not in ("ready", "running"):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(2)
                    info = _state._get_cluster(
                        name, namespace, kubeconfig=_state._kubeconfig
                    )
                    live.update(
                        format_cluster_created(info, _state.console, live=True)
                    )
        except KeyboardInterrupt:
            pass

        format_cluster_created(info, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("get")
def get(
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """List Ray clusters in a namespace."""
    try:
        clusters = _state._list_clusters(namespace, kubeconfig=_state._kubeconfig)
        if _state._output_json:
            format_json(clusters, _state.console)
        else:
            format_cluster_list(clusters, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("describe")
def describe(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """Show detailed information about a cluster."""
    from krayne.tunnel import is_tunnel_active, load_tunnel_state

    try:
        details = _state._describe_cluster(name, namespace, kubeconfig=_state._kubeconfig)
        if _state._output_json:
            format_json(details, _state.console)
        else:
            tunnel_state = None
            if is_tunnel_active(name, namespace):
                tunnel_state = load_tunnel_state(name, namespace)
            format_cluster_details(details, _state.console, tunnel_state=tunnel_state)
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("scale")
def scale(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    worker_group: str = typer.Option("worker", "--worker-group", "-g"),
    replicas: Optional[int] = typer.Option(None, "--replicas", "-r", help="Target replica count."),
    min_replicas: Optional[int] = typer.Option(None, "--min-replicas", help="Minimum replicas for autoscaling."),
    max_replicas: Optional[int] = typer.Option(None, "--max-replicas", help="Maximum replicas for autoscaling."),
) -> None:
    """Scale a worker group of a cluster."""
    try:
        info = _state._scale_cluster(
            name, namespace, worker_group, replicas,
            min_replicas=min_replicas, max_replicas=max_replicas,
            kubeconfig=_state._kubeconfig,
        )
        if _state._output_json:
            format_json(info, _state.console)
        else:
            format_cluster_created(info, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)


@_state.app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Delete a Ray cluster."""
    try:
        if not force:
            typer.confirm(
                f"Delete cluster '{name}' in namespace '{namespace}'?", abort=True
            )
        from krayne.tunnel import stop_tunnels

        stop_tunnels(name, namespace)
        _state._delete_cluster(name, namespace, kubeconfig=_state._kubeconfig)
        _state.console.print(f"Cluster '{name}' deleted.", style="green")
    except KrayneError as exc:
        _state._handle_error(exc)


def _resolve_kubeconfig_path(kubeconfig: str | None) -> Path:
    """Prompt for a kubeconfig path when none was given, then validate
    that the resolved file exists."""
    from krayne.config.settings import DEFAULT_KUBECONFIG
    from krayne.errors import ConfigValidationError
    from krayne.sandbox.manager import SANDBOX_KUBECONFIG

    if kubeconfig is None:
        import questionary

        CUSTOM_LABEL = "Custom path"
        choices = [
            questionary.Choice(
                f"Default kubeconfig ({DEFAULT_KUBECONFIG})",
                value=str(DEFAULT_KUBECONFIG),
            ),
            questionary.Choice(
                f"Sandbox kubeconfig ({SANDBOX_KUBECONFIG})",
                value=str(SANDBOX_KUBECONFIG),
            ),
            questionary.Choice(CUSTOM_LABEL, value=CUSTOM_LABEL),
        ]
        selected = questionary.select(
            "Select kubeconfig source:",
            choices=choices,
        ).ask()
        if selected is None:
            raise typer.Abort()
        if selected == CUSTOM_LABEL:
            kubeconfig = questionary.path("Enter kubeconfig path:").ask()
            if kubeconfig is None:
                raise typer.Abort()
        else:
            kubeconfig = selected

    resolved = Path(kubeconfig).resolve()
    if not resolved.exists():
        raise ConfigValidationError(f"Kubeconfig file not found: {resolved}")
    return resolved


def _resolve_kube_context(
    context: str | None, contexts: list[str], resolved_kubeconfig: Path
) -> str:
    """Pick a context: validate the explicit one, auto-pick a single
    available context, or prompt interactively otherwise."""
    from krayne.errors import ConfigValidationError

    if context is not None:
        if context not in contexts:
            raise ConfigValidationError(
                f"Context '{context}' not found in kubeconfig. "
                f"Available: {', '.join(contexts)}"
            )
        return context

    if len(contexts) == 1:
        chosen = contexts[0]
        _state.console.print(f"Auto-selected context: [bold]{chosen}[/bold]")
        return chosen

    import questionary

    # "current" is the context krayne is currently configured with —
    # not kubectl's ``current-context`` — and only counts when we're
    # re-initialising against the same kubeconfig.  Read the settings
    # file raw so a previously invalid/stale file doesn't stop us from
    # re-running init.
    current = _state._current_krayne_context_for(resolved_kubeconfig)
    choices = [
        questionary.Choice(
            f"{name}  (current)" if name == current else name,
            value=name,
        )
        for name in contexts
    ]
    default = current if current in contexts else None
    chosen = questionary.select(
        "Select kube context:",
        choices=choices,
        default=default,
    ).ask()
    if chosen is None:
        raise typer.Abort()
    return chosen


@_state.app.command("init")
def init(
    kubeconfig: str = typer.Option(
        None, "--kubeconfig", "-k", help="Path to kubeconfig file (skips interactive prompt)."
    ),
    context: str = typer.Option(
        None, "--context", "-c", help="Kubernetes context name (skips interactive prompt)."
    ),
) -> None:
    """Initialise Krayne with a kubeconfig and kube context.

    When run without flags an interactive menu is shown.  Pass --kubeconfig
    and --context for headless / CI usage.
    """
    import yaml as _yaml

    from krayne.errors import ConfigValidationError

    try:
        resolved = _resolve_kubeconfig_path(kubeconfig)

        raw = _yaml.safe_load(resolved.read_text()) or {}
        contexts = [c["name"] for c in raw.get("contexts", []) if "name" in c]
        if not contexts:
            raise ConfigValidationError(
                f"No contexts found in kubeconfig: {resolved}"
            )

        chosen_context = _resolve_kube_context(context, contexts, resolved)

        # Dry-run: try to initialise a kube client against the selected
        # (kubeconfig, context).  ``get_kube_client`` checks KubeRay is
        # installed before constructing the client, so a missing
        # operator, bad context, or unreachable API surfaces here — and
        # we refuse to persist a broken ~/.krayne/config.yaml.
        from krayne.kube.client import (
            clear_kube_client_cache,
            get_kube_client,
        )

        try:
            get_kube_client(kubeconfig=str(resolved), context=chosen_context)
        finally:
            # This dry-run cached a client keyed on the *current*
            # settings-file digest (which doesn't reflect what we're
            # about to save).  Drop it so real calls re-validate
            # against the saved settings.
            clear_kube_client_cache()

        _state.save_krayne_settings(
            KrayneSettings(kubeconfig=str(resolved), kube_context=chosen_context)
        )
        format_init_success(str(resolved), chosen_context, _state.console)
    except KrayneError as exc:
        _state._handle_error(exc)
