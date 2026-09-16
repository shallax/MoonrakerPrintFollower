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

# The controller is UM-free at module level: it imports safely in any
# environment (test or runtime), and every Cura dependency resolves
# lazily where a Cura application is guaranteed (attach/detach run
# only inside Cura, or no-op).
from .FollowMesh import PASS_NAME

ACTIVE = None  # type: Optional[object]
_ATTACHED = False
_ACTIVE_ADDED = False
_RENDERER = None
_ORIGINAL_BINDINGS: Optional[List[str]] = None
_SIMULATION_PASS = None
_CAPABLE = True


def _set_pass_active(renderer, render_pass, active: bool) -> None:
    """Enable/disable a render pass through its public API. The
    advertised SDK floor is 8.11, whose RenderPass has setEnabled;
    the hasattr guard keeps a hypothetical older runtime on the
    membership path instead of crashing."""
    if hasattr(render_pass, "setEnabled"):
        render_pass.setEnabled(bool(active))
        return
    if active:
        renderer.addRenderPass(render_pass)
    else:
        renderer.removeRenderPass(render_pass)


def _log(message: str) -> None:
    try:
        from UM.Logger import Logger
        Logger.log("i", "%s", message)
    except Exception:
        pass


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


def _ensure_nozzle_node(view):
    """Ensure Cura's normal SimulationView nozzle exists in the scene.

    This reproduces the public lifecycle work SimulationView performs
    on ViewActivateEvent — a live file loaded into an already-active
    Preview can miss that transition (the old lifecycle repair's
    legitimate half). It touches no SimulationPass internals.
    """
    try:
        nozzle = view.getNozzleNode()
        if nozzle is None:
            return None
        controller = view.getController()
        if controller is None:
            return None
        scene = controller.getScene()
        if scene is None:
            return None
        root = scene.getRoot()
        if root is None:
            return None
        if nozzle.getParent() is not root:
            nozzle.setParent(root)
        # SimulationView keeps it hidden from ordinary scene rendering
        # because the explicit pass renders it.
        nozzle.setVisible(False)
        return nozzle
    except Exception:
        return None


def attach(view) -> bool:
    """Substitute the follow pass for Cura's SimulationPass. Returns
    whether the substitution is active; the vanilla path stays active
    on any failure or missing capability. The mutation phase is
    transactional: any failure after the first mutation rolls the
    compositor, the native pass and the renderer membership back."""
    global ACTIVE, _ATTACHED, _ORIGINAL_BINDINGS, _SIMULATION_PASS, _CAPABLE, _ACTIVE_ADDED, _RENDERER
    if _ATTACHED and ACTIVE is not None:
        return True
    if not _CAPABLE:
        return False
    try:
        from .FollowPass import FollowPass
    except Exception:
        # No Cura renderer classes available: the capability is
        # absent, and the vanilla preview remains in control.
        _CAPABLE = False
        return False
    # The validation phase — nothing mutates before every requirement
    # has checked out.
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
        ACTIVE.setFollowView(view)
        ACTIVE.setFollowScene(node, layer_data)
        if ACTIVE._mesh is None:
            return False
        # Cura's own ViewActivateEvent lifecycle for the nozzle (the
        # legitimate repair), so the follow pass's toolhead has the
        # same nozzle Cura would use. Left in place on detach — the
        # restored native pass expects it; Cura unparents it on
        # ViewDeactivateEvent.
        _ensure_nozzle_node(view)
    except Exception as exc:
        _log(f"Moonraker follow pass attach skipped: {exc!r}")
        _CAPABLE = False
        return False
    # The mutation phase, with rollback.
    added_active = False
    try:
        if not _ACTIVE_ADDED:
            renderer.addRenderPass(ACTIVE)
            _ACTIVE_ADDED = True
            added_active = True
        composite.setLayerBindings(_bindings_swapped(bindings, "simulationview", PASS_NAME))
        _set_pass_active(renderer, simulation_pass, False)
        _ORIGINAL_BINDINGS = list(bindings)
        _SIMULATION_PASS = simulation_pass
        _RENDERER = renderer
        _ATTACHED = True
        _log("Moonraker follow pass attached (the review's render architecture)")
        return True
    except Exception as exc:
        try:
            composite.setLayerBindings(list(bindings))
        except Exception:
            pass
        try:
            _set_pass_active(renderer, simulation_pass, True)
        except Exception:
            pass
        if added_active:
            try:
                renderer.removeRenderPass(ACTIVE)
            except Exception:
                pass
            _ACTIVE_ADDED = False
        _ORIGINAL_BINDINGS = None
        _SIMULATION_PASS = None
        _ATTACHED = False
        _log(f"Moonraker follow pass attach rolled back: {exc!r}")
        return False


def update(layer: int, path_units: float, toolhead: bool = True) -> None:
    """The per-tick uniform update — the pass is fed the same value
    the preview path writes, so the visuals match the glide."""
    if _ATTACHED and ACTIVE is not None:
        ACTIVE.setFollowState(layer, path_units, toolhead)


def shutdown() -> None:
    """The plugin-teardown path: restore the compositor and Cura's
    pass (detach), then remove the follow pass from the renderer and
    clear every held reference — a plugin reload must not leave a
    stale pass or a stale binding behind."""
    global ACTIVE, _ATTACHED, _ORIGINAL_BINDINGS, _SIMULATION_PASS, _ACTIVE_ADDED, _RENDERER
    try:
        if _ATTACHED:
            detach()
        if ACTIVE is not None and _ACTIVE_ADDED and _RENDERER is not None:
            _RENDERER.removeRenderPass(ACTIVE)
        ACTIVE = None
        _ATTACHED = False
        _ORIGINAL_BINDINGS = None
        _SIMULATION_PASS = None
        _ACTIVE_ADDED = False
        _RENDERER = None
    except Exception:
        pass


def detach(view=None) -> None:
    """Restore Cura's SimulationPass at its current state. The view is
    optional — without one, the SimulationView is found through the
    application (the normal path); tests pass their own. The nozzle's
    scene parent is deliberately left alone — the restored native
    pass expects it, and Cura unparents on ViewDeactivateEvent."""
    global ACTIVE, _ATTACHED, _ORIGINAL_BINDINGS, _SIMULATION_PASS
    was_attached = _ATTACHED
    try:
        if _ATTACHED and ACTIVE is not None:
            ACTIVE.setFollowScene(None, None)
        if _SIMULATION_PASS is not None and not _SIMULATION_PASS.isEnabled():
            if _RENDERER is not None:
                _set_pass_active(_RENDERER, _SIMULATION_PASS, True)
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
        _ATTACHED = False
        if was_attached:
            _log("Moonraker follow pass detached")
    except Exception:
        pass
