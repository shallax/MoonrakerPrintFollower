"""The attach/detach controller for the follow render pass (the
review's architecture: a plugin-owned pass substitutes for Cura's
SimulationPass while following, through the public renderer and
compositor APIs — nothing in Cura or Uranium is modified).

While attached, Cura's SimulationPass is disabled and the follow pass
feeds the "simulationview" compositor layer. Cura's own path state
keeps being written every tick (the existing setPath flow is
untouched), so a detach lands the native preview at exactly the
position where following left off.

Every step capability-checks: when an API is missing, nothing is
substituted and the vanilla preview path stays active.
"""
from __future__ import annotations

from typing import List, Optional

from UM.Logger import Logger

from .FollowPass import FollowPass, PASS_NAME

ACTIVE: Optional[FollowPass] = None
_ORIGINAL_BINDINGS: Optional[List[str]] = None
_SIMULATION_PASS = None
_CAPABLE = True


def _find_layer_data():
    """The loaded toolpath's LayerData via the scene node decoration —
    the same access SimulationPass uses."""
    try:
        from UM.Application import Application
        root = Application.getInstance().getController().getScene().getRoot()
        for node in root.getAllChildren():
            layer_data = node.callDecoration("getLayerData")
            if layer_data is not None:
                return node, layer_data
    except Exception:
        pass
    return None, None


def _bindings_swapped(bindings, target: str, replacement: str) -> List[str]:
    return [replacement if name == target else name for name in bindings]


def attach(view) -> bool:
    """Substitute the follow pass for Cura's SimulationPass. Returns
    whether the substitution is active; the vanilla path stays active
    on any failure or missing capability."""
    global ACTIVE, _ORIGINAL_BINDINGS, _SIMULATION_PASS, _CAPABLE
    if ACTIVE is not None and ACTIVE.isEnabled():
        return True
    if not _CAPABLE:
        return False
    try:
        renderer = view.getRenderer()
        if renderer is None:
            return False
        composite = renderer.getRenderPass("composite")
        if composite is None:
            _CAPABLE = False
            return False
        bindings = composite.getLayerBindings()
        if "simulationview" not in bindings:
            # Cura has not registered its layer yet (no toolpath
            # loaded); keep the capability unknown and retry later.
            return False
        simulation_pass = view.getSimulationPass()
        node, layer_data = _find_layer_data()
        if node is None or layer_data is None:
            return False
        if ACTIVE is None:
            ACTIVE = FollowPass()
            renderer.addRenderPass(ACTIVE)
        ACTIVE.setFollowView(view)
        ACTIVE.setFollowScene(node, layer_data)
        if ACTIVE._mesh is None:
            return False
        _ORIGINAL_BINDINGS = list(bindings)
        _SIMULATION_PASS = simulation_pass
        composite.setLayerBindings(_bindings_swapped(bindings, "simulationview", PASS_NAME))
        if simulation_pass is not None:
            simulation_pass.setEnabled(False)
        ACTIVE.setEnabled(True)
        Logger.log("i", "Moonraker follow pass attached (the review's render architecture)")
        return True
    except Exception as exc:
        Logger.log("i", "Moonraker follow pass attach skipped: %r", exc)
        _CAPABLE = False
        return False


def update(layer: int, path_units: float, toolhead: bool = True) -> None:
    """The per-tick uniform update — the pass is fed the same value
    the preview path writes, so the visuals match the glide."""
    if ACTIVE is not None and ACTIVE.isEnabled():
        ACTIVE.setFollowState(layer, path_units, toolhead)


def shutdown() -> None:
    """The plugin-teardown path: restore the compositor and Cura's
    pass (detach), then remove the follow pass from the renderer and
    clear every held reference — a plugin reload must not leave a
    stale pass or a stale binding behind."""
    global ACTIVE, _ORIGINAL_BINDINGS, _SIMULATION_PASS
    try:
        if ACTIVE is not None and ACTIVE.isEnabled():
            detach()
        if ACTIVE is not None:
            from UM.Application import Application
            for v in Application.getInstance().getController().getAllViews():
                if hasattr(v, "getSimulationPass"):
                    renderer = v.getRenderer()
                    if renderer is not None:
                        renderer.removeRenderPass(ACTIVE)
                    break
        ACTIVE = None
        _ORIGINAL_BINDINGS = None
        _SIMULATION_PASS = None
    except Exception:
        pass


def detach(view=None) -> None:
    """Restore Cura's SimulationPass at its current state. The view is
    optional — without one, the SimulationView is found through the
    application (the normal path); tests pass their own."""
    global ACTIVE, _ORIGINAL_BINDINGS, _SIMULATION_PASS
    try:
        if ACTIVE is not None and ACTIVE.isEnabled():
            ACTIVE.setEnabled(False)
            ACTIVE.setFollowScene(None, None)
        if _SIMULATION_PASS is not None and not _SIMULATION_PASS.isEnabled():
            _SIMULATION_PASS.setEnabled(True)
            _SIMULATION_PASS = None
        if _ORIGINAL_BINDINGS is not None:
            if view is None:
                from UM.Application import Application
                for v in Application.getInstance().getController().getAllViews():
                    if hasattr(v, "getSimulationPass"):
                        view = v
                        break
            if view is not None:
                composite = view.getRenderer().getRenderPass("composite")
                if composite is not None:
                    composite.setLayerBindings(list(_ORIGINAL_BINDINGS))
            _ORIGINAL_BINDINGS = None
        Logger.log("i", "Moonraker follow pass detached")
    except Exception:
        pass
