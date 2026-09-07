"""Concrete follower runtime assembled from focused Cura-facing components."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from UM.Extension import Extension

from .FollowerBootstrap import FollowerBootstrapMixin
from .FollowerConfiguration import FollowerConfigurationMixin
from .CuraLifecycleRuntime import CuraLifecycleRuntimeMixin
from .CuraViewBridge import CuraViewBridgeMixin
from .CuraFileLifecycle import CuraFileLifecycleMixin
from .PreviewFollowerRuntime import PreviewFollowerRuntimeMixin
from .PreviewStatus import PreviewStatusMixin
from .PreviewEta import PreviewEtaMixin
from .PreviewControls import PreviewControlsMixin
from .PreviewLoad import PreviewLoadMixin
from .PreviewFollowEngine import PreviewFollowEngineMixin
from .PathFollowEngine import PathFollowEngineMixin
from .GCodeIndexRuntime import GCodeIndexRuntimeMixin
from .RemoteFileTransfer import RemoteFileTransferMixin


class MoonrakerPrintFollower(
    FollowerBootstrapMixin,
    FollowerConfigurationMixin,
    CuraLifecycleRuntimeMixin,
    CuraViewBridgeMixin,
    CuraFileLifecycleMixin,
    PreviewFollowerRuntimeMixin,
    PreviewStatusMixin,
    PreviewEtaMixin,
    PreviewControlsMixin,
    PreviewLoadMixin,
    PreviewFollowEngineMixin,
    PathFollowEngineMixin,
    GCodeIndexRuntimeMixin,
    RemoteFileTransferMixin,
    QObject,
    Extension,
):
    """Synchronise Cura Preview with one active Moonraker print."""

    _remoteIndexReady = pyqtSignal(int, str, object, int, int)
    _remoteLayerHydrated = pyqtSignal(int, int, bool)

    PLUGIN_ID = "Moonraker_Print_Follower"

    ACTIVE_STATES = {"printing", "paused"}
