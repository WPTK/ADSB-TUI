"""The Data screen: the in-TUI front end for the local aircraft-registry databases.

Everything the enrichment layer knows about an aircraft that is not derivable from its
ICAO address alone comes out of a registry the user has to obtain, and until now obtaining
one meant reading the docs and running a command. This screen is that command with a face:
it lists every configured source, says whether it is on disk, how many aircraft it holds
and how long ago it was fetched, warns when that is old enough to be misleading, and
downloads a fresh copy on a keypress.

Two things drive the design:

  * Updates run on a BACKGROUND THREAD. Fetching 8 MB and rebuilding a SQLite table takes
    tens of seconds; doing it inside handle_key() would wedge the event loop and freeze
    the map, the table and the clock along with it -- the same reason the feed fetch has
    its own thread. The screen owns the thread, a progress value written from it under a
    lock, and a list of finished outcomes the caller drains with take_finished() so it
    knows when to reload its Enricher against the rebuilt database.
  * Licensing is shown next to the source, not buried in a README. tar1090-db is
    NON-COMMERCIAL USE ONLY and the FAA extract is public domain; a user deciding whether
    to download something is exactly the person who needs to read the terms, and this is
    the screen where that decision is made.

The updater itself is injected (defaulting to registry_update's real entry points), which
is what lets the tests drive the whole thing -- progress, refusal, completion, failure --
with no network and no terminal. curses is imported only for its KEY_* integer constants,
which is safe without an initialized screen.

Keys: Up/Down select a source, 'u' updates the selected one, 'U' updates every
downloadable one in turn, Esc closes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from adsbtui.enrich.registry_update import (
    FAA_URL,
    SOURCE_FAA,
    SOURCE_TAR1090,
    TAR1090_URL,
    BuildResult,
    ProgressFn,
    RegistryError,
    SourceMeta,
    build_from_faa_zip,
    build_from_tar1090,
    read_meta,
)
from adsbtui.ui.widgets import ListPicker, ProgressBar

_ESCAPE_KEY = 27
_UPDATE_KEY = ord("u")
_UPDATE_ALL_KEY = ord("U")

#: The legacy user-supplied CSV. It is not a row in the meta table -- nothing fetched it,
#: so its "age" is the file's mtime and its size is however many lines it has.
SOURCE_CSV = "csv"

#: How wide the progress bar is drawn, terminal permitting. A full-width bar reads as a
#: loading screen; a short one reads as one line of status among several.
_BAR_WIDTH = 40

SECONDS_PER_DAY = 86400.0

#: Past this, a registry is old enough that a missing registration or a stale owner is
#: more likely the database's fault than the aircraft's. Overridable per screen.
DEFAULT_STALE_AFTER_DAYS = 30.0

#: (source, db_path, progress_fn) -> BuildResult. The screen never calls the registry
#: builders directly; it calls one of these, so tests can substitute a fake.
UpdateFn = Callable[[str, str, ProgressFn], BuildResult]


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


@dataclass(frozen=True)
class SourceSpec:
    """One registry source as the screen presents it.

    'license' is user-facing text shown on the source's own row, and 'can_update' is
    whether this screen is able to go and fetch it (the legacy CSV is a file the user
    points at, so it is listed but never downloaded).
    """

    key: str
    label: str
    license: str
    can_update: bool


TAR1090_SPEC = SourceSpec(
    key=SOURCE_TAR1090,
    label="tar1090-db",
    license="non-commercial use only",
    can_update=True,
)
FAA_SPEC = SourceSpec(
    key=SOURCE_FAA,
    label="FAA",
    license="public domain",
    can_update=True,
)
CSV_SPEC = SourceSpec(
    key=SOURCE_CSV,
    label="legacy CSV",
    license="your own file, not fetched by adsbtui",
    can_update=False,
)


@dataclass(frozen=True)
class LocalFile:
    """What a plain file on disk can tell us: is it there, how big, how old."""

    exists: bool
    row_count: int | None = None
    modified_at: float | None = None


@dataclass(frozen=True)
class SourceStatus:
    """A source's spec joined to what is actually on disk right now."""

    spec: SourceSpec
    present: bool
    row_count: int | None = None
    age_s: float | None = None
    stale: bool = False


