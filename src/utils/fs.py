from __future__ import annotations

from pathlib import Path
import shutil
from typing import BinaryIO, Iterator


def iter_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def open_unique_output(destination: Path) -> tuple[Path, BinaryIO]:
    """Reserve an output without overwriting an existing file, including in parallel."""
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidate = destination
    number = 2
    while True:
        try:
            return candidate, candidate.open("xb")
        except FileExistsError:
            candidate = destination.with_name(
                f"{destination.stem} ({number}){destination.suffix}"
            )
            number += 1


def move_output_file(source: Path, destination: Path, input_path: Path) -> Path:
    """Copy completely before removing the source; never replace an input or output."""
    source = Path(source).resolve()
    destination = Path(destination).expanduser().resolve()
    if destination == Path(input_path).resolve():
        raise ValueError("O destino nao pode ser o arquivo de entrada original.")
    if source == destination:
        return source

    final_path, output = open_unique_output(destination)
    try:
        with output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
        shutil.copystat(source, final_path)
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    source.unlink()
    return final_path
