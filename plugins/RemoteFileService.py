from __future__ import annotations

from dataclasses import dataclass, field
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
    cached: CachedGCode = field(default_factory=CachedGCode)


class RemoteFileService:
    """Own remote-file metadata identity and the local streamed G-code cache."""

    def __init__(self) -> None:
        self._state = RemoteFileState()

    @property
    def identity(self) -> Optional[RemoteFileIdentity]:
        return self._state.identity

    @property
    def metadata_job_key(self) -> Optional[JobKey]:
        return self._state.metadata_job_key

    @property
    def cached_filename(self) -> Optional[str]:
        return self._state.cached.filename

    @property
    def cached_path(self) -> Optional[str]:
        return self._state.cached.path

    @property
    def cached_job_key(self) -> Optional[JobKey]:
        return self._state.cached.job_key

    def set_identity(
        self,
        identity: Optional[RemoteFileIdentity],
        job_key: Optional[JobKey],
    ) -> None:
        self._state.identity = identity
        self._state.metadata_job_key = job_key if identity is not None else None

    def clear_identity(self) -> None:
        self._state.identity = None
        self._state.metadata_job_key = None

    def cache_matches(self, filename: str, job_key: Optional[JobKey]) -> bool:
        return self._state.cached.matches(str(filename or ""), job_key)

    def adopt(self, filename: str, path: str, job_key: Optional[JobKey]) -> Optional[str]:
        previous = self._state.cached.path
        self._state.cached = CachedGCode(str(filename), str(path), job_key)
        return previous

    def discard_cache(self) -> Optional[str]:
        previous = self._state.cached.path
        self._state.cached = CachedGCode()
        return previous

    def reset(self) -> Optional[str]:
        previous = self._state.cached.path
        self._state = RemoteFileState()
        return previous
