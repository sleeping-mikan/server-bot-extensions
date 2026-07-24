"""
world-visualizer — ワールド全体のマップ画像生成と、プレイヤー所持アイテムの画像表示を行う拡張機能。

## 何をする拡張か

- `/extension-world-visualizer map` — ワールドのリージョンファイル(.mca)を直接読み、
  チャンクごとの地表バイオーム+標高から簡易的な俯瞰マップ画像を1枚合成して返す。
- `/extension-world-visualizer inventory` — 指定プレイヤーの playerdata(.dat)を直接読み、
  所持アイテム(メインインベントリ+ホットバー)をグリッド画像として、防具/オフハンドを
  embedのテキストとして返す。

いずれもRCONやサーバーの標準出力に一切依存せず、`ctx.server_path` 配下のワールド保存ファイルを
直接パースする読み取り専用の実装(サーバー本体のファイルには一切書き込まない)。そのため
サーバーが停止中でも動作する(ただし当然、直近のオートセーブ以降の変更は反映されない)。

## 前提: Pillow (PIL) が必要

画像合成に Pillow を使う。Botの実行環境に無ければ

    pip install Pillow

を実行してから拡張を読み込み直すこと。未インストールの場合、両コマンドとも
「Pillow (PIL) がインストールされていません」というエラーembedを返すだけで、
他の拡張機能やBot本体には影響しない(モジュール読み込み時に ImportError を握りつぶしている)。

## map の仕組みと制約

- `<level-name>/region/*.mca`(ネザーは `DIM-1/region`、エンドは `DIM1/region`)を走査し、
  1チャンク(16x16ブロック)につき1ピクセルとして、チャンク中央付近の列の地表バイオームと
  標高から色を決める。ブロック単位の質感(草ブロック/水/砂など)までは再現しない簡易版で、
  Dynmap/BlueMapのような高精細レンダラーの代替にはならないが、「ワールド全体でどこに何が
  あるか」を大まかに把握する用途には十分な粒度を狙っている。
- 探索範囲が大きい(400チャンク四方を超える)場合は自動的に間引いてサンプリングする
  (embedの「サンプリング間隔」欄で確認できる)。間引きは大まかな全体像を保ちつつ
  処理時間・画像サイズを抑えるための措置で、間引き無し(stride=1)なら1ピクセル=1チャンク。
- チャンクデータ形式は 1.18 以降(sections直下・Heightmaps・セクション毎biomesパレット)を
  前提にしている。1.17以前のワールド(Levelタグの下に階層化された旧形式)は対象外
  (該当チャンクは高さ/バイオームが取得できず単に「未探索」として描画される)。
- 巨大チャンク用の外部ファイル参照(`.mcc`、圧縮後1チャンクが1MBを超える極めて稀なケース)は
  読み飛ばす。
- 標高の基準(ワールド最下部の高さ)はディメニションごとに固定値(`_DIMENSION_MIN_Y`)を
  仮定している。カスタムの `generator-settings` でワールド高さを変更している場合は
  標高による陰影がずれる可能性がある(バイオーム自体の判定には影響しない)。

## inventory の仕組みと制約

- `usercache.json` からプレイヤー名→UUIDを引き、`<level-name>/playerdata/<uuid>.dat`
  (無ければ `.dat_old`)を読む。RCONの `data get entity` と違い**オフライン中のプレイヤーでも
  参照できる**(サーバーが起動している必要すらない)。
- 一度もサーバーに参加したことのない名前は `usercache.json` に存在しないため取得できない。
- 1.20.5以降で導入されたアイテムコンポーネント形式("count"がint、"tag"が"components"に
  変更)と、それ以前の形式("Count"がbyte、"tag"がcompound)の両方から `id` / 数量を読める
  ようにしているが、エンチャントやカスタム名などの詳細情報までは表示しない(アイテムIDと
  個数のみ)。

## 権限レベル

いずれも読み取り専用のため、`whitelist-ops-viewer` と同様に権限チェックを設けていない
(誰でも実行可能)。個人〜身内数人規模のサーバー運用を前提としたこのリポジトリの他の
読み取り専用コマンドと揃えている。

登録される全コマンド: /extension-world-visualizer <map|inventory>
"""

