from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

try:
    from .Core import RemoteFileIdentity
except ImportError:  # test/import convenience
    from Core import RemoteFileIdentity


def status_endpoint(base_url: str) -> str:
    return (
        f"{base_url}/printer/objects/query?"
        "print_stats&gcode_move&virtual_sdcard&motion_report"
    )


def metadata_endpoint(base_url: str, filename: str) -> str:
    return f"{base_url}/server/files/metadata?filename={quote(filename, safe='/')}"


def download_endpoint(base_url: str, filename: str) -> str:
    return f"{base_url}/server/files/gcodes/{quote(filename, safe='/')}"


def parse_file_identity(filename: str, payload: Dict[str, Any], fallback_size: int = 0) -> RemoteFileIdentity:
    result = payload.get("result") if isinstance(payload, dict) else None
    data = result if isinstance(result, dict) else payload
    if not isinstance(data, dict):
        data = {}

    try:
        size = int(data.get("size") or fallback_size or 0)
    except (TypeError, ValueError):
        size = int(fallback_size or 0)
    try:
        modified = float(data.get("modified") or 0.0)
    except (TypeError, ValueError):
        modified = 0.0
    uuid = str(data.get("uuid") or "")
    canonical_filename = str(data.get("filename") or filename)
    return RemoteFileIdentity(
        filename=canonical_filename,
        size=size,
        modified=modified,
        uuid=uuid,
    )


def motion_live_position(status: Dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    report = status.get("motion_report") if isinstance(status, dict) else None
    if not isinstance(report, dict):
        return None
    raw = report.get("live_position")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def live_position_in_gcode_space(
    motion_report: Dict[str, Any],
    gcode_move: Optional[Dict[str, Any]] = None,
) -> Optional[tuple[float, float, float]]:
    """Convert motion_report.live_position to the XYZ space used by G-code.

    ``motion_report.live_position`` is in Klipper's configured machine
    coordinate space, while the G-code file contains coordinates relative to
    the current G-code origin. ``gcode_move.homing_origin`` is the offset
    between those spaces. Newer Klipper releases may reorder/extend coordinate
    vectors, so ``axis_map`` is honoured when present.
    """
    if not isinstance(motion_report, dict):
        return None
    raw = motion_report.get("live_position")
    if not isinstance(raw, (list, tuple)):
        return None
    move = gcode_move if isinstance(gcode_move, dict) else {}
    origin = move.get("homing_origin")
    if not isinstance(origin, (list, tuple)):
        origin = ()
    axis_map = move.get("axis_map")
    if not isinstance(axis_map, dict):
        axis_map = {}

    result = []
    for default_index, axis in enumerate(("X", "Y", "Z")):
        mapped = axis_map.get(axis, axis_map.get(axis.lower(), default_index))
        try:
            index = int(mapped)
        except (TypeError, ValueError):
            index = default_index
        if index < 0 or index >= len(raw):
            return None
        try:
            value = float(raw[index])
        except (TypeError, ValueError):
            return None
        # homing_origin historically uses XYZ order. If a future Klipper build
        # exposes a coordinate vector matching axis_map, use the mapped index;
        # otherwise fall back to conventional XYZ order.
        origin_index = index if index < len(origin) else default_index
        try:
            offset = float(origin[origin_index]) if origin_index < len(origin) else 0.0
        except (TypeError, ValueError):
            offset = 0.0
        result.append(value - offset)
    return (result[0], result[1], result[2])
