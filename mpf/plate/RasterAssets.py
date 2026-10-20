# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own raster publication, consumer references and scoped file retirement.

"""Image-file lifetime independent of surfaces, scheduling and QML objects."""
import os
import shutil
import tempfile
import threading
from PyQt6.QtCore import QUrl
from .RasterFiles import png_file


def file_key(path):
    return os.path.normcase(os.path.normpath(path))


class RasterAssets:
    def __init__(self):
        self.directory = tempfile.mkdtemp(prefix='mpf-raster-%d-' % os.getpid())
        self._owners = {}
        self._serial = 0
        self._holds = {}
        self._closed = threading.Event()

    def acquire(self):
        if self._closed.is_set(): return 0
        self._serial += 1
        self._owners[self._serial] = frozenset()
        return self._serial

    def replace(self, owner, urls):
        if owner not in self._owners: return
        self._owners[owner] = frozenset(self._paths(urls, own_only=True))

    def release(self, owner):
        self._owners.pop(owner, None)

    def hold(self, owner, url):
        if self._closed.is_set() or self.held(owner) == str(url or ""): return
        if url: self._holds[owner] = str(url)
        else: self._holds.pop(owner, None)

    def held(self, owner):
        return self._holds.get(owner, '')

    def _paths(self, urls, *, own_only=False):
        for url in urls:
            parsed = QUrl(str(url))
            if not parsed.isLocalFile(): continue
            path = file_key(parsed.toLocalFile())
            if not own_only or os.path.dirname(path) == file_key(self.directory):
                yield path

    def referenced(self, surface_urls=()):
        paths = set(self._paths((*surface_urls, *self._holds.values())))
        for files in self._owners.values(): paths.update(files)
        return paths

    def publish(self, image, name):
        if self._closed.is_set(): return ''
        url = png_file(image, self.directory, name)
        if self._closed.is_set():
            self._remove_directory()
            return ''
        return url

    def discard_result(self, images, surface_urls=()):
        if not isinstance(images, tuple) or not images: return
        if images[0] == 'checkpoints':
            urls = [url for _boundary, url in images[1]]
        elif images[0] == 'full':
            urls = [images[2], images[4], images[6]]
        elif images[0] in ('prefix', 'nav'):
            urls = [images[2]]
        else:
            return
        self._discard(urls, self.referenced(surface_urls))

    def discard_unpublished(self, urls):
        """Worker-owned outputs have not entered any presentation reference set."""
        self._discard(urls, frozenset())

    def _discard(self, urls, protected):
        for path in self._paths(urls, own_only=True):
            if path in protected: continue
            try: os.unlink(path)
            except OSError: pass

    def prune(self, surface_urls=(), keep=64):
        try:
            referenced = self.referenced(surface_urls)
            entries = []
            for name in os.listdir(self.directory):
                path = os.path.join(self.directory, name)
                if file_key(path) in referenced or '.tmp-' in name: continue
                try: entries.append((os.stat(path).st_mtime, path))
                except OSError: continue
            for _mtime, path in sorted(entries)[:-keep] if keep else entries:
                try: os.unlink(path)
                except OSError: pass
        except OSError: pass

    def accounting(self):
        count, size = 0, 0
        try:
            for name in os.listdir(self.directory):
                try:
                    size += os.stat(os.path.join(self.directory, name)).st_size
                    count += 1
                except OSError: pass
        except OSError: pass
        return {'rasterDirFiles':count, 'rasterDirBytes':size}

    def _remove_directory(self):
        try: shutil.rmtree(self.directory, ignore_errors=True)
        except OSError: pass

    def close(self):
        self._closed.set()
        self._owners.clear()
        self._holds.clear()
        self._remove_directory()
