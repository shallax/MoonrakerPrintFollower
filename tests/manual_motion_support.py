"""Tiny in-memory firmware for queue tests; it has no network capability."""
from collections.abc import Mapping
import re


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


class Firmware:
    def __init__(self, data):
        self.data = data
        self.source = None
        self.position = None
        self.origin = [0.0, 0.0, 0.0]
        self.base = [0.0, 0.0, 0.0]

    def status(self):
        core, aux = self.data.snapshot.core, self.data.snapshot.auxiliary
        live = tuple((core.get('motion_report') or {}).get('live_position') or ())
        if live != self.source:
            self.source = live
            self.position = list(live[:3])
        result = thaw(core)
        result.update(thaw(aux))
        result.setdefault('configfile', {'config': {'printer': {'kinematics': 'corexy'}}})
        result.setdefault('toolhead', {})['position'] = self.position
        result.setdefault('gcode_move', {}).update(
            position=self.position, gcode_position=[p-b for p, b in zip(self.position or (), self.base, strict=False)],
            homing_origin=list(self.origin))
        return result

    def accepted(self, script):
        self.status()
        if not self.position or len(self.position) < 3:
            return
        relative = False
        for line in script.splitlines():
            if line == 'G91':
                relative = True
            elif line == 'G90':
                relative = False
            elif line.startswith('G1 '):
                for axis, text in re.findall(r'([XYZ])([-+0-9.eE]+)', line):
                    i = 'XYZ'.index(axis)
                    self.position[i] = self.position[i] + float(text) if relative else float(text) + self.base[i]
            elif line.startswith('SET_GCODE_OFFSET '):
                adjustment = re.search(r'Z_ADJUST=([-+0-9.eE]+)', line)
                delta = float(adjustment[1]) if adjustment else -self.origin[2]
                self.position[2] += delta
                self.base[2] += delta
                self.origin[2] += delta


def attach(data, commands):
    firmware = Firmware(data)
    original = commands.send

    def send(label, path, body=None):
        accepted = original(label, path, body)
        if accepted and path == 'printer/gcode/script':
            firmware.accepted(body['script'])
        return accepted

    def request(channel, method, path, callback, **kwargs):
        assert channel == 'manual-motion-check' and path == 'printer/objects/query'
        callback({'result': {'status': firmware.status()}}, None)
        return True

    commands.send, data.request = send, request
    return firmware


def attach_monitor(model, transport):
    """Keep real MonitorData/MonitorCommands, supply invented HTTP replies."""
    firmware = Firmware(model._data)
    original_request, original_send = model._data.request, model._commands.send

    def request(channel, method, path, callback, **kwargs):
        started = original_request(channel, method, path, callback, **kwargs)
        if started and channel == 'manual-motion-check':
            transport.requests[-1].callback({'result': {'status': firmware.status()}}, None)
        return started

    def send(label, path, body=None, **kwargs):
        accepted = original_send(label, path, body, **kwargs)
        if accepted and path == 'printer/gcode/script':
            firmware.accepted(body['script'])
        return accepted

    model._data.request, model._commands.send = request, send


def seed_limits(model):
    aux = dict(model._data.snapshot.auxiliary)
    if 'toolhead' in aux and 'configfile' in aux:
        return
    aux.setdefault('toolhead', {'homed_axes': 'xyz', 'axis_minimum': [0, 0, 0],
                                'axis_maximum': [200, 200, 200]})
    aux.setdefault('configfile', {'config': {'printer': {'kinematics': 'corexy'}}})
    model._data._update(auxiliary=aux)
