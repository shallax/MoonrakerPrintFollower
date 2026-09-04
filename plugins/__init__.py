from .MoonrakerPrintFollower import MoonrakerPrintFollower
from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction


def getMetaData():
    return {}


def register(app):
    follower = MoonrakerPrintFollower(app)
    return {
        "extension": follower,
        "machine_action": MoonrakerFollowerMachineAction(app, follower),
    }
