"""File-manager view policy: pure projections over a resident listing.

No Qt, no network, no wall clock (callers pass ``now`` explicitly). One
owner for the popup's view state — paging, sorting, filtering,
searching, column-order merge, selection, attempts aggregation and the
Recents projection — so the QML layer renders only the published page
slice and never recomputes a rule itself.

The author's rulings encoded here (2026-09-10): exactly ONE column is
sorted at a time; unknown values always sort last in both directions;
filters apply FIRST and search narrows WITHIN the filtered subset;
search is global (name + path, token-AND); OR within a filter
category, AND across categories; any sort/filter/search/page-size
change resets the page to 1; the published slice is bounded by the
page size however many rows are resident (round-2 E8).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

# The pinned column order (Snapshot 0): the author's twelve plus Size.
DEFAULT_COLUMN_ORDER = [
    "name", "modified", "size", "attempts", "status", "object_height",
    "layer_height", "estimated_time", "last_print", "slicer",
    "extruder", "bed", "filament",
]

# The TRAILING columns' display-name identity (the QML's column keys),
# the pinned sequence (Snapshot 0), the sticky identity block's keys,
# and the resize band (Snapshot 3's column resize).
TRAILING_COLUMN_ORDER = [
    "Modified", "Size", "Attempts", "Status", "Object height",
    "Layer height", "Est. time", "Last print", "Slicer", "Extruder",
    "Bed", "Filament",
]
STICKY_COLUMNS = ("checkbox", "thumb", "name", "actions")
COLUMN_WIDTH_MIN = 40
COLUMN_WIDTH_MAX = 600


def normalise_columns(stored: Any) -> Dict[str, Any]:
    """Validate a stored column config against the known set (Snapshot
    3's column resize/config): unknown order entries and widths drop,
    missing order entries fill from the pinned sequence, widths clamp
    to the sane band."""
    stored = stored if isinstance(stored, dict) else {}
    known = set(TRAILING_COLUMN_ORDER)
    raw_order = stored.get("order")
    order = [str(name) for name in raw_order if name in known] if isinstance(raw_order, (list, tuple)) else []
    order += [name for name in TRAILING_COLUMN_ORDER if name not in order]
    raw_hidden = stored.get("hidden")
    hidden = sorted({str(name) for name in raw_hidden if name in known}) if isinstance(raw_hidden, (list, tuple)) else []
    widths: Dict[str, float] = {}
    raw_widths = stored.get("widths")
    if isinstance(raw_widths, dict):
        for key, value in raw_widths.items():
            if str(key) not in known and str(key) not in STICKY_COLUMNS:
                continue
            try:
                widths[str(key)] = max(COLUMN_WIDTH_MIN, min(COLUMN_WIDTH_MAX, float(value)))
            except (TypeError, ValueError):
                continue
    return {"order": order, "hidden": hidden, "widths": widths}

# First-click sort direction per column (UX F7): times and measures
# sort descending (newest/biggest/longest first), names ascending.
DESCENDING_FIRST = {
    "modified", "size", "attempts", "object_height", "layer_height",
    "estimated_time", "last_print", "extruder", "bed", "filament",
}

SORTABLE_COLUMNS = DEFAULT_COLUMN_ORDER

PAGE_SIZES = (25, 50, 100, "all")

# Moonraker's metascan and print accept only these (live-proven: a
# host-side 400 "not a valid gcode file" for everything else).
GCODE_EXTENSIONS = (".gcode", ".g", ".gco")

# History is fetched in a bounded window (the author's ruling: 200 by
# default, "Load all history" as the complete escape hatch).
HISTORY_WINDOW = 200


@dataclass(frozen=True)
class FileRow:
    """One row of the resident listing with its (root, relpath) identity.

    ``relpath`` is root-exclusive (``sub/foo.gcode``), which is the
    form both Moonraker's metadata joins and its history filenames use
    (round-2 D3/D4). Values are typed; anything the printer did not
    report is ``None`` (renders as an emdash, sorts last).
    """
    filename: str
    relpath: str
    root: str = "gcodes"
    modified: Optional[float] = None
    size: Optional[int] = None
    permissions: str = "rw"
    object_height: Optional[float] = None
    layer_height: Optional[float] = None
    estimated_time: Optional[float] = None
    slicer: Optional[str] = None
    extruder: Optional[float] = None
    bed: Optional[float] = None
    filament: Optional[float] = None
    # Joined later from history (attempts aggregation).
    attempts: Optional[int] = None
    last_status: Optional[str] = None
    last_print: Optional[float] = None
    # The metadata's largest thumbnail's root-relative path
    # (``.thumbs/<name>-<size>.png``), or None when the file has none.
    thumb_path: Optional[str] = None
    # The grid-sized thumbnail (>= 32 px) for the list cells: the
    # largest preview image decodes on the UI thread and stutters the
    # list, and the cells never render bigger than ~40 px.
    thumb_small: Optional[str] = None
    # The metadata's print_start_time: the honest "has this file ever
    # printed" signal while the history window is only partially
    # loaded (the author's live ruling).
    print_start_time: Optional[float] = None


def directory_rows(root: str, directory: str, files: Iterable[Dict[str, Any]]) -> List[FileRow]:
    """Turn one ``server/files/directory`` payload into typed rows.

    The response carries basenames only and never echoes the requested
    path (round-2 D3), so the walker stamps the identity: relpath is
    the directory joined with the filename, root-exclusive.
    """
    rows: List[FileRow] = []
    for entry in files:
        filename = str(entry.get("filename") or "")
        if not filename:
            continue
        relpath = f"{directory}/{filename}" if directory else filename
        metadata = {key: value for key, value in entry.items() if key not in
                    ("filename", "modified", "size", "permissions")}
        rows.append(FileRow(
            filename=filename,
            relpath=relpath,
            root=root,
            modified=as_float(entry.get("modified")),
            size=as_int(entry.get("size")),
            permissions=str(entry.get("permissions") or "rw"),
            object_height=as_float(metadata.get("object_height")),
            layer_height=as_float(metadata.get("layer_height")),
            estimated_time=as_float(metadata.get("estimated_time")),
            slicer=as_str(metadata.get("slicer")),
            extruder=as_float(metadata.get("first_layer_extr_temp")),
            bed=as_float(metadata.get("first_layer_bed_temp")),
            filament=as_float(metadata.get("filament_total")),
            thumb_path=thumbnail_path(entry.get("thumbnails")),
            thumb_small=thumbnail_path(entry.get("thumbnails"), prefer="small"),
            print_start_time=as_float(metadata.get("print_start_time")),
        ))
    return rows


def is_gcode_name(filename: Any) -> bool:
    """Whether the host would parse/print this filename. The per-row
    Print and Scan metadata entries gate on it — offering either for
    a .stl is a guaranteed silent host refusal."""
    return str(filename or "").lower().endswith(GCODE_EXTENSIONS)


def rename_path(current: str, new_name: Any) -> Optional[str]:
    """The root-exclusive path a file OR directory would become under
    a rename, or None when the name is unusable: empty, path-shaped
    (renames never move between folders), or unchanged."""
    name = str(new_name or "").strip()
    if not name or "/" in name or "\\" in name:
        return None
    base = str(current or "").rsplit("/", 1)[0] if "/" in str(current or "") else ""
    target = f"{base}/{name}" if base else name
    if target == current:
        return None
    return target


def rename_target(row: FileRow, new_name: Any) -> Optional[str]:
    """The relpath the row would become under a rename (files only —
    directories use rename_path directly)."""
    return rename_path(row.relpath, new_name)


def name_collides(rows: Iterable[FileRow], target_relpath: str) -> bool:
    """Whether another resident row already owns the target — the
    host's move silently overwrites, so the client prompts first
    (round-1 C2/C3, the author's ruling)."""
    return any(row.relpath == target_relpath for row in rows)


def path_collides(paths: Iterable[str], target_path: str) -> bool:
    """Whether a resident directory path already exists at the
    target (folders collide the same way — the host overwrites)."""
    return any(str(path) == target_path for path in paths)


def upload_relpath(directory: str, filename: str) -> str:
    """Where an upload lands: the current directory joined with the
    file's own basename (the author's ruling: use the filename from
    the actual file)."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    directory = str(directory or "").strip("/")
    return f"{directory}/{name}" if directory else name


def delete_candidates(rows: Iterable[FileRow], printing_relpath: Any) -> List[FileRow]:
    """The rows a delete may target: the currently-printing file is
    NEVER offered (the author's gate — the host 403s it anyway, and
    the client must not even ask)."""
    blocked = str(printing_relpath or "")
    return [row for row in rows if row.relpath != blocked]


def thumbnail_path(thumbnails: Any, *, prefer: str = "large") -> Optional[str]:
    """A thumbnail's root-relative path from the metadata, or None.

    Moonraker lists extracted thumbnails in the file's metadata
    (smallest first) with ``relative_path`` pointing into ``.thumbs/``.
    ``prefer="large"`` (the print dialog) takes the largest entry;
    ``prefer="small"`` (the list cells) takes the smallest entry that
    still covers the ~40 px cell (32 px is the slicers' standard
    small thumbnail), falling back to the largest when none qualify.
    The plain ``<file>.png`` sibling does NOT exist — a live Moonraker
    answers it 404 for every file; the fetch must follow this path
    (server/files/<root>/<relative_path>).
    """
    best = None
    for entry in thumbnails or ():
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("relative_path")
        width = as_int(entry.get("width"))
        if not path or width is None:
            continue
        if prefer == "small":
            if width >= 32 and (best is None or width < best[0]):
                best = (width, str(path))
        elif best is None or width > best[0]:
            best = (width, str(path))
    if best is not None:
        return best[1]
    if prefer == "small":
        return thumbnail_path(thumbnails, prefer="large")
    return None


def as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def default_direction(column: str) -> bool:
    """False = descending, True = ascending, on the FIRST click (UX F7)."""
    return column not in DESCENDING_FIRST


def _row_value(row: FileRow, column: str) -> Any:
    if column == "name":
        return row.filename
    if column == "status":
        # The column key is the header's vocabulary; the dataclass
        # field is last_status (the author's live crash: sorting by
        # Status raised AttributeError straight through the publish).
        return row.last_status
    return getattr(row, column)


def sort_rows(rows: Sequence[FileRow], column: str, ascending: bool) -> List[FileRow]:
    """Single-column sort with a name tiebreak and unknown-last.

    The tiebreak keeps equal-valued rows from swapping places on
    re-apply ("it's haunted" — UX F7); unknown values sort last in
    BOTH directions. Each column is typed by the parse (numbers or
    strings, never mixed), with None/"" as the unknown marker.
    """
    if column not in SORTABLE_COLUMNS:
        column = "name"
    direction = 1 if ascending else -1

    def sort_key(row: FileRow):
        value = _row_value(row, column)
        if value is None or value == "":
            return (2, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, direction * value, "")
        return (1, 0, direction * str(value).casefold())

    def tie(row: FileRow):
        return row.filename.casefold()

    return sorted(rows, key=lambda row: (sort_key(row), tie(row)))


def _value_text(value: Any) -> str:
    return "" if value is None else str(value)


def filter_rows(rows: Sequence[FileRow], filters: Dict[str, Any], *, now: float) -> List[FileRow]:
    """OR within a category, AND across categories (the author's ruling).

    ``filters`` keys:
    - ``slicer``: a list of slicer names; ``"unknown"`` selects files
      whose slicer is unreported (the Unknown bucket — a filter must
      never hide a file for an invisible reason, UX F4).
    - ``modified``: one of ``"today"``, ``"7d"``, ``"30d"``, ``"year"``
      or None.
    - ``print_time``: an upper bound in MINUTES or None (≤30/60/120/240/480).
    - ``never_printed``: bool or None (None = off; a file with no
      history entry counts as never printed).
    """
    result: List[FileRow] = []
    for row in rows:
        if not _match_slicer(row, filters.get("slicer")):
            continue
        if not _match_modified(row, filters.get("modified"), now):
            continue
        if not _match_print_time(row, filters.get("print_time")):
            continue
        if filters.get("never_printed"):
            # The row's own Status decides (last_status, then
            # print_start_time) — the filter must not contradict the
            # cell it selects, and membership must not change after
            # "Load all history" flips attempts.
            if row.last_status or row.print_start_time is not None:
                continue
        result.append(row)
    return list(result)


def _match_slicer(row: FileRow, selected: Any) -> bool:
    if not selected:
        return True
    if not isinstance(selected, (list, tuple, set)):
        selected = [selected]
    chosen = [str(item) for item in selected]
    if row.slicer is None or row.slicer == "":
        return "unknown" in chosen
    return any(row.slicer.casefold() == item.casefold() for item in chosen)


def _match_modified(row: FileRow, window: Any, now: float) -> bool:
    import datetime as _datetime
    if not window:
        return True
    if row.modified is None:
        return False  # the window legitimately excludes unknown dates
    modified = _datetime.datetime.fromtimestamp(row.modified)
    today = _datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    window = str(window)
    if window == "today":
        return modified >= today
    if window == "7d":
        return modified >= _datetime.datetime.fromtimestamp(now - 7 * 86400)
    if window == "30d":
        return modified >= _datetime.datetime.fromtimestamp(now - 30 * 86400)
    if window == "year":
        return modified.year == today.year
    return True


def _match_print_time(row: FileRow, minutes: Any) -> bool:
    if not minutes:
        return True
    if row.estimated_time is None:
        return False  # the bound legitimately excludes unknown durations
    try:
        return row.estimated_time <= float(minutes) * 60.0
    except (TypeError, ValueError):
        return True


def search_rows(rows: Sequence[FileRow], query: str) -> List[FileRow]:
    """Global name+path search, token-AND, case-insensitive (the author's rulings).

    Search is NOT scoped to the current directory — matches come from
    the whole resident listing, and the folder shows small in the name
    cell (the model's rendering).
    """
    query = (query or "").strip()
    if not query:
        return list(rows)
    tokens = query.casefold().split()
    result: List[FileRow] = []
    for row in rows:
        haystack = f"{row.filename} {row.relpath}".casefold()
        if all(token in haystack for token in tokens):
            result.append(row)
    return result


def clamp_page(page: int, total: int, page_size: Any) -> int:
    """The page index clamps to the last page (never an empty page)."""
    if total <= 0:
        return 1
    if page_size == "all":
        return 1
    try:
        size = max(1, int(page_size))
    except (TypeError, ValueError):
        size = 25
    last = (total + size - 1) // size
    return max(1, min(int(page), last))


def page_slice(rows: Sequence[FileRow], page: int, page_size: Any) -> List[FileRow]:
    """The published slice — bounded by the page size however many rows are resident (E8)."""
    if page_size == "all":
        return list(rows)
    try:
        size = max(1, int(page_size))
    except (TypeError, ValueError):
        size = 25
    start = (max(1, int(page)) - 1) * size
    return list(rows[start:start + size])


def page_count(total: int, page_size: Any) -> int:
    if page_size == "all":
        return 1 if total > 0 else 0
    try:
        size = max(1, int(page_size))
    except (TypeError, ValueError):
        size = 25
    return (total + size - 1) // size


def merge_column_order(persisted: Sequence[str], available: Sequence[str]) -> List[str]:
    """Stable merge (round-2 M5): persisted order stands, unknown
    columns append in default order, removed columns are retained so a
    later rehydration still knows their position."""
    result = list(persisted)
    have = set(result)
    for column in DEFAULT_COLUMN_ORDER:
        if column not in have:
            have.add(column)
            result.append(column)
    return result


def attempts_for(rows: Sequence[FileRow], history_jobs: Sequence[Dict[str, Any]]) -> List[FileRow]:
    """Join history onto the rows: attempts = jobs within the fetched
    window; last_status = the newest job's status word, never
    flattened to success/fail (the author's ruling); last_print = the
    newest job's end time. History filenames are root-exclusive
    relpaths, matching the rows'."""
    from dataclasses import replace
    jobs_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for job in history_jobs:  # most recent first
        name = str(job.get("filename") or "")
        if name:
            jobs_by_file.setdefault(name, []).append(job)
    joined: List[FileRow] = []
    for row in rows:
        jobs = jobs_by_file.get(row.relpath, [])
        if jobs:
            joined.append(replace(row,
                attempts=len(jobs),
                last_status=str(jobs[0].get("status") or "completed"),
                last_print=as_float(jobs[0].get("end_time"))))
        else:
            joined.append(replace(row, attempts=0, last_status=None, last_print=None))
    return joined


def recent_prints(history_jobs: Sequence[Dict[str, Any]], *, limit: int = 50) -> List[Dict[str, Any]]:
    """Top N DISTINCT files most recently printed — a file printed
    three times counts once (the author's ruling) — with the newest
    job's status and end time. Files the server no longer lists are
    DROPPED, not greyed (the author's live ruling, 2026-09-10): a
    locally-stored recents list could have kept a recently-deleted
    file visible because the user could clean it out by hand — but
    with Moonraker's history as the source of truth there is no way
    to dismiss it, so gone files never render."""
    seen: Set[str] = set()
    result: List[Dict[str, Any]] = []
    for job in history_jobs:
        name = str(job.get("filename") or "")
        if not name or name in seen:
            continue
        if not job.get("exists"):
            continue
        seen.add(name)
        result.append({
            "filename": name,
            "status": str(job.get("status") or "completed"),
            "end_time": as_float(job.get("end_time")),
        })
        if len(result) >= limit:
            break
    return result


def page_selection_state(page_rows: Sequence[FileRow], selected: Set[str]) -> str:
    """The select-all checkbox's three states (the author's ruling)."""
    if not page_rows:
        return "none"
    selected_count = sum(1 for row in page_rows if row.relpath in selected)
    if selected_count == 0:
        return "none"
    if selected_count == len(page_rows):
        return "all"
    return "some"


def empty_kind(total: int, visible: int) -> str:
    """Which empty state the grid shows: genuinely no files, or the
    over-filtered variant (UX F9 — its copy must name the active
    filters, so this flag is what the QML switches on)."""
    if total <= 0:
        return "no_files"
    if visible <= 0:
        return "over_filtered"
    return ""


def filter_option_counts(rows: Sequence[FileRow], *, now: float) -> Dict[str, Any]:
    """Option lists for the filter dropdowns: each option as a
    [key, label, count] triple over the RESIDENT listing (counts stay
    stable as filters change — a shrinking count while ticking is a
    feedback loop the author ruled out). The keys are exactly the
    vocabulary filter_rows accepts.

    - slicer: one entry per distinct slicer plus "unknown"
      (the Unknown bucket — UX F4).
    - modified: the four windows, counts of files inside each.
    - print_time: the five upper bounds, counts of files within each.
    - never_printed: a single count (the toggle's badge).
    """
    import datetime as _datetime
    today = _datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoffs = {
        "today": today.timestamp(),
        "7d": now - 7 * 86400,
        "30d": now - 30 * 86400,
    }

    slicers: Dict[str, int] = {}
    labels: Dict[str, str] = {}
    modified: Dict[str, int] = {"today": 0, "7d": 0, "30d": 0, "year": 0}
    print_time: Dict[str, int] = {"30": 0, "60": 0, "120": 0, "240": 0, "480": 0}
    never_printed = 0

    for row in rows:
        raw = row.slicer or ""
        key = raw.casefold() or "unknown"
        # Group case-insensitively (the matcher does the same — the
        # author's live report: a literal "Unknown" and an unreported
        # slicer split into TWO Unknown options); the first raw
        # spelling is the display label.
        labels.setdefault(key, raw if key != "unknown" else "unknown")
        slicers[key] = slicers.get(key, 0) + 1
        if row.modified is not None:
            stamped = _datetime.datetime.fromtimestamp(row.modified)
            if stamped >= today:
                modified["today"] += 1
            if stamped.timestamp() >= cutoffs["7d"]:
                modified["7d"] += 1
            if stamped.timestamp() >= cutoffs["30d"]:
                modified["30d"] += 1
            if stamped.year == today.year:
                modified["year"] += 1
        if row.estimated_time is not None:
            for bound in ("30", "60", "120", "240", "480"):
                if row.estimated_time <= float(bound) * 60.0:
                    print_time[bound] += 1
        if not row.last_status and row.print_start_time is None:
            never_printed += 1

    return {
        "slicer": [[name, "Unknown" if name == "unknown" else labels[name], count] for name, count in sorted(slicers.items(), key=lambda item: (-item[1], item[0].casefold()))],
        "modified": [
            ["today", "Today", modified["today"]],
            ["7d", "Last 7 days", modified["7d"]],
            ["30d", "Last 30 days", modified["30d"]],
            ["year", "This year", modified["year"]],
        ],
        "print_time": [
            ["30", "≤ 30 min", print_time["30"]],
            ["60", "≤ 1 h", print_time["60"]],
            ["120", "≤ 2 h", print_time["120"]],
            ["240", "≤ 4 h", print_time["240"]],
            ["480", "≤ 8 h", print_time["480"]],
        ],
        "never_printed": never_printed,
    }


@dataclass
class ViewState:
    """The popup's non-persisted view state and its change rules."""
    sort_column: str = "modified"
    sort_ascending: bool = False  # newest first (the default sort)
    filters: Dict[str, Any] = field(default_factory=dict)
    search: str = ""
    page: int = 1
    page_size: Any = 25

    def apply(self, rows: Sequence[FileRow], *, now: float) -> List[FileRow]:
        """Filters first, then search within the subset (the author's ruling)."""
        filtered = filter_rows(rows, self.filters, now=now)
        narrowed = search_rows(filtered, self.search)
        return sort_rows(narrowed, self.sort_column, self.sort_ascending)

    def change_sort(self, column: str) -> None:
        """Sorting another column CANCELS the current sort (the author's ruling)."""
        if column not in SORTABLE_COLUMNS:
            return
        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = default_direction(column)
        self.page = 1

    def change_filters(self, filters: Dict[str, Any]) -> None:
        self.filters = filters
        self.page = 1

    def change_search(self, query: str) -> None:
        self.search = query
        self.page = 1

    def change_page_size(self, size: Any) -> None:
        self.page_size = size
        self.page = 1
