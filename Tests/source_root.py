"""Finding a source file by name, not by the directory it sits in.

The plugin tree nests by domain, and the nesting is expected to keep
changing. A test that wants a module's source should name the module:
``PLUGINS / "UploadController.py"`` has to keep working after the file
moves from the top level into ``filemanager/``, or every future move
rewrites the suite again.

``SourceRoot`` answers that lookup, and refuses an ambiguous or absent
name rather than quietly returning the wrong file — a suite that
asserts against a document it did not mean to read is worse than one
that fails. Tree walks (``rglob``, ``glob``, ``iterdir``) and anything
else the underlying :class:`~pathlib.Path` offers delegate unchanged.
"""
from __future__ import annotations

import pathlib
from typing import Iterator, Union


class AmbiguousSource(LookupError):
    """Two files share the name; addressing one by basename is a bug."""


class SourceRoot:
    """A plugin tree that resolves ``/ "Name.ext"`` wherever the file is."""

    def __init__(self, root: Union[str, pathlib.Path]) -> None:
        self._root = pathlib.Path(root)

    def _matches(self, name: str):
        return [candidate for candidate in sorted(self._root.rglob(name))
                if candidate.name == name]

    def path(self, name: str) -> pathlib.Path:
        """Look the name up. It must exist, and exist once."""
        matches = self._matches(name)
        if not matches:
            raise FileNotFoundError(f"no {name!r} under {self._root}")
        if len(matches) > 1:
            where = ", ".join(str(m.relative_to(self._root)) for m in matches)
            raise AmbiguousSource(f"{name!r} is ambiguous under {self._root}: {where}")
        return matches[0]

    def __truediv__(self, name: str) -> pathlib.Path:
        """Address the name. Absence is an answer, not an error.

        The suite also asserts that a file is *not* shipped, and it does
        that by asking ``is_file()``. Raising here would turn every such
        negative into an error, so a name with no match is answered with
        the path it would have had — one that simply does not exist.
        Ambiguity still raises: that is never what the caller meant.
        """
        matches = self._matches(name)
        if len(matches) > 1:
            where = ", ".join(str(m.relative_to(self._root)) for m in matches)
            raise AmbiguousSource(f"{name!r} is ambiguous under {self._root}: {where}")
        return matches[0] if matches else self._root / name

    @property
    def root(self) -> pathlib.Path:
        """The real directory, for the callers that need a PurePath.

        ``relative_to`` refuses anything that is not one — a correct
        ``__fspath__`` is not enough — so a caller that wants a path
        relative to the tree asks for this instead.
        """
        return self._root

    def rglob(self, pattern: str) -> Iterator[pathlib.Path]:
        return self._root.rglob(pattern)

    def glob(self, pattern: str) -> Iterator[pathlib.Path]:
        return self._root.glob(pattern)

    def iterdir(self) -> Iterator[pathlib.Path]:
        return self._root.iterdir()

    def __getattr__(self, attribute: str):
        # Everything else is the plain directory: existing isinstance
        # checks, str(), and the odd resolve() keep working.
        return getattr(self._root, attribute)

    def __fspath__(self) -> str:
        return str(self._root)

    def __str__(self) -> str:
        return str(self._root)

    def __repr__(self) -> str:
        return f"SourceRoot({self._root})"
