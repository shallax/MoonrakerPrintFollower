from UM.Logger import Logger

from .MoonrakerPrintFollower import MoonrakerPrintFollower
from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
from .MoonrakerOutputDevicePlugin import MoonrakerOutputDevicePlugin


def getMetaData():
    return {}


def register(app):
    follower = MoonrakerPrintFollower(app)
    try:
        imported = follower._config_store.migrate_moonraker_connection()
        if imported:
            Logger.log(
                "i",
                "Moonraker Print Follower: imported standalone Moonraker Connection settings for %d printer(s)",
                imported,
            )
    except Exception as exc:
        # Migration must never make Cura fail plugin startup. The old preference
        # data is left untouched, so a later run can safely try again.
        Logger.log("w", "Moonraker Print Follower: Moonraker Connection migration failed: %s", exc)

    output_plugin = MoonrakerOutputDevicePlugin(app, follower)
    return {
        "extension": follower,
        "output_device": output_plugin,
        "machine_action": MoonrakerFollowerMachineAction(app, follower, output_plugin),
    }
