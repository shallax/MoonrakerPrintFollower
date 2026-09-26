"""Single owner for the live plate's accepted motion boundary.

Geometry matching supplies a raw physical candidate. This policy owns the
per-print/layer floor, ambiguity evidence and search-window feedback. Keeping
it independent of Qt and the index makes it deterministic and testable.
"""


class PlateSplitTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None
        self.floor = None
        self.refined = None
        self.advance_max = 512
        self.stall_polls = 0
        self.below_floor_polls = 0
        self.awaiting_layer_entry = False
        self.awaiting_resume_match = False

    def begin(self, job_key, anchor):
        """Start an observation, returning whether this is the next live layer."""
        key = (job_key, anchor)
        previous = self.key
        advanced = (previous is not None and previous[0] == job_key
                    and anchor == previous[1] + 1)
        if key != previous:
            self.reset()
            self.key = key
            self.awaiting_layer_entry = advanced
        return advanced

    @property
    def payload_ahead_window(self):
        if self.floor is not None and self.refined == self.floor:
            return self.advance_max * 2
        return 4096

    def observe_payload_advance(self, candidate):
        """Learn the observed jump only from the payload's physical match."""
        if candidate is not None and self.floor is not None and candidate > self.floor:
            self.advance_max = max(self.advance_max, candidate - self.floor)

    def accept(self, coarse, refined, raw, has_live, advanced=False, entry_confirmed=True, paused=False):
        """Publish a candidate, then update the floor from independent evidence.

        `refined` is the ordinary monotonic candidate; `raw` is the unclamped
        geometric match. A repeated below-floor raw match may correct the
        accepted floor even though the displayed split remains monotonic on
        ordinary noisy observations. With no live telemetry, coarse progress
        remains the only available estimate.
        """
        floor = self.floor
        if paused and floor is not None:
            # A pause macro can cross previously printed geometry. Its
            # repeated matches are neither extrusion nor correction evidence.
            self.stall_polls = 0
            self.below_floor_polls = 0
            self.awaiting_resume_match = has_live
            return floor
        if self.awaiting_resume_match:
            if raw is not None and raw >= floor:
                self.awaiting_resume_match = False
            else:
                self.stall_polls += 1
                self.below_floor_polls = 0
                return floor
        if has_live and ((advanced and floor is None)
                         or (self.awaiting_layer_entry and not entry_confirmed)):
            refined = None
            raw = None
        elif not has_live or raw is not None:
            self.awaiting_layer_entry = False
        if refined is None:
            if self.refined is not None and floor is not None:
                split = floor
            elif not has_live:
                split = coarse if floor is None else max(floor, coarse)
            else:
                split = 0 if floor is None else floor
        else:
            self.refined = refined
            split = refined if floor is None else max(floor, refined)

        if floor is None or not has_live or (raw is not None and raw > floor):
            self.stall_polls = 0
        else:
            self.stall_polls += 1
        # Search failures expand the next search, but are not independent
        # evidence that the floor overshot. Only consecutive physical
        # matches below it can authorize a correction.
        if has_live and floor is not None and raw is not None and raw < floor:
            self.below_floor_polls += 1
        else:
            self.below_floor_polls = 0
        if self.below_floor_polls >= 3:
            split = raw
            self.refined = raw
            self.floor = split
            self.below_floor_polls = 0
            self.stall_polls = 0
            return split

        self.floor = split if floor is None else max(floor, split)
        return split
