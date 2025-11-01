from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class CompressionResult:
    """
    Represents the outcome of running a compression routine against a single file.
    """

    source: Path
    changed: bool
    original_size: int
    new_size: int
    temp_path: Optional[Path] = None
    reason: Optional[str] = None
    relative_path: Optional[Path] = None
    encoder: Optional[str] = None

    @property
    def saved_bytes(self) -> int:
        if not self.changed:
            return 0
        return max(0, self.original_size - self.new_size)
