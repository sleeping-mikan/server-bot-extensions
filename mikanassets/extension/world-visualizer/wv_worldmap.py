"""wv_worldmap — リージョンファイル(.mca)の直接読み取りと、ワールド全体マップ画像の合成。

真上から見た俯瞰図(平面図)で、1チャンク(16x16ブロック)につき1ピクセル。チャンク内の
代表列(ローカルx=7,z=7、中心付近)のハイトマップ最上段のブロックを1個だけサンプリングし、
そのブロックの実テクスチャ色を基本にピクセル色を決める。標高による陰影は行わない。
草ブロック上面・葉・水などはMinecraft内部でも常にバイオーム色を掛け合わせる前提の
グレースケール「マスク」テクスチャなので、素の色のままだと灰色寄りになってしまう
(オーナー本人が実機で確認し指摘)。そのためこれらのブロックに限り、そのチャンクの
バイオームから引いた色を掛け合わせる(BIOME_TINTED_BLOCKS、実装は wv_blockcolors.py
冒頭のdocstring「色そのものについて」を参照)。それ以外の大多数のブロック(石・土・
木材など)は実テクスチャの色をそのまま使う。

チャンクデータ形式は 1.18 以降(sections直下・Heightmaps・セクション毎block_statesパレット)を
前提にしている。1.17以前のワールド(Levelタグの下に階層化された旧形式)は対象外(該当
チャンクは高さが取得できず単に「未探索」として描画される)。巨大チャンク用の外部ファイル
参照(.mcc、圧縮後1チャンクが1MBを超える極めて稀なケース)は読み飛ばす。

リージョンファイルの配置場所は、実機検証で確認した2種類のレイアウトを両方試す
(region_dir参照)。旧来は overworld が `<level-name>/region/`、nether/endが
`<level-name>/DIM-1/region/`・`<level-name>/DIM1/region/` だったが、現行バージョンでは
全ディメンションが `<level-name>/dimensions/minecraft/<id>/region/` に統一されていた。
"""

from __future__ import annotations

import gzip
import io
import struct
import zlib
from pathlib import Path
from typing import Iterator

from core.state import ctx

import wv_blockcolors
import wv_serverfiles
from wv_imaging import Image
from wv_nbt import NBTReader, unpack_longs, unpack_padded_longs

logger = ctx.extension_logger

DIMENSION_SUBDIR: dict[str, str] = {"overworld": "", "nether": "DIM-1", "end": "DIM1"}

# 探索範囲がこのチャンク数(1辺)を超えたら間引いてサンプリングする。
MAX_DIM_CHUNKS = 400
PIXELS_PER_CHUNK = 2

# ブロックの実色が一切解決できなかった場合(ブロック色キャッシュが無い等)のフォールバック色。
UNKNOWN_BLOCK_COLOR = (120, 120, 120)

