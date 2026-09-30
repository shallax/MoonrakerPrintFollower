"""Qt's own QTest module, loaded once with its fallbacks.

Synthesized input and the event-loop settle both need it, and neither
owns it: the loader caches the module it found — or the failure it hit —
so one import attempt serves the whole run and its error can be reported
without repeating it.
"""
from __future__ import annotations

QT_TEST = None


QT_TEST_ERROR = ""


def _import_qtest():
    # The injected binding: PyQt6-Qt6 6.6.0 + PyQt6 6.6.0 staged on the
    # interpreter's path at launch (the bundle ships no QtTest). QTest
    # synthesizes events INSIDE Qt — the working click path in this
    # Xvfb environment, where X-level button activation never lands.
    global QT_TEST, QT_TEST_ERROR
    if QT_TEST is not None:
        return QT_TEST
    try:
        from PyQt6 import QtTest
        QT_TEST = QtTest
    except Exception as first_error:
        # The bundle's PyQt6 package is already imported and lacks
        # QtTest; load the wheel's binding module and sip into the
        # EXISTING package instead of shadowing the package (a second
        # PyQt6 package would double-load QtCore).
        try:
            # The staged wheel dir is exported to the boot (CURA_WHEELS)
            # and is the one place the binding exists on every host —
            # the old /tmp/mpf/qt6wheel path was dev-box-only. The sip
            # module ships under a versioned filename; if the wheel dir
            # has none, the bundle's already-imported sip serves (both
            # are 6.6.0, one ABI).
            import importlib.util
            import sys
            import glob
            import os
            wheel = os.environ.get("CURA_WHEELS") or "/tmp/mpf/qt6wheel"
            test_path = f"{wheel}/PyQt6/QtTest.abi3.so"
            sip_candidates = sorted(glob.glob(f"{wheel}/PyQt6/sip.cpython-*.so"))
            if "PyQt6.sip" not in sys.modules and sip_candidates:
                sip_spec = importlib.util.spec_from_file_location("PyQt6.sip", sip_candidates[0])
                sip_module = importlib.util.module_from_spec(sip_spec)
                sys.modules["PyQt6.sip"] = sip_module
                sip_spec.loader.exec_module(sip_module)
            test_spec = importlib.util.spec_from_file_location("PyQt6.QtTest", test_path)
            test_module = importlib.util.module_from_spec(test_spec)
            sys.modules["PyQt6.QtTest"] = test_module
            test_spec.loader.exec_module(test_module)
            QT_TEST = test_module
        except Exception as exc:
            QT_TEST = False
            QT_TEST_ERROR = f"{first_error} | fallback: {exc!r}"
    return QT_TEST


def qtest_error():
    """The last QTest import failure, read live.

    The cache below is rebound by _import_qtest, so the server asks for
    the current value through this accessor rather than importing a name
    that was bound before the import ran.
    """
    return QT_TEST_ERROR
