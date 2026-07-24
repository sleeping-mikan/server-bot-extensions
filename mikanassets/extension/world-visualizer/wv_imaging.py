"""wv_imaging — Pillowの有無をここで一度だけ判定し、他のサブモジュールへ共有する。"""

from __future__ import annotations

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]
    PIL_AVAILABLE = False
