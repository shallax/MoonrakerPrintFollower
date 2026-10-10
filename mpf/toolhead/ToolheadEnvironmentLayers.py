"""Owned complete receiver planes; no Qt, GL, scene discovery or publication.

The caller fences each selector/record/query readback and retains its graphics
owners in the supplied ledger. An empty *selector* ends enumeration. A receiver
whose recovery is unavailable remains a real entry using ordinary map shading.
"""
from dataclasses import dataclass

import numpy as np

from .ToolheadEnvironmentPaths import MAX_ID, _generation
from .ToolheadCaptureValues import retained_array_bytes

METADATA_RESERVE = 64*1024
CHUNK_METADATA = 1024  # Three ndarray headers, tuple/list and gather references.


@dataclass(frozen=True, eq=False)
class LayerImage:
    key: tuple
    width: int
    height: int
    ranges: np.ndarray
    identities: np.ndarray
    colours: np.ndarray
    retained_bytes: int
    gpu_bytes: int
    peak_bytes: int
    samples: int = 1
    positions: tuple | None = None


def _allocation(array, shape):
    if (type(array) is not np.ndarray or array.dtype != np.float32 or array.shape != shape
            or not array.flags.c_contiguous or not array.flags.aligned):
        raise ValueError('Exact contiguous F32 layer readback required')
    root = array
    for _ in range(64):
        if isinstance(root, np.ndarray) and root.base is not None: root = root.base
        elif isinstance(root, memoryview): root = root.obj
        else: return id(root), retained_array_bytes(array)
    raise ValueError('Layer backing chain exceeds its bound')


