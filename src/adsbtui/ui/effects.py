"""What the Controller needs from outside the view. ``App`` implements it; tests fake it."""

from __future__ import annotations

from typing import Protocol

from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config


class Effects(Protocol):
    @property
    def watch_entries(self) -> list[watchlist_mod.WatchEntry]: ...

    def refresh_now(self) -> None:
        """Wake the fetch loop so a changed filter shows on the next frame."""

    def set_enricher(self) -> None:
        """Rebuild the enrichment chain, after a registry download or a Settings change."""

    def set_dispatcher(self) -> None:
        """Rebuild the alert dispatcher from the current alert settings."""

    def set_watch_entries(self, entries: list[watchlist_mod.WatchEntry]) -> None: ...

    def set_pipeline_cfg(self, cfg: Config) -> None:
        """Adopt ``cfg`` as the running configuration."""

    def save_config(self, cfg: Config, edited: set[tuple[str, str]]) -> str | None:
        """Write the edited settings to the config file; the error text, or None on success."""
