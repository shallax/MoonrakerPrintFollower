from __future__ import annotations

from typing import Optional, Tuple


def active_machine_identity(application) -> Tuple[str, str]:
    stack = None
    try:
        stack = application.getGlobalContainerStack()
    except Exception:
        pass
    # Do not fall back to the lazy MachineManager getter here. Cura 5.13
    # creates MachineManager lazily, and Extension constructors run while
    # startSplashWindowPhase() is still loading plugins -- before Cura creates
    # its i18n catalog. Forcing MachineManager into existence at that point
    # schedules setInitialActiveMachine(), which can call setGlobalContainerStack
    # against a half-initialized CuraApplication and crash startup.
    #
    # The global container stack is the authoritative printer identity once Cura
    # has selected a machine. Until then, report an unknown identity and let
    # globalContainerStackChanged finish migration/connection setup later.
    if stack is None:
        return "unknown", "Unknown Cura printer"

    machine_id = ""
    for getter_name in ("getId", "getMetaDataEntry"):
        try:
            getter = getattr(stack, getter_name)
            if getter_name == "getMetaDataEntry":
                value = getter("id", "")
            else:
                value = getter()
            if value:
                machine_id = str(value)
                break
        except Exception:
            pass
    if not machine_id:
        machine_id = str(id(stack))

    name = ""
    for getter_name in ("getName",):
        try:
            value = getattr(stack, getter_name)()
            if value:
                name = str(value)
                break
        except Exception:
            pass
    if not name:
        try:
            name = str(stack.getMetaDataEntry("name", machine_id))
        except Exception:
            name = machine_id
    return machine_id, name


def apply_preview_decision(view, current_layer: int, minimum_layer: Optional[int] = None) -> None:
    """Apply a layer/range decision through SimulationView's public API where available."""
    if minimum_layer is not None and hasattr(view, "setMinimumLayer"):
        view.setMinimumLayer(int(minimum_layer))
    view.setLayer(int(current_layer))


def preview_head_position(controller, view) -> Optional[Tuple[float, float, float]]:
    """Return Cura's rendered print-head position for the current layer/path.

    This intentionally mirrors SimulationPass rather than converting printer
    coordinates ourselves.  Layer polygon coordinates already include Cura's
    G-code-space transforms and extruder offsets, so deriving the position from
    the Preview geometry keeps a plugin-owned marker exactly aligned with the
    toolpath Cura is displaying.
    """
    try:
        layer_number = int(view.getCurrentLayer())
        path_value = float(view.getCurrentPath())
    except Exception:
        return None

    if layer_number < 0 or path_value != path_value:  # NaN without importing math.
        return None

    def children(node):
        try:
            return list(node.getChildren())
        except Exception:
            return []

    def nodes(root):
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(children(node)))

    try:
        scene = controller.getScene()
        root = scene.getRoot()
    except Exception:
        return None

    index = max(0, int(path_value))
    ratio = max(0.0, min(1.0, path_value - int(path_value)))

    for node in nodes(root):
        try:
            layer_data = node.callDecoration("getLayerData")
        except Exception:
            layer_data = None
        if not layer_data:
            continue

        try:
            layer = layer_data.getLayer(layer_number)
            polygons = list(layer.polygons)
        except Exception:
            continue

        remaining = index
        for polygon in polygons:
            try:
                data = polygon.data
                point_count = len(data)
            except Exception:
                continue
            if point_count <= 0:
                continue
            if remaining >= point_count:
                remaining -= point_count
                continue

            try:
                a = data[remaining]
                ax, ay, az = float(a[0]), float(a[1]), float(a[2])
                if ratio > 0.0001 and remaining + 1 < point_count:
                    b = data[remaining + 1]
                    bx, by, bz = float(b[0]), float(b[1]), float(b[2])
                    ax = ax * (1.0 - ratio) + bx * ratio
                    ay = ay * (1.0 - ratio) + by * ratio
                    az = az * (1.0 - ratio) + bz * ratio

                world = node.getWorldPosition()
                wx = float(getattr(world, "x", world[0] if hasattr(world, "__getitem__") else 0.0))
                wy = float(getattr(world, "y", world[1] if hasattr(world, "__getitem__") else 0.0))
                wz = float(getattr(world, "z", world[2] if hasattr(world, "__getitem__") else 0.0))
                return ax + wx, ay + wy, az + wz
            except Exception:
                return None

    return None