class LayerBuilder:
    """Dynamic bounded compaction, never a silently truncated fixed layer count.

    Sort/gather scratch and simultaneous GPU output copies are reserved before
    receiving a layer. Published arrays own their bytes and are read-only.
    Neither a completed value nor a valid generation key grants a GPU read lease.
    """
    def __init__(self, key, width, height, *, existing_bytes, byte_budget, texel_limit, cancel=None,
                 samples=1, sample_positions=None):
        if (any(type(value) is not int for value in (width, height, existing_bytes, byte_budget, texel_limit))
                or not 0 < width <= 8192 or not 0 < height <= 8192 or existing_bytes < 0
                or byte_budget < 0 or not 0 < texel_limit <= (1 << 31)-1
                or type(samples) is not int or samples not in (1,4)):
            raise ValueError('Bounded exact layer storage required')
        if samples==4:
            if (type(sample_positions) is not tuple or len(sample_positions)!=4
                    or any(type(point) is not tuple or len(point)!=2
                           or any(type(v) is not float or not np.isfinite(v) or not 0<=v<=1 for v in point)
                           for point in sample_positions) or len(set(sample_positions))!=4):
                raise ValueError('Ordered original sample position certificate required')
        elif sample_positions is not None: raise ValueError('S1 values have no MS pattern')
        self.key = _generation(key)
        if type(self.key) is not tuple: raise ValueError('Immutable complete image key required')
        self.width, self.height = width, height
        self.samples, self.positions = samples, sample_positions
        self.pixels = width*height*samples
        if self.pixels > texel_limit: raise MemoryError('Pixel ranges exceed texture addressing')
        self.existing_bytes, self.byte_budget, self.texel_limit = existing_bytes, byte_budget, texel_limit
        self.cancel = cancel; self.refused = self.terminal = False; self.image = None
        self.matches = 0; self.chunks = []; self.peak_bytes = 0
        self._room(9*self.pixels)
        self.previous = np.full((self.pixels, 2), -1., np.float32)
        self.ended = np.zeros(self.pixels, bool)

    def _room(self, owned, new_chunks=0):
        if self.cancel is not None and self.cancel(): raise RuntimeError('Cancelled receiver compaction')
        peak = self.existing_bytes+owned+METADATA_RESERVE+CHUNK_METADATA*(len(self.chunks)+new_chunks)
        if peak > self.byte_budget: raise MemoryError('Combined layered receiver budget exceeded')
        self.peak_bytes = max(self.peak_bytes, peak)

    def _live(self, key):
        if self.refused or self.image is not None or _generation(key) != self.key:
            raise ValueError('Receiver compaction is stale or unavailable')

    def accept(self, key, depth, identity, records, colours):
        """All inputs belong to one fenced completed scalar/record/query layer."""
        try:
            self._live(key)
            if self.terminal: raise ValueError('Receiver layers already ended')
            shape = (self.height, self.width) if self.samples == 1 else (4,self.height,self.width)
            backing = dict(_allocation(array, expected) for array, expected in (
                (depth, shape if self.samples==1 else (*shape,2)), (identity, shape), (records, (*shape, 4)), (colours, (*shape, 4))))
            # Retained chunks24B/match plus 128B/pixel validation workspace.
            inputs = sum(backing.values())
            self._room(9*self.pixels+24*self.matches+inputs+128*self.pixels)
            selector=depth.reshape(-1,2) if self.samples==4 else None
            depth, identity = (depth.reshape(-1) if selector is None else selector[:,0]), identity.reshape(-1)
            records, colours = records.reshape(-1, 4), colours.reshape(-1, 4)
            if any(not np.isfinite(array).all() for array in (depth, identity, records, colours)):
                raise ValueError('Nonfinite receiver layer')
            empty = ((depth==2.) if selector is None else
                     ((depth==np.finfo(np.float32).max)&(selector[:,1]==1.))) & (identity==MAX_ID+1)
            valid = (((depth>=0.)&(depth<=1.)) if selector is None else
                     ((selector[:,1]==0.)&np.isfinite(depth))) & (identity>=0.) & (identity<=MAX_ID)
            valid &= identity == np.floor(identity)
            if not np.all(empty | valid): raise ValueError('Malformed receiver selector pair')
            if (np.any(records[empty] != 0.) or np.any(colours[empty] != 0.)
                    or np.any(valid & self.ended)):
                raise ValueError('Empty receiver pixels cannot reappear or carry payload')
            if (np.any(records[valid, 3] != 1.) or np.any(records[valid, 2] != 0.)
                    or np.any(records[valid, 0] != depth[valid])
                    or np.any(records[valid, 1] != identity[valid])):
                raise ValueError('Selected receiver has no exact record')
            ordered = (depth > self.previous[:, 0]) | ((depth == self.previous[:, 0]) &
                                                      (identity > self.previous[:, 1]))
            if np.any(valid & (self.previous[:,1]>=0.) & ~ordered): raise ValueError('Receiver cursor did not advance')
            fallback = np.all(colours == 0., axis=1)
            if np.any(valid & ~(fallback | (colours[:, 3] == 1.))):
                raise ValueError('Incomplete receiver radiance')
            count = int(np.count_nonzero(valid)); total = self.matches+count
            if total > self.texel_limit: raise MemoryError('Layer records exceed texture addressing')
            # Includes input readbacks, retained chunks, final CSR/count scans,
            # conservative 128B/match sort/gather workspace and GPU duplicates.
            self._room(inputs+max(137*self.pixels+24*total, 64*self.pixels+192*total), int(count > 0))
            if count:
                self.chunks.append((np.flatnonzero(valid).astype(np.uint32),
                    identity[valid].astype(np.uint32), colours[valid].copy()))
                self.previous[valid, 0] = depth[valid]; self.previous[valid, 1] = identity[valid]
                self.matches = total
            self.ended |= empty
            self.terminal = count == 0
            return self.terminal
        except Exception:
            self.refused = True; raise

    def finish(self, key):
        """Only explicit terminal selector proof can create the complete value."""
        try:
            self._live(key)
            if not self.terminal: raise ValueError('Receiver enumeration is incomplete')
            self._room(64*self.pixels+192*self.matches)
            if self.matches:
                pixels = np.concatenate([chunk[0] for chunk in self.chunks])
                identities = np.concatenate([chunk[1] for chunk in self.chunks])
                colours = np.concatenate([chunk[2] for chunk in self.chunks])
                order = np.lexsort((identities, pixels))
                self._room(64*self.pixels+192*self.matches)
                pixels = pixels[order]; identities = identities[order]; colours = colours[order]
                if np.any((pixels[1:] == pixels[:-1]) & (identities[1:] == identities[:-1])):
                    raise ValueError('Original receiver identity repeated at one pixel')
                counts = np.bincount(pixels, minlength=self.pixels)
            else:
                identities = np.empty(0, np.uint32); colours = np.empty((0, 4), np.float32)
                counts = np.zeros(self.pixels, np.uint64)
            ends = np.cumsum(counts, dtype=np.uint64)
            if (np.any(ends > self.texel_limit) or np.any(counts > self.texel_limit)
                    or (int(ends[-1]) if len(ends) else 0) != self.matches):
                raise MemoryError('Receiver prefix sums exceed texture addressing')
            ranges = np.empty((self.pixels, 2), np.uint32)
            ranges[:, 0] = ends-counts; ranges[:, 1] = counts
            for array in (ranges, identities, colours): array.setflags(write=False)
            size = sum(array.nbytes for array in (ranges, identities, colours))
            self._room(64*self.pixels+192*self.matches)
            self._live(key)
            self.image = LayerImage(self.key, self.width, self.height, ranges, identities, colours,
                size, size, self.peak_bytes, self.samples, self.positions)
            self.chunks.clear(); self.previous = self.ended = None
            return self.image
        except Exception:
            self.refused = True; raise
