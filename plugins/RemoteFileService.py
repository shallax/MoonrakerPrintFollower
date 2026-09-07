from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .Core import RemoteFileIdentity
from .GCodeRepository import GCodeRepository


@dataclass
class RemoteFileState:
    identity: Optional[RemoteFileIdentity] = None
    metadata_job_key: Optional[Tuple[str, int, int]] = None


class RemoteFileService:
    """Authoritative owner of remote-file identity and local G-code cache state."""

    def __init__(self) -> None:
        self.repository = GCodeRepository()
        self.state = RemoteFileState()

    @property
    def identity(self) -> Optional[RemoteFileIdentity]:
        return self.state.identity

    @identity.setter
    def identity(self, value: Optional[RemoteFileIdentity]) -> None:
        self.state.identity = value

    @property
    def metadata_job_key(self) -> Optional[Tuple[str, int, int]]:
        return self.state.metadata_job_key

    @metadata_job_key.setter
    def metadata_job_key(self, value: Optional[Tuple[str, int, int]]) -> None:
        self.state.metadata_job_key = value

    @property
    def cached_filename(self) -> Optional[str]:
        return self.repository.cached.filename

    @cached_filename.setter
    def cached_filename(self, value: Optional[str]) -> None:
        self.repository.cached.filename = None if value is None else str(value)

    @property
    def cached_path(self) -> Optional[str]:
        return self.repository.cached.path

    @cached_path.setter
    def cached_path(self, value: Optional[str]) -> None:
        self.repository.cached.path = None if value is None else str(value)

    @property
    def cached_job_key(self) -> Optional[Tuple[str, int, int]]:
        return self.repository.cached.job_key

    @cached_job_key.setter
    def cached_job_key(self, value: Optional[Tuple[str, int, int]]) -> None:
        self.repository.cached.job_key = value

    def cache_matches(self, filename: str, job_key: Optional[Tuple[str, int, int]]) -> bool:
        return bool(
            self.cached_filename == str(filename or "")
            and self.cached_path
            and self.cached_job_key == job_key
        )

    def adopt(
        self,
        filename: str,
        path: str,
        job_key: Optional[Tuple[str, int, int]],
    ) -> Optional[str]:
        return self.repository.adopt(filename, path, job_key)

    def discard_cache(self) -> Optional[str]:
        return self.repository.discard()

    def clear_identity(self) -> None:
        self.state = RemoteFileState()