# バイオーム不明時のフォールバック、および草/葉/水等のティント色源として使うバイオーム毎の色。
BIOME_COLOR_DEFAULT = (120, 120, 120)
BIOME_COLORS: dict[str, tuple[int, int, int]] = {
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


# ── リージョンファイル (.mca) の読み取り ──────────────────────────────────────

# ディメンションIDのフォルダ名(新レイアウト "<world>/dimensions/minecraft/<id>/region"
# 用)。実機検証で確認したところ、現行バージョンでは overworld も含め全ディメンションが
# "<world>/dimensions/minecraft/<id>/region" に統一されており、旧来の
# "<world>/region"(overworld)・"<world>/DIM-1/region"(nether)・
# "<world>/DIM1/region"(end) というレイアウトはもう存在しなかった。バージョンに
# よってどちらのレイアウトかわからないため、両方を試す(新レイアウトを優先)。
_DIMENSION_ID: dict[str, str] = {"overworld": "overworld", "nether": "the_nether", "end": "the_end"}


def region_dir(dimension: str) -> Path:
    base = ctx.server_path / wv_serverfiles.level_name()

    new_layout = base / "dimensions" / "minecraft" / _DIMENSION_ID.get(dimension, dimension) / "region"
    if new_layout.exists():
        return new_layout

    subdir = DIMENSION_SUBDIR.get(dimension, "")
    return (base / subdir / "region") if subdir else (base / "region")


def parse_region_coords(path: Path) -> tuple[int, int] | None:
    parts = path.stem.split(".")  # "r.<x>.<z>" -> ["r", "<x>", "<z>"]
    if len(parts) != 3 or parts[0] != "r":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def iter_region_chunks(path: Path, rx: int, rz: int, stride: int) -> Iterator[tuple[int, int, dict]]:
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
                    chunk = NBTReader(decompressed).read_root()
                except Exception as e:
                    logger.error(f"failed to parse chunk nbt ({path.name}:{local_x},{local_z}) ({e})")
                    continue
                yield local_x, local_z, chunk
    except OSError as e:
        logger.error(f"failed to read region file {path} ({e})")
        return


def surface_height(chunk: dict) -> int | None:
    """チャンク中心付近の列(ローカルx=7,z=7)の地表ブロックの絶対Yを返す。

    Heightmapsは block_states/biomes のパレット付きコンテナ(unpack_longs、ロング境界を
    またいでタイトパックする形式)とは**別のパック形式**で、1ロング(64bit)に入りきらない
    端数は詰めずに切り捨てるパディング方式(unpack_padded_longs)。実際のワールドセーブ
    データで検証済み(9bit幅なら256要素に対して37 long、タイトパック方式が期待する
    36 longとは異なり、これを混同すると算出される高さが実際より大きく(数百ブロック)
    ずれる)。

    ワールド最下部の高さ(ハイトマップ値からブロックの絶対Yを逆算するための基準、および
    ハイトマップのbit幅)は、ディメンション毎の固定値を仮定せず、そのチャンク自身の
    sections(Y座標の最小値・セクション数)から都度算出する。カスタムワールド高さや
    将来のバージョンでの変更にも自動的に追従できる。"""
    heightmaps = chunk.get("Heightmaps")
    if not isinstance(heightmaps, dict):
        return None
    raw = heightmaps.get("WORLD_SURFACE") or heightmaps.get("MOTION_BLOCKING")
    if not raw:
        return None
    sections = chunk.get("sections")
    if not isinstance(sections, list) or not sections:
        return None
    section_ys = [s.get("Y") for s in sections if isinstance(s, dict) and isinstance(s.get("Y"), int)]
    if not section_ys:
        return None
    min_section_y, max_section_y = min(section_ys), max(section_ys)
    min_y = min_section_y * 16
    total_height_blocks = (max_section_y - min_section_y + 1) * 16
    bits = total_height_blocks.bit_length()
    if bits <= 0:
        return None
    values = unpack_padded_longs(raw, bits, 256)
    if len(values) <= 7 * 16 + 7:
        return None
    # ハイトマップの格納値は「最上段の非空気ブロックのY座標 + 1」(=その真上の空気ブロックのY)
    # を表す仕様なので、実際の地表ブロックのYを得るには1引く必要がある。
    top_block_y = values[7 * 16 + 7] - 1
    return top_block_y + min_y


def top_block_name(chunk: dict, target_y: int) -> str | None:
    """target_y にあるブロックの完全ID(例: "minecraft:grass_block")を、セクションの
    block_states パレット(16x16x16をタイトパックしたパレット付きコンテナ)から読む。

    block_states のローカルパレットは実際のブロック名を持つ形式(indirect palette)を
    前提にしている。1セクション内のブロック種類が非常に多い場合にMinecraft側が使う
    グローバルパレット(direct、ブロック名を持たず数値IDのみ)には対応していない
    (通常の生成済みワールドではセクション内の種類数がその閾値を超えることは稀)。
    その場合は None を返し、呼び出し側でフォールバック色を使う。"""
    sections = chunk.get("sections")
    if not isinstance(sections, list):
        return None
    section_y = target_y // 16
    local_y = target_y - section_y * 16
    local_x = local_z = 7
    for section in sections:
        if not isinstance(section, dict) or section.get("Y") != section_y:
            continue
        block_states = section.get("block_states")
        if not isinstance(block_states, dict):
            return None
        palette = block_states.get("palette")
        if not isinstance(palette, list) or not palette:
            return None
        data = block_states.get("data")
        if not data:
            idx = 0
        else:
            bits = (len(data) * 64) // 4096  # 16x16x16=4096要素をタイトパック
            if bits <= 0:
                return None
            values = unpack_longs(data, bits, 4096)
            cell_index = (local_y * 16 + local_z) * 16 + local_x
            if cell_index >= len(values):
                return None
            idx = values[cell_index]
        if not (0 <= idx < len(palette)):
            return None
        entry = palette[idx]
        return entry.get("Name") if isinstance(entry, dict) else None
    return None


def biome_at(chunk: dict, target_y: int) -> str | None:
    """target_y にあるバイオームの完全ID(例: "minecraft:plains")を、セクションの
    biomes パレット(4x4x4をタイトパックしたパレット付きコンテナ)から読む。"""
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
            values = unpack_longs(data, bits, 64)
            cell_index = (cell_y * 4 + cell_z) * 4 + cell_x
            if cell_index >= len(values):
                return None
            idx = values[cell_index]
        return palette[idx] if 0 <= idx < len(palette) else None
    return None


def build_map_image(
    dimension: str, cache: dict | None, version: str | None
) -> tuple[bytes, int, int, int, int, int] | None:
    """戻り値: (PNGバイト列, 描画チャンク数, stride, グリッド幅, グリッド高さ,
    新たに解決したブロック色の数) / 対象なしならNone。

    cache は wv_blockcolors.load_color_cache() が返す {"colors": {...}, "missing": [...]}
    形式の辞書(未取得ならNone)。渡されればブロック実色で、Noneなら全ピクセルが
    UNKNOWN_BLOCK_COLOR の単色になる。cache["colors"] に無い(=まだ見たことが無い、または
    既に missing判定済みの)ブロックに遭遇した場合はひとまず UNKNOWN_BLOCK_COLOR で塗って
    おき、チャンク走査が全て終わってから遭遇した未知のテクスチャをまとめて1回だけ
    取得しに行き(wv_blockcolors.resolve_unknown_textures、実際にはそのバージョンの
    全ブロックテクスチャが一括取得される)、該当ピクセルだけ実ブロック色で塗り直す
    (既知/missing判定済みのブロックしか無ければ一切ネットワークへアクセスしない)。

    重い処理(リージョンファイルの走査・展開・NBT解析、および未知ブロックがあれば
    その解決)なので呼び出し側で asyncio.to_thread に包んでイベントループを
    ブロックしないこと。cache はこの関数の中で直接書き換えられる(呼び出し側で
    保存済みのcacheを使い回すこと)。
    """
    block_colors = cache["colors"] if cache is not None else None
    dir_path = region_dir(dimension)
    if not dir_path.exists():
        return None
    region_files = sorted(dir_path.glob("r.*.*.mca"))
    if not region_files:
        return None

    region_coords: list[tuple[Path, int, int]] = []
    min_cx = min_cz = max_cx = max_cz = None
    for path in region_files:
        coords = parse_region_coords(path)
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
    stride = max(1, -(-span // MAX_DIM_CHUNKS))  # 切り上げ除算

    grid_w = (max_cx - min_cx) // stride + 1
    grid_h = (max_cz - min_cz) // stride + 1
    pixels: dict[tuple[int, int], tuple[int, int, int]] = {}
    # ブロック色がまだキャッシュに無いピクセル: coord -> (完全ブロック名, texture_key, バイオーム色)
    pending: dict[tuple[int, int], tuple[str, str, tuple[int, int, int]]] = {}

    for path, rx, rz in region_coords:
        for local_x, local_z, chunk in iter_region_chunks(path, rx, rz, stride):
            y = surface_height(chunk)
            if y is None:
                continue
            biome = biome_at(chunk, y)
            biome_color = BIOME_COLORS.get(biome, BIOME_COLOR_DEFAULT) if biome else BIOME_COLOR_DEFAULT
            gx, gz = rx * 32 + local_x, rz * 32 + local_z
            coord = ((gx - min_cx) // stride, (gz - min_cz) // stride)

            base = biome_color if block_colors is not None else UNKNOWN_BLOCK_COLOR
            if block_colors is not None:
                block_name = top_block_name(chunk, y)
                if block_name:
                    resolved, texture_key = wv_blockcolors.resolve_block_color(block_name, block_colors, biome_color)
                    if resolved is not None:
                        base = resolved
                    else:
                        pending[coord] = (block_name, texture_key, biome_color)

            pixels[coord] = base

    if not pixels:
        return None

    newly_resolved = 0
    if pending and cache is not None and version is not None:
        # resolve_unknown_textures にはテクスチャファイル名(例: "sand")を渡す必要があり、
        # ブロックの完全ID(例: "minecraft:sand")をそのまま渡すと存在しないパスを探しに
        # 行ってしまう(取得したキーが必ずtexture_keyであることをテストで確認済み)。
        unique_keys = {texture_key for _, texture_key, _ in pending.values()}
        before = len(block_colors)
        wv_blockcolors.resolve_unknown_textures(version, unique_keys, cache)
        newly_resolved = len(block_colors) - before
        for coord, (block_name, _, biome_color) in pending.items():
            resolved, _ = wv_blockcolors.resolve_block_color(block_name, block_colors, biome_color)
            if resolved is not None:
                pixels[coord] = resolved

    img = Image.new("RGB", (grid_w, grid_h), (12, 12, 24))
    for (px, pz), color in pixels.items():
        img.putpixel((px, pz), color)
    if PIXELS_PER_CHUNK > 1:
        img = img.resize((grid_w * PIXELS_PER_CHUNK, grid_h * PIXELS_PER_CHUNK), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), len(pixels), stride, grid_w, grid_h, newly_resolved