from __future__ import annotations

import asyncio
import colorsys
import gzip
import hashlib
import io
import json
import struct
import zlib
from pathlib import Path
from typing import Any, Iterator, Literal

import discord
from discord import app_commands

from bot.embeds import ModifiedEmbeds
from bot.utils import print_user
from core.state import ctx

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger


def _pillow_missing_embed() -> discord.Embed:
    return ModifiedEmbeds.ErrorEmbed(
        title="Pillow (PIL) がインストールされていません",
        description="この拡張機能の画像生成には Pillow が必要です。"
        "Botの実行環境で `pip install Pillow` を実行し、拡張を読み込み直してください。",
    )


# ── server.properties からのワールド名取得 (rcon拡張と同じ読み取りロジック) ──────

def _read_server_properties() -> dict[str, str]:
    path = ctx.server_path / "server.properties"
    props: dict[str, str] = {}
    if not path.exists():
        return props
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def _level_name() -> str:
    return _read_server_properties().get("level-name", "world") or "world"


# ── NBT (バイナリ) の最小リーダー ────────────────────────────────────────────
# チャンクデータ/playerdataのどちらも同じNBT形式なので共用する。
# タグ種別: 0=End,1=Byte,2=Short,3=Int,4=Long,5=Float,6=Double,7=ByteArray,
#           8=String,9=List,10=Compound,11=IntArray,12=LongArray

class _NBTReader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _read(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        if len(chunk) < n:
            raise ValueError("unexpected end of NBT data")
        self.pos += n
        return chunk

    def read_ubyte(self) -> int:
        return self._read(1)[0]

    def read_string(self) -> str:
        (length,) = struct.unpack(">H", self._read(2))
        return self._read(length).decode("utf-8", errors="replace")

    def read_payload(self, tag_type: int) -> Any:
        if tag_type == 1:  # Byte
            return struct.unpack(">b", self._read(1))[0]
        if tag_type == 2:  # Short
            return struct.unpack(">h", self._read(2))[0]
        if tag_type == 3:  # Int
            return struct.unpack(">i", self._read(4))[0]
        if tag_type == 4:  # Long
            return struct.unpack(">q", self._read(8))[0]
        if tag_type == 5:  # Float
            return struct.unpack(">f", self._read(4))[0]
        if tag_type == 6:  # Double
            return struct.unpack(">d", self._read(8))[0]
        if tag_type == 7:  # ByteArray
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}b", self._read(n)))
        if tag_type == 8:  # String
            return self.read_string()
        if tag_type == 9:  # List
            item_type = self.read_ubyte()
            (n,) = struct.unpack(">i", self._read(4))
            if item_type == 0 or n <= 0:
                return []
            return [self.read_payload(item_type) for _ in range(n)]
        if tag_type == 10:  # Compound
            compound: dict[str, Any] = {}
            while True:
                t = self.read_ubyte()
                if t == 0:
                    break
                name = self.read_string()
                compound[name] = self.read_payload(t)
            return compound
        if tag_type == 11:  # IntArray
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}i", self._read(4 * n)))
        if tag_type == 12:  # LongArray
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}q", self._read(8 * n)))
        raise ValueError(f"unsupported NBT tag type {tag_type}")

    def read_root(self) -> dict[str, Any]:
        tag_type = self.read_ubyte()
        if tag_type == 0:
            return {}
        self.read_string()  # ルートタグ名(通常は空文字列、未使用)
        payload = self.read_payload(tag_type)
        return payload if isinstance(payload, dict) else {}


