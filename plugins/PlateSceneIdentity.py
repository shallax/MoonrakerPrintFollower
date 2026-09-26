"""Immutable scene identity for the plate's asynchronous navigation assets.

An asset is compatible only when its entire scene matches. During attached
following, a newer numeric split may share a warm raster with a carried tail;
a missing split is never compatible with a numeric split, because a missing
split can bake the entire current layer as printed.
"""

from typing import NamedTuple


class NavigationSceneKey(NamedTuple):
    surface: str
    job_epoch: int
    payload_ids: tuple
    split: int | None
    show_previous: bool
    show_next: bool
    show_base: bool
    show_travels: bool
    line_scale: float
    width: int
    height: int
    bed_width: float
    bed_depth: float
    plot: tuple
    dpr: float
    zoom: float

    def without_progress(self):
        """Keep scene identity and whether a boundary was available."""
        return self._replace(split=self.split is None)


def navigation_hard_key(key):
    """Compatibility key for an incremental warm raster's static scene.

    Short synthetic keys used by the scheduler's tests have no split field;
    for those, all supplied fields must match exactly.
    """
    if key is None or len(key) <= 3:
        return key
    if isinstance(key, NavigationSceneKey):
        return key.without_progress()
    return key[:3] + (key[3] is None,) + key[4:]


def navigation_zoom(key):
    """Read the zoom term without relying on its numeric tuple position."""
    if key is None or len(key) != len(NavigationSceneKey._fields):
        return key
    if isinstance(key, NavigationSceneKey):
        return key.zoom
    return key[-1]
