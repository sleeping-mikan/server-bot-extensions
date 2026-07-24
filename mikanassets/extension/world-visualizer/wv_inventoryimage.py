"""wv_inventoryimage — プレイヤー所持アイテムのグリッド画像を合成する(Pillow使用)。"""

from __future__ import annotations

import colorsys
import hashlib
import io

from wv_imaging import Image, ImageDraw, ImageFont

CELL = 48
GRID_COLS = 9
GRID_ROWS = 4
PADDING = 6
HOTBAR_GAP = 10
MAIN_SLOT_ORDER = list(range(9, 36)) + list(range(0, 9))  # メイン欄3行 → ホットバーの順で並べる


def item_color(item_id: str) -> tuple[int, int, int]:
    digest = hashlib.md5(item_id.encode("utf-8")).digest()
    hue = digest[0] / 255
    r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


def _draw_wrapped_label(draw, text: str, x: int, y: int, max_width: int, font, max_lines: int = 3) -> None:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    for i, line in enumerate(lines[:max_lines]):
        draw.text((x, y + i * 10), line, font=font, fill=(255, 255, 255))


def build_inventory_image(items: list[dict]) -> bytes:
    by_slot = {item["slot"]: item for item in items}
    img_w = GRID_COLS * CELL + PADDING * 2
    img_h = GRID_ROWS * CELL + PADDING * 2 + HOTBAR_GAP
    img = Image.new("RGB", (img_w, img_h), (30, 30, 34))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    for i, slot in enumerate(MAIN_SLOT_ORDER):
        row, col = divmod(i, GRID_COLS)
        x0 = PADDING + col * CELL
        y0 = PADDING + row * CELL + (HOTBAR_GAP if row == 3 else 0)
        x1, y1 = x0 + CELL - 2, y0 + CELL - 2

        item = by_slot.get(slot)
        if item is None:
            draw.rectangle([x0, y0, x1, y1], outline=(70, 70, 76), width=1)
            continue

        draw.rectangle([x0, y0, x1, y1], fill=item_color(item["id"]), outline=(20, 20, 22), width=1)
        label = item["id"].split(":", 1)[-1].replace("_", " ")
        _draw_wrapped_label(draw, label, x0 + 3, y0 + 3, CELL - 6, font)
        if item["count"] > 1:
            count_text = str(item["count"])
            tw = draw.textlength(count_text, font=font)
            draw.text((x1 - tw - 3, y1 - 12), count_text, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
