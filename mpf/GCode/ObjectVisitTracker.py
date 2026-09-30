"""Incremental, bounded printed-object visitation and late-polygon replay.

One tracker owns the frontier and its geometry evidence. The service supplies
its current index view; no scheduler, transport or presentation state leaks in.
"""
from __future__ import annotations

import time
from .PlateProgress import motion_edges as _motion_edges
from ..Geometry.Polygons import segment_in_polygon, polygon_bounds

def _polygon_identity(polygon):
    """A polygon's CONTENT identity: its pairs, as a hashable tuple.

    The live walk cannot key on object identity — the coordinator
    rebuilds the rows (and their polygon lists) from the status on
    every poll, so equal geometry arrives as a fresh object. Only the
    content says whether the geometry actually changed."""
    return tuple((point[0], point[1]) for point in polygon)


class ObjectVisitTracker:
    _VISITED_WALK_BUDGET_S = 0.008
    _VISITED_WALK_STEP = 64
    _VISITED_REPLAY_SHARE = 0.5

    def __init__(self):
        self.reset()

    def reset(self):
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
        self._visited_settled = frozenset()
        self._visited_replay_upto = -1
        self._visited_pending = frozenset()

    def observe(self, view, anchor, split, rows):
        """The per-layer printed objects: which polygons the executed
        EXTRUSION edges have touched. Built HERE (the raw arrays never
        cross the boundary) and READ BACK FROM THE LAYER'S START — an
        attach part-way through a layer still marks everything the
        toolhead already printed (the live ruling: the DEFINE order
        is not the print order on every machine, so the visits are
        the truth). The walk advances only the new edges per poll.

        The edges are the G-code's own motion edges, and only the
        extruding ones count: a travel that merely crosses or ends
        inside a polygon deposits nothing there, while an extrusion
        edge that clips a corner does — the visit follows the material,
        never the motion endpoint.

        A bare cursor is not a valid cache here: EXCLUDE_OBJECT_DEFINE
        executes mid-layer on some machines, so a polygon can arrive
        after the extrusion it covers has already been walked. The
        cursor is therefore kept beside `_visited_settled` — the
        (name, content) geometry the consumed range has been judged
        against. A poll whose geometry still matches it walks only its
        new edges; a poll carrying a new or changed polygon replays the
        consumed range, for those polygons alone. The visited set
        only ever grows, so a backwards split keeps its verdicts.

        That replay is BOUNDED: one poll spends at most
        `_VISITED_WALK_BUDGET_S` on the walk and keeps its water mark,
        so a dense layer fills in over consecutive polls instead of
        stalling the Qt thread. Nothing is provisional — every poll's
        verdict is the truth about the edges walked so far, and the
        cursor advances by exactly the work done, so repeated polls
        never duplicate a scan. A cut replay resumes from its own
        frontier: the geometry it is judging is remembered, and the
        whole consumed range is still replayed once for it (the
        late-DEFINE ruling), merely spread over polls."""
        if view is None or split is None or anchor is None:
            return frozenset()
        index = view._index
        entries = []
        for row in rows:
            polygon = row.get("polygon")
            if not polygon or not row.get("name"):
                continue
            entries.append((row["name"], _polygon_identity(polygon),
                            polygon, polygon_bounds(polygon)))
        with index.cache_lock:
            if self._visited_key != (anchor,):
                self._visited_key = (anchor,)
                self._visited = set()
                self._visited_upto = 0
                self._visited_settled = frozenset()
                self._visited_replay_upto = 0
                self._visited_pending = frozenset()
            if not entries:
                # No usable polygon: nothing can be marked, so the
                # consumed range is never walked. The cursor still
                # advances — a polygon arriving later replays from the
                # layer's start regardless of it.
                if split > self._visited_upto:
                    self._visited_upto = split
                    self._visited_replay_upto = split
                self._visited_settled = frozenset()
                self._visited_pending = frozenset()
                return frozenset(self._visited)
            settled = self._visited_settled
            current = []
            pending = []
            for entry in entries:
                name, key = entry[0], entry[1]
                current.append((name, entry[2], entry[3]))
                if (name, key) not in settled:
                    pending.append(entry)
            # An object already marked printed cannot change its
            # verdict, so only the unmarked geometry is worth judging.
            outstanding = [entry for entry in pending if entry[0] not in self._visited]
            geometry = frozenset((name, key) for name, key, _p, _b in outstanding)
            if not geometry <= self._visited_pending:
                # Geometry this frontier has never judged: the replay
                # restarts at the layer's start.
                self._visited_replay_upto = 0
            self._visited_pending = geometry
            now = time.monotonic()
            deadline = now + self._VISITED_WALK_BUDGET_S
            if geometry and self._visited_upto > self._visited_replay_upto:
                # The late/changed geometry, against everything already
                # consumed. Replaying only these polygons is enough: the
                # settled ones have already seen every consumed edge.
                # The replay yields to the live delta after its own
                # share, so a long replay never starves the poll's own
                # verdict.
                self._visited_replay_upto = self._visit_edges(
                    index, anchor, self._visited_replay_upto, self._visited_upto,
                    [(name, polygon, bounds) for name, _k, polygon, bounds in outstanding],
                    min(deadline,
                        now + self._VISITED_WALK_BUDGET_S * self._VISITED_REPLAY_SHARE))
            if split > self._visited_upto:
                reached = self._visit_edges(index, anchor, self._visited_upto, split,
                                            current, deadline)
                if self._visited_replay_upto >= self._visited_upto:
                    # The delta walked its range with EVERY polygon, so
                    # a caught-up frontier rides it.
                    self._visited_replay_upto = reached
                self._visited_upto = reached
            if not geometry or self._visited_replay_upto >= self._visited_upto:
                # Every unmarked polygon has been judged against the
                # whole consumed range (or there is none): nothing is
                # left on the frontier.
                self._visited_replay_upto = self._visited_upto
                self._visited_pending = frozenset()
            self._visited_settled = frozenset(
                (name, key) for name, key, _p, _b in entries
                if (name, key) not in self._visited_pending)
            return frozenset(self._visited)

    def _visit_edges(self, index, anchor, first, stop, polygons, deadline=None):
        """Mark every polygon an extruding edge in [first, stop) meets.

        Returns the water mark the walk reached: *stop* when the whole
        range was covered, a lower motion when the deadline cut it
        short. The caller keeps its cursor there — the seek reconstructs
        the state, so a cut walk resumes exactly where it stopped.

        A polygon already in the visited set is dropped up front, and
        the list is re-dropped whenever a hit marks another: an object's
        verdict only ever grows, so retesting it buys nothing but
        vertices. An empty list walks no edge at all — the rest of the
        range has nothing left to decide.
        """
        remaining = [entry for entry in polygons if entry[0] not in self._visited]
        if not remaining:
            return stop
        check = None if deadline is None else first + self._VISITED_WALK_STEP
        for motion, x0, y0, x1, y1, _feature, extruding in _motion_edges(index, anchor, first):
            if motion >= stop:
                return stop
            if check is not None and motion >= check:
                # The deadline may cut the walk, never before the first
                # step: every poll advances the cursor.
                if time.monotonic() >= deadline:
                    return motion
                check = motion + self._VISITED_WALK_STEP
            if not extruding:
                continue
            left, right = (x0, x1) if x0 <= x1 else (x1, x0)
            bottom, top = (y0, y1) if y0 <= y1 else (y1, y0)
            marked = False
            for name, polygon, bounds in remaining:
                # The bounds reject most pairs for the price of four
                # comparisons, before any vertex is touched. Every
                # hull the edge meets records the visit — overlapping
                # object hulls all touch the toolhead's path, and the
                # verdict must never depend on the define order.
                if right < bounds[0] or left > bounds[2] or top < bounds[1] or bottom > bounds[3]:
                    continue
                if segment_in_polygon(x0, y0, x1, y1, polygon):
                    self._visited.add(name)
                    marked = True
            if marked:
                remaining = [entry for entry in remaining
                             if entry[0] not in self._visited]
        return stop
