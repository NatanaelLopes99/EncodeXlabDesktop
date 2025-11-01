from __future__ import annotations

from pathlib import Path
from typing import Iterator


def iter_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for p in root.rglob("*"):
        if p.is_file():
            yield p
