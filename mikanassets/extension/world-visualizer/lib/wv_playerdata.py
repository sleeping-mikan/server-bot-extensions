"""wv_playerdata — usercache.json / playerdata(.dat) の読み取りとアイテム抽出。

usercache.json からプレイヤー名→UUIDを引き、playerdata(.dat, 無ければ .dat_old)を読む。
RCONの `data get entity` と違いオフライン中のプレイヤーでも参照できる(サーバーが
起動している必要すらない)。一度もサーバーに参加したことのない名前は usercache.json に
存在しないため取得できない。

配置場所は `<level-name>/playerdata/` と `<level-name>/players/data/` の両方を試す
(実機検証したところ、現行バージョンでは後者に変わっていた。ただし実際にプレイヤーが
参加した後のファイル名までは実プレイヤーを参加させて確認できておらず、既存の
`<uuid>.dat` 形式を仮定した未検証のフォールバックであることに注意)。

1.20.5以降で導入されたアイテムコンポーネント形式("count"がint、"tag"が"components"に
変更)と、それ以前の形式("Count"がbyte、"tag"がcompound)の両方から id / 数量を読める
ようにしているが、エンチャントやカスタム名などの詳細情報までは表示しない(アイテムIDと
個数のみ)。
"""

from __future__ import annotations

import gzip
import json

from bot.extension_api import ctx

import wv_serverfiles
from wv_nbt import NBTReader

logger = ctx.extension_logger

ARMOR_SLOTS: dict[int, str] = {103: "ヘルメット", 102: "チェストプレート", 101: "レギンス", 100: "ブーツ", -106: "オフハンド"}


def load_usercache() -> list[dict]:
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


def resolve_uuid(player_name: str) -> str | None:
    matches = [e for e in load_usercache() if str(e.get("name", "")).lower() == player_name.lower()]
    if not matches:
        return None
    return matches[-1].get("uuid")


def load_player_nbt(uuid: str) -> dict | None:
    level_dir = ctx.server_path / wv_serverfiles.level_name()
    # 実機検証したところ、現行バージョンでは "playerdata/" ではなく "players/data/" に
    # 配置場所が変わっていた(wv_worldmap.py の region_dir と同様の傾向)。ただし実際に
    # プレイヤーが参加した後のファイル名まではこの検証環境では確認できていない
    # (テスト時に実プレイヤーを参加させられなかったため)ので、両方の場所を
    # 同じファイル名規則で試す形にしてある。
    for base in (level_dir / "playerdata", level_dir / "players" / "data"):
        for suffix in (".dat", ".dat_old"):
            path = base / f"{uuid}{suffix}"
            if not path.exists():
                continue
            try:
                with path.open("rb") as f:
                    raw = gzip.decompress(f.read())
                return NBTReader(raw).read_root()
            except Exception as e:
                logger.error(f"failed to parse playerdata for {uuid} ({e})")
                continue
    return None


def extract_items(player_nbt: dict) -> list[dict]:
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


def extract_armor_lines(items: list[dict]) -> list[str]:
    by_slot = {item["slot"]: item for item in items}
    lines: list[str] = []
    for slot, label in ARMOR_SLOTS.items():
        item = by_slot.get(slot)
        if item:
            name = item["id"].split(":", 1)[-1].replace("_", " ")
            lines.append(f"{label}: {name} x{item['count']}")
    return lines
