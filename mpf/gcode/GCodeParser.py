"""G-code token and marker recognition shared by scanning and hydration."""
from __future__ import annotations


import re

from typing import Dict, Optional, Tuple


_LAYER_COMMENT = re.compile(rb"^\s*;LAYER:\s*-?\d+\s*$", re.IGNORECASE)
_CURA_LAYER_VALUE = re.compile(rb"^\s*;LAYER:\s*(-?\d+)\s*$", re.IGNORECASE)
_ORCA_LAYER = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*\d+\s*/\s*\d+\s*$", re.IGNORECASE)
_ORCA_LAYER_VALUE = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*(\d+)\s*/\s*\d+\s*$", re.IGNORECASE)
_PRUSA_LAYER_CHANGE = re.compile(rb"^\s*;LAYER_CHANGE\s*$", re.IGNORECASE)
_STATS_MARKER = re.compile(
    rb"^\s*SET_PRINT_STATS_INFO\b.*\bCURRENT_LAYER\s*=\s*(-?\d+)",
    re.IGNORECASE,
)
_MOTION = re.compile(rb"^\s*(?:N\d+\s*)?G0?[0-3](?!\d)", re.IGNORECASE)
_ELAPSED = re.compile(rb"^\s*;TIME_ELAPSED:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
_COMMAND = re.compile(rb"^\s*(?:N\d+\s*)?([GMT]\d+)(?!\d)", re.IGNORECASE)
# XYZE and modal feedrate F are read here: an arc's centre offsets are not
# positions and never move the XYZ state, so they are parsed separately
# (_ARC_WORD) and only on a G2/G3 line.
_AXIS = re.compile(rb"([XYZEF])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
# An arc's I/J/K centre offsets, and the R a radius-form arc would carry
# (Klipper rejects that form; the R is what tells the two apart).
_ARC_WORD = re.compile(rb"([IJKR])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
# The motion words that describe an arc, in every spelling the motion
# regex accepts: G2/G02 and G3/G03. Both spellings are arcs to the
# parser, so both must be arcs to the geometry as well.
_ARC_CLOCKWISE = frozenset((b"G2", b"G02"))
_ARC_COUNTER = frozenset((b"G3", b"G03"))
# The slicer's feature marker. Leading whitespace is tolerated: a
# post-processed or macro-generated file does not always write it at
# column 0, and a missed marker silently mis-colours a whole block. The
# value runs to the line's end (a marker with no value at all is not one).
# The prefix is the per-line filter: the marker is a whole-line comment,
# so nothing else on a move line can start with it, and testing that is
# several times cheaper than searching every line for the substring.
_TYPE_PREFIX = b";TYPE:"
_TYPE_COMMENT = re.compile(rb"^\s*;TYPE:\s*(\S.*?)\s*$")
# A baked end-of-layer pause: the pause command word standing alone at
# the line start (comment lines never match). The PauseAtHeight
# post-processor emits the configured pause command inside its
# ;TYPE:CUSTOM block at the END of the target layer's moves, so the
# line's offset resolves to that layer through the block ranges.
_PAUSE_COMMAND = re.compile(rb"^\s*(?:PAUSE|M0|M25)\b")
# The extrusion rule is the G-code's own: a move with a POSITIVE E step
# extrudes, anything else (no E, a flat E, a falling E) deposits nothing
# and is travel. No magnitude floor — the slicer already decided; a
# floor misread slow extrusion as travel twice (the 0.05 floor ate the
# live file's fine walls, the epsilon ate a 0.05 mm layer height's
# short skin segments: the live reports).
# The layer-format sniff window: the file head read before the scan.
_MARKER_SNIFF_BYTES = 262144


def _parse_axes(code: bytes) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for match in _AXIS.finditer(code):
        try:
            values[match.group(1).decode("ascii").upper()] = float(match.group(2))
        except (UnicodeDecodeError, ValueError):
            continue
    return values


# The fast motion front's grammar: exactly what the three per-line
# regexes accept for the shape slicers emit for ~every motion line —
# b'G0'-b'G3' at column 0, uppercase, then space-separated axis words
# whose letter sits against its number. Any other shape returns None
# and the caller keeps the regex path (the parity contract: the fast
# path never reinterprets a line, it only claims the safe subset).
_AXIS_LETTERS = (88, 89, 90, 69, 70)   # X Y Z E F
_AXIS_LOWER = (120, 121, 122, 101, 102)  # x y z e f
_FAST_MOTIONS = (b"G0", b"G1", b"G2", b"G3")


def _fast_motion_line(stripped: bytes) -> Optional[Tuple[bytes, Dict[str, float]]]:
    """The cheap front for the dominant motion line shape (the seek
    profile: the three regexes cost most of the raw hydrate's second).
    ``stripped`` is the line's code part (the ``;`` comment already
    cut). Returns (command, axes) only when the line is exactly the
    safe shape; None always means the caller's regex path decides."""
    if len(stripped) < 3 or stripped[0] != 71:  # b'G'
        return None
    digit = stripped[1]
    if not 48 <= digit <= 51:  # b'0'..b'3' — G4/G10+ and everything else fall back
        return None
    third = stripped[2:3]
    if third not in (b"", b" ", b"\t"):
        return None  # G1X5-style crowding is the regex path's business
    tokens = stripped.split()
    axes: Dict[str, float] = {}
    for tok in tokens[1:]:
        if len(tok) < 2:
            if tok and tok[0] in _AXIS_LETTERS + _AXIS_LOWER:
                return None  # a spaced axis word — the regex reads it
            continue  # lone I/J/F/R words carry no axis value
        letter = tok[0]
        if letter in _AXIS_LOWER:
            return None  # lowercase words are the regex path's
        if letter not in _AXIS_LETTERS:
            continue
        if tok[1] not in b"+-.0123456789" or b"_" in tok:
            return None  # float() accepts shapes the regex grammar refuses
        try:
            axes[chr(letter)] = float(tok[1:])
        except ValueError:
            return None
    return b"G" + bytes((digit,)), axes


def _parse_arc_words(code: bytes) -> Dict[str, float]:
    """An arc line's I/J/K offsets (and its R, if it carries one).

    These are NOT positions: an I or a J describes where the centre sits
    relative to the move's start, so reading them as axes would move the
    head sideways and corrupt every following edge. They are parsed on
    their own, only on a G2/G3 line, and the arc plane decides which two
    of them apply (see ArcGeometry.descriptor).
    """
    values: Dict[str, float] = {}
    for match in _ARC_WORD.finditer(code):
        try:
            values[match.group(1).decode("ascii").upper()] = float(match.group(2))
        except (UnicodeDecodeError, ValueError):
            continue
    return values
