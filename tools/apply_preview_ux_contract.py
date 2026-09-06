from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests" / "test_source_contracts.py"
text = path.read_text(encoding="utf-8")
old = '''        self.assertIn('base.followingPaused ? "Resume" : "Pause"', QML_ACTION)\n        self.assertNotIn('base.followingPaused ? "Resume" : "Pause"', QML_EMPTY)\n'''
new = '''        self.assertIn('base.followingPaused ? "Attach" : "Detach"', QML_ACTION)\n        self.assertNotIn('base.followingPaused ? "Attach" : "Detach"', QML_EMPTY)\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one legacy Preview follow-button contract, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Updated Preview follow-button source contract to Attach/Detach")
