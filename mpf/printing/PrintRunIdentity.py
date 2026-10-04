"""Attest an active Moonraker history run, independently of follower job serials."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class PrintRunIdentity:
    job_id: str
    started_at: float
    filename: str
    binding: str

    def as_dict(self):
        return {"jobId": self.job_id, "startedAt": self.started_at,
                "filename": self.filename, "binding": self.binding}


def attest_active_run(payload, filename, binding):
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("result")
    jobs = value.get("jobs") if isinstance(value, Mapping) else None
    if not isinstance(jobs, list) or not jobs or not isinstance(jobs[0], Mapping):
        return None
    row = jobs[0]
    job_id, started = row.get("job_id"), row.get("start_time")
    if (row.get("status") != "in_progress" or row.get("end_time") is not None
            or row.get("filename") != filename or not isinstance(job_id, str)
            or not job_id or len(job_id) > 128 or type(started) not in (int, float)
            or not 0 < started <= 1e12 or not isfinite(started)):
        return None
    return PrintRunIdentity(job_id, float(started), filename, binding)
