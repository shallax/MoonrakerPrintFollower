# Architecture

Moonraker Print Follower deliberately keeps Cura-facing UI, Moonraker transport,
G-code indexing, and Preview following separated so a UI or printer capability
change does not have to destabilise the core follower.

## Core following

- `MoonrakerClient.py` owns the follower's core Moonraker status/download requests
  and generation-guards callbacks when printer configuration changes.
- `GCodeIndex.py` is pure G-code indexing/timing/motion logic and is covered by
  file fixtures rather than Cura runtime tests.
- `FollowController.py` contains follow-mode decisions.
- `CuraAdapter.py` and `NativeNozzleFallback.py` isolate Cura-specific Preview
  compatibility details.
- `MoonrakerPrintFollower.py` remains the orchestration boundary: Cura lifecycle,
  scene/view binding, remote-file loading, and coordination of the modules above.

## Monitor

The Monitor model is intentionally layered, with one class at each responsibility:

1. `MoonrakerMonitorModel.py` — HTTP/session lifecycle, core print state, ETA,
   capabilities, webcams, power and basic peripherals.
2. `MoonrakerMonitorRuntime.py` — follower-aware physical-layer resolution.
3. `MoonrakerMonitorControls.py` — printer actions and live tuning.
4. `MoonrakerMonitorTypedControls.py` — typed macro parameters, PWM, MCU stats,
   remembered webcams and bed-mesh Preview integration.

Core status is processed once in the base class and extended through
`_after_core_status`; subclasses must not re-poll or re-emit the same status.
Only the active Cura printer's Monitor may poll. Every Monitor HTTP reply carries a
request-generation token so a late reply from a previous URL/printer is ignored.

`configfile.config` is a static/heavy Klipper object. It is refreshed with
capability discovery, while only `save_config_pending` fields are included in the
one-second dynamic poll.

The active QML chain is `MoonrakerMonitorBedMesh.qml` →
`MoonrakerMonitorDashboard.qml` → `MoonrakerMonitor.qml`.

## Upload/output

- `MoonrakerOutputDevice.py` owns output preparation, power-on/readiness and upload.
- `MoonrakerOutputDeviceLifecycle.py` adds Cura/QML-safe deferred dialog teardown,
  folder discovery and exactly-once terminal write signalling.
- `MoonrakerOutputDevicePlugin.py` owns one cached device per Cura machine and
  activates polling only for the currently selected printer.

## Release invariants

- All QML must pass `tools/check_qml.py`; duplicate properties are a hard failure.
- Tests are discovered automatically with `unittest discover`.
- Python sources compile on 3.10, 3.11 and 3.12 in CI.
- The built `.curapackage` must be an exact byte-for-byte projection of the plugin
  source tree and contain no caches, editor backups or temporary patch files.
- New behaviour after the 3.0.0 seal belongs in a later version.

## Deliberate future refactors

`MoonrakerPrintFollower.py` is still the largest orchestration module, and there
are separate HTTP implementations for following, Monitor, and output/upload.
Those are real maintenance debts, but collapsing them immediately before a release
would couple independently working paths and create more release risk than it
removes. A later refactor should extract orchestration services and a shared small
HTTP/session utility behind existing tests, without changing user-visible behaviour.
