"""Travel classifications matching Cura's four motion categories.

Positive E first repays retracted filament; a partial prime leaves the tool
retracted. A negative balance denotes firmware retraction (G10/G11), whose
distance is not present in the G-code. Each tool keeps its own balance.
"""

TRAVEL_NAMES = ("TRAVEL", "TRAVEL_RETRACTING", "TRAVEL_RETRACTED", "TRAVEL_PRIMING")


def is_travel(name):
    return name in TRAVEL_NAMES


def advance(balances, tool, delta):
    balance = balances.get(tool, 0.0)
    if delta < -1e-9:
        balances[tool] = min(1e6, balance - delta) if balance >= 0 else balance
        return "TRAVEL_RETRACTING"
    if delta > 1e-9:
        if balance != 0:
            balances[tool] = max(0.0, balance - delta) if balance > 0 else 0.0
            return None if balance > 0 and delta > balance + 1e-7 else "TRAVEL_PRIMING"
        return None
    return "TRAVEL_RETRACTED" if balance != 0 else "TRAVEL"


def layer_states(index, layer):
    """One state per motion, from the same modal seed as cold hydration."""
    es = index.motion_extrusion[layer] if layer < len(index.motion_extrusion) else ()
    tools = index.motion_tools[layer] if layer < len(index.motion_tools) else ()
    if len(es) != index.motion_count(layer):
        return
    balances = dict(index.layer_start_retractions[layer]) if layer < len(index.layer_start_retractions) else {}
    if not balances and layer < len(index.layer_start_retracted) and index.layer_start_retracted[layer]:
        tool = index.layer_start_tools[layer] if layer < len(index.layer_start_tools) else 0
        balances[tool] = -1.0
    events = iter(index.firmware_retractions[layer] if layer < len(index.firmware_retractions) else ())
    event = next(events, None)
    for motion, delta in enumerate(es):
        tool = tools[motion] if motion < len(tools) else 0
        while event is not None and event[0] <= motion:
            balances[event[1]] = -1.0 if event[2] else 0.0
            event = next(events, None)
        yield advance(balances, tool, delta)
