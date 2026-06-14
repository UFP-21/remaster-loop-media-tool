"""Backward-compat shim.

Some UI code imports MASTERING_PRESETS from core.mastering_presets.
In this project the presets live in core.presets as PRESETS.
"""

from __future__ import annotations

from core.presets import PRESETS as MASTERING_PRESETS

__all__ = ["MASTERING_PRESETS"]
