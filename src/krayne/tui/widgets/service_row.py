"""Per-service tunnel control row widget."""

from __future__ import annotations

from functools import partial

from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Static


class ServiceRow(Horizontal):
    """A single service row showing availability, endpoint, and tunnel action."""

    class TunnelAction(Message):
        """Posted when a tunnel action button is pressed."""

        def __init__(self, service: str, action: str) -> None:
            super().__init__()
            self.service = service
            self.action = action  # "open" or "close"

    def __init__(
        self,
        service: str,
        available: bool,
        endpoint: str | None = None,
        tunnel_active: bool = False,
        tunnel_url: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(classes="service-row", **kwargs)
        self._service = service
        self._available = available
        self._endpoint = endpoint
        self._tunnel_active = tunnel_active
        self._tunnel_url = tunnel_url

    def compose(self):
        # Service name
        yield Static(self._service, classes="service-name")

        # Availability status
        if self._available:
            yield Static("[green]available[/green]", classes="service-status")
        else:
            yield Static("[dim]unavailable[/dim]", classes="service-status")

        # Endpoint / tunnel URL
        if self._tunnel_active and self._tunnel_url:
            yield Static(f"[cyan]{self._tunnel_url}[/cyan]", classes="service-endpoint")
        elif self._endpoint:
            yield Static(f"[dim]{self._endpoint}[/dim]", classes="service-endpoint")
        else:
            yield Static("[dim]—[/dim]", classes="service-endpoint")

        # Action button
        if self._available:
            if self._tunnel_active:
                yield Button(
                    "Close Tunnel",
                    variant="warning",
                    classes="service-action",
                    id=f"btn-tunnel-close-{self._service}",
                )
            else:
                yield Button(
                    "Open Tunnel",
                    variant="primary",
                    classes="service-action",
                    id=f"btn-tunnel-open-{self._service}",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("btn-tunnel-open-"):
            self.post_message(ServiceRow.TunnelAction(self._service, "open"))
        elif btn_id.startswith("btn-tunnel-close-"):
            self.post_message(ServiceRow.TunnelAction(self._service, "close"))
