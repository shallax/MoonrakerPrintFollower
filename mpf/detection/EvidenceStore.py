"""The alert's own evidence: the frame that raised it and a bounded
per-print score timeline, in a folder beside the detection assets.

Everything here is best-effort. An alert must never fail because a
diagnostic file could not be written, so each writer returns the path it
wrote or "" and lets the caller carry on.
"""

import json
import os
import time
from hashlib import sha256

EVIDENCE_DIRECTORY = "evidence"
# The bound, in files and in lines: the newest few prints' timelines and
# the newest handful of frames — enough to look at the incident that
# just happened, never an unbounded archive of a user's prints.
MAX_FRAMES = 12
MAX_TIMELINES = 6
# One hour at the ten-second cadence.
MAX_SAMPLES = 360


def evidence_directory(root: str) -> str:
    """The folder the Diagnostics tab reveals."""
    return os.path.join(root, "detection", EVIDENCE_DIRECTORY)


def _token(text) -> str:
    # Only ever a file-name token: print keys are arbitrary strings
    # (file names, job identities) and must not shape a path.
    return sha256(str(text).encode("utf-8")).hexdigest()[:12]


def _prune(directory: str, prefix: str, keep: int) -> None:
    try:
        paths = sorted((os.path.join(directory, name) for name in os.listdir(directory)
                        if name.startswith(prefix)),
                       key=os.path.getmtime, reverse=True)
    except OSError:
        return
    for path in paths[keep:]:
        try:
            os.unlink(path)
        except OSError:
            pass


def save_frame(root: str, image, *, printer, print_key, level, score, at=None) -> str:
    """The frame an alert was raised on, as a JPEG named for its print."""
    directory = evidence_directory(root)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(at if at is not None else time.time()))
        path = os.path.join(directory, "frame-%s-%s-%s-%s.jpg"
                            % (_token(printer), _token(print_key), stamp, _token(level)))
        if not image.save(path, "JPG", 85):
            return ""
    except OSError:
        return ""
    _prune(directory, "frame-", MAX_FRAMES)
    return path


def append_sample(root: str, *, printer, print_key, at, score, raw) -> str:
    """One analysed frame appended to its print's timeline.

    JSONL keeps the append O(1) per sample; the file is rewritten only
    when it has grown past twice the bound, which trims it back to the
    newest MAX_SAMPLES lines.
    """
    directory = evidence_directory(root)
    path = os.path.join(directory, "timeline-%s-%s.jsonl"
                        % (_token(printer), _token(print_key)))
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": round(float(at), 3), "score": score,
                                     "raw": round(float(raw), 6)}) + "\n")
        with open(path, encoding="utf-8") as handle:
            samples = handle.readlines()
        if len(samples) > 2 * MAX_SAMPLES:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(samples[-MAX_SAMPLES:])
    except (OSError, TypeError, ValueError):
        return ""
    _prune(directory, "timeline-", MAX_TIMELINES)
    return path


def clear(root: str) -> None:
    """Drop the evidence: the removal path takes the prints with the
    downloads, so nothing of the user's stays behind."""
    directory = evidence_directory(root)
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError:
            pass
