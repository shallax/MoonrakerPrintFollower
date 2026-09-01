from .MoonrakerPrintFollower import MoonrakerPrintFollower


def getMetaData():
    return {}


def register(app):
    return {"extension": MoonrakerPrintFollower(app)}
