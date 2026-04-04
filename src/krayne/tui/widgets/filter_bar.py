"""Multi-attribute filter bar for the cluster explorer."""

from __future__ import annotations

from textual.containers import Container
from textual.message import Message
from textual.widgets import Input

from krayne.api.types import ClusterInfo


class FilterBar(Container):
    """Filter input that supports name search and attribute prefixes.

    Prefixed filters:
        status:ready    — match by status
        ns:production   — match by namespace
        service:notebook — match by service availability
        tunnel:active   — match by tunnel state

    Plain text matches cluster name (fuzzy substring).
    """

    DEFAULT_CSS = """
    FilterBar {
        dock: bottom;
        height: auto;
        display: none;
    }
    FilterBar.visible {
        display: block;
    }
    """

    class Changed(Message):
        """Posted when filter text changes."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._filter_text: str = ""

    def compose(self):
        yield Input(
            placeholder="Filter: name or status:ready ns:prod service:notebook tunnel:active",
            id="filter-input",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._filter_text = event.value
            self.post_message(FilterBar.Changed(self._filter_text))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            self.remove_class("visible")

    @property
    def filter_text(self) -> str:
        return self._filter_text

    def clear(self) -> None:
        self._filter_text = ""
        try:
            self.query_one("#filter-input", Input).value = ""
        except Exception:
            pass

    def focus_input(self) -> None:
        try:
            self.query_one("#filter-input", Input).focus()
        except Exception:
            pass


def make_filter_fn(
    text: str,
    tunnel_states: dict[str, bool] | None = None,
) -> callable | None:
    """Build a filter function from filter bar text.

    Returns None if no filtering should be applied.
    """
    text = text.strip()
    if not text:
        return None

    # Parse prefixed filters
    parts = text.split()
    name_parts: list[str] = []
    status_filter: str | None = None
    ns_filter: str | None = None
    service_filter: str | None = None
    tunnel_filter: str | None = None

    for part in parts:
        if part.startswith("status:"):
            status_filter = part[7:].lower()
        elif part.startswith("ns:"):
            ns_filter = part[3:].lower()
        elif part.startswith("service:"):
            service_filter = part[8:].lower()
        elif part.startswith("tunnel:"):
            tunnel_filter = part[7:].lower()
        else:
            name_parts.append(part.lower())

    name_query = " ".join(name_parts) if name_parts else None
    tunnel_states = tunnel_states or {}

    def _matches(cluster: ClusterInfo) -> bool:
        if name_query and name_query not in cluster.name.lower():
            return False
        if status_filter and status_filter not in cluster.status.lower():
            return False
        if ns_filter and ns_filter not in cluster.namespace.lower():
            return False
        if service_filter:
            available = []
            if cluster.dashboard_url:
                available.extend(["dashboard", "dash"])
            if cluster.notebook_url:
                available.extend(["notebook", "nb"])
            if cluster.client_url:
                available.extend(["client", "ray"])
            if cluster.code_server_url:
                available.extend(["code-server", "code"])
            if cluster.ssh_url:
                available.extend(["ssh"])
            if not any(service_filter in s for s in available):
                return False
        if tunnel_filter:
            key = f"{cluster.namespace}/{cluster.name}"
            is_active = tunnel_states.get(key, False)
            if tunnel_filter == "active" and not is_active:
                return False
            if tunnel_filter in ("inactive", "none") and is_active:
                return False
        return True

    return _matches
