def getMetaData():
    return {}


def register(app):
    # Keep imports lazy so the package's pure modules can be imported in tools
    # and tests without eagerly importing Cura/UM Qt integration modules.
    from .MoonrakerPrintFollower import MoonrakerPrintFollower
    from .MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
    from .MoonrakerOutputDevicePlugin import MoonrakerOutputDevicePlugin

    # TEMPORARY overnight-leak instrumentation (stripped before
    # release): the diagnostic snapshot logs RSS, tracemalloc growth,
    # per-class QML counts and the runtime's collection sizes to
    # ~/moonraker_leak.log once a minute.
    from .LeakProbe import start_leak_probe

    follower = MoonrakerPrintFollower(app)
    # Parent the probe's timer to the application: the probe itself
    # has no owner after register() returns and would be collected
    # before its first tick (the sanity run's empty log).
    start_leak_probe(follower._runtime, app)

    output_plugin = MoonrakerOutputDevicePlugin(app, follower)
    return {
        "extension": follower,
        "output_device": output_plugin,
        "machine_action": MoonrakerFollowerMachineAction(app, follower, output_plugin),
    }