@dataclass(frozen=True)
class UpdateOutcome:
    """The result of one finished update, handed to the caller by take_finished().

    ok=False means the build failed and the database on disk was left untouched, so the
    caller has nothing to reload; 'message' is already phrased for display.
    """

    source: str
    ok: bool
    row_count: int | None
    message: str
    not_modified: bool = False


def format_age(seconds: float | None) -> str:
    """Render an age in seconds the way a person would say it out loud."""
    if seconds is None:
        return "never"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return _plural(int(seconds // 60), "minute")
    if seconds < SECONDS_PER_DAY:
        return _plural(int(seconds // 3600), "hour")
    return _plural(int(seconds // SECONDS_PER_DAY), "day")


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit} ago" if count == 1 else f"{count} {unit}s ago"


def _rows_text(count: int | None) -> str:
    return "present" if count is None else f"{count:,} rows"


def format_status(status: SourceStatus) -> str:
    """One aligned line: what the source is, how much of it there is, how old, its terms."""
    if status.present:
        detail = _rows_text(status.row_count)
        age = format_age(status.age_s)
        if status.stale:
            age += " (stale)"
    else:
        detail = "not downloaded"
        age = ""
    return f"{status.spec.label:<11} {detail:<16} {age:<20} {status.spec.license}"


def probe_csv(path: str) -> LocalFile:
    """Presence, data-row count and mtime of a legacy registry CSV.

    Rows are counted by streaming the file and counting newlines rather than parsing it:
    the screen only needs the size of the thing, and one sequential read is cheap enough
    to do whenever the listing is refreshed. A file we can stat but not read still counts
    as present -- that is a permissions problem worth showing, not a missing source.
    """
    if not path:
        return LocalFile(exists=False)
    expanded = os.path.expanduser(path)
    try:
        stat = os.stat(expanded)
    except OSError:
        return LocalFile(exists=False)

    newlines = 0
    last_byte = b"\n"
    try:
        with open(expanded, "rb") as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    break
                newlines += chunk.count(b"\n")
                last_byte = chunk[-1:]
    except OSError:
        return LocalFile(exists=True, row_count=None, modified_at=stat.st_mtime)

    # A final line with no trailing newline still counts; the header line does not.
    lines = newlines + (1 if last_byte != b"\n" else 0)
    return LocalFile(exists=True, row_count=max(0, lines - 1), modified_at=stat.st_mtime)


def default_updater(source: str, db_path: str, progress_fn: ProgressFn) -> BuildResult:
    """Fetch one source from its canonical URL into db_path (the real, networked path)."""
    if source == SOURCE_TAR1090:
        return build_from_tar1090(TAR1090_URL, db_path, progress_fn)
    if source == SOURCE_FAA:
        return build_from_faa_zip(FAA_URL, db_path, progress_fn)
    raise RegistryError(f"{source} is not a downloadable registry source")


class DataScreen:
    """Lists the registry sources and updates them on a background thread.

    db_path is the SQLite registry the builders write to; csv_path is the optional legacy
    registry CSV (an empty string simply leaves that row off the list). meta_reader and
    csv_probe exist so the listing can be driven from fakes in tests; updater is the one
    injection that matters in normal use, since it is the only thing here that would
    otherwise touch the network.
    """

    def __init__(
        self,
        db_path: str,
        csv_path: str = "",
        *,
        stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
        updater: UpdateFn | None = None,
        meta_reader: Callable[[str], dict[str, SourceMeta]] = read_meta,
        csv_probe: Callable[[str], LocalFile] = probe_csv,
    ) -> None:
        self._db_path = os.path.expanduser(db_path)
        self._csv_path = csv_path
        self._stale_after_s = max(0.0, stale_after_days) * SECONDS_PER_DAY
        self._updater: UpdateFn = updater if updater is not None else default_updater
        self._meta_reader = meta_reader
        self._csv_probe = csv_probe

        self._specs: list[SourceSpec] = [TAR1090_SPEC, FAA_SPEC]
        if csv_path:
            self._specs.append(CSV_SPEC)

        # An RLock, not a Lock: render_lines() takes it to snapshot the update state and
        # then calls the picker, whose labeler reads the statuses under the same lock.
        self._lock = threading.RLock()
        self._statuses: dict[str, SourceStatus] = {}
        self._progress = ProgressBar(0.0)
        self._message = ""
        self._error = ""
        self._notice = ""
        self._running: str | None = None
        self._finished: list[UpdateOutcome] = []
        self._thread: threading.Thread | None = None

        self._picker = ListPicker(
            self._specs,
            visible_rows=max(1, len(self._specs)),
            labeler=self._label_for,
        )
        self.refresh()

    # ----------------------------------------------------------------------------------
    # Listing
    # ----------------------------------------------------------------------------------

    @property
    def title(self) -> str:
        return "Data"

    @property
    def specs(self) -> list[SourceSpec]:
        return list(self._specs)

    @property
    def selected_spec(self) -> SourceSpec | None:
        cursor = self._picker.cursor
        if 0 <= cursor < len(self._specs):
            return self._specs[cursor]
        return None

    def status_for(self, source: str) -> SourceStatus | None:
        with self._lock:
            return self._statuses.get(source)

    def refresh(self) -> None:
        """Re-read what is on disk. Called at construction and after every update run."""
        statuses = self._read_statuses()
        with self._lock:
            self._statuses = statuses

    def _read_statuses(self) -> dict[str, SourceStatus]:
        meta = self._meta_reader(self._db_path)
        now = time.time()
        statuses: dict[str, SourceStatus] = {}
        for spec in self._specs:
            if spec.key == SOURCE_CSV:
                probe = self._csv_probe(self._csv_path)
                age = None if probe.modified_at is None else max(0.0, now - probe.modified_at)
                statuses[spec.key] = SourceStatus(
                    spec=spec,
                    present=probe.exists,
                    row_count=probe.row_count,
                    age_s=age,
                    stale=self._is_stale(age),
                )
                continue
            entry = meta.get(spec.key)
            if entry is None:
                statuses[spec.key] = SourceStatus(spec=spec, present=False)
                continue
            age = entry.age_s(now)
            statuses[spec.key] = SourceStatus(
                spec=spec,
                present=True,
                row_count=entry.row_count,
                age_s=age,
                stale=self._is_stale(age),
            )
        return statuses

    def _is_stale(self, age_s: float | None) -> bool:
        return age_s is not None and age_s >= self._stale_after_s

    def _label_for(self, spec: SourceSpec) -> str:
        with self._lock:
            status = self._statuses.get(spec.key)
        return format_status(status if status is not None else SourceStatus(spec, present=False))

    def _spec_for(self, source: str) -> SourceSpec | None:
        for spec in self._specs:
            if spec.key == source:
                return spec
        return None

    # ----------------------------------------------------------------------------------
    # Updating
    # ----------------------------------------------------------------------------------

    @property
    def is_updating(self) -> bool:
        with self._lock:
            return self._running is not None

    @property
    def running_source(self) -> str | None:
        with self._lock:
            return self._running

    @property
    def progress(self) -> float:
        with self._lock:
            return self._progress.value

    def take_finished(self) -> list[UpdateOutcome]:
        """Drain the updates that have completed since the last call.

        This is how the caller learns the database on disk changed under it and its
        Enricher needs rebuilding; a successful outcome means db_path now holds new rows.
        """
        with self._lock:
            outcomes = self._finished
            self._finished = []
        return outcomes

    def join(self, timeout: float | None = None) -> None:
        """Wait for any in-flight update thread. Used at shutdown, and by the tests."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _start(self, sources: Sequence[str]) -> bool:
        if not sources:
            return False
        with self._lock:
            if self._running is not None:
                self._notice = "an update is already running"
                return False
            first = self._spec_for(sources[0])
            self._running = sources[0]
            self._progress.value = 0.0
            self._message = f"{first.label if first else sources[0]}: starting"
            self._error = ""
            self._notice = ""

        thread = threading.Thread(
            target=self._run_updates,
            args=(list(sources),),
            name="adsbtui-registry-update",
            daemon=True,
        )
        self._thread = thread
        try:
            thread.start()
        except RuntimeError as exc:
            with self._lock:
                self._running = None
                self._error = f"could not start the update thread: {exc}"
            return False
        return True

    def _run_updates(self, sources: list[str]) -> None:
        """The background half: build each source in turn, then re-read the listing."""
        try:
            for source in sources:
                spec = self._spec_for(source)
                label = spec.label if spec is not None else source
                with self._lock:
                    self._running = source
                    self._progress.value = 0.0
                    self._message = f"{label}: starting"
                self._run_one(source, label)
        finally:
            # Refreshing here rather than in the caller means the row counts and ages on
            # screen are correct the moment the bar reaches the end.
            statuses = self._read_statuses()
            with self._lock:
                self._statuses = statuses
                self._running = None

    def _run_one(self, source: str, label: str) -> None:
        try:
            result = self._updater(source, self._db_path, self._on_progress)
        except Exception as exc:
            # A failed download or a corrupt source must land on screen, not take the
            # program down with it: this is running on a thread nobody is catching for.
            detail = str(exc) or exc.__class__.__name__
            message = f"{label}: {detail}"
            with self._lock:
                self._error = message
                self._message = f"{label}: failed"
                self._finished.append(
                    UpdateOutcome(source=source, ok=False, row_count=None, message=message)
                )
            return

        if result.not_modified:
            message = f"{label}: already up to date ({_rows_text(result.row_count)})"
        else:
            message = f"{label}: {_rows_text(result.row_count)}"
        with self._lock:
            self._progress.value = 1.0
            self._message = message
            self._finished.append(
                UpdateOutcome(
                    source=source,
                    ok=True,
                    row_count=result.row_count,
                    message=message,
                    not_modified=result.not_modified,
                )
            )

    def _on_progress(self, fraction: float, message: str) -> None:
        with self._lock:
            self._progress.value = fraction
            self._message = message

    def update_selected(self) -> bool:
        spec = self.selected_spec
        if spec is None:
            return False
        if not spec.can_update:
            with self._lock:
                self._notice = f"{spec.label} is a file you supply; adsbtui cannot fetch it"
            return False
        return self._start([spec.key])

    def update_all(self) -> bool:
        return self._start([spec.key for spec in self._specs if spec.can_update])

    # ----------------------------------------------------------------------------------
    # Screen contract
    # ----------------------------------------------------------------------------------

    def handle_key(self, key: int) -> str | None:
        if key == _ESCAPE_KEY:
            return "close"
        if key == _UPDATE_KEY:
            self.update_selected()
            return None
        if key == _UPDATE_ALL_KEY:
            self.update_all()
            return None
        self._picker.handle_key(key)
        return None

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        lines: list[str] = []
        with self._lock:
            running = self._running
            message = self._message
            error = self._error
            notice = self._notice
            bar = self._progress.render_lines(min(width, _BAR_WIDTH))[0]

            lines.append(_clip("Aircraft registry sources", width))
            lines.append(_clip(f"Database: {self._db_path}", width))
            lines.append("")
            lines.extend(self._picker.render_lines(width))
            lines.append("")

            if running is not None:
                spec = self._spec_for(running)
                label = spec.label if spec is not None else running
                lines.append(_clip(f"Updating {label}: {message}", width))
                lines.append(_clip(bar, width))
            elif message:
                lines.append(_clip(message, width))
                lines.append(_clip(bar, width))

            if error:
                lines.append(_clip(f"Error: {error}", width))
            if notice:
                lines.append(_clip(notice, width))

        lines.append("")
        lines.append(_clip("Up/Down: select  u: update  U: update all  Esc: close", width))
        return lines[:height]
