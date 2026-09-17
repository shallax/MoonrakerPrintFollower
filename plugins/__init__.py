def getMetaData():
    return {}


def register(app):
    # Keep imports lazy so the package's pure modules can be imported in tools
    # and tests without eagerly importing Cura/UM Qt integration modules.
    from .MoonrakerPrintFollower import MoonrakerPrintFollower
    from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
    from .MoonrakerOutputDevicePlugin import MoonrakerOutputDevicePlugin

    follower = MoonrakerPrintFollower(app)
    # The leak-hunt instrument ships OFF: the settings' diagnostics
    # toggle ("Log memory diagnostics") gates every tick, so an idle
    # timer is the whole cost until it is enabled.
    from .LeakProbe import start_leak_probe
    start_leak_probe(follower._runtime, app)

    output_plugin = MoonrakerOutputDevicePlugin(app, follower)
    return {
        "extension": follower,
        "output_device": output_plugin,
        "machine_action": MoonrakerFollowerMachineAction(app, follower, output_plugin),
    }
