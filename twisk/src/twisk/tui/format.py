"""Small rendering helpers shared by views and screens."""

from __future__ import annotations

import time

from rich.text import Text

_GOOD = {"UP", "READY", "RUNNING", "SUCCEEDED", "IN_USE", "HEALTHY", "ENABLED", "NO_REPLICA"}
_BUSY = {
    "INIT", "PENDING", "STARTING", "PROVISIONING", "CONTROLLER_INIT", "REPLICA_INIT", "RECOVERING",
    "CANCELLING", "SETTING_UP", "SHUTTING_DOWN", "SUBMITTED", "NOT_READY", "DEPRECATED", "WAITING",
    "ALIVE", "CREATING", "DELETING", "UPDATING", "SCHEDULED",
}
_DIM = {"STOPPED", "CANCELLED", "TERMINATED", "PREEMPTED", "-", ""}


def status_text(status: str) -> Text:
    s = (status or "-").upper()
    if s in _GOOD:
        style = "bold green"
    elif s in _BUSY:
        style = "bold yellow"
    elif s.startswith("FAILED") or s in ("ERROR", "UNHEALTHY", "CONTROLLER_FAILED"):
        style = "bold red"
    elif s in _DIM:
        style = "dim"
    else:
        style = "cyan"
    return Text(status or "-", style=style)


def age(ts: float | int | None, now: float | None = None) -> str:
    if not ts:
        return "-"
    now = now if now is not None else time.time()
    return duration(max(0.0, now - float(ts)))


def duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if m < 10 else f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m" if h < 10 else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d{h}h" if d < 10 else f"{d}d"


def autostop_text(minutes: int, to_down: bool) -> str:
    if minutes is None or minutes < 0:
        return "-"
    return f"{minutes}m{' (down)' if to_down else ''}"


def mark(flag: bool) -> Text:
    return Text("✓", style="green") if flag else Text("-", style="dim")
