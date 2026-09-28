"""Seeded config variants the harness boots from, as a transform over
an already-seeded config directory.

The committed fixture is an install that has already run the plugin's
one-shot. Some legs must NOT start from that:

  clean         a machine that has never run the plugin (the
                first-install leg's boot 1)
  premigration  the 4.3.0-era v1 blob (the migration leg's boot 1)

Each transform must LAND: a silent no-op would run the leg against the
fixture it exists to differ from, and the leg would then pass for the
wrong reason. Both raise when the thing they remove is absent.

tools/ui_test.sh carries the same two transforms inline for the
container leg. This module is what the native host scripts call, so a
native leg and a container leg boot from the same state; the two copies
are kept in step by hand because the container leg is a green path and
rewriting it is a separate change.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

PLUGIN_DIR_NAME = "MoonrakerPrintFollower"
STATE_FILE = "moonrakerprintfollower_sections.json"
CFG_FILE = "cura.cfg"
SECTION = "[moonrakerprintfollower]"


def _paths(config_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    cfg = config_dir / CFG_FILE
    if not cfg.is_file():
        raise SystemExit(f"seed_variants: no {CFG_FILE} under {config_dir}")
    return config_dir / PLUGIN_DIR_NAME, cfg


def _drop_section(cfg: pathlib.Path) -> None:
    """The plugin's own [section] leaves cura.cfg, and its absence is
    an error — the fixture is the only place the section is written."""
    lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, dropping = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            dropping = stripped == SECTION
        if not dropping:
            kept.append(line)
    if len(kept) == len(lines):
        raise SystemExit(f"seed_variants: {cfg} carries no {SECTION} section to remove")
    cfg.write_text("".join(kept), encoding="utf-8")


def clean(config_dir: pathlib.Path) -> list[str]:
    """What a first install must NOT have: the plugin's config folder,
    the old state file and the cura.cfg section go; Cura's own machine
    and preferences stay."""
    folder, cfg = _paths(config_dir)
    removed = []
    if folder.is_dir():
        shutil.rmtree(folder)
        removed.append(PLUGIN_DIR_NAME + "/")
    state = config_dir / STATE_FILE
    if state.exists():
        state.unlink()
        removed.append(STATE_FILE)
    if not removed:
        raise SystemExit(f"seed_variants: the clean seed found nothing to remove under {config_dir}")
    _drop_section(cfg)
    return removed + [f"the {SECTION} section"]


# The 4.3.0-era blob: two machine records — the multi-machine leg's
# switch target rides the second, and its console history makes the
# per-machine transcript migration part of the proof.
LEGACY_BLOB = {
    "FDM Printer Base Description": {"url": "http://127.0.0.1:7125", "enabled": True},
    "Second Machine": {"url": "http://127.0.0.1:7126", "enabled": True,
                       "console_history": ["// second machine history"]},
}


def premigration(config_dir: pathlib.Path) -> list[str]:
    """The tree rewound to the v1 blob the one-shot migrates from."""
    folder, cfg = _paths(config_dir)
    removed = []
    if folder.is_dir():
        shutil.rmtree(folder)
        removed.append(PLUGIN_DIR_NAME + "/")
    if not removed:
        raise SystemExit(f"seed_variants: premigration found no {PLUGIN_DIR_NAME}/ under {config_dir}")
    _drop_section(cfg)
    with open(cfg, "a", encoding="utf-8") as handle:
        handle.write(f"{SECTION}\n")
        handle.write("printer_configs_v1 = %s\n" % json.dumps(LEGACY_BLOB))
    state = config_dir / STATE_FILE
    state.write_text(json.dumps({"whatsNewSeen": "4.4.0", "sections": {}}), encoding="utf-8")
    return removed + [f"the {SECTION} section rewritten to the v1 blob (2 records)"]


VARIANTS = {"clean": clean, "premigration": premigration}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in VARIANTS:
        print(f"usage: {argv[0]} {{{'|'.join(VARIANTS)}}} <config-dir>", file=sys.stderr)
        return 2
    config_dir = pathlib.Path(argv[2])
    if not config_dir.is_dir():
        print(f"seed_variants: {config_dir} is not a directory", file=sys.stderr)
        return 1
    removed = VARIANTS[argv[1]](config_dir)
    print(f"seed_variants: {argv[1]} seed removed " + ", ".join(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
