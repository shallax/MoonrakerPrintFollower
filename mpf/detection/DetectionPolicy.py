"""Obico's adaptive, per-print failure decision over sampled model detections."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class DetectionState:
    name: str
    score: int | None
    raw_score: float | None = None


class DetectionPolicy:
    EWM_SPAN = 12
    SHORT_WINDOW = 310
    LONG_WINDOW = 7200
    BASELINE_LEARNING_FRAMES = 6
    SHORT_MULTIPLE = 3.8
    ESCALATION = 1.75

    def __init__(self, *, cadence_seconds: float = 10.0,
                 warning_threshold: int = 38, failure_threshold: int = 78,
                 safe_seconds: int = 300, sensitivity: float = 1.0):
        if not isfinite(cadence_seconds) or cadence_seconds <= 0:
            raise ValueError("Detection cadence must be positive")
        if (type(warning_threshold) is not int or type(failure_threshold) is not int
                or not 0 <= warning_threshold < failure_threshold <= 100):
            raise ValueError("Detection thresholds must be ordered percentages from 0 to 100")
        if type(safe_seconds) is not int or not 0 <= safe_seconds <= 900:
            raise ValueError("Detection safe period must be between 0 and 900 seconds")
        if type(sensitivity) not in (int, float) or not .8 <= sensitivity <= 1.2 or not isfinite(sensitivity):
            raise ValueError("Detection sensitivity must be between 0.8 and 1.2")
        self.sensitivity = float(sensitivity)
        self.warning_threshold = warning_threshold
        self.failure_threshold = failure_threshold
        self.safe_seconds = safe_seconds
        self._fresh_seconds = max(12.0, cadence_seconds * 3)
        self._long_mean = 0.0
        self._lifetime_frames = 0
        self.reset()

    @property
    def baseline(self) -> dict:
        return {"mean": self._long_mean, "frames": self._lifetime_frames}

    def restore_baseline(self, value) -> None:
        if not isinstance(value, dict):
            return
        mean, frames = value.get("mean"), value.get("frames")
        if (type(frames) is int and 0 <= frames <= 1_000_000_000
                and type(mean) in (float, int) and 0 <= mean <= 32768 and isfinite(mean)):
            self._long_mean, self._lifetime_frames = float(mean), frames

    def reset(self) -> None:
        self._context = None
        self._sample_at = None
        self._first_sample_at = None
        self._score = None
        self._raw_score = None
        self._level = "normal"
        self._current_frames = 0
        self._ewm_mean = 0.0
        self._short_mean = 0.0

    @staticmethod
    def _rolling(mean: float, value: float, count: int, window: int) -> float:
        return mean + (value - mean) / float(window if window <= count else count + 1)

    def _failing(self, escalation: float, elapsed_seconds: float) -> bool:
        if elapsed_seconds < self.safe_seconds:
            return False
        adjusted = (self._ewm_mean - self._long_mean) * self.sensitivity / escalation
        low, high = self.warning_threshold / 100, self.failure_threshold / 100
        if adjusted < low:
            return False
        if adjusted > high:
            return True
        return adjusted > (self._short_mean - self._long_mean) * self.SHORT_MULTIPLE

    def _normalized_score(self) -> int:
        low, high = self.warning_threshold / 100, self.failure_threshold / 100
        warning = min(high, max(low, (self._short_mean - self._long_mean) * self.SHORT_MULTIPLE))
        warning = max(warning, 1e-9)
        failure = warning * self.ESCALATION
        gap = (self._ewm_mean - self._long_mean) * self.sensitivity
        if gap > failure:
            result = self.failure_threshold + (gap - failure) / (failure * .5) * (
                100 - self.failure_threshold)
        elif gap > warning:
            result = self.warning_threshold + (gap - warning) / (failure - warning) * (
                self.failure_threshold - self.warning_threshold)
        else:
            result = gap / warning * self.warning_threshold
        return round(min(100.0, max(0.0, result)))

    def observe(self, confidence: float, *, now: float, context: tuple,
                print_elapsed_seconds: float | None = None) -> str:
        if not isfinite(confidence) or confidence < 0:
            raise ValueError("Detection confidence sum must be nonnegative and finite")
        if not isfinite(now):
            raise ValueError("Detection time must be finite")
        if print_elapsed_seconds is not None \
                and (not isfinite(print_elapsed_seconds) or print_elapsed_seconds < 0):
            raise ValueError("Print elapsed time must be nonnegative and finite")
        if not context or any(part is None for part in context):
            raise ValueError("Detection requires a print and camera identity")
        if context != self._context or (self._sample_at is not None
                                        and now - self._sample_at > self._fresh_seconds):
            self.reset()
            self._context = context
        if self._sample_at is not None and now < self._sample_at:
            raise ValueError("Detection samples must arrive in order")
        if self._first_sample_at is None:
            self._first_sample_at = now
        self._sample_at = now
        self._raw_score = confidence
        self._current_frames += 1
        self._lifetime_frames += 1
        self._ewm_mean = confidence * (2 / (self.EWM_SPAN + 1)) + self._ewm_mean * (1 - 2 / (self.EWM_SPAN + 1))
        self._short_mean = self._rolling(self._short_mean, confidence, self._current_frames, self.SHORT_WINDOW)
        learning = self._lifetime_frames <= self.BASELINE_LEARNING_FRAMES
        baseline_weight = self._lifetime_frames if learning else self.LONG_WINDOW
        self._long_mean += (confidence - self._long_mean) / baseline_weight
        if learning:
            # Calibrate on the cleared scene, then use a slow reference so a
            # newly introduced failure cannot immediately become normal.
            self._ewm_mean = self._short_mean = self._long_mean
            self._level = "learning"
            self._score = None
            return self._level
        elapsed = (print_elapsed_seconds if print_elapsed_seconds is not None
                   else now - self._first_sample_at)
        self._level = ("failure" if self._failing(self.ESCALATION, elapsed)
                       else "warning" if self._failing(1, elapsed) else "normal")
        # The banding follows the level, in the safe period as well: a
        # suppressed warning reports "normal", and a green state must
        # never carry a failing number.
        score = self._normalized_score()
        if self._level == "normal":
            score = min(score, max(0, self.warning_threshold - 1))
        elif self._level == "warning":
            score = max(self.warning_threshold, min(score, self.failure_threshold - 1))
        else:
            score = max(score, self.failure_threshold)
        self._score = score
        return self._level

    def state(self, *, now: float, context: tuple | None, active: bool) -> DetectionState:
        if not active:
            return DetectionState("idle", None)
        if context is None or context != self._context or self._sample_at is None:
            return DetectionState("waiting", None)
        if now - self._sample_at > self._fresh_seconds:
            return DetectionState("stale", None)
        return DetectionState(self._level, self._score, self._raw_score)
