from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Tuple

CACHE_PREFIX = "vcdesktop-"


def clear_temp_cache(prefix: str = CACHE_PREFIX) -> Tuple[List[Path], List[Tuple[Path, Exception]]]:
    """
    Remove diretórios e arquivos temporários com o prefixo informado
    dentro da pasta padrão de temporários da plataforma.
    Retorna uma tupla com itens removidos e eventuais falhas.
    """
    temp_root = Path(tempfile.gettempdir())
    if not temp_root.exists():
        return [], []

    removed: List[Path] = []
    failures: List[Tuple[Path, Exception]] = []

    for entry in temp_root.iterdir():
        if not entry.name.startswith(prefix):
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(entry)
        except Exception as exc:  # pragma: no cover - depende do filesystem
            failures.append((entry, exc))

    return removed, failures

