"""Pure file-table units and local-time labels; callers supply the clock."""
from datetime import datetime
import math


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError): return default


# The file manager's unit and time formats (UX F10): every string in
# the popup is produced HERE, on the Python side, so the capture
# harness's frozen clock covers them — a QML-side formatter would
# render a live clock into the committed screenshots.

STATUS_COLOURS = {
    "completed": "#43a047",
    "error": "#e53935",
    "cancelled": "#fb8c00",
    "interrupted": "#5c6bc0",
    "klippy_shutdown": "#5c6bc0",
    "server_exit": "#5c6bc0",
    "in_progress": "#1e88e5",
    "paused": "#8e24aa",
}


def file_size(value) -> str:
    size = _number(value)
    if size >= 1000 * 1000 * 1000:
        return f"{size / 1000000000:.1f} GB"
    if size >= 1000 * 1000:
        return f"{size / 1000000:.1f} MB"
    return f"{size / 1000:.0f} KB"


def file_timestamp(value, now) -> str:
    """Absolute local time: '10 Sep 14:32' within the year, '10 Sep
    2026' beyond — a pure function of the instant and the frozen now."""
    instant = _number(value)
    if instant <= 0:
        return "—"
    try:
        stamp = datetime.fromtimestamp(instant)
        reference = datetime.fromtimestamp(_number(now))
    except (ValueError, OverflowError, OSError):
        return "—"
    if stamp.year == reference.year:
        return f"{stamp.day} {stamp:%b %H:%M}"
    return f"{stamp.day} {stamp:%b %Y}"


def file_duration_short(seconds) -> str:
    total = max(0, int(round(_number(seconds))))
    if total <= 0:
        return "—"
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} h {minutes:02d} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def file_disk_text(usage) -> str:
    total, free = _number(usage.get("total")), _number(usage.get("free"))
    if total <= 0:
        return "—"
    return f"{file_size(free)} free of {file_size(total)}"


def file_temperature(value) -> str:
    degrees = _number(value)
    return f"{int(round(degrees))} °C" if degrees > 0 else "—"


def file_filament(value) -> str:
    millimetres = _number(value)
    return f"{millimetres / 1000:.2f} m" if millimetres > 0 else "—"


def file_row_payload(row, now) -> dict:
    """One QML-ready row dict for the pinned file-manager face.

    The status fallback distinguishes genuinely-never-printed files
    from files printed beyond the partially-loaded history window
    (the live ruling): the metadata's ``print_start_time``
    is the honest signal — absent means never printed, present but
    unjoined means the history hasn't been loaded far enough."""
    return {
        "name": row.filename,
        "relpath": row.relpath,
        "folder": row.relpath.rsplit("/", 1)[0] if "/" in row.relpath else "",
        "modified": file_timestamp(row.modified, now),
        "size": file_size(row.size) if row.size is not None else "—",
        "attempts": str(row.attempts) if row.attempts else "—",
        "status": row.last_status if row.last_status else ("Never printed" if row.print_start_time is None else "Missing history"),
        "statusColour": STATUS_COLOURS.get(row.last_status, "text_inactive"),
        "objH": f"{_number(row.object_height):.2f} mm" if row.object_height is not None else "—",
        "layerH": f"{_number(row.layer_height):.2f} mm" if row.layer_height is not None else "—",
        "est": file_duration_short(row.estimated_time),
        "lastPrint": file_timestamp(row.last_print, now),
        "slicer": row.slicer or "—",
        "extr": file_temperature(row.extruder),
        "bed": file_temperature(row.bed),
        "filament": file_filament(row.filament),
        "hasThumb": False,  # thumbnails land with the fetch queue
        "unparsed": row.object_height is None and row.layer_height is None,
    }
