"""Compare Cura's internal frame with macOS presentation, on a diagnostic run.

This is deliberately outside the release gate. A frameSwapped signal alone
cannot establish that WindowServer presented those pixels. Capture the two
boundaries separately and keep a native process sample for a stale surface.
No plugin preferences, models or printer state are changed by this probe.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("this diagnostic requires the macOS harness host")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "harness"))
    from tests.harness import runner
    out = Path(os.environ["HARNESS_RUN_DIR"]) / "renderer-diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    prefix = out / args.phase
    report = {"phase": args.phase, "time": time.time(),
              "render_loop": os.environ.get("QSG_RENDER_LOOP")}

    def still(suffix):
        target = str(prefix) + suffix + ".png"
        try:
            done = subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "png", target],
                                  capture_output=True, text=True, timeout=20)
            return {"path": target, "returncode": done.returncode,
                    "error": done.stderr[-2000:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"path": target, "error": str(exc)}

    try:
        report["hello"] = runner.rpc({"id": 1, "cmd": "hello"})
        # The driver starts before Cura creates its main QQuickWindow.
        report["window_ready"] = runner.wait_window()
        report["heartbeat"] = runner.rpc({"id": 1, "cmd": "frames", "heartbeat": True,
                                         "deadline_ms": 1500}, timeout=30)
    except Exception as exc:
        # A render-loop deadlock must still leave desktop evidence and a
        # native stack, rather than losing the probe to its first RPC.
        report["rpc_error"] = repr(exc)
    # Take the desktop picture FIRST: grabWindow may force a repaint and
    # heal a missing present. The second picture tells us if that happened.
    report["desktop_before_grab"] = still("-desktop-before-grab")
    internal = str(prefix) + "-internal.png"
    code = '''
from UM.Application import Application
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQuick import QQuickWindow
app = Application.getInstance()
main = app.getMainWindow()
rows = []
for w in QGuiApplication.allWindows():
    rows.append({"title": w.title(), "type": w.metaObject().className(),
                 "main": w is main, "visible": w.isVisible(), "exposed": w.isExposed(),
                 "rect": [w.x(), w.y(), w.width(), w.height()], "dpr": w.devicePixelRatio()})
image = main.grabWindow()
saved = image.save(PATH)
result = {"windows": rows, "saved": saved, "null": image.isNull(),
          "image_size": [image.width(), image.height()], "image_dpr": image.devicePixelRatio(),
          "graphics_api": main.rendererInterface().graphicsApi().value,
          "scene_graph_backend": QQuickWindow.sceneGraphBackend(),
          "context_preference": app.getPreferences().getValue("view/opengl_version_detect")}
'''.replace("PATH", repr(internal))
    try:
        report["internal"] = runner.exec_rpc(code, timeout=30, raise_on_error=True)
    except Exception as exc:
        report["internal"] = {"error": repr(exc)}
    time.sleep(2)
    report["desktop_after_grab"] = still("-desktop-after-grab")
    if args.phase == "after":
        # Harness-only experiment: distinguish Cura's external GL drawing
        # from Qt scene-graph presentation. Always restore the callback.
        detached = False
        try:
            detached = runner.exec_rpc('''
from UM.Application import Application
main = Application.getInstance().getMainWindow()
main.beforeRenderPassRecording.disconnect(main._render)
main.update()
result = True
''', timeout=30, raise_on_error=True) is True
            time.sleep(3)
            report["without_external_gl"] = {"detached": detached,
                "desktop": still("-without-external-gl")}
        except Exception as exc:
            report["without_external_gl"] = {"error": repr(exc)}
        finally:
            if detached:
                try:
                    report["external_gl_restored"] = runner.exec_rpc('''
from UM.Application import Application
from PyQt6.QtCore import Qt
main = Application.getInstance().getMainWindow()
main.beforeRenderPassRecording.connect(main._render, type=Qt.ConnectionType.DirectConnection)
main.update()
result = True
''', timeout=30, raise_on_error=True)
                except Exception as exc:
                    report["external_gl_restored"] = {"error": repr(exc)}
        # A second native QQuickWindow shares Cura's bundled Qt/backend
        # but has no Cura 3D callback or plugin content. Controlled colour
        # changes distinguish a general Qt presentation failure from the
        # main window's composition. This runs only after the real unit.
        created = False
        try:
            report["bare_qt_window"] = runner.exec_rpc('''
from UM.Application import Application
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtQuick import QQuickWindow
app = Application.getInstance()
control = QQuickWindow()
control.setFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
control.setTitle("Qt presentation control")
control.setPosition(40, 60)
control.resize(400, 300)
control.setColor(QColor("#ff0000"))
app._mpf_diagnostic_window = control
control.show()
control.raise_()
control.update()
result = {"rect": [control.x(), control.y(), control.width(), control.height()]}
''', timeout=30, raise_on_error=True)
            created = True
            time.sleep(2)
            report["bare_qt_red"] = still("-bare-qt-red")
            report["bare_qt_blue_update"] = runner.exec_rpc('''
from UM.Application import Application
from PyQt6.QtGui import QColor
control = Application.getInstance()._mpf_diagnostic_window
control.setColor(QColor("#0000ff"))
control.update()
result = {"colour": control.color().name(), "exposed": control.isExposed(),
          "graphics_api": control.rendererInterface().graphicsApi().value}
''', timeout=30, raise_on_error=True)
            time.sleep(2)
            report["bare_qt_blue"] = still("-bare-qt-blue")
        except Exception as exc:
            report["bare_qt_error"] = repr(exc)
        finally:
            if created:
                try:
                    report["bare_qt_closed"] = runner.exec_rpc('''
from UM.Application import Application
app = Application.getInstance()
app._mpf_diagnostic_window.hide()
app._mpf_diagnostic_window.deleteLater()
app._mpf_diagnostic_window = None
result = True
''', timeout=30, raise_on_error=True)
                except Exception as exc:
                    report["bare_qt_closed"] = {"error": repr(exc)}
    pid = report.get("hello", {}).get("pid")
    if not pid:
        found = subprocess.run(["/usr/bin/pgrep", "-f", "Contents/MacOS/UltiMaker-Cura"],
                               capture_output=True, text=True, timeout=5)
        candidates = [line for line in found.stdout.splitlines() if line.isdigit()]
        pid = int(candidates[0]) if candidates else None
    if pid:
        try:
            sampled = subprocess.run(["/usr/bin/sample", str(pid), "3", "-file",
                                      str(prefix) + "-process-sample.txt"],
                                     capture_output=True, text=True, timeout=15)
            report["sample"] = {"returncode": sampled.returncode,
                                "error": sampled.stderr[-2000:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["sample"] = {"error": str(exc)}
    (prefix.with_suffix(".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report.get("internal", {}).get("saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
