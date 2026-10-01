"""The files view model (4.3.0): the file projection's stable-identity
surface — a QAbstractListModel holding the current page's rows with
the relpath as the row identity, so a rebuild keeps the delegate's
anchor attached to the right file. The model's PUBLIC surface keeps
the list-valued projection (pinned by name and read as a value by the
harness); this model is the internal collaborator the file table's
extracted component binds to — the model keeps the public names, the
view model is never the surface.
"""
from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, Qt


class FilesViewModel(QAbstractListModel):
    """One row per file page entry; the relpath is the stable
    identity. Rebuilt ONLY when the projection's revision changes —
    the per-publish cost stays on the FileManager's cached rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=None):
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.row_identity(row)
        return None

    def set_rows(self, rows) -> None:
        """Replace the held rows wholesale: the identity (relpath)
        survives a rebuild — delegates re-anchor by identity, not by
        position."""
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def row_identity(self, row) -> str:
        """The identity the delegates key on — the same field the
        list-valued projection carries, so the two views can never
        disagree about which file a row is."""
        if isinstance(row, dict):
            return str(row.get("relpath", "") or "")
        return str(getattr(row, "relpath", "") or "")
