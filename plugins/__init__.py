from .MoonrakerPrintFollower import MoonrakerPrintFollower
from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
from .MoonrakerOutputDevicePlugin import MoonrakerOutputDevicePlugin


def getMetaData():
    return {}


def register(app):
    follower = MoonrakerPrintFollower(app)
    output_plugin = MoonrakerOutputDevicePlugin(app, follower)
    return {
        "extension": follower,
        "output_device": output_plugin,
        "machine_action": MoonrakerFollowerMachineAction(app, follower, output_plugin),
    }
