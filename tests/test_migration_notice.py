"""Pure tests for the migration-failure surfaces' ordering (the UX
spec): once per failure, after the What's-New gate when it is due,
and the record's toastShown latch. The Qt import guards the host run
(the container's suite exercises the real bindings)."""
import unittest

try:
    from plugins.MigrationNotice import MigrationNotice
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
    MigrationNotice = None


class _FakeSignal:
    """A plain signal double: connect/emit, no Qt (the host run)."""

    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self):
        for handler in list(self._handlers):
            handler()


class FakeModel:
    def __init__(self):
        self.whatsNewDismissed = _FakeSignal()


class FakePersistence:
    def __init__(self, record):
        self.record = record

    def migration_record(self):
        return dict(self.record) if self.record else None

    def set_migration_record(self, update):
        self.record = dict(self.record or {})
        self.record.update(update)
        return True


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MigrationNoticeTests(unittest.TestCase):
    def setUp(self):
        self.raised = []

    def _notice(self, record, gate=None, model=None):
        persistence = FakePersistence(record)
        notice = MigrationNotice(
            persistence,
            whats_new_gate=gate,
            raise_toast=lambda rec: self.raised.append(dict(rec)),
        )
        if model is not None:
            notice.attach_model(model)
        return notice, persistence

    def _failed(self):
        return {"status": "failed", "reason": "backup-failed", "backupWritten": False,
                "backupName": None, "toastShown": False, "bannerDismissed": False}

    def test_no_record_raises_nothing(self):
        notice, _ = self._notice(None)
        notice.announce()
        self.assertEqual(self.raised, [])

    def test_ok_record_raises_nothing(self):
        notice, _ = self._notice({"status": "ok", "toastShown": False})
        notice.announce()
        self.assertEqual(self.raised, [])

    def test_failure_raises_once_and_latches(self):
        notice, persistence = self._notice(self._failed(), gate=lambda: False)
        notice.announce()
        self.assertEqual(len(self.raised), 1)
        self.assertTrue(persistence.record["toastShown"])
        # A second announce (the escape hatch, a later boot) is quiet.
        notice.announce()
        self.assertEqual(len(self.raised), 1)

    def test_overlay_due_defers_to_the_dismissal_signal(self):
        model = FakeModel()
        notice, _ = self._notice(self._failed(), gate=lambda: True, model=model)
        notice.announce()
        self.assertEqual(self.raised, [])
        model.whatsNewDismissed.emit()
        self.assertEqual(len(self.raised), 1)

    def test_attach_model_after_announce_still_defers(self):
        # The model arrives after the boot-time announce: the attach
        # re-announces and the deferred path connects.
        notice, _ = self._notice(self._failed(), gate=lambda: True)
        notice.announce()
        self.assertEqual(self.raised, [])
        model = FakeModel()
        notice.attach_model(model)
        model.whatsNewDismissed.emit()
        self.assertEqual(len(self.raised), 1)

    def test_a_failed_raise_still_latches(self):
        # A broken toast host must not re-toast every launch: the
        # latch lands even when the surface failed.
        notice, persistence = self._notice(self._failed(), gate=lambda: False)
        notice._raise_toast = lambda record: (_ for _ in ()).throw(RuntimeError("no host"))
        notice.announce()
        self.assertEqual(self.raised, [])
        self.assertTrue(persistence.record["toastShown"])


if __name__ == "__main__":
    unittest.main()
