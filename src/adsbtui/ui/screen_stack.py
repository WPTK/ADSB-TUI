"""Opens screens onto the view and applies what they produced when they close.

No curses: a screen is a plain object that takes keys and renders strings.
"""

from __future__ import annotations

import logging
from typing import Any

from adsbtui import __version__
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import changed_keys
from adsbtui.ui.effects import Effects
from adsbtui.ui.keys import HELP_KEYMAP
from adsbtui.ui.screens.columns import ColumnsScreen
from adsbtui.ui.screens.data import DataScreen, make_updater
from adsbtui.ui.screens.detail import DetailScreen
from adsbtui.ui.screens.filters import FiltersScreen
from adsbtui.ui.screens.help import HelpScreen
from adsbtui.ui.screens.settings import SettingsScreen
from adsbtui.ui.screens.sort import SortScreen
from adsbtui.ui.screens.watchlist import WatchlistScreen
from adsbtui.ui.sorting import SORT_KEYS
from adsbtui.ui.view import ViewState

log = logging.getLogger("adsbtui")


class ScreenStack:
    def __init__(self, view: ViewState, effects: Effects) -> None:
        self.view = view
        self.effects = effects
        #: (section, key) pairs edited in Settings this session; saved with the file layer.
        self.edited: set[tuple[str, str]] = set()

    def open(self, name: str) -> None:
        """Construct and push the named screen. Unknown names are ignored."""
        view = self.view
        cfg = view.cfg
        display = cfg.display
        screen: Any = None
        if name == "help":
            screen = HelpScreen(HELP_KEYMAP, __version__)
        elif name == "detail":
            ac = view.selected()
            if ac is None:
                return
            screen = DetailScreen(ac, display.units)
        elif name == "sort":
            screen = SortScreen(SORT_KEYS, display.sort_key, display.sort_reverse)
        elif name == "filters":
            f = cfg.filter
            screen = FiltersScreen(
                {
                    "hide_ground": f.hide_ground,
                    "include_nonicao": f.include_nonicao,
                    "min_alt_ft": f.min_alt_ft,
                    "max_alt_ft": f.max_alt_ft,
                },
                search_text=view.search_text,
            )
        elif name == "columns":
            screen = ColumnsScreen(display.columns, display.borders)
        elif name == "watchlist":
            screen = WatchlistScreen(self.effects.watch_entries, view.selected())
        elif name == "settings":
            screen = SettingsScreen(cfg)
        elif name == "data":
            screen = DataScreen(
                cfg.registry.db,
                stale_after_days=cfg.registry.max_age_days,
                updater=make_updater(cfg.registry.tar1090_url, cfg.registry.faa_url),
            )
        if screen is not None:
            view.push(name, screen)

    def close(self) -> None:
        """Pop the active screen, applying whatever it produced back onto live config.

        Every screen keeps its own committed result (Escape leaves it untouched), so
        reading result() unconditionally here is safe: a cancelled screen returns exactly
        what it was given.
        """
        popped = self.view.pop()
        if popped is None:
            return
        name, screen = popped

        view = self.view
        cfg = view.cfg
        display = cfg.display
        if name == "sort":
            display.sort_key, display.sort_reverse = screen.result()
        elif name == "filters":
            result = screen.result()
            f = cfg.filter
            f.hide_ground = bool(result.get("hide_ground", f.hide_ground))
            f.include_nonicao = bool(result.get("include_nonicao", f.include_nonicao))
            f.min_alt_ft = int(result.get("min_alt_ft", f.min_alt_ft))
            f.max_alt_ft = int(result.get("max_alt_ft", f.max_alt_ft))
            view.search_text = str(result.get("search_text", view.search_text) or "")
        elif name == "columns":
            result = screen.result()
            display.columns = list(result.get("columns", display.columns))
            display.borders = str(result.get("borders", display.borders))
        elif name == "watchlist":
            entries = list(screen.result())
            self.effects.set_watch_entries(entries)
            if cfg.watchlist.path:
                try:
                    watchlist_mod.save(cfg.watchlist.path, entries)
                except OSError as exc:
                    log.warning("could not save watchlist: %s", exc)
        elif name == "settings":
            self._apply_settings(screen)
        elif name == "data":
            # A finished download changes what the enricher can resolve, so rebuild it
            # rather than leaving the session querying the databases it started with.
            outcomes = screen.take_finished()
            if any(outcome.ok for outcome in outcomes):
                self.effects.set_enricher()
                log.info("registry updated; enrichment reloaded")

    def _apply_settings(self, screen: SettingsScreen) -> None:
        """Adopt an edited config into the running session, and persist it if asked.

        Applying and saving are separate user intents: you may want to try a setting for
        this session without writing it to disk. The screen reports both, and the session
        adopts the change either way.
        """
        if not getattr(screen, "applied", False):
            return
        view = self.view
        old = view.cfg
        new_cfg = screen.result()
        # Carry over the bookkeeping attributes the screen does not own.
        new_cfg._args = old._args
        new_cfg._config_path = old._config_path
        new_cfg._file_layer = old._file_layer
        self.edited |= changed_keys(old, new_cfg)
        view.cfg = new_cfg
        self.effects.set_pipeline_cfg(new_cfg)

        # Settings that feed long-lived objects have to be re-read, not just stored.
        self.effects.set_enricher()
        self.effects.set_dispatcher()

        if getattr(screen, "save_requested", False):
            error = self.effects.save_config(new_cfg, self.edited)
            if error is not None:
                view.flash_for(error)
            else:
                self.edited.clear()
                view.flash_for("settings saved")
