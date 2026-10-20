# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-16 [9a7694f] Support Cura 5.14 beta 1 and advance CI version coverage
# 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
# 2026-09-27 [43ab36d] Accelerate follower rendering with retained GPU shader geometry

"""Regenerate the packaged Qt shader bundles; never needed by Cura users."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qsb', help='Qt Shader Tools qsb executable')
    args = parser.parse_args()
    executable = args.qsb or shutil.which('qsb') or shutil.which('pyside6-qsb')
    if executable is None:
        for name in ('pyside6-qsb', 'pyside6-qsb.exe'):
            sibling = Path(sys.executable).parent / name
            if sibling.is_file():
                executable = str(sibling)
                break
    if not executable:
        parser.error('Install Qt Shader Tools or supply --qsb; release packages already contain the bundles')
    directory = Path(__file__).resolve().parents[1] / 'mpf' / 'resources' / 'shaders'
    for name in ('stroke.vert', 'stroke.frag'):
        source = directory / name
        # Format 64 is readable by Qt 6.4 and later. Cura 5.7's pinned
        # dependencies already use Qt 6.6; keep the oldest bundle format
        # even when the build machine's shader tools are newer.
        subprocess.run([executable, '--qsbversion', '64', '--glsl', '100 es,120,150',
                        '--hlsl', '50', '--msl', '12', '-o', str(source) + '.qsb', str(source)],
                       check=True)
    print('Built shader bundles for Cura 5.7-5.14 beta 1 (Qt 6.4-compatible format; tested on Qt 6.6)')


if __name__ == '__main__':
    main()
