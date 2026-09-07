def getMetaData():
    return {}


def register(app):
    # Keep imports lazy so the package's pure modules can be imported in tools
    # and tests without eagerly importing Cura/UM Qt integration modules.
    from .MoonrakerPrintFollower import MoonrakerPrintFollower
    from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
    from .MoonrakerOutputDevicePlugin import MoonrakerOutputDevicePlugin

    follower = MoonrakerPrintFollower(app)

    output_plugin = MoonrakerOutputDevicePlugin(app, follower)
    return {
        "extension": follower,
        "output_device": output_plugin,
        "machine_action": MoonrakerFollowerMachineAction(app, follower, output_plugin),
    }
