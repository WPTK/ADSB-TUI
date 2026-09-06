"""The Help screen: a dismiss-on-any-key overlay listing every keybinding.

HelpScreen follows the Screen design contract used across ui/screens/: a read-only
``title``, a pure ``render_lines(width, height)`` that returns plain, already-clipped
strings with no curses calls, and a ``handle_key(key)`` that reports navigation intent
back to the caller as a short string ("close" here, since any key dismisses this overlay).
"""

from __future__ import annotations

from collections.abc import Mapping


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width]


def _action_label(action: str) -> str:
    """Turn a keymap action name (e.g. "watchlist") into a display label ("Watchlist")."""
    return action.replace("_", " ").title()


class HelpScreen:
    """Displays the full keybinding reference. Any keypress closes it.

    keymap maps action name -> key label (e.g. {"help": "F1", "quit": "F10 / q"}). The
    order of entries in render_lines follows the order the keymap was given in, so callers
    control the display order by the order they build the dict in.
    """

    def __init__(self, keymap: Mapping[str, str], version: str) -> None:
        self._keymap: dict[str, str] = dict(keymap)
        self._version = version

    @property
    def title(self) -> str:
        return "Help"

    @property
    def keymap(self) -> dict[str, str]:
        return dict(self._keymap)

    @property
    def version(self) -> str:
        return self._version

    def handle_key(self, key: int) -> str | None:
        """Any key at all dismisses this overlay."""
        return "close"

    def render_lines(self, width: int, height: int) -> list[str]:
        if height <= 0 or width <= 0:
            return []

        key_col = max((len(label) for label in self._keymap.values()), default=0)
        lines: list[str] = []
        for action, key_label in self._keymap.items():
            line = f"{key_label:<{key_col}}  {_action_label(action)}"
            lines.append(_clip(line, width))

        lines.append("")
        lines.append(_clip(f"Version: {self._version}", width))
        lines.append("")
        lines.append(_clip("Press any key to close", width))

        return lines[:height]
