from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from ..contracts import ImportResult, SourceFile


class BaseImporter(ABC):
    name = "base"

    @abstractmethod
    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        raise NotImplementedError
