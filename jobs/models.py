from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
from time import time


@dataclass
class Job:
    id: str
    title: str
    run: Callable[[], str]  # должна вернуть путь к результату (или текст)
    status: str = "queued"  # queued/running/done/error
    progress: str = ""      # текст стадий
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time)
