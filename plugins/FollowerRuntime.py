"""Concrete compatibility runtime assembled from focused follower components."""

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
    PREF_ROOT = "moonraker_print_follower"

    PREF_ENABLED = f"{PREF_ROOT}/enabled"
    PREF_URL = f"{PREF_ROOT}/url"
    PREF_API_KEY = f"{PREF_ROOT}/api_key"
    PREF_INTERVAL = f"{PREF_ROOT}/poll_interval_ms"
    PREF_ONE_BASED = f"{PREF_ROOT}/moonraker_layer_is_one_based"
    PREF_AUTO_PREVIEW = f"{PREF_ROOT}/auto_preview"
    PREF_Z_FALLBACK = f"{PREF_ROOT}/z_fallback"
    PREF_Z_TOLERANCE = f"{PREF_ROOT}/z_tolerance"
    PREF_PATH_FOLLOW = f"{PREF_ROOT}/path_follow"

    ACTIVE_STATES = {"printing", "paused"}
