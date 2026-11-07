"""Resource views: what a table shows for each SkyPilot resource type.

A view knows how to fetch its rows (in a worker thread), how to render them,
and which hotkeys apply. Actions are dispatched by :class:`MainScreen` to
``act_<name>`` methods, so views stay declarative.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.text import Text

from stratus.specs.models import SpecKind
from stratus.tui.format import age, autostop_text, duration, mark, status_text

if TYPE_CHECKING:
    from stratus.backend.base import Backend
    from stratus.specs.store import SpecStore


@dataclass(frozen=True)
class Action:
    key: str  # Textual key name, e.g. "c", "ctrl+d", "E", "enter"
    label: str
    name: str  # dispatched to MainScreen.act_<name>
    danger: bool = False
    hint: bool = True  # show in the header hint grid

    @property
    def display_key(self) -> str:
        k = self.key
        if k.startswith("ctrl+"):
            return "ctrl-" + k[5:]
        if len(k) == 1 and k.isupper():
            return f"shift-{k.lower()}"
        return k


@dataclass(frozen=True)
class Column:
    label: str
    width: int | None = None
    narrow: bool = True  # keep on narrow terminals


@dataclass
class Ctx:
    backend: "Backend"
    store: "SpecStore"


class View:
    name: str = ""
    title: str = ""
    aliases: tuple[str, ...] = ()
    hotkey: str | None = None  # digit shortcut
    columns: tuple[Column, ...] = ()
    actions: tuple[Action, ...] = ()
    spec_kind: SpecKind | None = None
    empty_hint: str = "Nothing here yet."

    def fetch(self, ctx: Ctx) -> list[Any]:
        raise NotImplementedError

    def key(self, row: Any) -> str:
        return str(row.name)

    def cells(self, row: Any, ctx: Ctx, extra: Any) -> list[Any]:
        raise NotImplementedError

    def search_text(self, row: Any) -> str:
        return " ".join(str(c) for c in self.cells(row, None, None))  # type: ignore[arg-type]

    def fetch_extra(self, ctx: Ctx) -> Any:
        """Cheap local data fetched alongside rows (e.g. which specs exist)."""
        return None

    def summary(self, rows: list[Any]) -> str:
        """Extra markup shown in the title bar (e.g. totals)."""
        return ""

    @property
    def crumb(self) -> str:
        return self.name


_COMMON_TAIL = (
    Action("d", "Describe", "describe"),
    Action("y", "YAML", "yaml"),
)


class ClustersView(View):
    name = "clusters"
    title = "Clusters"
    aliases = ("cl", "cluster", "clusters", "status")
    hotkey = "1"
    spec_kind = SpecKind.CLUSTER
    columns = (
        Column("NAME"), Column("STATUS"), Column("INFRA", narrow=False), Column("RESOURCES"),
        Column("GPU-HRS", narrow=False), Column("$/HR", narrow=False),
        Column("AUTOSTOP", narrow=False), Column("USER", narrow=False), Column("AGE"), Column("SPEC", narrow=False),
    )
    actions = (
        Action("c", "Create", "create"),
        Action("enter", "Jobs", "drill"),
        Action("l", "Logs", "logs"),
        Action("s", "Shell", "shell"),
        Action("x", "Exec", "exec"),
        Action("p", "Stop", "stop"),
        Action("u", "Start", "start"),
        Action("a", "Autostop", "autostop"),
        Action("ctrl+d", "Down", "down", danger=True),
        *_COMMON_TAIL,
        Action("e", "Edit", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("R", "Relaunch", "relaunch"),
        Action("i", "Import spec", "import_spec", hint=False),
    )
    empty_hint = "No clusters. Press [b]c[/b] to create one."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return sorted(ctx.backend.list_clusters(), key=lambda r: r.name)

    COST_TTL = 30.0  # seconds; cost data changes slowly, don't fetch it every tick

    def __init__(self) -> None:
        self._costs: dict[str, Any] = {}
        self._costs_at = 0.0

    def fetch_extra(self, ctx: Ctx) -> Any:
        if time.monotonic() - self._costs_at > self.COST_TTL:
            try:
                self._costs = {c.name: c for c in ctx.backend.list_costs(days=None) if c.status != "TERMINATED"}
            except Exception:  # noqa: BLE001 - cost columns are best-effort
                self._costs = {}
            self._costs_at = time.monotonic()
        return {"specs": ctx.store.names(SpecKind.CLUSTER), "costs": self._costs}

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        extra = extra or {}
        cost = extra.get("costs", {}).get(r.name)
        return [
            Text(r.name, style="bold"), status_text(r.status), r.infra, r.resources,
            gpu_hours_text(cost), hourly_text(cost),
            autostop_text(r.autostop, r.to_down), r.user, age(r.launched_at), mark(r.name in extra.get("specs", ())),
        ]


class ClusterJobsView(View):
    name = "jobs@cluster"
    title = "Jobs"
    columns = (
        Column("ID"), Column("NAME"), Column("STATUS"), Column("SUBMITTED"), Column("DURATION"),
        Column("RESOURCES", narrow=False), Column("USER", narrow=False),
    )
    actions = (
        Action("l", "Logs", "logs"),
        Action("enter", "Logs", "logs", hint=False),
        Action("ctrl+d", "Cancel", "cancel", danger=True),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No jobs on this cluster. Press [b]esc[/b] then [b]x[/b] to run a command."

    def __init__(self, cluster: str) -> None:
        self.cluster = cluster

    @property
    def crumb(self) -> str:
        return f"jobs@{self.cluster}"

    def fetch(self, ctx: Ctx) -> list[Any]:
        return sorted(ctx.backend.list_cluster_jobs(self.cluster), key=lambda r: -r.job_id)

    def key(self, row: Any) -> str:
        return str(row.job_id)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        end = r.end_at
        dur = duration((end or time.time()) - r.start_at) if r.start_at else "-"
        return [str(r.job_id), r.name, status_text(r.status), age(r.submitted_at), dur, r.resources, r.user]


class ManagedJobsView(View):
    name = "jobs"
    title = "Managed jobs"
    aliases = ("mj", "job", "jobs", "managed-jobs")
    hotkey = "2"
    spec_kind = SpecKind.JOB
    columns = (
        Column("ID"), Column("NAME"), Column("STATUS"), Column("RESOURCES", narrow=False), Column("INFRA", narrow=False),
        Column("SUBMITTED"), Column("DURATION"), Column("RECOVERIES", narrow=False), Column("USER", narrow=False),
    )
    actions = (
        Action("c", "Create", "create"),
        Action("l", "Logs", "logs"),
        Action("enter", "Logs", "logs", hint=False),
        Action("L", "Controller logs", "controller_logs"),
        Action("ctrl+d", "Cancel", "cancel", danger=True),
        *_COMMON_TAIL,
        Action("e", "Edit", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("R", "Resubmit", "relaunch"),
        Action("i", "Import spec", "import_spec", hint=False),
    )
    empty_hint = "No managed jobs. Press [b]c[/b] to submit one."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_managed_jobs()

    def key(self, row: Any) -> str:
        return str(row.job_id)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [
            str(r.job_id), Text(r.name, style="bold"), status_text(r.status), r.resources, r.infra,
            age(r.submitted_at), duration(r.duration), str(r.recoveries), r.user,
        ]


class ServicesView(View):
    name = "services"
    title = "Services"
    aliases = ("svc", "serve", "service", "services")
    hotkey = "3"
    spec_kind = SpecKind.SERVICE
    columns = (
        Column("NAME"), Column("STATUS"), Column("READY"), Column("VERSIONS", narrow=False),
        Column("ENDPOINT"), Column("UPTIME", narrow=False), Column("SPEC", narrow=False),
    )
    actions = (
        Action("c", "Create", "create"),
        Action("enter", "Replicas", "drill"),
        Action("l", "LB logs", "logs"),
        Action("L", "Controller logs", "controller_logs"),
        Action("ctrl+d", "Down", "down", danger=True),
        *_COMMON_TAIL,
        Action("e", "Edit+update", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("R", "Rolling update", "relaunch"),
        Action("i", "Import spec", "import_spec", hint=False),
    )
    empty_hint = "No services. Press [b]c[/b] to deploy one."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return sorted(ctx.backend.list_services(), key=lambda r: r.name)

    def fetch_extra(self, ctx: Ctx) -> Any:
        return ctx.store.names(SpecKind.SERVICE)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [
            Text(r.name, style="bold"), status_text(r.status), f"{r.ready_replicas}/{r.total_replicas}", r.versions,
            r.endpoint or "-", age(r.uptime), mark(bool(extra and r.name in extra)),
        ]


class ReplicasView(View):
    name = "replicas"
    title = "Replicas"
    columns = (
        Column("ID"), Column("STATUS"), Column("VERSION"), Column("INFRA", narrow=False),
        Column("RESOURCES", narrow=False), Column("ENDPOINT"), Column("AGE"),
    )
    actions = (
        Action("l", "Logs", "logs"),
        Action("enter", "Logs", "logs", hint=False),
        Action("ctrl+d", "Terminate", "terminate", danger=True),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No replicas yet."

    def __init__(self, service: str) -> None:
        self.service = service

    @property
    def crumb(self) -> str:
        return f"replicas@{self.service}"

    def fetch(self, ctx: Ctx) -> list[Any]:
        for s in ctx.backend.list_services():
            if s.name == self.service:
                return list(s.replicas)
        return []

    def key(self, row: Any) -> str:
        return str(row.replica_id)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [str(r.replica_id), status_text(r.status), str(r.version or "-"), r.infra, r.resources, r.endpoint or "-", age(r.launched_at)]


class VolumesView(View):
    name = "volumes"
    title = "Volumes"
    aliases = ("vol", "volume", "volumes", "pvc")
    hotkey = "5"
    spec_kind = SpecKind.VOLUME
    columns = (
        Column("NAME"), Column("TYPE"), Column("INFRA", narrow=False), Column("SIZE"), Column("STATUS"),
        Column("USED BY", narrow=False), Column("AGE"), Column("SPEC", narrow=False),
    )
    actions = (
        Action("c", "Create", "create"),
        Action("ctrl+d", "Delete", "down", danger=True),
        *_COMMON_TAIL,
        Action("e", "Edit", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("R", "Re-apply", "relaunch"),
        Action("i", "Import spec", "import_spec", hint=False),
    )
    empty_hint = "No volumes. Press [b]c[/b] to create one."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return sorted(ctx.backend.list_volumes(), key=lambda r: r.name)

    def fetch_extra(self, ctx: Ctx) -> Any:
        return ctx.store.names(SpecKind.VOLUME)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [
            Text(r.name, style="bold"), r.type, r.infra, r.size, status_text(r.status),
            ", ".join(r.used_by) or "-", age(r.launched_at), mark(bool(extra and r.name in extra)),
        ]


class SpecsView(View):
    name = "specs"
    title = "Spec library"
    aliases = ("sp", "spec", "specs", "yaml", "lib", "library")
    hotkey = "8"
    columns = (Column("KIND"), Column("NAME"), Column("VALID"), Column("SUMMARY", narrow=False), Column("MODIFIED"), Column("PATH", narrow=False))
    actions = (
        Action("enter", "Launch/apply", "launch_spec"),
        Action("c", "Create", "create"),
        Action("e", "Edit", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("y", "View", "yaml"),
        Action("D", "Duplicate", "duplicate"),
        Action("ctrl+d", "Delete file", "delete_spec", danger=True),
        Action("o", "Show path", "path"),
    )
    empty_hint = "No specs yet. They are saved here whenever you create something (or press [b]c[/b])."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.store.list()

    def key(self, row: Any) -> str:
        return f"{row.kind.value}/{row.name}"

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        from pathlib import Path

        valid = Text("yes", style="green") if r.spec else Text(f"no: {r.error}", style="red")
        summary = "-"
        if r.spec is not None:
            res = getattr(r.spec, "resources", None)
            summary = res.summary() if res is not None else f"{r.spec.type} {r.spec.size}"
            if getattr(r.spec, "pool", None):
                summary += f" → pool {r.spec.pool}"
            if getattr(r.spec, "workers", None):
                summary += f", {r.spec.workers} worker(s)"
        try:
            path = "~/" + str(r.path.relative_to(Path.home()))
        except ValueError:
            path = str(r.path)
        return [r.kind.value, Text(r.name, style="bold"), valid, summary, age(r.modified), path]


class RequestsView(View):
    name = "requests"
    title = "API requests"
    aliases = ("req", "request", "requests", "api")
    hotkey = "9"
    columns = (Column("ID"), Column("NAME"), Column("STATUS"), Column("CLUSTER"), Column("USER", narrow=False), Column("CREATED"), Column("TOOK", narrow=False))
    actions = (
        Action("enter", "Follow logs", "logs"),
        Action("l", "Logs", "logs", hint=False),
        Action("ctrl+d", "Cancel", "cancel", danger=True),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No API requests."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_requests()

    def key(self, row: Any) -> str:
        return row.request_id

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        took = duration(r.finished_at - r.created_at) if r.finished_at and r.created_at else "-"
        return [r.request_id[:13], r.name, status_text(r.status), r.cluster or "-", r.user, age(r.created_at), took]


class InfraView(View):
    name = "infra"
    title = "Enabled infra"
    aliases = ("inf", "infra", "clouds", "check")
    hotkey = "0"
    columns = (Column("INFRA"), Column("ENABLED"), Column("DETAIL"))
    actions = ()
    empty_hint = "No infra enabled on the API server. Run `sky check` on the server."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_infra()

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [Text(r.name, style="bold"), mark(r.enabled), r.detail]


class PoolsView(View):
    name = "pools"
    title = "Job pools"
    aliases = ("pool", "pools", "pl")
    hotkey = "4"
    spec_kind = SpecKind.POOL
    columns = (Column("NAME"), Column("STATUS"), Column("WORKERS"), Column("RESOURCES", narrow=False), Column("SPEC", narrow=False))
    actions = (
        Action("c", "Create", "create"),
        Action("enter", "Workers", "drill"),
        Action("l", "Controller logs", "logs"),
        Action("ctrl+d", "Down", "down", danger=True),
        *_COMMON_TAIL,
        Action("e", "Edit+apply", "edit"),
        Action("E", "Edit YAML", "edit_raw"),
        Action("R", "Re-apply", "relaunch"),
        Action("i", "Import spec", "import_spec", hint=False),
    )
    empty_hint = "No pools. A pool keeps workers warm so managed jobs start instantly — press [b]c[/b] to create one."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return sorted(ctx.backend.list_pools(), key=lambda r: r.name)

    def fetch_extra(self, ctx: Ctx) -> Any:
        return ctx.store.names(SpecKind.POOL)

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [Text(r.name, style="bold"), status_text(r.status), f"{r.ready_workers}/{r.total_workers}", r.resources,
                mark(bool(extra and r.name in extra))]


class PoolWorkersView(ReplicasView):
    name = "workers"
    title = "Pool workers"
    actions = (
        Action("l", "Logs", "logs"),
        Action("enter", "Logs", "logs", hint=False),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No workers yet."

    @property
    def crumb(self) -> str:
        return f"workers@{self.service}"

    def fetch(self, ctx: Ctx) -> list[Any]:
        for p in ctx.backend.list_pools():
            if p.name == self.service:
                return list(p.workers)
        return []


class StorageView(View):
    name = "storage"
    title = "Storage buckets"
    aliases = ("st", "storage", "bucket", "buckets")
    hotkey = "6"
    columns = (Column("NAME"), Column("STORES"), Column("STATUS"), Column("AGE"), Column("LAST USE", narrow=False))
    actions = (
        Action("ctrl+d", "Delete bucket", "down", danger=True),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No SkyPilot-managed buckets. (Buckets appear here when a task's file_mounts creates one.)"

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_storage()

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [Text(r.name, style="bold"), r.stores, status_text(r.status), age(r.launched_at), r.last_use]


COST_WINDOWS: tuple[int | None, ...] = (1, 7, 30, None)


class CostView(View):
    name = "cost"
    title = "Usage & cost"
    aliases = ("cost", "costs", "usage", "$", "spend")
    hotkey = "7"
    columns = (
        Column("NAME"), Column("STATUS"), Column("INFRA", narrow=False), Column("RESOURCES", narrow=False),
        Column("UP"), Column("GPU-HRS"), Column("$/HR"), Column("TOTAL $"), Column("USER", narrow=False),
    )
    actions = (
        Action("t", "Time window", "window"),
        Action("d", "Describe", "describe"),
    )
    empty_hint = "No cluster usage recorded in this window. Press [b]t[/b] to widen it."

    def __init__(self) -> None:
        self.days: int | None = 30

    @property
    def crumb(self) -> str:
        return f"cost@{self.days}d" if self.days else "cost@all"

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_costs(days=self.days)

    def key(self, row: Any) -> str:
        return f"{row.name}@{row.launched_at}"

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [
            Text(r.name, style="bold"), status_text(r.status), r.infra, r.resources, duration(r.duration),
            gpu_hours_text(r), hourly_text(r), total_text(r), r.user,
        ]

    def summary(self, rows: list[Any]) -> str:
        window = f"last {self.days}d" if self.days else "all time"
        gpu = sum(r.gpu_hours for r in rows)
        priced = [r for r in rows if r.priced]
        unpriced = [r for r in rows if not r.priced]
        spent = sum(r.total or 0 for r in priced)
        parts = [f"{window}", f"[b]{gpu:,.1f}[/b] GPU-hrs", f"[b]${spent:,.2f}[/b] priced"]
        if unpriced:
            parts.append(f"[yellow]{len(unpriced)} unpriced[/yellow] [dim](Kubernetes without kubernetes.pricing)[/dim]")
        return " · ".join(parts)


class WorkspacesView(View):
    name = "workspaces"
    title = "Workspaces"
    aliases = ("ws", "workspace", "workspaces")
    columns = (Column("NAME"), Column("ACTIVE"), Column("WRITABLE"), Column("DETAIL"))
    actions = (Action("enter", "Switch to", "switch_workspace"),)
    empty_hint = "No workspaces visible."

    def fetch(self, ctx: Ctx) -> list[Any]:
        return ctx.backend.list_workspaces()

    def cells(self, r: Any, ctx: Ctx, extra: Any) -> list[Any]:
        return [Text(r.name, style="bold"), mark(r.active), mark(r.writable), r.detail]


def gpu_hours_text(cost: Any) -> Text:
    if cost is None or not cost.gpus:
        return Text("-", style="dim")
    return Text(f"{cost.gpu_hours:,.1f}")


def hourly_text(cost: Any) -> Text:
    if cost is None:
        return Text("-", style="dim")
    if not cost.priced:
        return Text("unpriced", style="dim yellow")
    return Text(f"{cost.hourly:,.2f}")


def total_text(cost: Any) -> Text:
    if not cost.priced:
        return Text("unpriced", style="dim yellow")
    style = "bold red" if cost.total >= 100 else ("yellow" if cost.total >= 10 else "")
    return Text(f"{cost.total:,.2f}", style=style)


TOP_VIEWS: list[type[View]] = [
    ClustersView, ManagedJobsView, ServicesView, PoolsView, VolumesView, StorageView, CostView, SpecsView,
    RequestsView, InfraView, WorkspacesView,
]


def view_for_command(command: str) -> type[View] | None:
    command = command.strip().lower().lstrip(":")
    for v in TOP_VIEWS:
        if command == v.name or command in v.aliases:
            return v
    return None


def all_aliases() -> list[str]:
    out: list[str] = []
    for v in TOP_VIEWS:
        out.append(v.name)
        out.extend(a for a in v.aliases if a != v.name)
    return out + ["quit", "q", "help"]