def _unpack_longs(signed_longs: list[int], bits_per_entry: int, count: int) -> list[int]:
    """1.16以降のリージョンファイルのパック形式(ロング境界をまたいでも詰めて格納する)で
    packされたlong配列から、bits_per_entryビットの値をcount個取り出す。"""
    longs = [v & 0xFFFFFFFFFFFFFFFF for v in signed_longs]
    mask = (1 << bits_per_entry) - 1
    result: list[int] = []
    for i in range(count):
        bit_index = i * bits_per_entry
        long_index = bit_index // 64
        bit_offset = bit_index % 64
        if long_index >= len(longs):
            break
        value = (longs[long_index] >> bit_offset) & mask
        overflow = bit_offset + bits_per_entry - 64
        if overflow > 0 and long_index + 1 < len(longs):
            value |= (longs[long_index + 1] << (bits_per_entry - overflow)) & mask
        result.append(value)
    return result


# ── リージョンファイル (.mca) の読み取り ──────────────────────────────────────

_DIMENSION_SUBDIR: dict[str, str] = {"overworld": "", "nether": "DIM-1", "end": "DIM1"}
_DIMENSION_MIN_Y: dict[str, int] = {"overworld": -64, "nether": 0, "end": 0}
_DIMENSION_SEA_LEVEL: dict[str, int] = {"overworld": 64, "nether": 32, "end": 0}


def _region_dir(dimension: str) -> Path:
    base = ctx.server_path / _level_name()
    subdir = _DIMENSION_SUBDIR.get(dimension, "")
    return (base / subdir / "region") if subdir else (base / "region")


def _parse_region_coords(path: Path) -> tuple[int, int] | None:
    parts = path.stem.split(".")  # "r.<x>.<z>" -> ["r", "<x>", "<z>"]
    if len(parts) != 3 or parts[0] != "r":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _iter_region_chunks(path: Path, rx: int, rz: int, stride: int) -> Iterator[tuple[int, int, dict]]:
    """リージョン内で、ワールド全体のチャンク座標がstrideの倍数に一致するチャンクのみ
    デコードして (ローカルx, ローカルz, チャンクNBT) を返す(strideが大きい世界ほど
    デコード対象を間引いて処理時間を抑える)。"""
    try:
        with path.open("rb") as f:
            header = f.read(4096)
            if len(header) < 4096:
                return
            for idx in range(1024):
                local_x, local_z = idx % 32, idx // 32
                if (rx * 32 + local_x) % stride or (rz * 32 + local_z) % stride:
                    continue
                entry = header[idx * 4 : idx * 4 + 4]
                offset_sectors = (entry[0] << 16) | (entry[1] << 8) | entry[2]
                sector_count = entry[3]
                if offset_sectors == 0 or sector_count == 0:
                    continue
                f.seek(offset_sectors * 4096)
                length_bytes = f.read(4)
                if len(length_bytes) < 4:
                    continue
                (length,) = struct.unpack(">I", length_bytes)
                if length <= 1:
                    continue
                comp_byte = f.read(1)
                if not comp_byte:
                    continue
                comp_type = comp_byte[0]
                raw = f.read(length - 1)
                if comp_type & 0x80:
                    continue  # 巨大チャンクの外部ファイル(.mcc)参照は対象外
                try:
                    if comp_type == 1:
                        decompressed = gzip.decompress(raw)
                    elif comp_type == 2:
                        decompressed = zlib.decompress(raw)
                    elif comp_type == 3:
                        decompressed = raw
                    else:
                        continue
                    chunk = _NBTReader(decompressed).read_root()
                except Exception as e:
                    logger.error(f"failed to parse chunk nbt ({path.name}:{local_x},{local_z}) ({e})")
                    continue
                yield local_x, local_z, chunk
    except OSError as e:
        logger.error(f"failed to read region file {path} ({e})")
        return


def _surface_height(chunk: dict, dimension: str) -> int | None:
    heightmaps = chunk.get("Heightmaps")
    if not isinstance(heightmaps, dict):
        return None
    raw = heightmaps.get("WORLD_SURFACE") or heightmaps.get("MOTION_BLOCKING")
    if not raw:
        return None
    bits = (len(raw) * 64) // 256
    if bits <= 0:
        return None
    values = _unpack_longs(raw, bits, 256)
    if len(values) <= 7 * 16 + 7:
        return None
    return values[7 * 16 + 7] + _DIMENSION_MIN_Y.get(dimension, -64)


