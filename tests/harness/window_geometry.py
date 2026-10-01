"""Host-safe window bounds; no Qt application is constructed on import."""


def fit_content_rectangle(size, available, margins, minimum):
    """Fit the complete frame inside the available desktop, in logical px."""
    x, y, width, height = available
    left, top, right, bottom = margins
    limit = [width - left - right, height - top - bottom]
    if any(limit[i] < minimum[i] for i in range(2)):
        raise ValueError("available desktop cannot fit the window minimum")
    return [x + left, y + top, min(size[0], limit[0]), min(size[1], limit[1])]


def verified_geometry(reply, requested):
    """Accept a bounded target only with matching request and frame evidence."""
    if not reply.get("ok") or reply.get("watchdog_error"):
        return False
    if not reply.get("fit_available"):
        return list(reply.get("size") or ()) == list(requested)
    if list(reply.get("requested") or ()) != list(requested):
        return False
    wanted = reply.get("wanted") or []
    if len(wanted) != 2 or list(reply.get("size") or ()) != list(wanted):
        return False
    if any(wanted[i] > requested[i] or wanted[i] <= 0 for i in range(2)):
        return False
    available = reply.get("available") or []
    frame = reply.get("frame") or []
    if len(available) != 4 or len(frame) != 4:
        return False
    ax, ay, aw, ah = available
    fx, fy, fw, fh = frame
    return fw > 0 and fh > 0 and ax <= fx and ay <= fy and fx + fw <= ax + aw and fy + fh <= ay + ah
