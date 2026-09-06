"""Serialise a Config back out to TOML.

tomllib (stdlib, 3.11+) only *reads* TOML, and this project takes no third-party
dependencies, so writing a config file means writing the emitter too. That is only
tractable because the schema is closed and tiny: config.Config is a flat tree of exactly
nine dataclass sections whose field types are str, bool, int, float, list[str] and
list[int] -- no nested tables, no arrays of tables, no dates. Every literal form TOML
needs beyond that is deliberately unimplemented rather than half-implemented: a value of
an unexpected type raises TypeError instead of being coerced into something that would
round-trip wrong.

The contract this module guarantees is round-tripping: for any Config this writes,
tomllib.load() of the result parses back to the same values, and config.build_config() of
that dict rebuilds an equal Config. That is what makes it safe for the settings screen and
the setup wizard to hand a user's edits straight to dump_config().

Writes are atomic. dump_config() renders the whole file into a temp file in the *same*
directory as the target (so os.replace() stays within one filesystem and is therefore a
real atomic rename) and only then replaces the target. A crash, a full disk, or a SIGKILL
mid-write leaves the user's previous config intact rather than a truncated file that would
fail to parse on next start -- the one failure mode that would lock a user out of their own
tool. dump_config_str() exposes the same rendering with no filesystem involvement, for
tests and for a future --print-config.
"""

from __future__ import annotations

import contextlib
import math
import os
import tempfile
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from adsbtui.config import Config

#: Two comment lines that head the generated file, so someone opening it in an editor
#: knows what wrote it and that hand-edits are fine.
_FILE_HEADER = (
    "# adsbtui configuration.",
    "# Written by adsbtui; safe to hand-edit. See the README for every key's meaning.",
)

#: section name -> the one-line comment written directly above its [section] header.
_SECTION_COMMENTS: dict[str, str] = {
    "source": "Where aircraft.json comes from, and how often to poll it.",
    "home": "Your receiver/home coordinates; every distance and bearing is measured from here.",
    "filter": "Which aircraft make it into the table.",
    "display": "Table layout, units, colors, and sort order.",
    "alerts": "What counts as an alert, and how alerts are delivered.",
    "watchlist": "The watchlist file and how matches are treated.",
    "registry": "Optional FAA registry CSV used to fill in the owner column.",
    "history": "Optional SQLite database of closed tracks.",
    "logging": "Log file location, level, and rotation.",
}

#: Characters TOML basic strings must escape with a short escape rather than a \\uXXXX one.
_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _quote(text: str) -> str:
    """Render a str as a TOML basic string, escaping what TOML requires be escaped.

    Backslash and double quote get their short escapes (which is what makes Windows paths
    and quoted text survive the round trip), as do the five whitespace/control characters
    TOML names; every other control character falls back to the \\uXXXX form. Everything
    else -- including non-ASCII -- is written literally, since TOML files are UTF-8.
    """
    out = []
    for ch in text:
        short = _SHORT_ESCAPES.get(ch)
        if short is not None:
            out.append(short)
        elif ch < " " or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _format_float(value: float) -> str:
    """Render a float so tomllib reads back the identical value.

    repr() is exact for finite floats, but a whole number reprs as e.g. '15.0' only
    because Python says so -- an int-valued float that somehow reprs without a '.' or an
    exponent would come back from tomllib as an *int*, so a '.0' is appended in that case
    to keep the type stable. Non-finite values use TOML's own inf/nan literals.
    """
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    text = repr(float(value))
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def _format_value(value: Any) -> str:
    """Render one scalar or list value as a TOML literal.

    bool is checked before int on purpose: bool is a subclass of int in Python, and
    writing 'true' as '1' would come back from tomllib as an int and break the round trip.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} value {value!r} as TOML")


def dump_config_str(cfg: Config) -> str:
    """Render a Config as the full text of a TOML config file.

    Sections come out in the order they are declared on Config, each preceded by its
    one-line comment; keys within a section come out in their dataclass declaration order.
    The private bookkeeping attributes load_config() stamps on a Config (_args,
    _config_path) are skipped -- they are not part of the file schema.
    """
    lines: list[str] = list(_FILE_HEADER)

    for section_field in fields(cfg):
        if section_field.name.startswith("_"):
            continue
        section = getattr(cfg, section_field.name)
        if not is_dataclass(section):
            continue

        lines.append("")
        comment = _SECTION_COMMENTS.get(section_field.name)
        if comment:
            lines.append(f"# {comment}")
        lines.append(f"[{section_field.name}]")
        for value_field in fields(section):
            value = getattr(section, value_field.name)
            lines.append(f"{value_field.name} = {_format_value(value)}")

    lines.append("")
    return "\n".join(lines)


def dump_config(cfg: Config, path: str | os.PathLike[str]) -> None:
    """Write a Config to path as TOML, atomically, creating parent directories.

    A leading '~' in path is expanded, so the value of a config key like
    watchlist.path can be handed straight to this function. The write goes to a temp file
    beside the target and is fsync'd before os.replace() swaps it in, so an interrupted
    write can never leave a truncated config behind; on any failure the temp file is
    removed and the original target is untouched.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_config_str(cfg)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
