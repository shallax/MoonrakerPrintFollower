from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class OperationPhase(str, Enum):
    IDLE = "idle"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    CURA_LOADING = "cura_loading"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class RemoteFileIdentity:
    filename: str
    size: int = 0
    modified: float = 0.0
    uuid: str = ""

    def stable_key(self) -> str:
        if self.uuid:
            return f"uuid:{self.uuid}"
        return f"file:{self.filename}|size:{int(self.size)}|modified:{self.modified:.6f}"

    def matches_job(self, filename: str, size: int) -> bool:
        return self.filename == filename and (self.size <= 0 or size <= 0 or self.size == size)


@dataclass
class OperationContext:
    phase: OperationPhase = OperationPhase.IDLE
    filename: Optional[str] = None
    job_key: Optional[Tuple[str, int, int]] = None
    force_load: bool = False
    local_path: Optional[str] = None
    started_at: Optional[float] = None
    message: str = ""

    def reset(self, phase: OperationPhase = OperationPhase.IDLE) -> None:
        self.phase = phase
        self.filename = None
        self.job_key = None
        self.force_load = False
        self.local_path = None
        self.started_at = None
        self.message = ""

    def transition(
        self,
        phase: OperationPhase,
        *,
        filename: Optional[str] = None,
        job_key: Optional[Tuple[str, int, int]] = None,
        local_path: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        """Move the remote-operation state forward without scattering flags.

        Fields are only replaced when explicitly supplied so callers can move
        from resolving -> downloading -> Cura loading/indexing while retaining
        the identity of the same operation.
        """
        self.phase = phase
        if filename is not None:
            self.filename = filename
        if job_key is not None:
            self.job_key = job_key
        if local_path is not None:
            self.local_path = local_path
        if message is not None:
            self.message = message

    @property
    def is_busy(self) -> bool:
        return self.phase in {
            OperationPhase.RESOLVING,
            OperationPhase.DOWNLOADING,
            OperationPhase.CURA_LOADING,
            OperationPhase.INDEXING,
        }

    @property
    def is_cura_loading(self) -> bool:
        return self.phase == OperationPhase.CURA_LOADING

    @property
    def is_downloading(self) -> bool:
        return self.phase == OperationPhase.DOWNLOADING

    @property
    def is_indexing(self) -> bool:
        return self.phase == OperationPhase.INDEXING
