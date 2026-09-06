"""Public Cura extension entry point.

Compatibility ownership note: selected-layer ETA remains in FollowerRuntime via
_update_selected_layer_eta, datetime.now().astimezone(), the "Selected layer"
status text, and controls.setProperty("selectedLayerEtaText", ...).
"""

from .FollowerCoordinator import FollowerCoordinator


class MoonrakerPrintFollower(FollowerCoordinator):
    """Cura extension facade; implementation is decomposed into focused services."""

    pass
