"""Public Cura extension entry point.

The implementation is composed by FollowerCoordinator from focused domain
services, transport/session infrastructure and Cura-facing runtime mixins.
Keep this facade intentionally tiny.
"""

from .FollowerCoordinator import FollowerCoordinator


class MoonrakerPrintFollower(FollowerCoordinator):
    """Cura extension facade for the active Moonraker print follower."""

    pass
