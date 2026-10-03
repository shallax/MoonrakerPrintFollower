"""How an alert reaches the user: the desktop notification's gate, and
the evidence the alert leaves behind."""

import unittest
from unittest.mock import patch

from tests import qt_runtime_support as runtime_support
from tests.qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from mpf.detection import DesktopAlert


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class DesktopAlertTests(unittest.TestCase):
    def setUp(self):
        self._runtime = runtime_support.runtime()
        self.qt = self._runtime.__enter__()
        self.addCleanup(self._runtime.__exit__, None, None, None)

    def _with_application(self, state, tray):
        """The two seams DesktopAlert reads: the application state and
        Cura's own tray widget."""
        import sys
        from types import ModuleType, SimpleNamespace
        from PyQt6.QtGui import QGuiApplication

        package = ModuleType("UM.Qt")
        package.__path__ = []
        module = ModuleType("UM.Qt.QtApplication")
        application = SimpleNamespace(getInstance=lambda: SimpleNamespace(_tray_icon_widget=tray))
        module.QtApplication = application
        patches = [
            patch.object(QGuiApplication, "applicationState", staticmethod(lambda: state)),
            patch.dict(sys.modules, {"UM.Qt": package, "UM.Qt.QtApplication": module}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_an_active_cura_keeps_the_desktop_to_itself(self):
        from PyQt6.QtCore import Qt

        class Tray:
            messages = []

            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                self.messages.append(args)

            def icon(self):
                return None

        self._with_application(Qt.ApplicationState.ApplicationActive, Tray())
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertEqual(Tray.messages, [])

    def test_an_inactive_cura_shows_the_message_with_the_frame_icon(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIcon

        class Tray:
            def __init__(self):
                self.messages = []

            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                self.messages.append(args)

            def icon(self):
                return QIcon()

        tray = Tray()
        self._with_application(Qt.ApplicationState.ApplicationInactive, tray)
        self.assertTrue(DesktopAlert.notify("Title", "Body", frame_path="/tmp/mpf/frame.jpg",
                                            timeout_ms=7000))
        title, body, icon, timeout = tray.messages[0]
        self.assertEqual((title, body, timeout), ("Title", "Body", 7000))
        self.assertIsInstance(icon, QIcon)

    def test_every_refusal_is_logged_with_its_reason(self):
        # A live report of "no notification appeared" is otherwise
        # indistinguishable from the alert never firing: each refusal
        # says why in Cura's log.
        import sys
        from types import SimpleNamespace
        from PyQt6.QtCore import Qt
        logged = []
        logger = SimpleNamespace(log=lambda level, message, *args: logged.append(message % args))
        patcher = patch.dict(sys.modules, {"UM.Logger": SimpleNamespace(Logger=logger)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._with_application(Qt.ApplicationState.ApplicationActive, None)
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertTrue(any("active window" in line for line in logged), logged)
        logged.clear()
        self._with_application(Qt.ApplicationState.ApplicationInactive, None)
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertTrue(any("no tray widget" in line for line in logged), logged)
        logged.clear()

        class NoTray:
            def isSystemTrayAvailable(self):
                return False

        self._with_application(Qt.ApplicationState.ApplicationInactive, NoTray())
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertTrue(any("unavailable" in line for line in logged), logged)
        logged.clear()

        class Broken:
            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                raise RuntimeError("no daemon")

            def icon(self):
                return None

        self._with_application(Qt.ApplicationState.ApplicationInactive, Broken())
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertTrue(any("raised" in line for line in logged), logged)
        logged.clear()

        class Fine:
            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                pass

            def icon(self):
                return None

        self._with_application(Qt.ApplicationState.ApplicationInactive, Fine())
        self.assertTrue(DesktopAlert.notify("Delivered", "b"))
        self.assertTrue(any("shown" in line for line in logged), logged)

    def test_logging_never_breaks_the_alert(self):
        # The diagnostic is best-effort like the notification: a
        # missing or hostile logger must not take the alert path down
        # with it. A None entry makes the import itself fail.
        import sys
        from unittest.mock import patch
        from PyQt6.QtCore import Qt

        class Tray:
            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                pass

            def icon(self):
                return None

        self._with_application(Qt.ApplicationState.ApplicationInactive, Tray())
        with patch.dict(sys.modules, {"UM.Logger": None}):
            self.assertTrue(DesktopAlert.notify("t", "b"))

    def test_a_machine_without_a_tray_falls_through_quietly(self):
        from PyQt6.QtCore import Qt
        self._with_application(Qt.ApplicationState.ApplicationInactive, None)
        self.assertFalse(DesktopAlert.notify("t", "b"))

    def test_an_absent_notification_area_is_not_a_delivery(self):
        # Cura's widget exists where no tray does (measured: the
        # container reports isSystemTrayAvailable() False), and a
        # message handed to it there would claim a delivery that never
        # happened.
        from PyQt6.QtCore import Qt

        class Tray:
            def __init__(self):
                self.messages = []

            def isSystemTrayAvailable(self):
                return False

            def showMessage(self, *args):
                self.messages.append(args)

            def icon(self):
                return None

        tray = Tray()
        self._with_application(Qt.ApplicationState.ApplicationInactive, tray)
        self.assertFalse(DesktopAlert.notify("t", "b"))
        self.assertEqual(tray.messages, [], "a message went to a tray that cannot show it")

    def test_a_raising_tray_never_reaches_the_alert_path(self):
        from PyQt6.QtCore import Qt

        class Tray:
            def isSystemTrayAvailable(self):
                return True

            def showMessage(self, *args):
                raise RuntimeError("no notification daemon")

            def icon(self):
                return None

        self._with_application(Qt.ApplicationState.ApplicationInactive, Tray())
        self.assertFalse(DesktopAlert.notify("t", "b"))


if __name__ == "__main__":
    unittest.main()
