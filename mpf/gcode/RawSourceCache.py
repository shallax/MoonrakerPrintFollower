"""Durable raw G-code beside its index and prepared layers."""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import threading
import time

from .CachePolicy import evict_to_budget, temporary_owner_alive


class RawSourceCache:
    def __init__(self, directory: str, max_bytes: int):
        self.directory = directory
        self.max_bytes = int(max_bytes)
        os.makedirs(directory, exist_ok=True)

    def _path(self, identity):
        digest = hashlib.sha256(identity.stable_key().encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.directory, f"p-{digest}", "source.gcode")

    @staticmethod
    def _pin(source):
        marker = f"{source}.tmp-{os.getpid()}-pin-{threading.get_ident()}-{time.time_ns()}"
        with open(marker, "xb"):
            pass
        return marker

    @staticmethod
    def unpin(marker):
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass

    @staticmethod
    def eligible(identity, filename):
        # A fallback identity or a listing's filename alone cannot prove
        # that a file still contains the same bytes after a restart.
        return (identity is not None and identity.filename == filename
                and identity.size > 0 and math.isfinite(identity.modified)
                and identity.modified > 0)

    def restore(self, identity, filename, destination, *, cancelled=None,
                progress=None, on_error=None):
        if not self.eligible(identity, filename):
            return False
        source = self._path(identity)
        marker = None
        restored = False
        try:
            if os.path.getsize(source) != identity.size:
                return False
            marker = self._pin(source)
            # A separate working link keeps active leases intact when a
            # cache clear or another process evicts the persistent entry.
            try:
                os.link(source, destination)
            except OSError:
                copied = 0
                with open(source, "rb") as reader, open(destination, "xb") as writer:
                    while chunk := reader.read(4 * 1024 * 1024):
                        if cancelled is not None and cancelled():
                            raise OSError("Raw cache restoration cancelled") from None
                        writer.write(chunk)
                        copied += len(chunk)
                        if progress is not None:
                            progress(copied)
            if (cancelled is not None and cancelled()
                    or os.path.getsize(destination) != identity.size
                    or os.path.getsize(source) != identity.size):
                return False
            os.utime(source, None)
            restored = True
            return marker
        except OSError as error:
            if on_error is not None and not isinstance(error, FileNotFoundError) \
                    and not (cancelled is not None and cancelled()):
                on_error(error)
            return False
        finally:
            if not restored:
                if marker is not None:
                    self.unpin(marker)
                try:
                    os.unlink(destination)
                except FileNotFoundError:
                    pass

    def publish(self, identity, filename, source):
        if not self.eligible(identity, filename):
            return
        if os.path.getsize(source) != identity.size:
            raise OSError("Downloaded G-code changed before cache publication")
        destination = self._path(identity)
        folder = os.path.dirname(destination)
        if not os.path.isdir(self.directory):
            return  # explicit cache clear retired the namespace
        try:
            os.mkdir(folder)
        except FileExistsError:
            pass
        temp = f"{destination}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        marker = None
        published = False
        try:
            marker = self._pin(destination)
            try:
                os.link(source, temp)
            except OSError:
                shutil.copyfile(source, temp)
            if os.path.getsize(temp) != identity.size:
                raise OSError("Raw G-code cache write is incomplete")
            # Never publish a partial name; a live tmp also protects the
            # folder from an index/prepared eviction in another session.
            os.replace(temp, destination)
            self.prune(keep=destination)
            published = True
            return marker
        finally:
            if marker is not None and not published:
                self.unpin(marker)
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass

    def prune(self, keep=None):
        totals = {}
        if not os.path.isdir(self.directory):
            return
        for entry in os.scandir(self.directory):
            if not entry.is_dir() or not entry.name.startswith("p-"):
                continue
            try:
                stats = [os.stat(os.path.join(entry.path, name))
                         for name in os.listdir(entry.path)
                         if name.endswith((".mpfi.gz", ".mpfp", "source.gcode"))]
            except OSError:
                continue
            if stats:
                totals[entry.path] = (max(stat.st_mtime for stat in stats),
                                      sum(stat.st_size for stat in stats))
        keep_dir = os.path.dirname(keep) if keep else None
        evict_to_budget(totals, self.max_bytes, None,
                        keep_dir)

    def sweep(self):
        if not os.path.isdir(self.directory):
            return
        for root, _dirs, names in os.walk(self.directory):
            for name in names:
                if ".gcode.tmp-" not in name or temporary_owner_alive(name):
                    continue
                try:
                    os.unlink(os.path.join(root, name))
                except OSError:
                    pass
