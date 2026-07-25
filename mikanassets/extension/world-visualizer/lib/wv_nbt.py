"""wv_nbt — チャンクデータ/playerdataが共通で使うNBT(バイナリ)の最小リーダーと、
リージョンファイルのパック済みlong配列展開ユーティリティ。

world-visualizer拡張の内部モジュール。ファイル名に "wv_" prefixを付けているのは、
拡張フォルダ名 "world-visualizer" にハイフンが含まれ正式なPythonパッケージ名にできない
関係で、commands.py がこのディレクトリを sys.path に足したうえで単純importする方式を
取っているため(詳細は commands.py 冒頭のコメント参照)。sys.pathへの追加は他の
拡張機能とプロセスを共有するBot全体に影響するため、PyPIパッケージ(例: "nbt")や
他拡張の同名モジュールと衝突しないよう、あえて一般的な名前を避けている。
"""

from __future__ import annotations

import struct
from typing import Any

# タグ種別: 0=End,1=Byte,2=Short,3=Int,4=Long,5=Float,6=Double,7=ByteArray,
#           8=String,9=List,10=Compound,11=IntArray,12=LongArray


class NBTReader:
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


def unpack_longs(signed_longs: list[int], bits_per_entry: int, count: int) -> list[int]:
    """1.16以降の block_states/biomes パレット付きコンテナのパック形式(ロング境界を
    またいでも詰めて格納する、パディング無し)で packされたlong配列から、
    bits_per_entryビットの値をcount個取り出す。

    Heightmaps には**使えない**(そちらは別形式、unpack_padded_longs を使うこと)。"""
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


def unpack_padded_longs(signed_longs: list[int], bits_per_entry: int, count: int) -> list[int]:
    """Heightmaps(WORLD_SURFACE等)のパック形式で packされたlong配列から、
    bits_per_entryビットの値をcount個取り出す。

    block_states/biomesのパレット付きコンテナ(unpack_longs)とは異なり、Heightmapsは
    1個のlong(64bit)に収まりきらない端数分は詰めずに切り捨てる、常に固定9bit幅の
    パディング方式(1個のlongには 64//bits_per_entry 個までしか詰めず、1エントリが
    long境界をまたぐことは無い)。実際のワールドセーブデータで検証済み(bits=9のとき
    256要素に対して37 long、tight方式が期待する36 longとは異なる)。この2つの形式を
    混同すると、算出される高さが数百ブロックずれる(実機で発覚した不具合、詳細は
    wv_worldmap.py の surface_height() を参照)。"""
    longs = [v & 0xFFFFFFFFFFFFFFFF for v in signed_longs]
    entries_per_long = 64 // bits_per_entry
    mask = (1 << bits_per_entry) - 1
    result: list[int] = []
    for i in range(count):
        long_index = i // entries_per_long
        if long_index >= len(longs):
            break
        bit_offset = (i % entries_per_long) * bits_per_entry
        result.append((longs[long_index] >> bit_offset) & mask)
    return result
