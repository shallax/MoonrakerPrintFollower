"""The listing's thumbnails: their queue, their replies and their cache.

The policy this owner holds whole, rather than the fetcher alone:

- one one-shot fetch per (row, variant), following the METADATA's
  ``relative_path`` — the plain ``<file>.png`` sibling does not exist on
  Moonrakers current in the field, so it is never asked for;
- a bounded fetch queue whose slots always come back, whatever a fetch
  raises, because a leaked permit is a permanently stuck queue;
- an in-flight reply registry with an identity check, so a late reply
  cannot unregister its successor's fetch under the same key;
- a generation that invalidates both together, so a reply from the
  previous view retires without writing;
- the temp tree the bodies land in, whose file:// URLs die in the same
  publish as the entries that named them;
- a cache the listing REKEYS on rename and drops on delete, instead of
  every caller mutating it in place.

Nothing here publishes rows. A landing is one signal, never a listing
rebuild — the reason this cache is not part of the walk.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict, List, Sequence
from urllib.parse import quote

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply


MAX_THUMBNAIL_BYTES = 16 * 1024 * 1024


class ThumbnailCache(QObject):
    # The fetch queue's concurrency cap: scrolling past rows enqueues
    # their downloads, which fire together as the queue drains — a
    # bounded burst, never chained through the previous request's
    # completion handler (that serialised the queue and stretched the
    # settle across the whole listing).
    MAX_FETCHES = 3

    changed = pyqtSignal()

    def __init__(self, transport, bodies, parent=None) -> None:
        super().__init__(parent)
        self._transport = transport
        self._bodies = bodies
        self._root = tempfile.mkdtemp(prefix="mpf-thumbs-%d-" % os.getpid())
        self._entries: Dict[str, Dict[str, str]] = {}
        # In-flight replies ride this registry until their handlers run —
        # the transport's own lifetime pattern: a bare closure connected
        # to a network signal is a use-after-free trap in PyQt.
        self._replies: Dict[str, QNetworkReply] = {}
        self._generation = 0
        self._queue: List[tuple] = []
        self._active = 0

    # ---- lifecycle -------------------------------------------------

    def reset(self) -> None:
        """Every entry, reply and byte is the previous printer's: drop
        them together and start a fresh temp tree. The generation bump
        comes FIRST — the aborts below fire their handlers inline, and
        those must already read as stale or they would write into the
        tree this call is about to delete."""
        self._generation += 1
        self.abort()
        self._drop()

    def clear(self) -> None:
        """Regenerate every thumbnail with the file (a refresh). In-flight
        fetches retire through the generation guard, so only the cache and
        its tree reset here: the published file:// URLs die in the same
        publish, and a stale reply writes nothing."""
        self._generation += 1
        self._drop()

    def abort(self) -> None:
        """Hard-stop every in-flight fetch (printer change or shutdown).
        The bookkeeping resets FIRST: abort fires each reply's finished
        handler inline, and a drained registry means those handlers
        cannot issue new fetches the reset would orphan into a deleted
        temp tree."""
        replies = list(self._replies.values())
        self._replies = {}
        self._queue = []
        self._active = 0
        for reply in replies:
            try:
                reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _drop(self) -> None:
        self._queue = []
        self._active = 0
        self._entries = {}
        try:
            shutil.rmtree(self._root, ignore_errors=True)
        except Exception:
            pass
        self._root = tempfile.mkdtemp(prefix="mpf-thumbs-%d-" % os.getpid())

    # ---- the cache's own bookkeeping -------------------------------

    def payload(self) -> Dict[str, Dict[str, str]]:
        return {relpath: dict(entry) for relpath, entry in self._entries.items()}

    def drop(self, relpath: str) -> None:
        self._entries.pop(relpath, None)

    def adopt(self, old: str, new: str) -> None:
        """A renamed file keeps the thumbnails already fetched for it."""
        entry = self._entries.pop(old, None)
        if entry is not None:
            self._entries[new] = entry

    def rekey(self, directory: str, target: str) -> None:
        """Point every entry under ``directory`` at ``target`` (the folder
        rename's local bookkeeping — the walk re-confirms)."""
        prefix = directory + "/"
        moved = {}
        for relpath, entry in list(self._entries.items()):
            if relpath == directory or relpath.startswith(prefix):
                moved[target + relpath[len(directory):]] = entry
            else:
                moved[relpath] = entry
        self._entries = moved

    # ---- fetching --------------------------------------------------

    def request(self, rows: Sequence[Any], large: bool = False) -> None:
        """The visible rows' thumbnails: one one-shot fetch per row per
        variant, following the METADATA's thumbnail relative_path
        (``.thumbs/<name>-<size>.png``). Rows whose metadata has no
        thumbnail record as "none" (the placeholder) and are never
        fetched. The cache is keyed by relpath with TWO slots: the list
        cells fetch the small (>= 32 px) thumbnail — the largest
        preview's UI-thread decode stutters the list — and the print
        dialog asks for the large one with ``large=True``."""
        changed_any = False
        for row in rows:
            relpath = str(row.relpath)
            entry = self._entries.get(relpath) or {}
            slot = "state_large" if large else "state"
            url_slot = "url_large" if large else "url"
            if entry.get(slot):
                continue
            changed_any = True
            thumb_path = row.thumb_path if large else (row.thumb_small or row.thumb_path)
            entry[slot] = "none" if not thumb_path else "loading"
            entry[url_slot] = ""
            self._entries[relpath] = entry
            if thumb_path:
                # Enqueue, never fetch directly: the queue drains at a
                # bounded concurrency and the hourglass spins until the
                # callback lands.
                self._queue.append((relpath, row.root, thumb_path, large))
        if changed_any:
            self.changed.emit()
        self._drain()

    def _drain(self) -> None:
        while self._active < self.MAX_FETCHES and self._queue:
            relpath, root, thumb_path, large = self._queue.pop(0)
            self._active += 1
            self._fetch(relpath, root, thumb_path, large)

    def _fetch(self, relpath: str, root: str, thumb_path: str, large: bool) -> None:
        generation = self._generation
        try:
            directory = tempfile.mkdtemp(prefix="thumb-", dir=self._root)
            path = os.path.join(directory, os.path.basename(thumb_path) or "thumb.png")
            # relative_path is relative to the gcode FILE's parent: a
            # folder-resident file's thumbnail lives under
            # <root>/<dirname(relpath)>/.thumbs/, not <root>/.thumbs/
            # (live-proven against Moonraker's metadata builder).
            parent = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
            prefix = f"{quote(root, safe='/')}/{quote(parent, safe='/')}" if parent else quote(root, safe='/')
            request = self._transport.request(
                f"server/files/{prefix}/{quote(thumb_path, safe='/')}", timeout_ms=10000)
            request.setRawHeader(b"Accept", b"image/png")
            reply = self._transport.network.get(request)
            # All fetch state (the reply, relpath, target path,
            # generation) binds as a default argument and the signal
            # connects into a BOUND method, so nothing can be collected
            # mid-flight.
            self._replies[relpath] = reply
            self._bodies.watch(reply, MAX_THUMBNAIL_BYTES)
            reply.finished.connect(
                lambda r=reply, p=relpath, g=generation, t=path, l=large: self._finished(p, r, g, t, l)
            )
        except Exception:
            self._fail(relpath, large)
            self.changed.emit()
            # The slot must always come back, whatever failed — a
            # leaked permit is a permanently stuck queue.
            self._active = max(0, self._active - 1)
            self._drain()

    def _finished(self, relpath: str, reply, generation: int, path: str, large: bool) -> None:
        reply._mpf_body_finished = True
        # The identity check keeps a stale reply (a refresh cleared the
        # cache while it was still in flight) from unregistering its
        # successor's fetch under the same key.
        if self._replies.get(relpath) is reply:
            self._replies.pop(relpath, None)
        # One slot frees: the queue drains the next waiting row.
        self._active = max(0, self._active - 1)
        if generation != self._generation:
            self._retire(reply)
            self._drain()
            return
        try:
            # PyQt6 enum comparison: ``error() != 0`` is ALWAYS true —
            # the enum members never equal plain ints, so the success
            # path raised on every fetch. Compare against the enum.
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError("thumbnail fetch failed")
            data = self._bodies.take(reply, MAX_THUMBNAIL_BYTES)
            # No thumbnail to display: the hourglass must NEVER spin
            # forever — an empty or non-PNG body falls back to the
            # placeholder like any failure.
            if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("no thumbnail")
            with open(path, "wb") as handle:
                handle.write(data)
            entry = self._entries.get(relpath) or {}
            if large:
                entry["state_large"] = "ready"
                entry["url_large"] = QUrl.fromLocalFile(path).toString()
            else:
                entry["state"] = "ready"
                entry["url"] = QUrl.fromLocalFile(path).toString()
            self._entries[relpath] = entry
        except Exception:
            self._fail(relpath, large)
        self._retire(reply)
        self.changed.emit()
        # One slot frees: the queue drains the next waiting row
        # immediately — the fetches fire together, not chained.
        self._drain()

    def _fail(self, relpath: str, large: bool) -> None:
        """A cell that cannot show an image reads "failed", whatever the
        cause — the hourglass must never spin forever. Publishing is the
        caller's: one emit per landing, on the thumbnail channel alone."""
        entry = self._entries.get(relpath) or {}
        entry["state_large" if large else "state"] = "failed"
        self._entries[relpath] = entry

    @staticmethod
    def _retire(reply) -> None:
        try:
            reply.deleteLater()
        except Exception:
            pass
