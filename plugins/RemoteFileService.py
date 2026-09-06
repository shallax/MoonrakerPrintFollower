from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .Core import RemoteFileIdentity


JobKey = Tuple[str, int, int]


@dataclass
class CachedGCode:
    filename: Optional[str] = None
    path: Optional[str] = None
    job_key: Optional[JobKey] = None

    def matches(self, filename: str, job_key: Optional[JobKey]) -> bool:
        return bool(self.path and self.filename == filename and self.job_key == job_key)


@dataclass
class RemoteFileState:
    identity: Optional[RemoteFileIdentity] = None
    metadata_job_key: Optional[JobKey] = None
    cached: CachedGCode = CachedGCode()


class RemoteFileService:
    """Authoritative owner of remote-file identity and local G-code cache state."""

    def __init__(self) -> None:
        self.state = RemoteFileState(cached=CachedGCode())

    @property
    def identity(self) -> Optional[RemoteFileIdentity]:
        return self.state.identity

    @identity.setter
    def identity(self, value: Optional[RemoteFileIdentity]) -> None:
        self.state.identity = value

    @property
    def metadata_job_key(self) -> Optional[JobKey]:
        return self.state.metadata_job_key

    @metadata_job_key.setter
    def metadata_job_key(self, value: Optional[JobKey]) -> None:
        self.state.metadata_job_key = value

    @property
    def cached_filename(self) -> Optional[str]:
        return self.state.cached.filename

    @cached_filename.setter
    def cached_filename(self, value: Optional[str]) -> None:
        self.state.cached.filename = None if value is None else str(value)

    @property
    def cached_path(self) -> Optional[str]:
        return self.state.cached.path

    @cached_path.setter
    def cached_path(self, value: Optional[str]) -> None:
        self.state.cached.path = None if value is None else str(value)

    @property
    def cached_job_key(self) -> Optional[JobKey]:
        return self.state.cached.job_key

    @cached_job_key.setter
    def cached_job_key(self, value: Optional[JobKey]) -> None:
        self.state.cached.job_key = value

    def cache_matches(self, filename: str, job_key: Optional[JobKey]) -> bool:
        return self.state.cached.matches(str(filename or ""), job_key)

    def adopt(self, filename: str, path: str, job_key: Optional[JobKey]) -> Optional[str]:
        previous = self.state.cached.path
        self.state.cached = CachedGCode(str(filename), str(path), job_key)
        return previous

    def discard_cache(self) -> Optional[str]:
        previous = self.state.cached.path
        self.state.cached = CachedGCode()
        return previous

    def clear_identity(self) -> None:
        self.state.identity = None
        self.state.metadata_job_key = None
