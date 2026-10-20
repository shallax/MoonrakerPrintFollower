# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Declare the standalone distribution's complete source and asset boundary.
# 2026-10-20 — 6.0.0: Include portable bed-mesh geometry in the shared source distribution.

"""One explicit inventory shared by wheel construction and external verification."""
PACKAGE_DIRS = {
    "moonraker_toolpath": "library",
    "moonraker_toolpath.gcode": "mpf/gcode",
    "moonraker_toolpath.geometry": "mpf/geometry",
    "moonraker_toolpath.plate": "mpf/plate",
    "moonraker_toolpath.resources": "mpf/resources",
    "moonraker_toolpath.storage": "mpf/storage",
}
MODULES = {
    "moonraker_toolpath": ("__init__",),
    "moonraker_toolpath.gcode": (
        "__init__", "GCodeDocument", "ArcGeometry", "FeatureTracker", "GCodeIndex", "GCodeParser",
        "IndexAssembly", "IndexHeader", "IndexResidency", "IndexHydrator", "IndexLimits", "IndexView",
        "IndexWork", "cache", "IndexCache", "IndexCodec", "CachePolicy", "PreparedStore", "RawSourceCache", "SourceIdentity", "MotionIndex", "MotionRanges", "ObjectWork", "PlateProgress", "TravelStates",
    ),
    "moonraker_toolpath.geometry": ("__init__", "Polygons", "BedMeshGeometry"),
    "moonraker_toolpath.plate": (
        "__init__", "ToolpathGeometry", "qt", "GpuFollower", "GpuStrokeMaterial",
        "PlateAxisGeometry", "PlateQt", "PlateRaster", "raster", "scenegraph", "PreviewColours",
    ),
    "moonraker_toolpath.resources": ("__init__", "PluginPaths"),
    "moonraker_toolpath.storage": ("__init__", "ProcessOwner"),
}
RESOURCES = {"moonraker_toolpath.resources": (
    "shaders/stroke.vert", "shaders/stroke.frag",
    "shaders/stroke.vert.qsb", "shaders/stroke.frag.qsb",
)}


def source_files():
    """Map each archive member to its single authoritative checkout source."""
    return {
        f"{package.replace('.', '/')}/{name}": f"{PACKAGE_DIRS[package]}/{name}"
        for package, modules in MODULES.items()
        for name in [*(module + ".py" for module in modules), *RESOURCES.get(package, ())]
    }
