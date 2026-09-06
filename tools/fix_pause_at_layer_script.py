from pathlib import Path

path = Path(__file__).with_name("apply_pause_at_layer.py")
text = path.read_text(encoding="utf-8")
text = text.replace("{base_url.rstrip('/')}\\/printer", "{base_url.rstrip('/')}/printer")
path.write_text(text, encoding="utf-8")
print("Fixed pause-at-layer patch markers")
