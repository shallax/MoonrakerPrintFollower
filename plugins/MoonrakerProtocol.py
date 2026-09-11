from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit


@dataclass(frozen=True)
class RemoteFileIdentity:
    filename: str
    size: int = 0
    modified: float = 0.0
    uuid: str = ""

    def stable_key(self) -> str:
        if self.uuid:
            return f"uuid:{self.uuid}"
        return f"file:{self.filename}|size:{int(self.size)}|modified:{self.modified:.6f}"

    def matches_job(self, filename: str, size: int) -> bool:
        return self.filename == filename and (self.size <= 0 or size <= 0 or self.size == size)


def _effective_port(scheme: str, port: Optional[int]) -> int:
    if port is not None:
        return port
    return 443 if scheme == "https" else 80


def moonraker_error_text(payload: Dict[str, Any]) -> str:
    """Moonraker refusal bodies carry their words in two shapes:
    ``{"error": "..."}`` (handled at the call sites) and
    ``{"code": 400, "message": "Unknown", "traceback": ...}`` — whose
    ``message`` is often just "Unknown" while the real text sits in
    the traceback tail (the author's live report: a cold extrude
    surfaced a bare 400, hiding "Extrude below minimum temp — see
    the 'min_extrude_temp' config option")."""
    message = str(payload.get("message") or "").strip()
    # The author's live report: "Extrude refused: <the whole
    # exception>" — some Moonraker builds put the full multi-line
    # exception in `message`. Only a short single-line message is
    # usable; anything bigger falls through to the traceback tail.
    if message and message != "Unknown" and "\n" not in message and len(message) <= 120:
        return message
    traceback_text = str(payload.get("traceback") or "")
    if not traceback_text:
        # Some builds put the exception into `message` alone — the
        # same markers live there.
        traceback_text = message
    for marker in ("HTTPError: ", "ServerError: "):
        index = traceback_text.rfind(marker)
        if index >= 0:
            tail = traceback_text[index + len(marker):]
            line = tail.split("\n", 1)[0].strip()
            if marker == "HTTPError: " and line.startswith("HTTP ") and ": " in line:
                # "HTTP 400: Extrude below minimum temp" — the code
                # is transport noise next to the server's words.
                line = line.split(": ", 1)[1].strip()
            if line:
                return line
    if message:
        # Last resort: never the whole exception — a traceback's
        # FIRST line is its header, so take the LAST; a plain long
        # message keeps its first.
        lines = [line.strip() for line in message.split("\n") if line.strip()]
        if lines:
            pick = lines[-1] if "Traceback" in message else lines[0]
            return "" if pick == "Unknown" else pick[:120]
    return ""


# The transport's historical import name (kept for its call sites
# and the real-socket tests that pin the shape).
_moonraker_error_text = moonraker_error_text


def same_origin(base_url: str, target: str) -> bool:
    """True when ``target`` shares the base URL's origin (scheme, host, port).

    The Moonraker API key may only ride requests to the printer's own
    origin — a webcam host, a tunnel alias or a redirect target is
    foreign and must never carry the key. Fail-closed: any parse
    failure returns False (no key). QUrl does not implement origin
    comparison; isParentOf is path semantics, not origin semantics
    (round-2 security F2).
    """
    try:
        base = urlsplit(str(base_url or "").strip())
        other = urlsplit(str(target or "").strip())
        if not base.scheme or not base.hostname:
            return False
        if not other.scheme or not other.hostname:
            return False
        return (
            base.scheme.lower() == other.scheme.lower()
            and base.hostname.lower() == other.hostname.lower()
            and _effective_port(base.scheme, base.port) == _effective_port(other.scheme, other.port)
        )
    except (TypeError, ValueError):
        return False


def status_endpoint(base_url: str) -> str:
    return (
        f"{base_url}/printer/objects/query?"
        "print_stats&gcode_move&virtual_sdcard&motion_report&bed_mesh"
    )


def metadata_endpoint(base_url: str, filename: str) -> str:
    return f"{base_url}/server/files/metadata?filename={quote(filename, safe='/')}"


def download_endpoint(base_url: str, filename: str) -> str:
    return f"{base_url}/server/files/gcodes/{quote(filename, safe='/')}"


def print_start_path(filename: str) -> str:
    """The print/start filename is ROOT-EXCLUSIVE: the path as the
    printer's SD-card layer knows it, WITHOUT the "gcodes/" root the
    file endpoints carry (round-2 D4 — the printer refuses the
    rooted form)."""
    return str(filename).strip().lstrip("/")


def print_start_endpoint(base_url: str, filename: str) -> str:
    path = f"printer/print/start?filename={quote(print_start_path(filename), safe='/')}"
    return path if not str(base_url or "") else f"{str(base_url).rstrip('/')}/{path}"


def delete_endpoint(base_url: str, root: str, filename: str) -> str:
    """HTTP DELETE /server/files/{root}/{filename} — BOTH parts are
    included, unlike print/start's root-exclusive filename (round-1
    C1 / round-2 E1: the only delete route; a POST form does not
    exist)."""
    path = f"server/files/{str(root).strip('/')}/{quote(str(filename).lstrip('/'), safe='/')}"
    return path if not str(base_url or "") else f"{str(base_url).rstrip('/')}/{path}"


def move_endpoint(base_url: str) -> str:
    """Rename via the host's move route: POST server/files/move with
    a {source, dest} body, both root-inclusive (round-1 C2/C3: move
    silently overwrites, so the client prompts first)."""
    return "server/files/move" if not str(base_url or "") else f"{str(base_url).rstrip('/')}/server/files/move"


def server_info_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/server/info"


def objects_list_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/printer/objects/list"


def gcode_script_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/printer/gcode/script"


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
