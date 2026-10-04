"""The alert's own evidence: the frame that raised it and a bounded
per-print score timeline, in a folder beside the detection assets.

Everything here is best-effort. An alert must never fail because a
diagnostic file could not be written, so each writer returns the path it
wrote or "" and lets the caller carry on.
"""

import json
import os
import shutil
import time
import tempfile
from collections import deque
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
        if any(os.path.islink(path) for path in (root, os.path.join(root, "detection"), directory)):
            return ""
        os.makedirs(directory, mode=0o700, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(at if at is not None else time.time()))
        path = os.path.join(directory, "frame-%s-%s-%s-%s.jpg"
                            % (_token(printer), _token(print_key), stamp, _token(level)))
        descriptor, temporary = tempfile.mkstemp(prefix=".frame-", suffix=".jpg", dir=directory)
        os.close(descriptor)
        try:
            if not image.save(temporary, "JPG", 85):
                return ""
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
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
        if any(os.path.islink(path) for path in (root, os.path.join(root, "detection"), directory)):
            return ""
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.path.islink(path):
            return ""
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags, 0o600), "r+", encoding="utf-8") as handle:
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps({"at": round(float(at), 3), "score": score,
                                     "raw": round(float(raw), 6)}) + "\n")
            handle.flush()
            handle.seek(0)
            samples = deque(maxlen=MAX_SAMPLES)
            count = 0
            for line in handle:
                samples.append(line)
                count += 1
            if count > 2 * MAX_SAMPLES:
                # O_APPEND must be removed for rewriting on this same no-follow fd.
                handle.close()
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)),
                               "w", encoding="utf-8") as rewrite:
                    rewrite.writelines(samples)
    except (OSError, TypeError, ValueError):
        return ""
    _prune(directory, "timeline-", MAX_TIMELINES)
    return path


def clear(root: str) -> None:
    """Drop the evidence: the removal path takes the prints with the
    downloads, so nothing of the user's stays behind."""
    directory = evidence_directory(root)
    # Never traverse a substituted preferences/evidence directory.
    for parent in (root, os.path.join(root, "detection")):
        if os.path.islink(parent):
            raise ValueError("Unsafe detection evidence path (symbolic link)")
    if os.path.islink(directory):
        os.unlink(directory)
    elif os.path.isdir(directory):
        for entry in os.scandir(directory):
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                try:
                    os.unlink(entry.path)
                except FileNotFoundError:
                    pass
