"""Resource bounds shared by scanning, hydration and index-cache validation."""
from __future__ import annotations


_LARGE_FILE_COMPACT_THRESHOLD = 128 * 1024 * 1024
# Hardening bounds for hostile/corrupt gcode (panel security P2-4): a
# real gcode line is well under 1 KB, real prints stay under ~100k
# layers, and no single layer carries more than a few hundred thousand
# motions. Past a bound the index DEGRADES to the coarser fallbacks
# (byte-range fraction, last known layer) instead of growing structures
# without limit — a poisoned file on the printer must not OOM Cura.
_MAX_LINE_BYTES = 64 * 1024
_MAX_LAYER_BLOCKS = 100_000
_MAX_MOTIONS_PER_LAYER = 200_000
# The feature columns have their own bounds: a hostile file can write a
# distinct ;TYPE: value (or alternate travel and print every motion) on
# every line, and neither the vocabulary nor the run list may grow with
# the line count. Past the vocabulary cap every further distinct name is
# _TYPE_OTHER; past the run cap the layer's tail is _TYPE_OTHER too —
# coarser, never wrong in a way the caller cannot see.
_MAX_TYPE_NAMES = 64
_MAX_TYPE_NAME_BYTES = 64
_MAX_TYPE_RUNS_PER_LAYER = 4096
