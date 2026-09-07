from __future__ import annotations

from typing import Optional, Tuple


def active_machine_identity(application) -> Tuple[str, str]:
    stack = None
    try:
        stack = application.getGlobalContainerStack()
    except Exception:
        pass
    # Do not fall back to the lazy MachineManager getter here. Cura 5.x
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


def _view_value(view, method: str, cast=int):
    try:
        return cast(getattr(view, method)())
    except Exception:
        return None


def preview_current_layer(view) -> Optional[int]:
    return _view_value(view, "getCurrentLayer", int)


def preview_minimum_layer(view) -> Optional[int]:
    return _view_value(view, "getMinimumLayer", int)


def preview_current_path(view) -> Optional[float]:
    return _view_value(view, "getCurrentPath", float)


def preview_minimum_path(view) -> Optional[int]:
    return _view_value(view, "getMinimumPath", int)


def preview_max_paths(view) -> Optional[int]:
    return _view_value(view, "getMaxPaths", int)


def set_preview_path(view, value: float) -> None:
    if view is not None and hasattr(view, "setPath"):
        view.setPath(value)


def set_preview_minimum_path(view, value: int) -> None:
    if view is not None and hasattr(view, "setMinimumPath"):
        view.setMinimumPath(value)
