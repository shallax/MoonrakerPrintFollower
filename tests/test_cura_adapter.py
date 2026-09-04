import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mpf_cura_adapter", ROOT / "plugins" / "CuraAdapter.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
active_machine_identity = MODULE.active_machine_identity


class _Stack:
    def getId(self):
        return "machine-123"

    def getName(self):
        return "Voron 2.4"


class _AppBeforeMachineSelection:
    def __init__(self):
        self.machine_manager_touched = False

    def getGlobalContainerStack(self):
        return None

    def getMachineManager(self):
        self.machine_manager_touched = True
        raise AssertionError("MachineManager must not be forced during plugin bootstrap")


class _AppWithMachine:
    def getGlobalContainerStack(self):
        return _Stack()

    def getMachineManager(self):
        raise AssertionError("Global stack is authoritative; MachineManager is unnecessary")


class CuraAdapterTests(unittest.TestCase):
    def test_bootstrap_identity_does_not_force_machine_manager(self):
        app = _AppBeforeMachineSelection()
        self.assertEqual(active_machine_identity(app), ("unknown", "Unknown Cura printer"))
        self.assertFalse(app.machine_manager_touched)

    def test_established_global_stack_supplies_identity(self):
        self.assertEqual(active_machine_identity(_AppWithMachine()), ("machine-123", "Voron 2.4"))


if __name__ == "__main__":
    unittest.main()