def _biome_at(chunk: dict, target_y: int) -> str | None:
    sections = chunk.get("sections")
    if not isinstance(sections, list):
        return None
    section_y = target_y // 16
    local_y = target_y - section_y * 16
    cell_x, cell_y, cell_z = 7 // 4, local_y // 4, 7 // 4
    for section in sections:
        if not isinstance(section, dict) or section.get("Y") != section_y:
            continue
        biomes = section.get("biomes")
        if not isinstance(biomes, dict):
            return None
        palette = biomes.get("palette")
        if not isinstance(palette, list) or not palette:
            return None
        data = biomes.get("data")
        if not data:
            idx = 0
        else:
            bits = len(data)  # 4x4x4=64要素をタイトパックしているのでlong本数=bits数
            values = _unpack_longs(data, bits, 64)
            cell_index = (cell_y * 4 + cell_z) * 4 + cell_x
            if cell_index >= len(values):
                return None
            idx = values[cell_index]
        return palette[idx] if 0 <= idx < len(palette) else None
    return None


_BIOME_COLOR_DEFAULT = (120, 120, 120)
_BIOME_COLORS: dict[str, tuple[int, int, int]] = {
    "minecraft:plains": (145, 189, 89),
    "minecraft:sunflower_plains": (160, 196, 90),
    "minecraft:desert": (216, 189, 118),
    "minecraft:windswept_hills": (136, 138, 122),
    "minecraft:windswept_gravelly_hills": (150, 150, 140),
    "minecraft:windswept_forest": (110, 130, 100),
    "minecraft:windswept_savanna": (170, 165, 100),
    "minecraft:taiga": (89, 125, 100),
    "minecraft:snowy_taiga": (170, 200, 190),
    "minecraft:savanna": (177, 166, 90),
    "minecraft:savanna_plateau": (170, 160, 95),
    "minecraft:badlands": (192, 116, 60),
    "minecraft:eroded_badlands": (200, 130, 75),
    "minecraft:wooded_badlands": (170, 120, 70),
    "minecraft:forest": (85, 135, 70),
    "minecraft:flower_forest": (110, 165, 90),
    "minecraft:birch_forest": (135, 165, 95),
    "minecraft:old_growth_birch_forest": (125, 155, 90),
    "minecraft:dark_forest": (65, 100, 55),
    "minecraft:old_growth_pine_taiga": (75, 110, 90),
    "minecraft:old_growth_spruce_taiga": (80, 115, 95),
    "minecraft:jungle": (60, 150, 65),
    "minecraft:sparse_jungle": (90, 160, 80),
    "minecraft:bamboo_jungle": (95, 170, 75),
    "minecraft:swamp": (95, 115, 85),
    "minecraft:mangrove_swamp": (85, 120, 95),
    "minecraft:river": (70, 120, 200),
    "minecraft:frozen_river": (150, 190, 220),
    "minecraft:beach": (220, 210, 150),
    "minecraft:snowy_beach": (230, 235, 235),
    "minecraft:stony_shore": (150, 150, 150),
    "minecraft:ocean": (40, 90, 180),
    "minecraft:deep_ocean": (25, 65, 150),
    "minecraft:warm_ocean": (60, 140, 210),
    "minecraft:lukewarm_ocean": (45, 110, 195),
    "minecraft:deep_lukewarm_ocean": (30, 90, 175),
    "minecraft:cold_ocean": (50, 95, 170),
    "minecraft:deep_cold_ocean": (35, 75, 155),
    "minecraft:frozen_ocean": (140, 175, 210),
    "minecraft:deep_frozen_ocean": (110, 150, 195),
    "minecraft:mushroom_fields": (155, 90, 90),
    "minecraft:ice_spikes": (200, 225, 230),
    "minecraft:snowy_plains": (235, 240, 240),
    "minecraft:grove": (200, 215, 210),
    "minecraft:snowy_slopes": (215, 225, 225),
    "minecraft:jagged_peaks": (225, 230, 232),
    "minecraft:frozen_peaks": (220, 230, 235),
    "minecraft:stony_peaks": (160, 160, 155),
    "minecraft:cherry_grove": (230, 170, 190),
    "minecraft:meadow": (140, 190, 90),
    "minecraft:dripstone_caves": (130, 105, 85),
    "minecraft:lush_caves": (70, 150, 100),
    "minecraft:deep_dark": (40, 45, 50),
    "minecraft:the_void": (10, 10, 15),
    "minecraft:nether_wastes": (110, 55, 50),
    "minecraft:crimson_forest": (150, 35, 55),
    "minecraft:warped_forest": (35, 130, 120),
    "minecraft:soul_sand_valley": (75, 65, 60),
    "minecraft:basalt_deltas": (95, 90, 95),
    "minecraft:the_end": (140, 130, 160),
    "minecraft:end_highlands": (150, 140, 170),
    "minecraft:end_midlands": (145, 135, 165),
    "minecraft:end_barrens": (120, 110, 140),
    "minecraft:small_end_islands": (135, 125, 155),
}


