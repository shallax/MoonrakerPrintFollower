"""Keep Cura's own SimulationView nozzle available during live following.

Cura renders its nozzle inside ``SimulationPass`` after the G-code paths, which
is exactly the rendering behaviour the follower wants.  The fragile part is the
lifecycle: Cura normally creates and parents its ``NozzleNode`` only from the
SimulationView ``ViewActivateEvent`` handler.  If a live G-code file is loaded
while Preview is already active, that activation handler may not run again and
SimulationPass has no nozzle node to discover.  Leaving and re-entering Preview
then appears to "fix" the nozzle because the activation handler finally runs.

The follower therefore repairs that small piece of SimulationView lifecycle
before nudging the pass out of its transient layer-switch state.  All access is
capability-checked and uses interfaces present throughout Cura 5.x / SDK 8.x.
"""

from __future__ import annotations


def keep_native_nozzle_visible(simulation_view) -> bool:
    """Ensure Cura's native nozzle can render for the followed path.

    This deliberately does *not* draw a plugin-owned nozzle.  It makes sure the
    SimulationPass exists and is enabled, ensures Cura's own NozzleNode exists
    and is parented to the current scene root, then clears the pass's temporary
    layer-switch suppression.  The actual nozzle is still positioned and drawn
    by Cura's SimulationPass using Cura's normal mesh, shader and compositing.
    """

    try:
        get_pass = getattr(simulation_view, "getSimulationPass", None)
        get_layer = getattr(simulation_view, "getCurrentLayer", None)
        get_nozzle = getattr(simulation_view, "getNozzleNode", None)
        get_controller = getattr(simulation_view, "getController", None)
        if not all(callable(value) for value in (get_pass, get_layer, get_nozzle, get_controller)):
            return False

        simulation_pass = get_pass()
        if simulation_pass is None:
            return False

        # Compatibility mode deliberately does not render Cura's nozzle.
        if bool(getattr(simulation_pass, "_compatibility_mode", False)):
            return False

        if not hasattr(simulation_pass, "_switching_layers") or not hasattr(
            simulation_pass, "_old_current_layer"
        ):
            return False

        controller = get_controller()
        if controller is None:
            return False
        get_scene = getattr(controller, "getScene", None)
        if not callable(get_scene):
            return False
        scene = get_scene()
        if scene is None:
            return False
        get_root = getattr(scene, "getRoot", None)
        if not callable(get_root):
            return False
        root = get_root()
        if root is None:
            return False

        nozzle = get_nozzle()
        if nozzle is None:
            return False

        # Cura normally does this only on ViewActivateEvent.  Do it here as a
        # lifecycle repair so loading a live file while Preview is already open
        # cannot leave SimulationPass with no NozzleNode to render.
        get_parent = getattr(nozzle, "getParent", None)
        set_parent = getattr(nozzle, "setParent", None)
        if not callable(set_parent):
            return False
        parent = get_parent() if callable(get_parent) else None
        if parent is not root:
            set_parent(root)

        set_visible = getattr(nozzle, "setVisible", None)
        if callable(set_visible):
            # SimulationPass renders NozzleNode separately; the scene copy must
            # remain hidden, matching Cura's own ViewActivateEvent behaviour.
            set_visible(False)

        set_enabled = getattr(simulation_pass, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(True)

        # View activation normally establishes activity while inspecting layer
        # data.  A live file loaded into an already-active Preview can miss that
        # transition too.  At this point the follower has a trustworthy exact
        # path, so allowing SimulationPass to render is appropriate.
        get_activity = getattr(simulation_view, "getActivity", None)
        set_activity = getattr(simulation_view, "setActivity", None)
        if callable(get_activity) and callable(set_activity) and not bool(get_activity()):
            set_activity(True)

        # Follower updates often change layer and path together.  Cura otherwise
        # suppresses its nozzle for that frame as if the user were dragging the
        # layer slider.  Synchronise the old-layer marker after the lifecycle
        # repair and allow the standard nozzle batch to render immediately.
        simulation_pass._old_current_layer = int(get_layer())
        simulation_pass._switching_layers = False
        return True
    except Exception:
        return False
