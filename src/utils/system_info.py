from __future__ import annotations

import os
import platform
from typing import Dict, Optional, Union


def collect_system_info() -> Dict[str, Union[str, int, None]]:
    uname = platform.uname()
    processor = uname.processor or platform.processor() or "desconhecido"
    machine = uname.machine or platform.machine() or "desconhecido"
    operating_system = platform.platform()
    cpu_count = os.cpu_count() or 1

    memory_bytes: Optional[int] = None
    try:
        import psutil  # type: ignore
    except ImportError:
        pass
    else:
        try:
            memory_bytes = psutil.virtual_memory().total
        except Exception:
            memory_bytes = None

    info: Dict[str, Union[str, int, None]] = {
        "os": operating_system,
        "machine": machine,
        "processor": processor,
        "cpu_count": str(cpu_count),
        "memory_bytes": memory_bytes,
        "python": platform.python_version(),
    }
    return info