def _chunk_color(chunk: dict, dimension: str) -> tuple[int, int, int] | None:
    y = _surface_height(chunk, dimension)
    if y is None:
        return None
    biome = _biome_at(chunk, y)
    base = _BIOME_COLORS.get(biome, _BIOME_COLOR_DEFAULT) if biome else _BIOME_COLOR_DEFAULT
    sea_level = _DIMENSION_SEA_LEVEL.get(dimension, 64)
    shade = 1.0 + max(-0.35, min(0.35, (y - sea_level) / 200))
    return tuple(max(0, min(255, round(c * shade))) for c in base)  # type: ignore[return-value]


# 探索範囲がこのチャンク数(1辺)を超えたら間引いてサンプリングする。
_MAX_DIM_CHUNKS = 400
_PIXELS_PER_CHUNK = 2


def _build_map_image(dimension: str) -> tuple[bytes, int, int, int, int] | None:
    """戻り値: (PNGバイト列, 描画チャンク数, stride, グリッド幅, グリッド高さ) / 対象なしならNone。

    重い処理(リージョンファイルの走査・展開・NBT解析)なので呼び出し側で
    asyncio.to_thread に包んでイベントループをブロックしないこと。
    """
    region_dir = _region_dir(dimension)
    if not region_dir.exists():
        return None
    region_files = sorted(region_dir.glob("r.*.*.mca"))
    if not region_files:
        return None

    region_coords: list[tuple[Path, int, int]] = []
    min_cx = min_cz = max_cx = max_cz = None
    for path in region_files:
        coords = _parse_region_coords(path)
        if coords is None:
            continue
        rx, rz = coords
        region_coords.append((path, rx, rz))
        cx0, cz0 = rx * 32, rz * 32
        cx1, cz1 = cx0 + 31, cz0 + 31
        min_cx = cx0 if min_cx is None else min(min_cx, cx0)
        max_cx = cx1 if max_cx is None else max(max_cx, cx1)
        min_cz = cz0 if min_cz is None else min(min_cz, cz0)
        max_cz = cz1 if max_cz is None else max(max_cz, cz1)
    if not region_coords or min_cx is None or min_cz is None:
        return None

    span = max(max_cx - min_cx + 1, max_cz - min_cz + 1)
    stride = max(1, -(-span // _MAX_DIM_CHUNKS))  # 切り上げ除算

    grid_w = (max_cx - min_cx) // stride + 1
    grid_h = (max_cz - min_cz) // stride + 1
    pixels: dict[tuple[int, int], tuple[int, int, int]] = {}

    for path, rx, rz in region_coords:
        for local_x, local_z, chunk in _iter_region_chunks(path, rx, rz, stride):
            color = _chunk_color(chunk, dimension)
            if color is None:
                continue
            gx, gz = rx * 32 + local_x, rz * 32 + local_z
            pixels[((gx - min_cx) // stride, (gz - min_cz) // stride)] = color

    if not pixels:
        return None

    img = Image.new("RGB", (grid_w, grid_h), (12, 12, 24))
    for (px, pz), color in pixels.items():
        img.putpixel((px, pz), color)
    if _PIXELS_PER_CHUNK > 1:
        img = img.resize((grid_w * _PIXELS_PER_CHUNK, grid_h * _PIXELS_PER_CHUNK), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), len(pixels), stride, grid_w, grid_h


# ── プレイヤーインベントリ ────────────────────────────────────────────────────

def _load_usercache() -> list[dict]:
    path = ctx.server_path / "usercache.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"failed to read usercache.json ({e})")
        return []


def _resolve_uuid(player_name: str) -> str | None:
    matches = [e for e in _load_usercache() if str(e.get("name", "")).lower() == player_name.lower()]
    if not matches:
        return None
    return matches[-1].get("uuid")


def _load_player_nbt(uuid: str) -> dict | None:
    base = ctx.server_path / _level_name() / "playerdata"
    for suffix in (".dat", ".dat_old"):
        path = base / f"{uuid}{suffix}"
        if not path.exists():
            continue
        try:
            with path.open("rb") as f:
                raw = gzip.decompress(f.read())
            return _NBTReader(raw).read_root()
        except Exception as e:
            logger.error(f"failed to parse playerdata for {uuid} ({e})")
            continue
    return None


def _extract_items(player_nbt: dict) -> list[dict]:
    inventory = player_nbt.get("Inventory")
    if not isinstance(inventory, list):
        return []
    items: list[dict] = []
    for entry in inventory:
        if not isinstance(entry, dict):
            continue
        slot, item_id = entry.get("Slot"), entry.get("id")
        if slot is None or not item_id:
            continue
        # 1.20.5以降は count(int)、それ以前は Count(byte)
        count = entry.get("count", entry.get("Count", 1))
        items.append({"slot": int(slot), "id": str(item_id), "count": int(count)})
    return items


_ARMOR_SLOTS: dict[int, str] = {103: "ヘルメット", 102: "チェストプレート", 101: "レギンス", 100: "ブーツ", -106: "オフハンド"}


def _extract_armor_lines(items: list[dict]) -> list[str]:
    by_slot = {item["slot"]: item for item in items}
    lines: list[str] = []
    for slot, label in _ARMOR_SLOTS.items():
        item = by_slot.get(slot)
        if item:
            name = item["id"].split(":", 1)[-1].replace("_", " ")
            lines.append(f"{label}: {name} x{item['count']}")
    return lines


def _item_color(item_id: str) -> tuple[int, int, int]:
    digest = hashlib.md5(item_id.encode("utf-8")).digest()
    hue = digest[0] / 255
    r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


_CELL = 48
_GRID_COLS = 9
_GRID_ROWS = 4
_PADDING = 6
_HOTBAR_GAP = 10
_MAIN_SLOT_ORDER = list(range(9, 36)) + list(range(0, 9))  # メイン欄3行 → ホットバーの順で並べる


def _draw_wrapped_label(draw: "ImageDraw.ImageDraw", text: str, x: int, y: int, max_width: int, font, max_lines: int = 3) -> None:
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


def _build_inventory_image(items: list[dict]) -> bytes:
    by_slot = {item["slot"]: item for item in items}
    img_w = _GRID_COLS * _CELL + _PADDING * 2
    img_h = _GRID_ROWS * _CELL + _PADDING * 2 + _HOTBAR_GAP
    img = Image.new("RGB", (img_w, img_h), (30, 30, 34))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    for i, slot in enumerate(_MAIN_SLOT_ORDER):
        row, col = divmod(i, _GRID_COLS)
        x0 = _PADDING + col * _CELL
        y0 = _PADDING + row * _CELL + (_HOTBAR_GAP if row == 3 else 0)
        x1, y1 = x0 + _CELL - 2, y0 + _CELL - 2

        item = by_slot.get(slot)
        if item is None:
            draw.rectangle([x0, y0, x1, y1], outline=(70, 70, 76), width=1)
            continue

        draw.rectangle([x0, y0, x1, y1], fill=_item_color(item["id"]), outline=(20, 20, 22), width=1)
        label = item["id"].split(":", 1)[-1].replace("_", " ")
        _draw_wrapped_label(draw, label, x0 + 3, y0 + 3, _CELL - 6, font)
        if item["count"] > 1:
            count_text = str(item["count"])
            tw = draw.textlength(count_text, font=font)
            draw.text((x1 - tw - 3, y1 - 12), count_text, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── map コマンド ─────────────────────────────────────────────────────────────

@tree.command(name="map", description="ワールド全体のマップ画像を生成する(バイオーム+標高による簡易レンダリング)")
@app_commands.describe(dimension="対象ディメンション")
async def map_command(
    interaction: discord.Interaction,
    dimension: Literal["overworld", "nether", "end"] = "overworld",
) -> None:
    await print_user(logger, interaction.user)
    if not _PIL_AVAILABLE:
        await interaction.response.send_message(embed=_pillow_missing_embed(), ephemeral=True)
        return

    await interaction.response.defer()
    try:
        result = await asyncio.to_thread(_build_map_image, dimension)
    except Exception as e:
        logger.error(f"failed to build map image ({dimension}) ({e})")
        embed = ModifiedEmbeds.ErrorEmbed(title="マップ画像の生成に失敗しました", description=str(e))
        await interaction.followup.send(embed=embed)
        return

    if result is None:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="マップ画像を生成できませんでした",
            description="対象ディメンションのリージョンファイルが見つかりませんでした(まだ生成されていない可能性があります)",
        )
        await interaction.followup.send(embed=embed)
        return

    png_bytes, chunk_count, stride, grid_w, grid_h = result
    file = discord.File(io.BytesIO(png_bytes), filename="map.png")
    embed = ModifiedEmbeds.DefaultEmbed(title=f"ワールドマップ ({dimension})")
    embed.add_field(name="描画チャンク数", value=str(chunk_count), inline=True)
    embed.add_field(name="サンプリング間隔", value=(f"{stride}チャンクごと" if stride > 1 else "全チャンク"), inline=True)
    embed.add_field(name="範囲(概算)", value=f"約 {grid_w * stride * 16} x {grid_h * stride * 16} ブロック", inline=True)
    embed.set_image(url="attachment://map.png")
    await interaction.followup.send(embed=embed, file=file)


# ── inventory コマンド ───────────────────────────────────────────────────────

@tree.command(name="inventory", description="プレイヤーの所持アイテムを画像で表示する")
@app_commands.describe(player="対象プレイヤー名")
async def inventory_command(interaction: discord.Interaction, player: str) -> None:
    await print_user(logger, interaction.user)
    if not _PIL_AVAILABLE:
        await interaction.response.send_message(embed=_pillow_missing_embed(), ephemeral=True)
        return

    uuid = _resolve_uuid(player)
    if uuid is None:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="プレイヤーが見つかりません",
            description=f"usercache.json に `{player}` の記録がありません(一度もサーバーに参加していない可能性があります)",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer()
    player_nbt = await asyncio.to_thread(_load_player_nbt, uuid)
    if player_nbt is None:
        embed = ModifiedEmbeds.ErrorEmbed(title="プレイヤーデータが見つかりません", description=f"playerdata/{uuid}.dat が見つかりませんでした")
        await interaction.followup.send(embed=embed)
        return

    items = _extract_items(player_nbt)
    try:
        png_bytes = await asyncio.to_thread(_build_inventory_image, items)
    except Exception as e:
        logger.error(f"failed to render inventory image for {player} ({e})")
        embed = ModifiedEmbeds.ErrorEmbed(title="画像の生成に失敗しました", description=str(e))
        await interaction.followup.send(embed=embed)
        return

    file = discord.File(io.BytesIO(png_bytes), filename="inventory.png")
    embed = ModifiedEmbeds.DefaultEmbed(title=f"{player} の所持アイテム")
    armor_lines = _extract_armor_lines(items)
    if armor_lines:
        embed.add_field(name="防具・オフハンド", value="\n".join(armor_lines), inline=False)
    embed.set_footer(text=f"メイン欄+ホットバーのアイテム数: {len(items) - len(armor_lines)} (最終セーブ時点のデータです)")
    embed.set_image(url="attachment://inventory.png")
    await interaction.followup.send(embed=embed, file=file)
