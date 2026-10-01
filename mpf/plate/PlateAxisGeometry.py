"""Border-aligned bed axis arrows shared by raster and GPU rendering."""


def axis_arrows(left, top, right, bottom, stroke, max_head=9):
    """Return X-left and Y-down segments, including inward-only half heads."""
    half = stroke / 2
    x_right, y_top = right - half, top + half
    x_tip = x_right - .15 * (right - left)
    y_tip = y_top + .15 * (bottom - top)
    x_head = min(max_head, .3 * (x_right - x_tip))
    y_head = min(max_head, .3 * (y_tip - y_top))
    return (
        ((x_right, y_top, x_tip, y_top),
         (x_tip, y_top, x_tip + x_head, y_top + x_head)),
        ((x_right, y_top, x_right, y_tip),
         (x_right, y_tip, x_right - y_head, y_tip - y_head)),
    )
